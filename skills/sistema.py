"""
Habilidades del sistema: información, apps y multimedia.

Son las acciones de bajo riesgo (consultar cosas, abrir programas, subir el
volumen). Todas pasan igualmente por el guardián, aunque la política las tenga
marcadas como 'permitir': así quedan registradas en el log.
"""

from __future__ import annotations

import os
import re
import platform
import shutil
import subprocess
import winreg
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from skills.registro import registro

# Apps que Jarvis puede abrir. Solo estos nombres: si no está en la tabla, no se
# abre, para que el modelo no pueda lanzar cualquier ejecutable que se invente.
#
# Cada entrada dice CÓMO se abre, porque de eso depende cómo se comprueba que
# existe. Anunciar que algo se abrió sin verificarlo primero es peor que
# fallar: deja al usuario creyendo que funcionó.
#   ("exe", [rutas candidatas])  -> se abre la primera que exista
#   ("path", "programa")         -> se busca en el PATH del sistema
#   ("uri", "protocolo:")        -> se entrega a Windows (Configuración, etc.)
APPS: dict[str, tuple[str, object]] = {
    "opera": ("exe", [
        r"%LOCALAPPDATA%\Programs\Opera GX\opera.exe",
        r"%LOCALAPPDATA%\Programs\Opera\opera.exe",
        r"%PROGRAMFILES%\Opera GX\opera.exe",
    ]),
    "opera gx": ("exe", [
        r"%LOCALAPPDATA%\Programs\Opera GX\opera.exe",
        r"%PROGRAMFILES%\Opera GX\opera.exe",
    ]),
    "chrome": ("exe", [
        r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe",
        r"%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe",
    ]),
    "edge": ("exe", [
        r"%PROGRAMFILES(X86)%\Microsoft\Edge\Application\msedge.exe",
        r"%PROGRAMFILES%\Microsoft\Edge\Application\msedge.exe",
    ]),
    "firefox": ("exe", [
        r"%PROGRAMFILES%\Mozilla Firefox\firefox.exe",
    ]),
    "spotify": ("exe", [
        r"%APPDATA%\Spotify\Spotify.exe",
        r"%LOCALAPPDATA%\Microsoft\WindowsApps\Spotify.exe",
    ]),
    "vscode": ("exe", [
        r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe",
        r"%PROGRAMFILES%\Microsoft VS Code\Code.exe",
    ]),
    "explorador": ("path", "explorer"),
    "bloc de notas": ("path", "notepad"),
    "notepad": ("path", "notepad"),
    "calculadora": ("path", "calc"),
    "terminal": ("path", "wt"),
    "ajustes": ("uri", "ms-settings:"),
    "configuracion": ("uri", "ms-settings:"),
}

# Sinónimos, para que "abre el navegador" funcione sin que el modelo tenga que
# saber cuál usas. 'navegador' se resuelve al que tengas por defecto.
ALIAS = {
    "code": "vscode",
    "visual studio code": "vscode",
    "explorer": "explorador",
    "archivos": "explorador",
    "musica": "spotify",
    "música": "spotify",
    "configuración": "configuracion",
    "opera gx browser": "opera gx",
}


def _expandir(ruta: str) -> Path:
    return Path(os.path.expandvars(ruta))


def _navegador_por_defecto() -> Path | None:
    """Localiza el ejecutable del navegador que Windows tiene por defecto.

    Se lee del registro para respetar tu elección en lugar de imponer uno.
    Si algo falla, se devuelve None y quien llame decide qué hacer.
    """
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"SOFTWARE\Microsoft\Windows\Shell\Associations\UrlAssociations"
            r"\https\UserChoice",
        ) as clave:
            prog_id = winreg.QueryValueEx(clave, "ProgId")[0]

        with winreg.OpenKey(
            winreg.HKEY_CLASSES_ROOT, rf"{prog_id}\shell\open\command"
        ) as clave:
            comando = winreg.QueryValueEx(clave, "")[0]
    except OSError:
        return None

    # El comando viene como '"C:\...\opera.exe" -- "%1"'; interesa solo el exe.
    ejecutable = comando.split('"')[1] if comando.startswith('"') else comando.split()[0]
    ruta = Path(ejecutable)
    return ruta if ruta.is_file() else None


