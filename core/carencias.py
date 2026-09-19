"""
Registro de lo que Jarvis NO sabe hacer.

Cada vez que le pides algo para lo que no tiene herramienta, queda anotado aquí
con lo que dijiste exactamente. Ese archivo es la lista de trabajo: al revisarlo
se ve qué habilidades faltan de verdad, ordenadas por cuántas veces las has
pedido, en lugar de por lo que a alguien le parezca interesante construir.

UNA CARENCIA SE DETECTA DE CUATRO FORMAS, porque "no puedo hacer eso" se
manifiesta de maneras distintas y cada vía sola dejaría huecos:

  1. El modelo llama a una herramienta que no existe. Es la señal más fuerte:
     se ha inventado el nombre porque esperaba que existiera, y ese nombre
     inventado suele describir bastante bien lo que hacía falta.
  2. El guardián rechaza una acción por no estar en la lista blanca. Puede ser
     una habilidad nueva o solo un permiso que falta, y la diferencia importa
     al revisarlo.
  3. El propio modelo lo anota, llamando a `anotar_carencia` cuando entiende
     que le estás pidiendo algo que no puede hacer.
  4. Se deduce de su respuesta: si dijo que no sabe y no ejecutó nada, queda
     anotado igualmente.

La cuarta es la que de verdad sostiene el registro, y está ahí por una razón
medida: las tres primeras dependen de que el modelo llame a alguna herramienta,
y un modelo de 8B no lo hace de forma consistente. Probando en este equipo, ante
"ponme una alarma" llegó a escribir la llamada como texto plano en vez de
emitirla, y otra vez preguntó "¿quieres que lo anote?" en lugar de anotarlo. En
ambos casos la carencia se habría perdido. La cuarta vía no depende de que el
modelo colabore.

El formato es JSON por líneas para poder leerlo tal cual, filtrarlo y contarlo
sin depender de ninguna herramienta.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
RUTA_CARENCIAS = RAIZ / "data" / "carencias.jsonl"

# Tope de peticiones guardadas por carencia. Sirven para entender qué querías
# decir; guardarlas todas solo engorda el archivo sin añadir información.
MAX_EJEMPLOS = 5


class Origen(str, Enum):
    HERRAMIENTA_INVENTADA = "herramienta_inventada"
    ACCION_NO_PERMITIDA = "accion_no_permitida"
    DECLARADA_POR_EL_MODELO = "declarada_por_el_modelo"
    DEDUCIDA_DE_LA_RESPUESTA = "deducida_de_la_respuesta"


# Formas en que Jarvis dice que no sabe hacer algo. Se buscan en su respuesta
# cuando no ejecutó ninguna acción, que es la señal de que se quedó en palabras.
_NEGATIVAS = re.compile(
    r"\b("
    # "No sé" seguido de un infinitivo o de "cómo": así se enuncia no saber
    # HACER algo. Escrito sin más, "no se" es el pronombre reflexivo y aparece
    # en frases que no tienen nada que ver: lo detectó una prueba con la
    # respuesta "un robot que no se cansa de responder preguntas", que se
    # anotaba como habilidad pendiente.
    r"no s[eé]\s+(?:\w+(?:ar|er|ir)\b|c[oó]mo\b)|"
    r"no s[eé]\s*[,.!]|"
    r"no puedo\s+\w+(?:ar|er|ir)\b|"
    r"no soy capaz de|no dispongo de|no cuento con|"
    r"no tengo (?:forma|manera|acceso|ninguna herramienta|herramientas)|"
    r"no est[aá] entre mis|no forma parte de mis|"
    r"no tengo esa (?:capacidad|funci[oó]n|habilidad)"
    r")",
    re.IGNORECASE,
)

# Frases que parecen negativas pero no lo son: Jarvis respondiendo sobre el
# mundo, no sobre sus capacidades. Sin esto, "no sé quién ganó el mundial" se
# anotaría como una habilidad que falta.
_FALSAS_NEGATIVAS = re.compile(
    r"\b("
    # Ignorancia sobre el mundo, no sobre sus capacidades.
    r"no s[eé]\s+(qui[eé]n|qu[eé]\s+a[ñn]o|cu[aá]ndo|d[oó]nde|si\b)|"
    # "No sé por qué pasó eso" habla de una causa, no de algo que no sepa
    # hacer. Lo detectó una prueba con un chiste propio: "se me ha estropeado
    # el teclado y no sé por qué".
    r"no s[eé]\s+por\s+qu[eé]|"
    r"no tengo esa informaci[oó]n|no me consta|no estoy seguro de si"
    r")\b",
    re.IGNORECASE,
)

# Cuánto del principio de la respuesta se mira. Cuando Jarvis no sabe hacer
# algo lo dice enseguida, no en el párrafo cuarto. Mirar el texto entero es lo
# que hacía que un chiste largo con un "no tengo" dentro se anotara como una
# habilidad que falta.
CARACTERES_DE_CABECERA = 160

# Diálogo entrecomillado. Lo que dice un personaje dentro de un chiste o una
# cita no es Jarvis hablando de sí mismo, así que se quita antes de buscar.
# Este fue el fallo real: los chistes que contó incluían frases como
# "Soy ingeniero, no sé nada de drogas" y "Lo siento, no tengo vaso", y ambas
# acabaron en la lista de habilidades pendientes.
_ENTRECOMILLADO = re.compile(r'["«"“”].*?["»"“”]', re.DOTALL)

# Señales de que la respuesta es un relato y no una respuesta sobre Jarvis:
# chistes, cuentos, citas. Aquí las negativas son de un personaje.
_ES_UN_RELATO = re.compile(
    r"\b(un d[ií]a|[eé]rase una vez|va un |entra en un bar|"
    r"le dice|le responde|le pregunta|le contesta|"
    r"se encuentra con|resulta que)\b",
    re.IGNORECASE,
)


@dataclass
class Carencia:
    """Una cosa que Jarvis no supo hacer."""

    momento: str
    origen: str
    que_falta: str  # Nombre de la herramienta o descripción de la capacidad.
    peticion: str  # Lo que pediste, con tus palabras.
    sesion: str = ""
    detalle: str = ""  # Motivo del rechazo, argumentos inventados, etc.
    estado: str = "pendiente"  # pendiente | propuesta | aprobada | descartada


@dataclass
class Resumen:
    """Una carencia agrupada, con todas las veces que apareció."""

    que_falta: str
    origen: str
    veces: int
    ejemplos: list[str] = field(default_factory=list)
    primera_vez: str = ""
    ultima_vez: str = ""
    estado: str = "pendiente"


class RegistroDeCarencias:
    def __init__(self, ruta: Path = RUTA_CARENCIAS) -> None:
        self.ruta = ruta

    # -- Escritura -----------------------------------------------------------

    def anotar(
        self,
        origen: Origen,
        que_falta: str,
        peticion: str = "",
        sesion: str = "",
        detalle: str = "",
    ) -> None:
        """Deja constancia de una carencia. Nunca falla de forma ruidosa.

        Si esto reventara, rompería la conversación por intentar apuntar algo
        para más tarde, que sería absurdo: lo peor que puede pasar es perder
        una anotación.
        """
        carencia = Carencia(
            momento=datetime.now().isoformat(timespec="seconds"),
            origen=origen.value,
            que_falta=(que_falta or "sin identificar").strip()[:120],
            peticion=(peticion or "").strip()[:500],
            sesion=sesion,
            detalle=(detalle or "").strip()[:400],
        )
        try:
            self.ruta.parent.mkdir(parents=True, exist_ok=True)
            with open(self.ruta, "a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(carencia), ensure_ascii=False) + "\n")
        except Exception:
            # Se captura todo, no solo OSError: una ruta inválida lanza
            # ValueError, y serializar algo raro podría lanzar TypeError.
            # Perder una anotación es aceptable; romper la conversación por
            # intentar apuntar algo para más tarde, no.
            pass

    # -- Lectura -------------------------------------------------------------

    def todas(self) -> list[Carencia]:
        if not self.ruta.exists():
            return []

        carencias: list[Carencia] = []
        try:
            with open(self.ruta, "r", encoding="utf-8") as f:
                for linea in f:
                    linea = linea.strip()
                    if not linea:
                        continue
                    try:
                        datos = json.loads(linea)
                    except json.JSONDecodeError:
                        # Una línea rota no debe invalidar el resto del archivo.
                        continue
                    # Se filtran claves desconocidas para que un archivo escrito
                    # por una versión anterior siga leyéndose.
                    validas = {
                        k: v
                        for k, v in datos.items()
                        if k in Carencia.__dataclass_fields__
                    }
                    try:
                        carencias.append(Carencia(**validas))
                    except TypeError:
                        continue
        except OSError:
            return []

        return carencias

    def resumen(self, solo_pendientes: bool = True) -> list[Resumen]:
        """Agrupa las carencias repetidas y las ordena por frecuencia.

        Ordenar por cuántas veces lo has pedido es lo que hace útil el archivo:
        indica qué construir primero según lo que de verdad echas de menos, no
        según lo que parezca más vistoso.
        """
        carencias = self.todas()
        if solo_pendientes:
            carencias = [c for c in carencias if c.estado == "pendiente"]

        grupos: dict[tuple[str, str], list[Carencia]] = {}
        for c in carencias:
            grupos.setdefault((c.que_falta.lower(), c.origen), []).append(c)

        resumenes = [
            Resumen(
                que_falta=lista[0].que_falta,
                origen=origen,
                veces=len(lista),
                # Sin repetir: si has pedido lo mismo con las mismas palabras
                # cinco veces, verlo cinco veces no añade nada. Lo que importa
                # al revisar son las formas DISTINTAS en que lo has pedido.
                ejemplos=list(
                    dict.fromkeys(c.peticion for c in lista if c.peticion)
                )[:MAX_EJEMPLOS],
                primera_vez=min(c.momento for c in lista),
                ultima_vez=max(c.momento for c in lista),
                estado=lista[0].estado,
            )
            for (_, origen), lista in grupos.items()
        ]

        # Más pedido primero; a igualdad, lo más reciente antes.
        resumenes.sort(key=lambda r: (-r.veces, r.ultima_vez), reverse=False)
        return resumenes

    def cuenta_por_origen(self) -> dict[str, int]:
        return dict(Counter(c.origen for c in self.todas()))

    # -- Cambios de estado ---------------------------------------------------

    def marcar(self, que_falta: str, estado: str) -> int:
        """Cambia el estado de todas las anotaciones de una carencia.

        Se usa tras decidir qué hacer con cada propuesta, para que la próxima
        revisión no vuelva a sacar lo ya tratado. Reescribe el archivo entero
        porque son pocas líneas y así queda consistente.
        """
        carencias = self.todas()
        if not carencias:
            return 0

        objetivo = que_falta.strip().lower()
        cambiadas = 0
        for c in carencias:
            if c.que_falta.strip().lower() == objetivo:
                c.estado = estado
                cambiadas += 1

        if cambiadas:
            try:
                with open(self.ruta, "w", encoding="utf-8") as f:
                    for c in carencias:
                        f.write(json.dumps(asdict(c), ensure_ascii=False) + "\n")
            except OSError:
                return 0

        return cambiadas


def parece_una_carencia(respuesta: str, hubo_acciones: bool) -> bool:
    """True si Jarvis dijo que no sabe hacer algo, sin llegar a intentarlo.

    Es la vía de detección que no depende de que el modelo colabore, y por eso
    es la más fiable. Las otras tres necesitan que llame a una herramienta
    —inventada, bloqueada o la de anotar— y un modelo de 8B no lo hace de forma
    consistente: a veces se limita a contestar "no sé hacer eso", o incluso
    pregunta si quieres que lo anote en vez de anotarlo.

    Solo se mira cuando NO se ejecutó ninguna acción. Si Jarvis hizo algo, la
    petición se resolvió aunque la respuesta lleve la palabra "no".
    """
    if hubo_acciones or not respuesta:
        return False

    # Un relato (chiste, cuento, cita) lleva negativas que dice un personaje,
    # no Jarvis. Contar un chiste no es una habilidad que falte.
    if _ES_UN_RELATO.search(respuesta):
        return False

    # Se quita el diálogo entrecomillado antes de buscar, por el mismo motivo.
    sin_dialogo = _ENTRECOMILLADO.sub(" ", respuesta)

    # Y solo se mira el principio: cuando Jarvis no sabe hacer algo lo dice de
    # entrada, no enterrado en mitad de una respuesta larga.
    cabecera = sin_dialogo[:CARACTERES_DE_CABECERA]

    if _FALSAS_NEGATIVAS.search(cabecera):
        return False
    return bool(_NEGATIVAS.search(cabecera))


registro_de_carencias = RegistroDeCarencias()

# La última petición del usuario, para poder guardarla junto a la carencia. Lo
# pone el agente al empezar cada turno: sin esto, el archivo tendría el nombre
# de la herramienta que faltaba pero no qué querías conseguir, que es lo que de
# verdad hace falta para decidir si merece la pena construirla.
_peticion_actual = ""
_sesion_actual = ""


def fijar_contexto(peticion: str, sesion: str = "") -> None:
    global _peticion_actual, _sesion_actual
    _peticion_actual = peticion
    _sesion_actual = sesion


def contexto() -> tuple[str, str]:
    return _peticion_actual, _sesion_actual
