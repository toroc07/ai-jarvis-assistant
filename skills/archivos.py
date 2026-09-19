"""
Habilidades de archivos.

Ninguna de estas funciones comprueba permisos por su cuenta: el guardián ya lo
hizo antes de que se ejecuten, y la ruta que reciben ya fue validada contra la
política. Aquí solo va la operación en sí.
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from skills.registro import registro

# Carpeta donde van a parar los archivos "borrados". Borrar de verdad es
# irreversible, así que Jarvis nunca lo hace: mueve a esta papelera propia y tú
# decides después. Es la diferencia entre un error molesto y uno grave.
PAPELERA = Path(__file__).resolve().parent.parent / "data" / "papelera"


@registro.registrar(
    nombre="leer_archivo",
    descripcion="Lee el contenido de un archivo de texto y lo devuelve.",
    accion="leer_archivo",
    parametros={
        "ruta": {
            "type": "string",
            "description": "Ruta completa del archivo que se quiere leer.",
        }
    },
    campo_objetivo="ruta",
)
def leer_archivo(ruta: str) -> str:
    archivo = Path(ruta).expanduser().resolve()
    if not archivo.is_file():
        return f"No existe el archivo: {archivo}"
    try:
        return archivo.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return f"El archivo {archivo.name} no es texto legible."


@registro.registrar(
    nombre="listar_carpeta",
    descripcion="Lista los archivos y subcarpetas que hay en una carpeta.",
    accion="listar_carpeta",
    parametros={
        "ruta": {
            "type": "string",
            "description": "Ruta de la carpeta que se quiere listar.",
        }
    },
    campo_objetivo="ruta",
)
def listar_carpeta(ruta: str) -> str:
    carpeta = Path(ruta).expanduser().resolve()
    if not carpeta.is_dir():
        return f"No existe la carpeta: {carpeta}"

    entradas = sorted(carpeta.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    if not entradas:
        return f"La carpeta {carpeta} está vacía."

    lineas = []
    for e in entradas[:100]:
        if e.is_dir():
            lineas.append(f"[carpeta] {e.name}")
        else:
            kb = e.stat().st_size / 1024
            lineas.append(f"          {e.name}  ({kb:.0f} KB)")

    resultado = f"Contenido de {carpeta}:\n" + "\n".join(lineas)
    if len(entradas) > 100:
        resultado += f"\n... y {len(entradas) - 100} elementos más."
    return resultado


@registro.registrar(
    nombre="buscar_archivos",
    descripcion=(
        "Busca archivos por nombre dentro de una carpeta y sus subcarpetas. "
        "Acepta comodines como *.pdf o informe*."
    ),
    accion="buscar_archivos",
    parametros={
        "carpeta": {"type": "string", "description": "Carpeta donde buscar."},
        "patron": {
            "type": "string",
            "description": "Patrón del nombre, por ejemplo '*.pdf' o 'factura*'.",
        },
    },
    campo_objetivo="carpeta",
)
def buscar_archivos(carpeta: str, patron: str) -> str:
    base = Path(carpeta).expanduser().resolve()
    if not base.is_dir():
        return f"No existe la carpeta: {base}"

    encontrados = list(base.rglob(patron))[:50]
    if not encontrados:
        return f"No se encontró nada que coincida con '{patron}' en {base}."
    return f"Encontrados {len(encontrados)}:\n" + "\n".join(
        str(p) for p in encontrados
    )


@registro.registrar(
    nombre="escribir_archivo",
    descripcion="Crea un archivo de texto o reemplaza su contenido.",
    accion="escribir_archivo",
    parametros={
        "ruta": {"type": "string", "description": "Ruta del archivo a escribir."},
        "contenido": {"type": "string", "description": "Texto que se va a guardar."},
    },
    campo_objetivo="ruta",
)
def escribir_archivo(ruta: str, contenido: str) -> str:
    archivo = Path(ruta).expanduser().resolve()
    archivo.parent.mkdir(parents=True, exist_ok=True)

    # Si ya existía, se guarda una copia antes de pisarlo. Sobrescribir sin
    # copia es la forma más fácil de perder trabajo sin darse cuenta.
    if archivo.exists():
        marca = datetime.now().strftime("%Y%m%d-%H%M%S")
        copia = PAPELERA / f"{archivo.stem}.{marca}{archivo.suffix}"
        PAPELERA.mkdir(parents=True, exist_ok=True)
        shutil.copy2(archivo, copia)

    archivo.write_text(contenido, encoding="utf-8")
    return f"Guardado en {archivo} ({len(contenido)} caracteres)."


@registro.registrar(
    nombre="borrar_archivo",
    descripcion=(
        "Envía un archivo a la papelera interna de Jarvis. No lo elimina del "
        "disco: queda recuperable."
    ),
    accion="borrar_archivo",
    parametros={
        "ruta": {"type": "string", "description": "Ruta del archivo a retirar."}
    },
    campo_objetivo="ruta",
)
def borrar_archivo(ruta: str) -> str:
    archivo = Path(ruta).expanduser().resolve()
    if not archivo.is_file():
        return f"No existe el archivo: {archivo}"

    PAPELERA.mkdir(parents=True, exist_ok=True)
    marca = datetime.now().strftime("%Y%m%d-%H%M%S")
    destino = PAPELERA / f"{archivo.stem}.{marca}{archivo.suffix}"
    shutil.move(str(archivo), str(destino))
    return (
        f"'{archivo.name}' se movió a la papelera de Jarvis. "
        f"Si fue un error, está en {destino}."
    )


@registro.registrar(
    nombre="crear_carpeta",
    descripcion="Crea una carpeta nueva.",
    accion="crear_carpeta",
    parametros={
        "ruta": {"type": "string", "description": "Ruta de la carpeta a crear."}
    },
    campo_objetivo="ruta",
)
def crear_carpeta(ruta: str) -> str:
    carpeta = Path(ruta).expanduser().resolve()
    if carpeta.exists():
        return f"La carpeta {carpeta} ya existe."
    carpeta.mkdir(parents=True)
    return f"Carpeta creada: {carpeta}"
