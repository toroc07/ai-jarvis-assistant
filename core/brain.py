"""
El cerebro de Jarvis: decide qué modelo responde a cada cosa.

La idea es que el modelo local (gratis, privado, sin latencia de red) resuelva
la inmensa mayoría de las peticiones, y que Claude entre solo cuando la tarea
lo justifica. Así el coste mensual se queda en casi nada sin renunciar a tener
razonamiento de calidad cuando de verdad hace falta.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable

import httpx

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

_URL_OLLAMA_POR_DEFECTO = "http://localhost:11434"
_HOSTS_LOCALES = {"localhost", "127.0.0.1", "::1"}


def url_de_ollama_segura(url: str, permitir_remoto: bool = False) -> str:
    """Devuelve la URL de Ollama, o la local si la configurada no es de este equipo.

    Todo lo que dices y todo lo que lee Jarvis viaja a esa dirección, sin cifrar
    y sin autenticación. Si una variable de entorno cambiada la apuntara a otra
    máquina, tu conversación saldría del PC sin avisar. Un Ollama en otro equipo
    de tu red es legítimo, pero hay que pedirlo (JARVIS_PERMITIR_OLLAMA_REMOTO=1).
    """
    from urllib.parse import urlparse

    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        host = ""
    if host in _HOSTS_LOCALES or permitir_remoto:
        return url
    return _URL_OLLAMA_POR_DEFECTO


OLLAMA_URL = url_de_ollama_segura(
    os.getenv("JARVIS_OLLAMA_URL", _URL_OLLAMA_POR_DEFECTO),
    permitir_remoto=os.getenv("JARVIS_PERMITIR_OLLAMA_REMOTO") == "1",
)
MODELO_LOCAL = os.getenv("JARVIS_MODELO_LOCAL", "qwen3:8b")

# Cuánto tiempo mantiene Ollama el modelo cargado en memoria sin usarlo. El
# valor de fábrica son 5 minutos, y al descargarse se pierde el prompt ya
# procesado: la siguiente pregunta vuelve a costar más de 20 segundos. Media
# hora cubre el uso normal a cambio de unos 6 GB de RAM ocupados, que sobran
# en un equipo de 32 GB.
KEEP_ALIVE = os.getenv("JARVIS_KEEP_ALIVE", "30m")

# Tamaño del contexto que se pide a Ollama, en tokens. Su valor de fábrica es
# 4096, y el prompt de Jarvis (instrucciones + 31 herramientas) ocupa unos
# 4600: Ollama recortaba el PRINCIPIO sin avisar, así que el modelo perdía parte
# de sus instrucciones y las primeras herramientas de la lista. Medido: con 4096
# no usaba la del tiempo; con 8192 sí. Cuesta unos 0,6 GB más de memoria.
CONTEXTO = int(os.getenv("JARVIS_CONTEXTO", "8192"))

# Cuánto se fía de que Ollama sigue en marcha tras comprobarlo. Si se cae antes,
# la petición falla y se vuelve a comprobar en la siguiente.
SEGUNDOS_DE_CONFIANZA = 30.0

# El modelo de Claude por la API para las consultas complejas. Haiku es el más
# barato, que es lo que pide un presupuesto cercano a cero.
MODELO_CLAUDE = os.getenv("JARVIS_MODELO_CLAUDE", "claude-haiku-5-5")

# Cuánto razona Claude antes de contestar: low, medium, high, xhigh o max.
# "medium" equilibra calidad y coste para consultas puntuales. Vacío = el valor
# por defecto del modelo.
ESFUERZO_CLAUDE = os.getenv("JARVIS_ESFUERZO_CLAUDE", "medium").strip().lower()

# Modelos que rechazan el parámetro de esfuerzo con un error 400. Mandárselo
# haría fallar cada llamada y Jarvis volvería al modelo local sin avisar.
_SIN_ESFUERZO = ("claude-haiku-4-5", "claude-sonnet-4-5", "claude-3")


def admite_esfuerzo(modelo: str) -> bool:
    return not modelo.startswith(_SIN_ESFUERZO)


# Por dónde se llega a Claude:
#   api          -> la API de Anthropic con ANTHROPIC_API_KEY (factura por uso).
#   claude_code  -> el programa Claude Code instalado en este equipo, con tu
#                   sesión de claude.ai: gasta el uso de tu suscripción, no
#                   créditos de la API. Solo sirve en tu equipo.
CLAUDE_VIA = os.getenv("JARVIS_CLAUDE_VIA", "api").strip().lower()

# Modelo cuando se va por Claude Code. Un alias ("haiku", "sonnet", "opus")
# apunta siempre al último de esa familia, sin tener que saber su identificador.
MODELO_CLAUDE_CODE = os.getenv("JARVIS_MODELO_CLAUDE_CODE", "haiku")


class Motor(str, Enum):
    LOCAL = "local"
    CLAUDE = "claude"


@dataclass
class Mensaje:
    rol: str  # "system", "user", "assistant" o "tool"
    contenido: str
    # En un mensaje del asistente: las herramientas que pidió, como
    # {"name": ..., "arguments": {...}}. Van en el formato nativo del modelo y
    # no como texto: si el modelo ve sus llamadas pasadas escritas como texto,
    # las imita escribiéndolas en vez de hacerlas (pasó, y se inventaba datos).
    llamadas: list[dict[str, Any]] = field(default_factory=list)
    # En un mensaje "tool": de qué herramienta es el resultado.
    herramienta: str = ""


@dataclass
class Respuesta:
    texto: str
    motor: Motor
    modelo: str
    herramientas: list[dict[str, Any]] = field(default_factory=list)


class ErrorDeModelo(Exception):
    """Fallo al hablar con un modelo."""


# ---------------------------------------------------------------------------
# Enrutado
# ---------------------------------------------------------------------------

# Señales de que una petición supera lo que un modelo de 8B resuelve bien.
# No pretende ser exhaustivo: es una primera criba barata, y el modelo local
# puede además pedir ayuda explícitamente (ver 'pidio_ayuda').
_SENALES_DE_COMPLEJIDAD = [
    r"\b(escribe|programa|refactoriza|depura|corrige)\b.*\b(c[oó]digo|script|funci[oó]n|programa)\b",
    r"\banaliza\b.*\b(en profundidad|a fondo|detalladamente)\b",
    r"\bcompara\b.*\b(ventajas|desventajas|pros y contras)\b",
    r"\b(planifica|dise[ñn]a|arquitectura)\b",
    r"\bexplica\b.*\b(por qu[eé]|c[oó]mo funciona)\b.*\b(t[eé]cnic|interno)",
    r"\bresume\b.*\b(documento|informe|pdf|libro)\b",
    r"\btraduce\b.*\b(t[eé]cnico|literario)\b",
]

# Frase que el modelo local puede emitir para delegar. Se le explica en su
# prompt de sistema que la use cuando no se vea capaz.
MARCA_DE_AYUDA = "[NECESITO_AYUDA]"

# Tokens de control de los modelos de razonamiento, que a veces acaban en la
# respuesta visible en lugar de quedarse en su sitio.
_TOKENS_DE_CONTROL = re.compile(
    r"\s*/(?:no_)?think|<\|[^|>]*\|>", re.IGNORECASE
)


def necesita_claude(texto: str, longitud_contexto: int = 0) -> bool:
    """Decide si una petición merece ir a Claude en lugar del modelo local."""
    bajo = texto.lower()

    for patron in _SENALES_DE_COMPLEJIDAD:
        if re.search(patron, bajo):
            return True

    # Una petición muy larga suele implicar mucho contexto que el modelo local
    # maneja mal, y el coste sigue siendo bajo porque pasa pocas veces.
    if len(texto) > 1500 or longitud_contexto > 8000:
        return True

    return False


def modelo_descargado(modelo: str, descargados: list[str]) -> bool:
    """True si Ollama tiene exactamente el modelo pedido.

    Antes bastaba con que coincidiera el nombre base, así que con solo
    'qwen3:0.6b' descargado se daba por bueno 'qwen3:8b' y la primera
    pregunta fallaba. Un nombre sin etiqueta es, para Ollama, ':latest'.
    """
    buscado = modelo if ":" in modelo else f"{modelo}:latest"
    return any(m == buscado or m == modelo for m in descargados)


def pidio_ayuda(respuesta: str) -> bool:
    """True si el modelo local reconoció que no puede con la tarea."""
    return MARCA_DE_AYUDA in respuesta


# Marcadores con los que los modelos de razonamiento envuelven su monólogo
# interno cuando se les escapa dentro de la respuesta.
_BLOQUES_DE_RAZONAMIENTO = [
    (
        re.compile(f"{apertura}.*?{cierre}", re.DOTALL | re.IGNORECASE),
        # Un bloque abierto y nunca cerrado significa que la respuesta se cortó
        # a media reflexión: de ahí en adelante no hay nada aprovechable.
        re.compile(f"{apertura}.*", re.DOTALL | re.IGNORECASE),
    )
    for apertura, cierre in (
        (r"<think>", r"</think>"),
        (r"<thinking>", r"</thinking>"),
        (r"<reasoning>", r"</reasoning>"),
    )
]


def limpiar_razonamiento(texto: str) -> str:
    """Quita el razonamiento interno que algunos modelos cuelan en la respuesta.

    Comprobado con qwen3:4b, que ignora la orden de no razonar y vuelca su
    monólogo entero en el contenido. Sin esta limpieza Jarvis leería en voz alta
    cosas como "Okay, I need to explain...", que además suelen venir en inglés.
    """
    if not texto:
        return texto

    limpio = texto
    for cerrado, sin_cerrar in _BLOQUES_DE_RAZONAMIENTO:
        limpio = sin_cerrar.sub("", cerrado.sub("", limpio))

    # Tokens de control que Qwen3 deja sueltos en la respuesta. Se ha visto
    # de verdad: a "¿cómo estás?" contestó "¿Cómo estás? /no_think". Son
    # instrucciones internas del modelo, no algo que deba leer el usuario.
    limpio = _TOKENS_DE_CONTROL.sub("", limpio)

    limpio = limpio.strip()

    # Si tras la limpieza no queda nada, es mejor devolver el original que
    # dejar al usuario sin respuesta por una limpieza demasiado agresiva.
    return limpio or texto.strip()


# ---------------------------------------------------------------------------
# Modelo local (Ollama)
# ---------------------------------------------------------------------------


def mensaje_para_ollama(m: Mensaje) -> dict[str, Any]:
    """Un mensaje en el formato de la API de chat de Ollama."""
    datos: dict[str, Any] = {"role": m.rol, "content": m.contenido}
    if m.llamadas:
        datos["tool_calls"] = [
            {"function": {"name": ll["name"], "arguments": ll.get("arguments") or {}}}
            for ll in m.llamadas
        ]
    if m.rol == "tool" and m.herramienta:
        datos["tool_name"] = m.herramienta
    return datos


class ModeloLocal:
    """Cliente de Ollama. Es el motor por defecto."""

    def __init__(self, modelo: str = MODELO_LOCAL, url: str = OLLAMA_URL) -> None:
        self.modelo = modelo
        self.url = url.rstrip("/")
        # Una sola conexión reutilizada: con httpx.get/post sueltos cada
        # pregunta abría una conexión nueva con Ollama.
        self._http = httpx.Client()
        # Hasta cuándo se da por bueno que Ollama estaba disponible. Antes se
        # comprobaba en CADA turno, con hasta 3 segundos de espera si tardaba.
        self._disponible_hasta = 0.0

    def arrancar_servidor(self, espera: float = 25.0) -> bool:
        """Arranca Ollama si no está corriendo. Devuelve si quedó disponible.

        Jarvis depende de Ollama para todo, así que encargarse de levantarlo es
        parte de su trabajo. Sin esto, si Ollama no está arrancado Jarvis se
        queda mudo: escucha la petición, la transcribe y muere al pedirle la
        respuesta al modelo, que es justo lo que pasaba.
        """
        if self.disponible(intentar_arrancar=False):
            return True

        import shutil
        import subprocess
        import time

        ejecutable = shutil.which("ollama")
        if ejecutable is None:
            # Ollama se instala aquí por defecto y no siempre queda en el PATH
            # del proceso que hereda la aplicación.
            candidata = (
                Path(os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama"))
                / "ollama.exe"
            )
            if candidata.is_file():
                ejecutable = str(candidata)

        if ejecutable is None:
            return False

        try:
            subprocess.Popen(
                [ejecutable, "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                # Sin ventana de consola: Jarvis corre sin interfaz de texto y
                # no debe abrir uno negro por detrás.
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError:
            return False

        # El servidor tarda unos segundos en aceptar conexiones. Se comprueba
        # periódicamente en lugar de esperar un tiempo fijo.
        limite = time.monotonic() + espera
        while time.monotonic() < limite:
            if self.disponible(intentar_arrancar=False):
                return True
            time.sleep(0.8)

        return False

    def disponible(self, intentar_arrancar: bool = True) -> bool:
        """Comprueba que Ollama responde y tiene el modelo descargado.

        Con 'intentar_arrancar' se levanta el servidor si está parado. Se pone
        a False al llamar desde dentro del propio arranque, para no entrar en
        una recursión infinita.
        """
        if time.monotonic() < self._disponible_hasta:
            return True

        try:
            r = self._http.get(f"{self.url}/api/tags", timeout=3.0)
            r.raise_for_status()
        except (httpx.HTTPError, httpx.TimeoutException):
            if intentar_arrancar:
                return self.arrancar_servidor()
            return False

        modelos = [m.get("name", "") for m in r.json().get("models", [])]
        listo = modelo_descargado(self.modelo, modelos)
        if listo:
            # Solo se recuerda el "sí": un "no" se vuelve a comprobar en el
            # siguiente turno, por si lo acabas de arrancar.
            self._disponible_hasta = time.monotonic() + SEGUNDOS_DE_CONFIANZA
        return listo

    def marcar_no_disponible(self) -> None:
        """Olvida que estaba disponible; se llama cuando una petición falla."""
        self._disponible_hasta = 0.0

    def responder(
        self,
        mensajes: list[Mensaje],
        herramientas: list[dict[str, Any]] | None = None,
        temperatura: float = 0.7,
        al_recibir_texto: Callable[[str], None] | None = None,
    ) -> Respuesta:
        """Pide una respuesta al modelo local.

        Con 'al_recibir_texto' la respuesta llega por trozos según se genera, y
        la función se llama con cada uno. Es lo que permite que la ventana
        empiece a mostrar texto a los dos segundos en lugar de a los nueve.
        """
        cuerpo: dict[str, Any] = {
            "model": self.modelo,
            "messages": [mensaje_para_ollama(m) for m in mensajes],
            # Siempre en streaming, aunque nadie vaya a leer los trozos. Ollama
            # no reaprovecha el prompt ya procesado entre peticiones con y sin
            # streaming: medido aquí, una sin streaming tras calentar tardaba
            # 30 s y la misma con streaming 3,5 s. Así todas comparten caché.
            "stream": True,
            "options": {"temperature": temperatura, "num_ctx": CONTEXTO},
            # Qwen3 razona en voz alta por defecto, lo que multiplica por varias
            # veces el tiempo de respuesta. Para un asistente que contesta
            # hablando, la latencia importa más que ese extra de razonamiento:
            # lo que de verdad lo necesita se deriva a Claude.
            "think": False,
            "keep_alive": KEEP_ALIVE,
        }
        if herramientas:
            cuerpo["tools"] = herramientas

        return self._respuesta_en_trozos(cuerpo, al_recibir_texto or (lambda _: None))

    def calentar(
        self,
        sistema: str,
        herramientas: list[dict[str, Any]] | None = None,
    ) -> float:
        """Carga el modelo y deja procesado el prompt fijo. Devuelve segundos.

        La primera petición con las definiciones de herramientas cuesta unos 9
        segundos solo en procesar el prompt; a partir de ahí Ollama reaprovecha
        ese trabajo y baja a medio segundo. Haciéndolo al arrancar, ese precio
        se paga mientras se abre la ventana y no en la primera pregunta.

        El prompt debe ser idéntico al que se usará después, y la petición tiene
        que recorrer el mismo camino: se hace en streaming porque una petición
        sin streaming no deja preparado lo que necesita una con streaming, y el
        calentamiento no servía de nada.
        """
        inicio = time.perf_counter()
        try:
            self.responder(
                [
                    Mensaje("system", sistema),
                    Mensaje("user", "hola"),
                ],
                herramientas,
                al_recibir_texto=lambda _: None,
            )
        except ErrorDeModelo:
            # Que falle el calentamiento no es grave: solo significa que la
            # primera pregunta irá lenta. No debe impedir arrancar.
            return 0.0
        return time.perf_counter() - inicio

    def _respuesta_en_trozos(
        self, cuerpo: dict[str, Any], al_recibir_texto: Callable[[str], None]
    ) -> Respuesta:
        partes: list[str] = []
        llamadas: list[dict[str, Any]] = []

        try:
            with self._http.stream(
                "POST", f"{self.url}/api/chat", json=cuerpo, timeout=180.0
            ) as r:
                r.raise_for_status()
                for linea in r.iter_lines():
                    if not linea:
                        continue
                    try:
                        mensaje = json.loads(linea).get("message", {})
                    except json.JSONDecodeError:
                        continue

                    # Las llamadas a herramientas también llegan por el stream.
                    if mensaje.get("tool_calls"):
                        llamadas.extend(mensaje["tool_calls"])

                    trozo = mensaje.get("content", "")
                    if trozo:
                        partes.append(trozo)
                        # Solo se muestra si no hay herramientas pendientes: el
                        # texto que acompaña a una llamada suele ser ruido del
                        # modelo pensando, no la respuesta para el usuario.
                        if not llamadas:
                            al_recibir_texto(trozo)
        except httpx.HTTPError as e:
            self.marcar_no_disponible()
            raise ErrorDeModelo(f"Ollama falló durante el streaming: {e}") from e

        return Respuesta(
            texto=limpiar_razonamiento("".join(partes)),
            motor=Motor.LOCAL,
            modelo=self.modelo,
            herramientas=llamadas,
        )


# ---------------------------------------------------------------------------
# Claude (API de Anthropic)
# ---------------------------------------------------------------------------


def herramientas_para_claude(esquemas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Traduce las herramientas del formato de Ollama al de Claude.

    El registro las genera como {"type": "function", "function": {...}}, que
    es lo que entiende Ollama. Claude espera {name, description, input_schema}
    y rechazaba la petición entera con el otro formato, así que en cuanto había
    herramientas Jarvis volvía en silencio al modelo local.
    """
    convertidas = []
    for esquema in esquemas:
        funcion = esquema.get("function", esquema)
        parametros = dict(funcion.get("parameters") or {"type": "object", "properties": {}})
        # 'requerido' es una marca interna del registro, no de JSON Schema.
        parametros["properties"] = {
            nombre: {k: v for k, v in prop.items() if k != "requerido"}
            for nombre, prop in parametros.get("properties", {}).items()
        }
        convertidas.append(
            {
                "name": funcion["name"],
                "description": funcion.get("description", ""),
                "input_schema": parametros,
            }
        )
    return convertidas


