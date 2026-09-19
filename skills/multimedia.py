"""
Buscar y reproducir en YouTube.

POR QUÉ NO SE AUTOMATIZA EL NAVEGADOR
Lo natural sería abrir YouTube, buscar y hacer clic en el primer resultado. Eso
exige controlar el ratón y el teclado sobre la ventana que haya delante, y un
asistente con esa capacidad puede pulsar cualquier cosa en cualquier programa:
un "aceptar" de un diálogo bancario, un "borrar" en tu correo. No hay forma de
acotarlo desde la política, porque a ese nivel todo son píxeles.

Así que se hace al revés: se consulta la búsqueda de YouTube, se extrae el
identificador del primer vídeo y se abre esa dirección directamente. El
resultado para ti es el mismo —empieza a sonar la canción— pero Jarvis nunca
toca el ratón ni ve otras ventanas.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from urllib.parse import quote_plus

from skills.registro import registro

BUSQUEDA = "https://www.youtube.com/results?search_query={}"
VER = "https://www.youtube.com/watch?v={}"

# YouTube manda los resultados dentro de un JSON incrustado en el HTML. Se
# extraen los identificadores en el orden en que aparecen, que es el orden de
# los resultados.
_PATRON_VIDEO = re.compile(r'"videoId":"([A-Za-z0-9_-]{11})"')

# El título va dentro de "title":{"runs":[{"text":"..."}]}, justo detrás del
# identificador. Buscar el primer "text" cualquiera detrás del videoId no vale:
# ahí aparecen antes etiquetas de la interfaz, y por eso Jarvis anunciaba estar
# reproduciendo «Mix» en lugar del nombre de la canción.
_PATRON_TITULO = re.compile(
    r'"videoId":"([A-Za-z0-9_-]{11})".{0,400}?'
    r'"title":\{"runs":\[\{"text":"((?:[^"\\]|\\.)*)"',
    re.DOTALL,
)

# Entradas que no son un vídeo concreto sino listas automáticas de YouTube.
# Reproducirlas funciona, pero el nombre no dice nada de lo que suena.
_TITULOS_INUTILES = {"mix", "mezcla", "radio", "(sin título)"}

# Cuántos resultados se recuerdan de la última búsqueda, para poder decir
# "reproduce el segundo" después.
MAX_RESULTADOS = 8


@dataclass
class Video:
    id: str
    titulo: str

    @property
    def url(self) -> str:
        return VER.format(self.id)


# Última búsqueda hecha en esta sesión. Es lo que permite que "reproduce la
# primera" funcione sin repetir la búsqueda ni depender de que el modelo
# recuerde bien los identificadores, que es donde uno pequeño se inventa cosas.
_ultimos_resultados: list[Video] = []
_ultima_busqueda = ""


def _buscar(consulta: str) -> list[Video]:
    """Devuelve los primeros vídeos que YouTube da para una búsqueda."""
    import httpx

    respuesta = httpx.get(
        BUSQUEDA.format(quote_plus(consulta)),
        timeout=20.0,
        follow_redirects=True,
        headers={
            # Sin un navegador reconocible, YouTube devuelve una página
            # distinta y sin resultados utilizables.
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
            ),
            "Accept-Language": "es-ES,es;q=0.9",
        },
    )
    respuesta.raise_for_status()
    html = respuesta.text

    vistos: dict[str, str] = {}
    for coincidencia in _PATRON_TITULO.finditer(html):
        video_id, titulo = coincidencia.group(1), coincidencia.group(2)
        if video_id in vistos:
            continue
        try:
            # El título viene con escapes JSON (é y similares).
            vistos[video_id] = json.loads(f'"{titulo}"')
        except json.JSONDecodeError:
            vistos[video_id] = titulo
        if len(vistos) >= MAX_RESULTADOS:
            break

    # Respaldo: si el patrón con título no encaja (YouTube cambia el formato a
    # menudo), al menos se sacan los identificadores. Reproducir con un título
    # feo es mejor que no reproducir.
    if not vistos:
        for video_id in _PATRON_VIDEO.findall(html)[:MAX_RESULTADOS]:
            vistos.setdefault(video_id, "(sin título)")

    videos = [Video(i, t) for i, t in vistos.items()]

    # Las listas automáticas ("Mix", "Radio") se mandan al final: suenan igual
    # de bien, pero su nombre no le dice nada al usuario cuando Jarvis anuncia
    # en voz alta lo que está poniendo.
    videos.sort(key=lambda v: v.titulo.strip().lower() in _TITULOS_INUTILES)
    return videos


@registro.registrar(
    nombre="buscar_en_youtube",
    descripcion=(
        "Busca vídeos o canciones en YouTube y devuelve la lista de resultados "
        "numerada, SIN reproducir nada. Úsala cuando el usuario quiera ver qué "
        "hay antes de elegir."
    ),
    accion="buscar_web",
    parametros={
        "consulta": {
            "type": "string",
            "description": "Qué buscar, por ejemplo 'Smells Like Teen Spirit Nirvana'.",
        }
    },
    campo_objetivo="consulta",
)
def buscar_en_youtube(consulta: str) -> str:
    global _ultimos_resultados, _ultima_busqueda

    try:
        resultados = _buscar(consulta)
    except Exception as e:
        return f"No se pudo buscar en YouTube: {e}. NO digas que lo encontraste."

    if not resultados:
        return f"YouTube no devolvió resultados para '{consulta}'."

    _ultimos_resultados = resultados
    _ultima_busqueda = consulta

    lineas = [f"{i}. {v.titulo}" for i, v in enumerate(resultados, 1)]
    return (
        f"Resultados para '{consulta}':\n" + "\n".join(lineas) + "\n\n"
        "Para reproducir uno, usa reproducir_en_youtube con su número."
    )


@registro.registrar(
    nombre="reproducir_en_youtube",
    descripcion=(
        "Busca algo en YouTube y lo reproduce, todo en un paso. ES LA "
        "HERRAMIENTA CORRECTA cuando el usuario dice cosas como 'abre YouTube "
        "y busca una canción de Nirvana', 'ponme música', 'reproduce X' o "
        "'búscame X en YouTube'. NO uses abrir_app ni abrir_url para eso: "
        "abrirían una pestaña vacía sin reproducir nada. "
        "Con 'consulta' busca y reproduce directamente el primer resultado. "
        "Con 'numero' reproduce ese resultado de la última búsqueda, que es lo "
        "que hay que usar cuando el usuario dice 'reproduce la primera' o "
        "'pon la segunda'."
    ),
    accion="abrir_url",
    parametros={
        "consulta": {
            "type": "string",
            "description": "Qué reproducir. Omítelo si vas a usar 'numero'.",
            "requerido": False,
        },
        "numero": {
            "type": "integer",
            "description": (
                "Número del resultado de la última búsqueda, empezando en 1. "
                "'la primera' es 1."
            ),
            "requerido": False,
        },
    },
)
def reproducir_en_youtube(consulta: str = "", numero: int | None = None) -> str:
    global _ultimos_resultados, _ultima_busqueda

    from skills.sistema import abrir_url

    # Caso 1: "reproduce la primera" referido a lo que se acaba de buscar.
    if numero is not None and not consulta:
        if not _ultimos_resultados:
            return (
                "No hay ninguna búsqueda reciente de la que elegir. Busca algo "
                "primero. NO digas que reprodujiste nada."
            )
        if not 1 <= numero <= len(_ultimos_resultados):
            return (
                f"Solo hay {len(_ultimos_resultados)} resultados de "
                f"'{_ultima_busqueda}', así que el número {numero} no existe. "
                "NO digas que reprodujiste nada."
            )
        elegido = _ultimos_resultados[numero - 1]
        resultado = abrir_url(elegido.url)
        if "NO digas" in resultado:
            return resultado
        return f"Reproduciendo «{elegido.titulo}»."

    # Caso 2: buscar y reproducir el primero de una vez.
    if not consulta:
        return (
            "Necesito saber qué reproducir, o un número de la última búsqueda. "
            "NO digas que reprodujiste nada."
        )

    try:
        resultados = _buscar(consulta)
    except Exception as e:
        return f"No se pudo buscar en YouTube: {e}. NO digas que lo reprodujiste."

    if not resultados:
        return (
            f"YouTube no devolvió resultados para '{consulta}'. "
            "NO digas que lo reprodujiste."
        )

    _ultimos_resultados = resultados
    _ultima_busqueda = consulta

    elegido = resultados[0]
    resultado = abrir_url(elegido.url)
    if "NO digas" in resultado:
        return resultado
    return f"Reproduciendo «{elegido.titulo}»."


def ultimos_resultados() -> list[Video]:
    """Los resultados de la última búsqueda. Para las pruebas."""
    return list(_ultimos_resultados)


def limpiar_resultados() -> None:
    """Olvida la última búsqueda. Para las pruebas."""
    global _ultimos_resultados, _ultima_busqueda
    _ultimos_resultados = []
    _ultima_busqueda = ""
