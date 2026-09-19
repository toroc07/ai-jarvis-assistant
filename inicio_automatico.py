"""
Arrancar Jarvis con Windows.

    python inicio_automatico.py --estado       ver cómo está
    python inicio_automatico.py --activar      arrancar con Windows
    python inicio_automatico.py --desactivar   dejar de hacerlo

Se usa la carpeta de Inicio del usuario, no el registro de Windows. Es
deliberado: un acceso directo en una carpeta se ve, se entiende y se borra a
mano si algo va mal. Una entrada en el registro es invisible para quien no sepa
buscarla, y este proyecto trata de no hacer nada a tus espaldas.

También ofrece activar Ollama al inicio. Jarvis lo arranca solo si hace falta,
pero eso añade unos segundos al primer uso.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
CARPETA_INICIO = (
    Path(os.environ["APPDATA"])
    / "Microsoft"
    / "Windows"
    / "Start Menu"
    / "Programs"
    / "Startup"
)
ACCESO_JARVIS = CARPETA_INICIO / "Jarvis.lnk"
ACCESO_OLLAMA = CARPETA_INICIO / "Ollama.lnk"

PYTHONW = RAIZ / "venv" / "Scripts" / "pythonw.exe"
ENTRADA = RAIZ / "jarvis.py"


def _crear_acceso(destino: Path, programa: Path, argumentos: str = "") -> bool:
    """Crea un acceso directo con PowerShell, que es lo que Windows entiende."""
    script = (
        "$w = New-Object -ComObject WScript.Shell; "
        f"$s = $w.CreateShortcut('{destino}'); "
        f"$s.TargetPath = '{programa}'; "
        f"$s.Arguments = '{argumentos}'; "
        f"$s.WorkingDirectory = '{RAIZ}'; "
        "$s.Save()"
    )
    resultado = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return resultado.returncode == 0 and destino.exists()


def _buscar_ollama() -> Path | None:
    import shutil

    encontrado = shutil.which("ollama")
    if encontrado:
        return Path(encontrado)
    candidata = Path(os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe"))
    return candidata if candidata.is_file() else None


def estado() -> int:
    print("Arranque automático con Windows")
    print("=" * 46)
    print(f"  Jarvis: {'activado' if ACCESO_JARVIS.exists() else 'desactivado'}")
    print(f"  Ollama: {'activado' if ACCESO_OLLAMA.exists() else 'desactivado'}")
    print()
    print(f"Los accesos directos viven en:\n  {CARPETA_INICIO}")
    print("Puedes borrarlos a mano cuando quieras.")
    return 0


def activar() -> int:
    if not PYTHONW.exists():
        print(f"No encuentro el entorno virtual en {PYTHONW}.")
        print("Créalo antes con: py -3.12 -m venv venv")
        return 1

    CARPETA_INICIO.mkdir(parents=True, exist_ok=True)

    if _crear_acceso(ACCESO_JARVIS, PYTHONW, str(ENTRADA)):
        print(f"Jarvis arrancará con Windows.\n  {ACCESO_JARVIS}")
    else:
        print("No se pudo crear el acceso directo de Jarvis.")
        return 1

    ollama = _buscar_ollama()
    if ollama is None:
        print()
        print("No encuentro Ollama. Jarvis intentará arrancarlo igualmente")
        print("cuando lo necesite, pero tardará unos segundos más.")
        return 0

    if _crear_acceso(ACCESO_OLLAMA, ollama, "serve"):
        print(f"Ollama también.\n  {ACCESO_OLLAMA}")
        print()
        print("Con esto Jarvis estará escuchando poco después de encender el PC.")
    return 0


def desactivar() -> int:
    quitados = []
    for acceso, nombre in ((ACCESO_JARVIS, "Jarvis"), (ACCESO_OLLAMA, "Ollama")):
        if acceso.exists():
            acceso.unlink()
            quitados.append(nombre)

    if quitados:
        print(f"Ya no arrancan con Windows: {', '.join(quitados)}.")
    else:
        print("No estaban configurados para arrancar solos.")
    return 0


def main() -> int:
    argumentos = sys.argv[1:]
    if "--activar" in argumentos:
        return activar()
    if "--desactivar" in argumentos:
        return desactivar()
    return estado()


if __name__ == "__main__":
    raise SystemExit(main())
