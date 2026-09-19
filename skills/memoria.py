"""
Lo que Jarvis recuerda de ti.

Hasta ahora la tabla de hechos existía y se inyectaba en el prompt, pero nada
escribía en ella: cada sesión empezaba en blanco y Jarvis no podía aprender
nada. Estas herramientas son las que la llenan.

QUÉ SE GUARDA Y QUÉ NO
Solo hechos duraderos sobre ti: cómo te llamas, a qué te dedicas, cómo prefieres
que te responda, nombres de gente cercana. No el contenido de la conversación,
que ya está en el historial, ni cosas de un rato ("estoy cansado", "hoy llueve").
La diferencia importa porque cada hecho ocupa sitio en el prompt de TODAS las
peticiones siguientes.

EL COSTE QUE HAY QUE TENER EN CUENTA
El prompt de sistema es lo que Ollama reaprovecha entre peticiones, y solo lo
reaprovecha si coincide carácter por carácter. Añadir un hecho lo cambia, así
que la siguiente pregunta paga de nuevo los ~9 segundos de procesar el prompt
entero. Por eso hay un tope de hechos y por eso el agente vuelve a calentar el
modelo en segundo plano justo después de guardar uno.
"""

from __future__ import annotations

import re
import unicodedata

from core.memory import Memoria
from skills.registro import registro

# Tope de hechos. No es arbitrario: cada uno se manda en el prompt de cada
# petición, así que cien hechos serían cientos de tokens extra en todo lo que
# hagas. Treinta cubren de sobra lo que de verdad define a una persona.
MAX_HECHOS = 30

# Longitudes máximas. Un "hecho" de un párrafo no es un hecho, es una nota, y
# engorda el prompt sin aportar.
MAX_CLAVE = 60
MAX_VALOR = 200

CATEGORIAS = ("personal", "preferencia", "trabajo", "contacto", "general")

# Una única instancia compartida, para no abrir la base de datos en cada uso.
_memoria = Memoria()

# Se avisa al agente de que los hechos cambiaron, para que vuelva a calentar el
# modelo. Lo rellena el agente al arrancar; si nadie lo hace, no pasa nada.
_al_cambiar = None


def fijar_aviso_de_cambio(funcion) -> None:
    """Registra a quién avisar cuando los hechos cambian."""
    global _al_cambiar
    _al_cambiar = funcion


def _avisar_cambio() -> None:
    if _al_cambiar is not None:
        try:
            _al_cambiar()
        except Exception:
            # Que falle el recalentamiento solo significa una pregunta lenta.
            pass


def normalizar_clave(clave: str) -> str:
    """Deja la clave en una forma canónica para que no se dupliquen hechos.

    "Nombre", "nombre" y "NOMBRE " son el mismo dato. Sin normalizar acabarías
    con tres entradas distintas diciendo lo mismo y ocupando sitio tres veces.
    """
    sin_tildes = "".join(
        c
        for c in unicodedata.normalize("NFD", clave.strip().lower())
        if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"[^a-z0-9]+", "_", sin_tildes).strip("_")[:MAX_CLAVE]


@registro.registrar(
    nombre="recordar_dato",
    descripcion=(
        "Guarda un dato duradero sobre el usuario para recordarlo en futuras "
        "conversaciones. Úsala cuando te cuente algo estable sobre sí mismo "
        "(su nombre, a qué se dedica, cómo se llaman sus allegados, dónde "
        "vive) o cuando te diga cómo quiere que te comportes ('llámame por mi "
        "nombre', 'respóndeme más corto'). También cuando te pida "
        "explícitamente que recuerdes algo. NO la uses para cosas pasajeras "
        "como el tiempo que hace hoy o cómo se siente ahora mismo, ni para "
        "guardar lo que ya se ha dicho en esta conversación."
    ),
    accion="recordar_dato",
    parametros={
        "clave": {
            "type": "string",
            "description": (
                "Qué tipo de dato es, en una o dos palabras. Por ejemplo: "
                "'nombre', 'profesion', 'hermano', 'ciudad', "
                "'longitud_respuestas'."
            ),
        },
        "valor": {
            "type": "string",
            "description": "El dato en sí, en una frase corta.",
        },
        "categoria": {
            "type": "string",
            "description": (
                "Una de: personal, preferencia, trabajo, contacto, general."
            ),
            "requerido": False,
        },
    },
    campo_objetivo="clave",
)
def recordar_dato(clave: str, valor: str, categoria: str = "general") -> str:
    clave_limpia = normalizar_clave(clave)
    valor = (valor or "").strip()[:MAX_VALOR]

    if not clave_limpia or not valor:
        return "No me has dicho qué recordar. NO digas que lo has guardado."

    if categoria not in CATEGORIAS:
        categoria = "general"

    existentes = _memoria.hechos()
    es_nuevo = clave_limpia not in existentes

    if es_nuevo and len(existentes) >= MAX_HECHOS:
        return (
            f"Ya recuerdo {MAX_HECHOS} datos, que es el máximo. Pídele al "
            "usuario que te diga cuál olvidar primero, con olvidar_dato. "
            "NO digas que lo has guardado."
        )

    anterior = existentes.get(clave_limpia)
    _memoria.recordar(clave_limpia, valor, categoria)
    _avisar_cambio()

    if anterior and anterior != valor:
        return f"Actualizado: {clave_limpia} era «{anterior}» y ahora es «{valor}»."
    return f"Guardado: {clave_limpia} = «{valor}»."


@registro.registrar(
    nombre="olvidar_dato",
    descripcion=(
        "Borra un dato que Jarvis recordaba sobre el usuario. Úsala cuando te "
        "pida que olvides algo o cuando un dato haya dejado de ser cierto."
    ),
    accion="olvidar_dato",
    parametros={
        "clave": {
            "type": "string",
            "description": "Qué dato borrar, con la misma palabra con la que se guardó.",
        }
    },
    campo_objetivo="clave",
)
def olvidar_dato(clave: str) -> str:
    clave_limpia = normalizar_clave(clave)
    if not clave_limpia:
        return "No me has dicho qué olvidar. NO digas que lo has borrado."

    if _memoria.olvidar(clave_limpia):
        _avisar_cambio()
        return f"Olvidado: {clave_limpia}."

    conocidas = ", ".join(sorted(_memoria.hechos())) or "nada"
    return (
        f"No recordaba nada llamado '{clave_limpia}'. Lo que sé: {conocidas}. "
        "NO digas que lo has borrado."
    )


@registro.registrar(
    nombre="consultar_datos",
    descripcion=(
        "Dice qué datos recuerda Jarvis sobre el usuario. Úsala cuando "
        "pregunte qué sabes de él, qué recuerdas o qué tienes guardado."
    ),
    accion="consultar_datos",
    parametros={},
)
def consultar_datos() -> str:
    hechos = _memoria.hechos()
    if not hechos:
        return (
            "No recuerdo nada todavía. Dile al usuario que puede contarte "
            "cosas sobre él y las guardarás."
        )

    lineas = [f"{clave}: {valor}" for clave, valor in sorted(hechos.items())]
    return f"Recuerdo {len(hechos)} datos:\n" + "\n".join(lineas)