def mensajes_para_claude(mensajes: list[Mensaje]) -> list[dict[str, str]]:
    """La conversación sin el sistema, en la forma que acepta la API de Claude.

    Se quitan los mensajes vacíos (el agente guarda un turno del asistente en
    blanco cuando solo llamó a herramientas, y la API los rechaza) y los del
    asistente al principio, porque la conversación debe empezar por el usuario.
    """
    conversacion = []
    for m in mensajes:
        if m.rol == "tool":
            # Claude no ha visto la llamada (no se guardan sus ids), así que
            # el resultado va como texto del usuario con su cabecera.
            conversacion.append(
                {"role": "user", "content": f"[resultado de {m.herramienta}]\n{m.contenido}"}
            )
        elif m.rol in ("user", "assistant") and m.contenido.strip():
            conversacion.append({"role": m.rol, "content": m.contenido})
    while conversacion and conversacion[0]["role"] != "user":
        conversacion.pop(0)
    return conversacion


class ModeloClaude:
    """Cliente de la API de Claude, para las tareas que el local no cubre."""

    def __init__(self, modelo: str = MODELO_CLAUDE) -> None:
        self.modelo = modelo
        self._cliente = None

    def disponible(self) -> bool:
        return bool(os.getenv("ANTHROPIC_API_KEY"))

    def _obtener_cliente(self):
        # Se importa y se crea tarde para que Jarvis arranque aunque no haya
        # clave ni el paquete instalado: sin clave, simplemente no se usa.
        if self._cliente is None:
            try:
                import anthropic
            except ImportError as e:
                raise ErrorDeModelo(
                    "Falta el paquete 'anthropic'. Instálalo con: pip install anthropic"
                ) from e

            clave = os.getenv("ANTHROPIC_API_KEY")
            if not clave:
                raise ErrorDeModelo(
                    "No hay ANTHROPIC_API_KEY configurada. Añádela al archivo .env."
                )
            self._cliente = anthropic.Anthropic(api_key=clave)
        return self._cliente

    def responder(
        self,
        mensajes: list[Mensaje],
        herramientas: list[dict[str, Any]] | None = None,
        max_tokens: int = 16000,
        al_recibir_texto: Callable[[str], None] | None = None,
    ) -> Respuesta:
        """Pide una respuesta a Claude.

        'max_tokens' es holgado a propósito: el modelo razona antes de
        contestar y ese razonamiento cuenta dentro del tope. Con 4096 una
        tarea compleja podía cortarse a medias.
        """
        cliente = self._obtener_cliente()

        # Claude recibe el prompt de sistema aparte, no como un mensaje más.
        sistema = "\n\n".join(m.contenido for m in mensajes if m.rol == "system")

        parametros: dict[str, Any] = {
            "model": self.modelo,
            "max_tokens": max_tokens,
            "messages": mensajes_para_claude(mensajes),
        }
        if sistema:
            # Caché del prompt: herramientas y sistema son iguales en cada
            # petición, y leerlos de caché cuesta una décima parte. El orden
            # de la API es herramientas -> sistema -> mensajes, así que una
            # marca aquí cubre ambos.
            parametros["system"] = [
                {"type": "text", "text": sistema, "cache_control": {"type": "ephemeral"}}
            ]
        if herramientas:
            parametros["tools"] = herramientas_para_claude(herramientas)
        if ESFUERZO_CLAUDE and admite_esfuerzo(self.modelo):
            parametros["output_config"] = {"effort": ESFUERZO_CLAUDE}

        try:
            if al_recibir_texto is None:
                r = cliente.messages.create(**parametros)
            else:
                with cliente.messages.stream(**parametros) as flujo:
                    for trozo in flujo.text_stream:
                        al_recibir_texto(trozo)
                    r = flujo.get_final_message()
        except Exception as e:
            # El texto original de la excepción puede traer cabeceras o partes
            # de la petición, y este mensaje acaba en la ventana y en los logs.
            estado = getattr(e, "status_code", None)
            detalle = f"{type(e).__name__}" + (f", HTTP {estado}" if estado else "")
            raise ErrorDeModelo(f"La API de Claude falló ({detalle}).") from e

        if r.stop_reason == "refusal":
            return Respuesta(
                texto="No puedo ayudar con eso.", motor=Motor.CLAUDE, modelo=self.modelo
            )

        texto = "".join(b.text for b in r.content if b.type == "text")
        llamadas = [
            {"name": b.name, "arguments": b.input, "id": b.id}
            for b in r.content
            if b.type == "tool_use"
        ]
        return Respuesta(
            texto=texto, motor=Motor.CLAUDE, modelo=self.modelo, herramientas=llamadas
        )