def _resolver_app(nombre: str) -> tuple[str, str] | None:
    """Devuelve (tipo, destino) de una app, o None si no existe en el equipo.

    Comprobar la existencia aquí es lo que permite decir la verdad después.
    """
    clave = ALIAS.get(nombre.lower().strip(), nombre.lower().strip())

    # 'navegador' no es una app fija: es el que tú tengas configurado.
    if clave in ("navegador", "browser"):
        navegador = _navegador_por_defecto()
        return ("exe", str(navegador)) if navegador else None

    entrada = APPS.get(clave)
    if entrada is None:
        return None

    tipo, destino = entrada
    if tipo == "exe":
        for candidata in destino:  # type: ignore[union-attr]
            ruta = _expandir(candidata)
            if ruta.is_file():
                return ("exe", str(ruta))
        return None
    if tipo == "path":
        encontrado = shutil.which(str(destino))
        return ("exe", encontrado) if encontrado else None
    return ("uri", str(destino))


@registro.registrar(
    nombre="hora_fecha",
    descripcion="Dice la hora y la fecha actuales.",
    accion="hora_fecha",
    parametros={},
)
def hora_fecha() -> str:
    ahora = datetime.now()
    dias = [
        "lunes",
        "martes",
        "miércoles",
        "jueves",
        "viernes",
        "sábado",
        "domingo",
    ]
    meses = [
        "enero",
        "febrero",
        "marzo",
        "abril",
        "mayo",
        "junio",
        "julio",
        "agosto",
        "septiembre",
        "octubre",
        "noviembre",
        "diciembre",
    ]
    return (
        f"{dias[ahora.weekday()]} {ahora.day} de {meses[ahora.month - 1]} "
        f"de {ahora.year}, las {ahora.strftime('%H:%M')}."
    )


@registro.registrar(
    nombre="info_sistema",
    descripcion="Informa del estado del equipo: disco libre, memoria y sistema.",
    accion="info_sistema",
    parametros={},
)
def info_sistema() -> str:
    uso = shutil.disk_usage("C:/")
    lineas = [
        f"Sistema: {platform.system()} {platform.release()}",
        f"Procesador: {platform.processor()}",
        f"Disco C: {uso.free / 1024**3:.0f} GB libres de {uso.total / 1024**3:.0f} GB",
    ]
    try:
        import psutil

        mem = psutil.virtual_memory()
        lineas.append(
            f"Memoria: {mem.available / 1024**3:.1f} GB libres de "
            f"{mem.total / 1024**3:.1f} GB ({mem.percent:.0f}% en uso)"
        )
        lineas.append(f"CPU: {psutil.cpu_percent(interval=0.3):.0f}% de uso")
    except ImportError:
        pass
    return "\n".join(lineas)


@registro.registrar(
    nombre="abrir_app",
    descripcion=(
        "Abre un PROGRAMA instalado en el PC, sin abrir ninguna página. "
        f"Programas conocidos: navegador, {', '.join(sorted(APPS))}. "
        "NO la uses para sitios web: para YouTube, Google, Gmail o cualquier "
        "dirección usa abrir_url. Para música o vídeos de YouTube usa "
        "reproducir_en_youtube. Abrir el navegador vacío casi nunca es lo que "
        "el usuario quiere."
    ),
    accion="abrir_app",
    parametros={
        "nombre": {"type": "string", "description": "Nombre de la aplicación."}
    },
    campo_objetivo="nombre",
)
def abrir_app(nombre: str) -> str:
    resuelta = _resolver_app(nombre)

    if resuelta is None:
        conocidas = sorted(set(APPS) | {"navegador"})
        return (
            f"No encuentro '{nombre}' instalado en este equipo. "
            f"Aplicaciones que puedo abrir: {', '.join(conocidas)}. "
            "NO le digas al usuario que la abriste."
        )

    # Abrir el navegador a secas suele ser el modelo equivocándose de
    # herramienta: el usuario pedía ir a algún sitio, no ver una pestaña vacía.
    # Se abre igualmente, pero se le recuerda cuál era la correcta.
    aviso = ""
    if nombre.lower().strip() in ("navegador", "browser", "opera", "opera gx",
                                  "chrome", "edge", "firefox"):
        aviso = (
            " Si el usuario quería ir a una página concreta, usa abrir_url; "
            "si quería música o vídeo de YouTube, usa reproducir_en_youtube."
        )

    tipo, destino = resuelta
    try:
        if tipo == "uri":
            os.startfile(destino)  # noqa: S606 - protocolo de Windows, no un exe
        else:
            # Sin shell=True: se lanza el ejecutable ya resuelto y verificado,
            # sin que pase por el intérprete de comandos.
            subprocess.Popen([destino])
    except OSError as e:
        return f"No se pudo abrir '{nombre}': {e}. NO digas que se abrió."

    return f"Abierto: {nombre}.{aviso}"


