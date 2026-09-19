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


@registro.registrar(
    nombre="copiar_archivo",
    descripcion=(
        "Copia un archivo a otra ubicación, dejando el original donde estaba."
    ),
    accion="copiar_archivo",
    parametros={
        "origen": {"type": "string", "description": "Archivo que se quiere copiar."},
        "destino": {"type": "string", "description": "Dónde dejar la copia."},
    },
    campo_objetivo="destino",
)
def copiar_archivo(origen: str, destino: str) -> str:
    # El guardián valida el destino, que es donde se escribe. El origen se
    # comprueba aparte contra las rutas de LECTURA: copiar algo implica leerlo,
    # y sin esta comprobación se podría sacar un archivo de una zona prohibida
    # copiándolo a una permitida.
    from security.guard import Peticion, guardian

    ruta_origen = Path(origen).expanduser().resolve()
    veredicto = guardian.evaluar(
        Peticion(accion="leer_archivo", objetivo=str(ruta_origen))
    )
    if not veredicto.permitida:
        return f"No puedo leer el origen: {veredicto.razon}"

    if not ruta_origen.is_file():
        return f"No existe el archivo: {ruta_origen}"

    ruta_destino = Path(destino).expanduser().resolve()
    if ruta_destino.is_dir():
        ruta_destino = ruta_destino / ruta_origen.name

    ruta_destino.parent.mkdir(parents=True, exist_ok=True)

    # Si el destino ya existe se guarda copia antes de pisarlo, igual que al
    # escribir: una copia nunca debería destruir algo sin dejar rastro.
    if ruta_destino.exists():
        PAPELERA.mkdir(parents=True, exist_ok=True)
        marca = datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copy2(ruta_destino, PAPELERA / f"{ruta_destino.stem}.{marca}{ruta_destino.suffix}")

    shutil.copy2(ruta_origen, ruta_destino)
    return f"Copiado a {ruta_destino}."


@registro.registrar(
    nombre="mover_archivo",
    descripcion=(
        "Mueve un archivo a otra ubicación, o lo renombra. El original deja de "
        "estar donde estaba."
    ),
    accion="mover_archivo",
    parametros={
        "origen": {"type": "string", "description": "Archivo que se quiere mover."},
        "destino": {"type": "string", "description": "Dónde dejarlo."},
    },
    campo_objetivo="destino",
)
def mover_archivo(origen: str, destino: str) -> str:
    # Mover ELIMINA el original de su sitio, así que el origen se valida contra
    # las rutas de ESCRITURA, no las de lectura. Es más estricto que copiar a
    # propósito: aquí sí se pierde algo del sitio de partida.
    from security.guard import Peticion, guardian

    ruta_origen = Path(origen).expanduser().resolve()
    veredicto = guardian.evaluar(
        Peticion(accion="borrar_archivo", objetivo=str(ruta_origen))
    )
    if veredicto.decision.value == "denegado":
        return f"No puedo sacar el archivo de ahí: {veredicto.razon}"

    if not ruta_origen.is_file():
        return f"No existe el archivo: {ruta_origen}"

    ruta_destino = Path(destino).expanduser().resolve()
    if ruta_destino.is_dir():
        ruta_destino = ruta_destino / ruta_origen.name

    ruta_destino.parent.mkdir(parents=True, exist_ok=True)

    if ruta_destino.exists():
        PAPELERA.mkdir(parents=True, exist_ok=True)
        marca = datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.move(
            str(ruta_destino),
            str(PAPELERA / f"{ruta_destino.stem}.{marca}{ruta_destino.suffix}"),
        )

    shutil.move(str(ruta_origen), str(ruta_destino))
    return f"Movido a {ruta_destino}."