# ---------------------------------------------------------------------------
# Claude a través de Claude Code (tu suscripción)
# ---------------------------------------------------------------------------

_PROTOCOLO_DE_HERRAMIENTAS = """

HERRAMIENTAS
Para usar una herramienta responde ÚNICAMENTE con un objeto JSON en una línea,
sin nada antes ni después:
{{"name": "nombre_de_la_herramienta", "arguments": {{...}}}}
Recibirás su resultado como "[resultado de ...]" y entonces contestas al
usuario. Si no hace falta ninguna, contesta normalmente. Una por respuesta.

Herramientas disponibles:
{lista}"""


def _texto_de_herramientas(herramientas: list[dict[str, Any]]) -> str:
    lineas = []
    for h in herramientas_para_claude(herramientas):
        parametros = json.dumps(h["input_schema"].get("properties", {}), ensure_ascii=False)
        lineas.append(f"- {h['name']}: {h['description']} Parámetros: {parametros}")
    return "\n".join(lineas)


def _transcripcion(mensajes: list[Mensaje]) -> str:
    """La conversación como texto, que es lo que admite Claude Code en -p."""
    partes = []
    for m in mensajes:
        if m.rol == "user":
            partes.append(f"Usuario: {m.contenido}")
        elif m.rol == "assistant" and m.llamadas:
            for ll in m.llamadas:
                partes.append("Jarvis: " + json.dumps(ll, ensure_ascii=False))
        elif m.rol == "assistant" and m.contenido.strip():
            partes.append(f"Jarvis: {m.contenido}")
        elif m.rol == "tool":
            partes.append(f"[resultado de {m.herramienta}]\n{m.contenido}")
    partes.append("Responde ahora como Jarvis al último mensaje.")
    return "\n\n".join(partes)