@registro.registrar(
    nombre="abrir_url",
    descripcion=(
        "Abre una página web en el navegador por defecto del usuario. Úsala "
        "siempre que haya que abrir un sitio web como YouTube, Gmail o "
        "cualquier dirección."
    ),
    accion="abrir_url",
    parametros={
        "url": {
            "type": "string",
            "description": "Dirección web completa, por ejemplo https://youtube.com",
        }
    },
    campo_objetivo="url",
)
def abrir_url(url: str) -> str:
    direccion = url.strip()
    if not direccion:
        return "No me has dicho qué dirección abrir. NO digas que se abrió."

    # Se busca el esquema por su forma real ('algo:'), no por '://'. Esquemas
    # peligrosos como javascript: o file: no llevan barras, así que buscar
    # '://' los dejaría pasar y acabarían pegados detrás de un https inventado.
    esquema = re.match(r"^([a-zA-Z][a-zA-Z0-9+.\-]*):", direccion)
    if esquema:
        if esquema.group(1).lower() not in ("http", "https"):
            return (
                f"Solo puedo abrir direcciones http o https, y '{url}' usa "
                f"'{esquema.group(1)}'. NO digas que se abrió."
            )
    else:
        # Sin esquema se asume https, que es lo que significa "abre youtube".
        direccion = f"https://{direccion}"

    # Se valida ya montada, porque esquemas como file: o javascript: podrían
    # llegar a archivos locales o ejecutar código en el navegador.
    partes = urlparse(direccion)
    if partes.scheme not in ("http", "https") or not partes.netloc:
        return f"'{url}' no es una dirección web válida. NO digas que se abrió."

    navegador = _navegador_por_defecto()
    try:
        if navegador:
            subprocess.Popen([str(navegador), direccion])
        else:
            # Sin navegador identificado, se deja que Windows decida.
            os.startfile(direccion)  # noqa: S606
    except OSError as e:
        return f"No se pudo abrir {direccion}: {e}. NO digas que se abrió."

    nombre = navegador.stem if navegador else "el navegador"
    return f"Abierto {direccion} en {nombre}."


@registro.registrar(
    nombre="control_volumen",
    descripcion="Sube, baja o silencia el volumen del sistema.",
    accion="control_volumen",
    parametros={
        "accion": {
            "type": "string",
            "description": "Una de: subir, bajar, silenciar, activar.",
        },
        "pasos": {
            "type": "integer",
            "description": "Cuántos pasos de volumen mover. Por defecto 5.",
            "requerido": False,
        },
    },
)
def control_volumen(accion: str, pasos: int = 5) -> str:
    # Se envían las teclas multimedia del sistema, que es la forma que funciona
    # igual con cualquier tarjeta de sonido y sin dependencias externas.
    teclas = {
        "subir": "$([char]0xAF)",
        "bajar": "$([char]0xAE)",
        "silenciar": "$([char]0xAD)",
        "activar": "$([char]0xAD)",
    }
    tecla = teclas.get(accion.lower())
    if tecla is None:
        return "No entiendo esa acción. Puedo subir, bajar, silenciar o activar."

    repeticiones = 1 if accion.lower() in ("silenciar", "activar") else max(1, min(pasos, 20))
    script = (
        "$w = New-Object -ComObject WScript.Shell; "
        f"1..{repeticiones} | ForEach-Object {{ $w.SendKeys('{tecla}') }}"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        timeout=10,
    )
    return f"Volumen: {accion}."


@registro.registrar(
    nombre="control_musica",
    descripcion="Controla la reproducción de música: reproducir, pausar, siguiente, anterior.",
    accion="control_musica",
    parametros={
        "accion": {
            "type": "string",
            "description": "Una de: reproducir, pausar, siguiente, anterior.",
        }
    },
)
def control_musica(accion: str) -> str:
    teclas = {
        "reproducir": "$([char]0xB3)",
        "pausar": "$([char]0xB3)",
        "siguiente": "$([char]0xB0)",
        "anterior": "$([char]0xB1)",
    }
    tecla = teclas.get(accion.lower())
    if tecla is None:
        return "No entiendo esa acción. Puedo reproducir, pausar, siguiente o anterior."

    script = f"(New-Object -ComObject WScript.Shell).SendKeys('{tecla}')"
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        timeout=10,
    )
    return f"Música: {accion}."
