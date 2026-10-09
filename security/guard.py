"""
El guardián de Jarvis.

Toda acción que Jarvis quiera ejecutar sobre el sistema pasa por aquí primero.
Ninguna habilidad habla con el sistema operativo directamente: piden permiso,
y el guardián decide contra la política definida en policy.yaml.

El diseño parte de asumir que el modelo puede equivocarse o ser manipulado.
Por eso las comprobaciones no confían en lo que el modelo dice que va a hacer,
sino que verifican la acción ya resuelta: la ruta real tras seguir enlaces, el
comando ya montado, la extensión efectiva del archivo.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import yaml

from security.parada import interruptor

log = logging.getLogger("jarvis.guardian")

RAIZ = Path(__file__).resolve().parent.parent

# Carpetas del propio proyecto que Jarvis nunca debe tocar por la vía de los
# archivos, aunque la política deje pasar la carpeta que las contiene. Se
# derivan de dónde está instalado el proyecto, no de una ruta escrita a mano.
_RUTAS_PROPIAS_PROHIBIDAS = ("security", "data", ".env", ".git", "venv")

# Tamaño a partir del cual el registro de auditoría se rota.
_MAX_BYTES_REGISTRO = 2 * 1024 * 1024
RUTA_POLITICA = RAIZ / "security" / "policy.yaml"

# Cuántos intentos de hacer algo prohibido, y en cuánto tiempo, bastan para
# que Jarvis se detenga solo. Dos son deliberadamente pocos: el coste de una
# parada de más es que la rearmes; el de una de menos puede ser tu equipo.
INTENTOS_ANTES_DE_PARAR = 2
MINUTOS_DE_VIGILANCIA = 5


class Nivel(str, Enum):
    """Qué hacer con una acción según la política."""

    PERMITIR = "permitir"
    CONFIRMAR = "confirmar"
    PROHIBIR = "prohibir"


class Decision(str, Enum):
    """Resultado de una evaluación del guardián."""

    CONCEDIDO = "concedido"
    NECESITA_CONFIRMACION = "necesita_confirmacion"
    DENEGADO = "denegado"


@dataclass
class Peticion:
    """Una acción que una habilidad quiere ejecutar.

    'accion' debe ser una de las claves de la sección 'acciones' de policy.yaml.
    'objetivo' es sobre qué actúa: una ruta, un comando, un nombre de app.
    """

    accion: str
    objetivo: str = ""
    detalles: dict[str, Any] = field(default_factory=dict)
    motivo: str = ""  # Explicación en lenguaje natural, para mostrarte al confirmar.
    # La ruta tal y como la validó el guardián (absoluta, con enlaces ya
    # resueltos). La habilidad debe usar ESTA y no la cadena original: si no,
    # un enlace cambiado entre la validación y el uso saltaría la política.
    ruta_resuelta: str | None = None


@dataclass
class Veredicto:
    """Lo que el guardián responde a una petición."""

    decision: Decision
    razon: str
    peticion: Peticion

    @property
    def permitida(self) -> bool:
        return self.decision is Decision.CONCEDIDO


class ErrorDePolitica(Exception):
    """Se lanza cuando una acción viola la política de seguridad."""


class Guardian:
    """Aplica la política de seguridad y deja registro de todo."""

    def __init__(self, ruta_politica: Path = RUTA_POLITICA) -> None:
        self.ruta_politica = ruta_politica
        self.politica = self._cargar_politica()
        # La voz, la interfaz y el agente llaman al guardián desde hilos
        # distintos; los contadores no son atómicos sin esto.
        self._lock = threading.RLock()
        self._historial_confirmaciones: list[datetime] = []
        self._acciones_peticion_actual = 0
        # Intentos de hacer algo prohibido, para detectar insistencia.
        self._intentos_prohibidos: list[datetime] = []
        self._acciones_prohibidas: list[str] = []

        registro = self.politica.get("registro", {})
        self.registro_activo = registro.get("activo", True)
        self.ruta_registro = RAIZ / registro.get("ruta", "security/audit.log")
        self.registrar_rechazadas = registro.get("incluir_rechazadas", True)

    # -- Carga de la política ------------------------------------------------

    def _cargar_politica(self) -> dict[str, Any]:
        if not self.ruta_politica.exists():
            raise ErrorDePolitica(
                f"No existe la política de seguridad en {self.ruta_politica}. "
                "Jarvis no arranca sin ella."
            )
        with open(self.ruta_politica, "r", encoding="utf-8") as f:
            politica = yaml.safe_load(f)

        # Sin estas secciones no se puede decidir nada, así que es mejor fallar
        # al arrancar que descubrirlo a mitad de una operación.
        for seccion in ("acciones", "rutas", "archivos"):
            if seccion not in politica:
                raise ErrorDePolitica(
                    f"La política no tiene la sección obligatoria '{seccion}'."
                )
        return politica

    def recargar(self) -> None:
        """Vuelve a leer policy.yaml. Útil tras editarla a mano."""
        self.politica = self._cargar_politica()

    # -- Evaluación principal ------------------------------------------------

    def evaluar(self, peticion: Peticion) -> Veredicto:
        """Decide si una acción puede ejecutarse. No ejecuta nada."""
        veredicto = self._evaluar_sin_registrar(peticion)
        if self.registro_activo and (
            veredicto.permitida or self.registrar_rechazadas
        ):
            self._registrar(veredicto)
        return veredicto

    def _evaluar_sin_registrar(self, peticion: Peticion) -> Veredicto:
        def denegar(razon: str) -> Veredicto:
            return Veredicto(Decision.DENEGADO, razon, peticion)

        # 0. El interruptor de emergencia gana sobre todo lo demás. Va primero
        #    para que estando activado no se evalúe ni se registre nada más.
        if interruptor.activado:
            return denegar(
                "El interruptor de emergencia está activado. Jarvis no puede "
                "ejecutar ninguna acción hasta que lo rearmes desde la ventana "
                "o el menú de la bandeja."
            )

        # 1. Tope de acciones por petición: corta bucles del modelo.
        limites = self.politica.get("limites", {})
        max_acciones = limites.get("max_acciones_por_peticion", 15)
        with self._lock:
            acciones_hechas = self._acciones_peticion_actual
        if acciones_hechas >= max_acciones:
            return denegar(
                f"Se alcanzó el límite de {max_acciones} acciones en una sola "
                "petición. Se detiene por seguridad."
            )

        # 2. La acción debe existir en la política. Una acción desconocida es
        #    una acción no autorizada: no hay comportamiento por defecto.
        nivel_bruto = self.politica["acciones"].get(peticion.accion)
        if nivel_bruto is None:
            # Puede ser una habilidad que falta o solo un permiso sin añadir.
            # La diferencia se decide al revisar, no aquí.
            from core.carencias import Origen, contexto, registro_de_carencias

            peticion_original, sesion = contexto()
            registro_de_carencias.anotar(
                Origen.ACCION_NO_PERMITIDA,
                que_falta=peticion.accion,
                peticion=peticion_original,
                sesion=sesion,
                detalle=f"Sobre: {peticion.objetivo}" if peticion.objetivo else "",
            )
            return denegar(
                f"La acción '{peticion.accion}' no está en la lista blanca. "
                "Para permitirla, añádela a policy.yaml."
            )

        try:
            nivel = Nivel(nivel_bruto)
        except ValueError:
            return denegar(
                f"La acción '{peticion.accion}' tiene un nivel inválido "
                f"('{nivel_bruto}') en la política."
            )

        if nivel is Nivel.PROHIBIR:
            self._anotar_intento_prohibido(peticion)
            return denegar(
                f"La acción '{peticion.accion}' está prohibida de forma "
                "absoluta. No se puede autorizar ni confirmándola."
            )

        # 3. Validaciones específicas según el tipo de acción.
        if self._es_accion_de_archivo(peticion.accion):
            problema = self._validar_ruta(peticion)
            if problema:
                return denegar(problema)

        if peticion.accion == "ejecutar_comando":
            problema = self._validar_comando(peticion.objetivo)
            if problema:
                return denegar(problema)

        # Abrir una dirección fuera de la lista de confianza se confirma aunque
        # la acción sea 'permitir': una página puede llevar datos en la propia
        # dirección, y así una inyección de prompt no puede sacarlos sin que lo veas.
        url_no_confiable = False
        if peticion.accion == "abrir_url":
            problema, confiable = self._evaluar_url(peticion.objetivo)
            if problema:
                return denegar(problema)
            url_no_confiable = not confiable

        # 4. Tope de confirmaciones por hora: evita que te acostumbres a decir
        #    que sí en cadena y que un fallo se convierta en muchos.
        if nivel is Nivel.CONFIRMAR or url_no_confiable:
            if not self._hay_cupo_de_confirmaciones():
                max_conf = limites.get("max_confirmaciones_por_hora", 30)
                return denegar(
                    f"Se superó el límite de {max_conf} confirmaciones por hora. "
                    "Es una señal de que algo se está descontrolando."
                )
            razon = (
                "Esta dirección no está en la lista de sitios de confianza."
                if url_no_confiable and nivel is not Nivel.CONFIRMAR
                else "Esta acción modifica tu sistema y necesita tu aprobación."
            )
            return Veredicto(Decision.NECESITA_CONFIRMACION, razon, peticion)

        return Veredicto(Decision.CONCEDIDO, "Acción permitida por la política.", peticion)

    # -- Validaciones específicas -------------------------------------------

    _ACCIONES_DE_ARCHIVO = {
        "leer_archivo",
        "listar_carpeta",
        "buscar_archivos",
        "crear_archivo",
        "escribir_archivo",
        "mover_archivo",
        "copiar_archivo",
        "borrar_archivo",
        "crear_carpeta",
    }

    _ACCIONES_DE_ESCRITURA = {
        "crear_archivo",
        "escribir_archivo",
        "mover_archivo",
        "copiar_archivo",
        "borrar_archivo",
        "crear_carpeta",
    }

    def _es_accion_de_archivo(self, accion: str) -> bool:
        return accion in self._ACCIONES_DE_ARCHIVO

    def _validar_ruta(self, peticion: Peticion) -> str | None:
        """Devuelve el motivo del rechazo, o None si la ruta es aceptable."""
        if not peticion.objetivo:
            return "La acción sobre archivos no indica ninguna ruta."

        sospechosa = self._ruta_sospechosa(peticion.objetivo)
        if sospechosa:
            return sospechosa

        # resolve() sigue enlaces simbólicos y normaliza '..'. Sin esto, una
        # ruta como "Documents/../../Windows/System32" pasaría los filtros.
        try:
            ruta = Path(peticion.objetivo).expanduser().resolve()
        except (OSError, ValueError) as e:
            return f"La ruta '{peticion.objetivo}' no es válida: {e}"

        # Un enlace puede apuntar a una unidad de red aunque la ruta escrita
        # no lo parezca. El prefijo '\\?\' con unidad es solo la forma larga de
        # una ruta local, así que se quita antes de mirar.
        texto_resuelto = str(ruta)
        if texto_resuelto.startswith("\\\\?\\") and re.match(
            r"^\\\\\?\\[A-Za-z]:", texto_resuelto
        ):
            texto_resuelto = texto_resuelto[4:]
        if texto_resuelto.startswith("\\\\"):
            return "La ruta apunta a un recurso de red o de dispositivo."

        rutas = self.politica["rutas"]

        # Las prohibidas ganan sobre todo lo demás. Se comprueban primero.
        for prohibida in self._rutas_prohibidas():
            if self._esta_dentro(ruta, prohibida):
                return (
                    f"La ruta está dentro de '{prohibida}', que es una zona "
                    "prohibida de forma permanente."
                )

        escribe = peticion.accion in self._ACCIONES_DE_ESCRITURA
        permitidas = rutas.get("escritura" if escribe else "lectura", [])
        etiqueta = "escritura" if escribe else "lectura"

        if not any(self._esta_dentro(ruta, base) for base in permitidas):
            return (
                f"La ruta '{ruta}' está fuera de las carpetas autorizadas para "
                f"{etiqueta}. Carpetas permitidas: {', '.join(permitidas)}."
            )

        # Extensión: solo para archivos, no para carpetas. Al copiar o mover a
        # una carpeta existente, la extensión del archivo final la comprueba la
        # propia habilidad cuando ya sabe el nombre completo.
        a_carpeta = peticion.accion in ("copiar_archivo", "mover_archivo") and ruta.is_dir()
        if not a_carpeta and peticion.accion not in (
            "listar_carpeta", "crear_carpeta", "buscar_archivos"
        ):
            extensiones = self.politica["archivos"].get("extensiones_permitidas", [])
            if extensiones and ruta.suffix.lower() not in extensiones:
                tipo = ruta.suffix or "(sin extensión)"
                return (
                    f"El tipo de archivo '{tipo}' no está permitido. "
                    f"Permitidos: {', '.join(extensiones)}."
                )

            # Algunos tipos se pueden leer pero no escribir: un script dejado en
            # el Escritorio es código que luego puede ejecutarse.
            solo_lectura = self.politica["archivos"].get("extensiones_solo_lectura", [])
            if escribe and ruta.suffix.lower() in solo_lectura:
                return (
                    f"Los archivos '{ruta.suffix}' se pueden leer, pero no "
                    "crear ni modificar."
                )

        # Tamaño: solo tiene sentido al leer algo que ya existe.
        if peticion.accion == "leer_archivo" and ruta.is_file():
            max_mb = self.politica["archivos"].get("max_mb_lectura", 25)
            tam_mb = ruta.stat().st_size / (1024 * 1024)
            if tam_mb > max_mb:
                return (
                    f"El archivo pesa {tam_mb:.1f} MB y el límite de lectura es "
                    f"{max_mb} MB."
                )

        peticion.ruta_resuelta = str(ruta)
        return None

    @staticmethod
    def _ruta_sospechosa(objetivo: str) -> str | None:
        """Rechaza formas de escribir una ruta que esquivan la comprobación."""
        texto = objetivo.strip()
        if texto.startswith(("\\\\", "//")):
            return "No se permiten rutas de red ni de dispositivo (\\\\...)."
        # Tras la unidad ('C:'), un ':' más es un flujo alternativo de NTFS:
        # 'nota.txt:oculto.exe' parece un .txt pero esconde otro archivo.
        sin_unidad = re.sub(r"^[A-Za-z]:", "", texto)
        if ":" in sin_unidad:
            return "No se permiten flujos alternativos de datos (nombre:flujo)."
        return None

    def filtro_de_lectura(self) -> Callable[[Path], bool]:
        """Una comprobación rápida y sin registro de si una ruta se puede leer.

        Sirve para filtrar lo que encuentra una búsqueda: validar cada archivo
        con evaluar() llenaría el registro con miles de líneas. Las carpetas
        base se resuelven una sola vez.
        """

        def resolver(base: str) -> Path | None:
            try:
                return Path(os.path.normcase(Path(base).expanduser().resolve()))
            except (OSError, ValueError):
                return None

        prohibidas = [p for p in map(resolver, self._rutas_prohibidas()) if p]
        permitidas = [p for p in map(resolver, self.politica["rutas"].get("lectura", [])) if p]

        def dentro(ruta: Path, base: Path) -> bool:
            return ruta == base or base in ruta.parents

        def permitida(ruta: Path) -> bool:
            r = Path(os.path.normcase(ruta))
            if any(dentro(r, p) for p in prohibidas):
                return False
            return any(dentro(r, b) for b in permitidas)

        return permitida

    def _rutas_prohibidas(self) -> list[str]:
        """Las prohibidas de la política más las carpetas propias del proyecto."""
        propias = [str(RAIZ / nombre) for nombre in _RUTAS_PROPIAS_PROHIBIDAS]
        return list(self.politica["rutas"].get("prohibidas", [])) + propias

    def _evaluar_url(self, objetivo: str) -> tuple[str | None, bool]:
        """Devuelve (motivo de rechazo o None, si el sitio es de confianza)."""
        direccion = objetivo.strip()
        if not direccion:
            return "No se indicó ninguna dirección.", False

        esquema = re.match(r"^([a-zA-Z][a-zA-Z0-9+.\-]*):", direccion)
        if esquema:
            if esquema.group(1).lower() not in ("http", "https"):
                return (
                    f"Solo se pueden abrir direcciones http o https, no "
                    f"'{esquema.group(1)}'."
                ), False
        else:
            direccion = f"https://{direccion}"

        try:
            partes = urlparse(direccion)
            host = (partes.hostname or "").lower()
        except ValueError:
            return "La dirección no es válida.", False

        if not host:
            return "La dirección no es válida.", False
        # 'https://google.com@sitio-malo.com' engaña a quien lee solo el principio.
        if "@" in partes.netloc:
            return "No se permiten direcciones con usuario o contraseña.", False

        confiables = self.politica.get("urls", {}).get("dominios_confiables", [])
        confiable = any(
            host == d.lower() or host.endswith("." + d.lower()) for d in confiables
        )
        return None, confiable

    def _validar_comando(self, comando: str) -> str | None:
        """Devuelve el motivo del rechazo, o None si el comando es aceptable."""
        if not comando or not comando.strip():
            return "No se indicó ningún comando."

        config = self.politica.get("comandos", {})
        comando_limpio = comando.strip()

        # Los patrones prohibidos se buscan en el comando entero, sin distinguir
        # mayúsculas, porque incluyen operadores para encadenar órdenes.
        bajo = comando_limpio.lower()
        for patron in config.get("patrones_prohibidos", []):
            if patron.lower() in bajo:
                return (
                    f"El comando contiene '{patron}', que está bloqueado por "
                    "permitir encadenar o esconder otras órdenes."
                )

        programa = comando_limpio.split()[0].lower()
        programa = Path(programa).name.removesuffix(".exe")

        permitidos = [p.lower() for p in config.get("programas_permitidos", [])]
        if programa not in permitidos:
            return (
                f"El programa '{programa}' no está en la lista de programas "
                f"autorizados: {', '.join(permitidos)}."
            )

        return None

    @staticmethod
    def _esta_dentro(ruta: Path, base: str) -> bool:
        """True si 'ruta' es 'base' o está por debajo de ella."""
        try:
            base_resuelta = Path(base).expanduser().resolve()
        except (OSError, ValueError):
            return False
        # normcase unifica mayúsculas y barras en Windows: 'c:/windows' y
        # 'C:\Windows' son la misma carpeta, y resolve() no lo corrige en las
        # partes de la ruta que todavía no existen.
        try:
            Path(os.path.normcase(ruta)).relative_to(
                Path(os.path.normcase(base_resuelta))
            )
            return True
        except ValueError:
            return False

    def _anotar_intento_prohibido(self, peticion: Peticion) -> None:
        """Cuenta los intentos de hacer algo prohibido y para si insisten.

        Un intento suelto suele ser el modelo equivocándose: pide formatear
        cuando quería listar. Varios seguidos son otra cosa, y da igual si es un
        fallo del modelo o alguien manipulándolo con lo que le dice: en ambos
        casos lo correcto es parar y que lo mires tú.
        """
        ahora = datetime.now()
        with self._lock:
            self._acciones_prohibidas = (self._acciones_prohibidas + [peticion.accion])[-20:]
            ventana = ahora - timedelta(minutes=MINUTOS_DE_VIGILANCIA)
            self._intentos_prohibidos = [
                t for t in self._intentos_prohibidos if t > ventana
            ] + [ahora]
            intentos = len(self._intentos_prohibidos)
            implicadas = set(self._acciones_prohibidas)

        if intentos < INTENTOS_ANTES_DE_PARAR:
            return

        acciones = ", ".join(sorted(implicadas | {peticion.accion}))
        interruptor.activar(
            motivo=(
                f"Jarvis intentó {intentos} acciones "
                f"prohibidas en {MINUTOS_DE_VIGILANCIA} minutos "
                f"(la última: {peticion.accion} sobre '{peticion.objetivo}'). "
                f"Acciones implicadas: {acciones}. "
                "Se detuvo solo para que revises qué está pasando."
            ),
            quien="automatica",
        )

    def _hay_cupo_de_confirmaciones(self) -> bool:
        limite = self.politica.get("limites", {}).get("max_confirmaciones_por_hora", 30)
        hace_una_hora = datetime.now() - timedelta(hours=1)
        with self._lock:
            self._historial_confirmaciones = [
                t for t in self._historial_confirmaciones if t > hace_una_hora
            ]
            return len(self._historial_confirmaciones) < limite

    # -- Ejecución controlada ------------------------------------------------

    def ejecutar(
        self,
        peticion: Peticion,
        funcion: Callable[[], Any],
        pedir_confirmacion: Callable[[Peticion], bool] | None = None,
    ) -> Any:
        """Evalúa la petición y solo entonces ejecuta 'funcion'.

        Este es el único camino por el que una habilidad debe llegar al sistema.
        Si la política pide confirmación, se llama a 'pedir_confirmacion', que
        es quien muestra la acción al usuario y devuelve True o False.
        """
        veredicto = self.evaluar(peticion)

        if veredicto.decision is Decision.DENEGADO:
            raise ErrorDePolitica(veredicto.razon)

        if veredicto.decision is Decision.NECESITA_CONFIRMACION:
            if pedir_confirmacion is None:
                raise ErrorDePolitica(
                    f"'{peticion.accion}' requiere confirmación y no hay forma de "
                    "pedírtela en este contexto. Se cancela."
                )
            if not pedir_confirmacion(peticion):
                self._registrar(
                    Veredicto(Decision.DENEGADO, "Rechazada por el usuario.", peticion)
                )
                raise ErrorDePolitica("Has rechazado la acción.")
            with self._lock:
                self._historial_confirmaciones.append(datetime.now())

        with self._lock:
            self._acciones_peticion_actual += 1

        # evaluar() deja constancia de lo que se DECIDIÓ; aquí se anota lo que
        # de verdad PASÓ. Sin esta segunda línea el registro afirmaría que algo
        # se hizo aunque la habilidad fallara después.
        try:
            resultado = funcion()
        except Exception as e:
            self._registrar_resultado(peticion, f"fallo: {type(e).__name__}")
            raise
        self._registrar_resultado(peticion, "ejecutada")
        return resultado

    def nueva_peticion(self) -> None:
        """Reinicia el contador de acciones. Se llama al empezar cada turno."""
        with self._lock:
            self._acciones_peticion_actual = 0

    # -- Registro ------------------------------------------------------------

    def _registrar_resultado(self, peticion: Peticion, resultado: str) -> None:
        if self.registro_activo:
            self._escribir_registro(
                {
                    "accion": peticion.accion,
                    "objetivo": peticion.objetivo,
                    "resultado": resultado,
                }
            )

    def _registrar(self, veredicto: Veredicto) -> None:
        self._escribir_registro(
            {
                "accion": veredicto.peticion.accion,
                "objetivo": veredicto.peticion.objetivo,
                "decision": veredicto.decision.value,
                "razon": veredicto.razon,
            }
        )

    def _escribir_registro(self, datos: dict[str, Any]) -> None:
        entrada = {"momento": datetime.now().isoformat(timespec="seconds"), **datos}
        # Un objetivo enorme (un texto pegado como "ruta") no debe inflar el log.
        if len(str(entrada.get("objetivo", ""))) > 300:
            entrada["objetivo"] = str(entrada["objetivo"])[:300] + "…"
        try:
            with self._lock:
                self.ruta_registro.parent.mkdir(parents=True, exist_ok=True)
                self._rotar_registro()
                with open(self.ruta_registro, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entrada, ensure_ascii=False) + "\n")
        except OSError:
            # Un fallo al registrar nunca debe tumbar a Jarvis, pero tampoco
            # debe pasar desapercibido.
            log.error("No se pudo escribir en el registro: %s", self.ruta_registro)

    def _rotar_registro(self) -> None:
        """Conserva el registro anterior como .1 cuando el actual crece mucho."""
        try:
            if self.ruta_registro.stat().st_size < _MAX_BYTES_REGISTRO:
                return
        except OSError:
            return
        anterior = self.ruta_registro.with_name(self.ruta_registro.name + ".1")
        os.replace(self.ruta_registro, anterior)


# Instancia compartida: todas las habilidades usan el mismo guardián para que
# los contadores y el registro sean coherentes.
guardian = Guardian()