def llamada_en_respuesta(texto: str, nombres: set[str]) -> dict[str, Any] | None:
    """Si la respuesta entera es una petición de herramienta, la devuelve.

    Solo si es TODO el texto: un JSON a mitad de una frase no se ejecuta. Y el
    nombre tiene que ser una herramienta que existe.
    """
    limpio = texto.strip()
    limpio = re.sub(r"^```(?:json)?\s*|\s*```$", "", limpio).strip()
    if not (limpio.startswith("{") and limpio.endswith("}")):
        return None
    try:
        datos = json.loads(limpio)
    except json.JSONDecodeError:
        return None
    nombre = datos.get("name") if isinstance(datos, dict) else None
    argumentos = datos.get("arguments", {}) if isinstance(datos, dict) else None
    if nombre not in nombres or not isinstance(argumentos, dict):
        return None
    return {"name": nombre, "arguments": argumentos}


class ModeloClaudeCode:
    """Claude vía el programa Claude Code, con la sesión de claude.ai del usuario.

    Se lanza en modo no interactivo SIN ninguna herramienta propia de Claude
    Code: tiene las suyas para ejecutar comandos y editar archivos, y usarlas
    se saltaría el guardián entero. Las herramientas de Jarvis se le describen
    en el prompt; cuando pide una, la ejecuta el agente pasando por la política
    como cualquier otra.
    """

    def __init__(self, modelo: str = MODELO_CLAUDE_CODE) -> None:
        self.modelo = modelo

    @staticmethod
    def _ejecutable() -> str | None:
        import shutil

        encontrado = shutil.which("claude")
        if encontrado:
            return encontrado
        candidato = Path.home() / ".local" / "bin" / "claude.exe"
        return str(candidato) if candidato.is_file() else None

    def disponible(self) -> bool:
        return self._ejecutable() is not None

    def responder(
        self,
        mensajes: list[Mensaje],
        herramientas: list[dict[str, Any]] | None = None,
        al_recibir_texto: Callable[[str], None] | None = None,
    ) -> Respuesta:
        import subprocess
        import tempfile

        ejecutable = self._ejecutable()
        if ejecutable is None:
            raise ErrorDeModelo("No encuentro Claude Code (el programa 'claude').")

        sistema = "\n\n".join(m.contenido for m in mensajes if m.rol == "system")
        if herramientas:
            sistema += _PROTOCOLO_DE_HERRAMIENTAS.format(lista=_texto_de_herramientas(herramientas))

        # Sin la clave de la API en el entorno: si estuviera, Claude Code la
        # usaría y facturaría por uso en vez de gastar tu suscripción.
        entorno = {
            k: v
            for k, v in os.environ.items()
            if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL")
        }

        # Una carpeta vacía como directorio de trabajo: así no carga memorias,
        # CLAUDE.md ni ajustes de ningún proyecto.
        with tempfile.TemporaryDirectory(prefix="jarvis-claude-") as carpeta:
            archivo_sistema = Path(carpeta) / "sistema.txt"
            archivo_sistema.write_text(sistema, encoding="utf-8")
            orden = [
                ejecutable, "-p",
                "--model", self.modelo,
                "--tools", "",
                "--strict-mcp-config",
                "--disable-slash-commands",
                "--no-session-persistence",
                "--system-prompt-file", str(archivo_sistema),
                "--output-format", "json",
            ]
            if ESFUERZO_CLAUDE:
                orden += ["--effort", ESFUERZO_CLAUDE]
            try:
                proceso = subprocess.run(
                    orden,
                    input=_transcripcion([m for m in mensajes if m.rol != "system"]),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    cwd=carpeta,
                    env=entorno,
                    timeout=180,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except (OSError, subprocess.TimeoutExpired) as e:
                raise ErrorDeModelo(f"Claude Code no respondió ({type(e).__name__}).") from e

        try:
            datos = json.loads(proceso.stdout)
        except json.JSONDecodeError as e:
            raise ErrorDeModelo(
                f"Claude Code devolvió algo inesperado (código {proceso.returncode})."
            ) from e
        if proceso.returncode != 0 or datos.get("is_error"):
            raise ErrorDeModelo(
                f"Claude Code falló ({datos.get('subtype') or proceso.returncode})."
            )

        texto = str(datos.get("result") or "")
        nombres = {h["name"] for h in herramientas_para_claude(herramientas or [])}
        llamada = llamada_en_respuesta(texto, nombres)
        if llamada is not None:
            return Respuesta(texto="", motor=Motor.CLAUDE, modelo=self.modelo, herramientas=[llamada])

        if al_recibir_texto is not None and texto:
            al_recibir_texto(texto)
        return Respuesta(texto=texto, motor=Motor.CLAUDE, modelo=self.modelo)


# ---------------------------------------------------------------------------
# Cerebro
# ---------------------------------------------------------------------------


class Cerebro:
    """Enruta cada petición al motor adecuado y gestiona los fallos."""

    def __init__(self, preferir_local: bool = True) -> None:
        self.local = ModeloLocal()
        self.claude = ModeloClaudeCode() if CLAUDE_VIA == "claude_code" else ModeloClaude()
        self.preferir_local = preferir_local

    def estado(self) -> dict[str, Any]:
        """Qué motores hay disponibles ahora mismo. Se muestra al arrancar."""
        return {
            "local_disponible": self.local.disponible(),
            "modelo_local": self.local.modelo,
            "claude_disponible": self.claude.disponible(),
            "modelo_claude": self.claude.modelo,
        }

    def responder(
        self,
        mensajes: list[Mensaje],
        herramientas: list[dict[str, Any]] | None = None,
        forzar: Motor | None = None,
        al_recibir_texto: Callable[[str], None] | None = None,
    ) -> Respuesta:
        """Responde usando el motor más apropiado.

        Con 'forzar' se puede saltar el enrutado, para cuando tú decides
        explícitamente qué modelo quieres que conteste. Con 'al_recibir_texto'
        la respuesta llega por trozos según se genera, venga del motor que venga.
        """
        ultimo = next(
            (m.contenido for m in reversed(mensajes) if m.rol == "user"), ""
        )
        contexto = sum(len(m.contenido) for m in mensajes)

        if forzar is Motor.CLAUDE:
            return self.claude.responder(
                mensajes, herramientas, al_recibir_texto=al_recibir_texto
            )
        if forzar is Motor.LOCAL:
            return self.local.responder(
                mensajes, herramientas, al_recibir_texto=al_recibir_texto
            )

        quiere_claude = necesita_claude(ultimo, contexto)

        # Claude solo si además está configurado; si no, se sigue con el local.
        if quiere_claude and self.claude.disponible():
            try:
                return self.claude.responder(
                    mensajes, herramientas, al_recibir_texto=al_recibir_texto
                )
            except ErrorDeModelo:
                # Que falle la nube no debe dejarte sin asistente.
                pass

        if not self.local.disponible():
            if self.claude.disponible():
                return self.claude.responder(
                    mensajes, herramientas, al_recibir_texto=al_recibir_texto
                )
            raise ErrorDeModelo(
                "No hay ningún modelo disponible. Comprueba que Ollama esté "
                "arrancado, o configura ANTHROPIC_API_KEY."
            )

        respuesta = self.local.responder(
            mensajes, herramientas, al_recibir_texto=al_recibir_texto
        )

        # Segunda oportunidad: el modelo local reconoció que no puede.
        if pidio_ayuda(respuesta.texto) and self.claude.disponible():
            try:
                return self.claude.responder(
                    mensajes, herramientas, al_recibir_texto=al_recibir_texto
                )
            except ErrorDeModelo:
                respuesta.texto = respuesta.texto.replace(MARCA_DE_AYUDA, "").strip()

        return respuesta
