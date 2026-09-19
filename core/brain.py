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

OLLAMA_URL = os.getenv("JARVIS_OLLAMA_URL", "http://localhost:11434")
MODELO_LOCAL = os.getenv("JARVIS_MODELO_LOCAL", "qwen3:8b")

# Cuánto tiempo mantiene Ollama el modelo cargado en memoria sin usarlo. El
# valor de fábrica son 5 minutos, y al descargarse se pierde el prompt ya
# procesado: la siguiente pregunta vuelve a costar más de 20 segundos. Media
# hora cubre el uso normal a cambio de unos 6 GB de RAM ocupados, que sobran
# en un equipo de 32 GB.
KEEP_ALIVE = os.getenv("JARVIS_KEEP_ALIVE", "30m")

# El modelo de Claude para las consultas complejas. Sonnet 5 da la mejor
# relación entre capacidad y coste para este uso puntual.
MODELO_CLAUDE = os.getenv("JARVIS_MODELO_CLAUDE", "claude-sonnet-5")


class Motor(str, Enum):
    LOCAL = "local"
    CLAUDE = "claude"


@dataclass
class Mensaje:
    rol: str  # "system", "user" o "assistant"
    contenido: str


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


def pidio_ayuda(respuesta: str) -> bool:
    """True si el modelo local reconoció que no puede con la tarea."""
    return MARCA_DE_AYUDA in respuesta


# Marcadores con los que los modelos de razonamiento envuelven su monólogo
# interno cuando se les escapa dentro de la respuesta.
_BLOQUES_DE_RAZONAMIENTO = [
    (r"<think>", r"</think>"),
    (r"<thinking>", r"</thinking>"),
    (r"<reasoning>", r"</reasoning>"),
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
    for apertura, cierre in _BLOQUES_DE_RAZONAMIENTO:
        limpio = re.sub(
            f"{apertura}.*?{cierre}", "", limpio, flags=re.DOTALL | re.IGNORECASE
        )
        # Un bloque abierto y nunca cerrado significa que la respuesta se cortó
        # a media reflexión: de ahí en adelante no hay nada aprovechable.
        limpio = re.sub(f"{apertura}.*", "", limpio, flags=re.DOTALL | re.IGNORECASE)

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


class ModeloLocal:
    """Cliente de Ollama. Es el motor por defecto."""

    def __init__(self, modelo: str = MODELO_LOCAL, url: str = OLLAMA_URL) -> None:
        self.modelo = modelo
        self.url = url.rstrip("/")

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
        try:
            r = httpx.get(f"{self.url}/api/tags", timeout=3.0)
            r.raise_for_status()
        except (httpx.HTTPError, httpx.TimeoutException):
            if intentar_arrancar:
                return self.arrancar_servidor()
            return False

        modelos = [m.get("name", "") for m in r.json().get("models", [])]
        # Ollama etiqueta como "qwen3:8b"; aceptamos también el nombre sin tag.
        base = self.modelo.split(":")[0]
        return any(m == self.modelo or m.startswith(base) for m in modelos)

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
            "messages": [{"role": m.rol, "content": m.contenido} for m in mensajes],
            "stream": al_recibir_texto is not None,
            "options": {"temperature": temperatura},
            # Qwen3 razona en voz alta por defecto, lo que multiplica por varias
            # veces el tiempo de respuesta. Para un asistente que contesta
            # hablando, la latencia importa más que ese extra de razonamiento:
            # lo que de verdad lo necesita se deriva a Claude.
            "think": False,
            "keep_alive": KEEP_ALIVE,
        }
        if herramientas:
            cuerpo["tools"] = herramientas

        if al_recibir_texto is None:
            return self._respuesta_completa(cuerpo)
        return self._respuesta_en_trozos(cuerpo, al_recibir_texto)

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

    def _respuesta_completa(self, cuerpo: dict[str, Any]) -> Respuesta:
        try:
            r = httpx.post(f"{self.url}/api/chat", json=cuerpo, timeout=180.0)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise ErrorDeModelo(f"Ollama no respondió: {e}") from e

        datos = r.json().get("message", {})
        return Respuesta(
            texto=limpiar_razonamiento(datos.get("content", "")),
            motor=Motor.LOCAL,
            modelo=self.modelo,
            herramientas=datos.get("tool_calls", []),
        )

    def _respuesta_en_trozos(
        self, cuerpo: dict[str, Any], al_recibir_texto: Callable[[str], None]
    ) -> Respuesta:
        partes: list[str] = []
        llamadas: list[dict[str, Any]] = []

        try:
            with httpx.stream(
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
        max_tokens: int = 4096,
    ) -> Respuesta:
        cliente = self._obtener_cliente()

        # Claude recibe el prompt de sistema aparte, no como un mensaje más.
        sistema = "\n\n".join(m.contenido for m in mensajes if m.rol == "system")
        conversacion = [
            {"role": m.rol, "content": m.contenido}
            for m in mensajes
            if m.rol != "system"
        ]

        parametros: dict[str, Any] = {
            "model": self.modelo,
            "max_tokens": max_tokens,
            "messages": conversacion,
        }
        if sistema:
            parametros["system"] = sistema
        if herramientas:
            parametros["tools"] = herramientas

        try:
            r = cliente.messages.create(**parametros)
        except Exception as e:
            raise ErrorDeModelo(f"La API de Claude falló: {e}") from e

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
# Cerebro
# ---------------------------------------------------------------------------


class Cerebro:
    """Enruta cada petición al motor adecuado y gestiona los fallos."""

    def __init__(self, preferir_local: bool = True) -> None:
        self.local = ModeloLocal()
        self.claude = ModeloClaude()
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
        la respuesta del modelo local llega por trozos según se genera.
        """
        ultimo = next(
            (m.contenido for m in reversed(mensajes) if m.rol == "user"), ""
        )
        contexto = sum(len(m.contenido) for m in mensajes)

        if forzar is Motor.CLAUDE:
            return self.claude.responder(mensajes, herramientas)
        if forzar is Motor.LOCAL:
            return self.local.responder(
                mensajes, herramientas, al_recibir_texto=al_recibir_texto
            )

        quiere_claude = necesita_claude(ultimo, contexto)

        # Claude solo si además está configurado; si no, se sigue con el local.
        if quiere_claude and self.claude.disponible():
            try:
                return self.claude.responder(mensajes, herramientas)
            except ErrorDeModelo:
                # Que falle la nube no debe dejarte sin asistente.
                pass

        if not self.local.disponible():
            if self.claude.disponible():
                return self.claude.responder(mensajes, herramientas)
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
                return self.claude.responder(mensajes, herramientas)
            except ErrorDeModelo:
                respuesta.texto = respuesta.texto.replace(MARCA_DE_AYUDA, "").strip()

        return respuesta
