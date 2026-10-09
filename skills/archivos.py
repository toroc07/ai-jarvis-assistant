"""
Habilidades de archivos.

Ninguna de estas funciones comprueba permisos por su cuenta: el guardián ya lo
hizo antes de que se ejecuten, y la ruta que reciben ya fue validada contra la
política. Aquí solo va la operación en sí.
"""

from __future__ import annotations

import fnmatch
import os
import shutil
import time
import uuid
from datetime import datetime
from pathlib import Path

from skills.registro import registro

# Carpeta donde van a parar los archivos "borrados". Borrar de verdad es
# irreversible, así que Jarvis nunca lo hace: mueve a esta papelera propia y tú
# decides después. Es la diferencia entre un error molesto y uno grave.
PAPELERA = Path(__file__).resolve().parent.parent / "data" / "papelera"

# Lo que se lee vuelve entero al modelo. Un archivo de varios MB desbordaría su
# contexto y la respuesta se iría a la basura, así que se corta y se avisa.
MAX_CARACTERES_LECTURA = 20_000

# Tope de lo que se puede escribir de una vez (en caracteres).
MAX_CARACTERES_ESCRITURA = 1_000_000

# Límites de búsqueda: sin ellos, buscar en Documentos recorría entornos
# virtuales enteros y podía tardar minutos.
MAX_RESULTADOS_BUSQUEDA = 50
MAX_SEGUNDOS_BUSQUEDA = 5.0
_CARPETAS_QUE_NO_SE_RECORREN = {
    "venv", ".venv", ".git", "node_modules", "__pycache__", ".pytest_cache"
}


def _nombre_en_papelera(archivo: Path) -> Path:
    """Dónde guardar 'archivo' en la papelera sin pisar otro del mismo nombre."""
    PAPELERA.mkdir(parents=True, exist_ok=True)
    marca = datetime.now().strftime("%Y%m%d-%H%M%S")
    # La marca de tiempo sola chocaba si dos archivos con el mismo nombre se
    # retiraban en el mismo segundo, y el segundo pisaba al primero.
    return PAPELERA / f"{archivo.stem}.{marca}-{uuid.uuid4().hex[:6]}{archivo.suffix}"


def _destino_final_permitido(ruta: Path) -> str | None:
    """Comprueba el archivo de destino ya con su nombre definitivo.

    Al copiar o mover a una carpeta, el guardián solo vio la carpeta. Aquí se
    valida el archivo concreto que se va a crear (su extensión, sobre todo).
    """
    from security.guard import Decision, Peticion, guardian

    veredicto = guardian.evaluar(Peticion(accion="escribir_archivo", objetivo=str(ruta)))
    if veredicto.decision is Decision.DENEGADO:
        return veredicto.razon
    return None


class _ErrorDeDocumento(Exception):
    """Un PDF o Word que no se ha podido abrir; el mensaje es para el modelo."""


def _texto_de_pdf(archivo: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        raise _ErrorDeDocumento("falta la librería pypdf (pip install pypdf).")
    try:
        lector = PdfReader(str(archivo))
        if lector.is_encrypted:
            raise _ErrorDeDocumento("está protegido con contraseña.")
        paginas = []
        total = 0
        for numero, pagina in enumerate(lector.pages, 1):
            texto = pagina.extract_text() or ""
            paginas.append(f"[Página {numero}]\n{texto.strip()}")
            total += len(texto)
            # No tiene sentido extraer cien páginas si solo se van a mostrar
            # las primeras: el recorte posterior las tiraría igualmente.
            if total > MAX_CARACTERES_LECTURA:
                paginas.append(f"[…el documento tiene {len(lector.pages)} páginas…]")
                break
        return "\n\n".join(paginas)
    except _ErrorDeDocumento:
        raise
    except Exception as e:
        raise _ErrorDeDocumento(f"el PDF parece dañado ({type(e).__name__}).")


def _texto_de_docx(archivo: Path) -> str:
    try:
        import docx
    except ImportError:
        raise _ErrorDeDocumento("falta la librería python-docx (pip install python-docx).")
    try:
        documento = docx.Document(str(archivo))
    except Exception as e:
        raise _ErrorDeDocumento(f"el documento parece dañado ({type(e).__name__}).")

    partes = [p.text for p in documento.paragraphs if p.text.strip()]
    # Las tablas no salen en los párrafos; sin esto se perdía su contenido.
    for tabla in documento.tables:
        for fila in tabla.rows:
            celdas = [c.text.strip() for c in fila.cells if c.text.strip()]
            if celdas:
                partes.append(" | ".join(celdas))
    return "\n".join(partes)


@registro.registrar(
    nombre="leer_archivo",
    descripcion=(
        "Lee el contenido de un archivo y lo devuelve. Sirve para texto (.txt, "
        ".md, .csv, .json...), PDF y documentos de Word (.docx). Úsala también "
        "para resumir un documento: léelo y resume tú lo que devuelva."
    ),
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

    tipo = archivo.suffix.lower()
    try:
        if tipo == ".pdf":
            texto = _texto_de_pdf(archivo)
        elif tipo == ".docx":
            texto = _texto_de_docx(archivo)
        else:
            texto = archivo.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return f"El archivo {archivo.name} no es texto legible."
    except _ErrorDeDocumento as e:
        return f"No he podido leer {archivo.name}: {e} NO te inventes su contenido."

    if not texto.strip():
        return (
            f"{archivo.name} no tiene texto que se pueda extraer (puede ser un "
            "escaneo o solo imágenes). NO te inventes su contenido."
        )
    if len(texto) > MAX_CARACTERES_LECTURA:
        return (
            texto[:MAX_CARACTERES_LECTURA]
            + f"\n\n[Archivo recortado: solo se muestran los primeros "
            f"{MAX_CARACTERES_LECTURA} de {len(texto)} caracteres. Díselo al "
            "usuario si importa lo que falta.]"
        )
    return texto


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
    from security.guard import guardian

    base = Path(carpeta).expanduser().resolve()
    if not base.is_dir():
        return f"No existe la carpeta: {base}"

    patron = (patron or "").strip() or "*"
    # El patrón es un nombre con comodines, no una ruta: con barras o '..' se
    # podría apuntar fuera de la carpeta que el guardián validó.
    if any(c in patron for c in ("/", "\\", ":")) or ".." in patron:
        return "El patrón debe ser solo un nombre con comodines, como '*.pdf'."

    # Cada subcarpeta se comprueba contra la política: la carpeta base puede
    # estar permitida y contener otras prohibidas (Documents/Jarvis/data).
    permitida = guardian.filtro_de_lectura()
    limite = time.monotonic() + MAX_SEGUNDOS_BUSQUEDA
    encontrados: list[Path] = []
    incompleta = False

    for raiz, carpetas, archivos in os.walk(base):
        carpetas[:] = [
            c for c in carpetas
            if c.lower() not in _CARPETAS_QUE_NO_SE_RECORREN
            and permitida((Path(raiz) / c).resolve())
        ]
        for nombre in carpetas + archivos:
            if fnmatch.fnmatch(nombre.lower(), patron.lower()):
                encontrados.append(Path(raiz) / nombre)
        if len(encontrados) >= MAX_RESULTADOS_BUSQUEDA or time.monotonic() > limite:
            incompleta = True
            break

    encontrados = encontrados[:MAX_RESULTADOS_BUSQUEDA]
    if not encontrados:
        aviso = " (la búsqueda se cortó por tiempo)" if incompleta else ""
        return f"No se encontró nada que coincida con '{patron}' en {base}{aviso}."

    resultado = f"Encontrados {len(encontrados)}:\n" + "\n".join(str(p) for p in encontrados)
    if incompleta:
        resultado += "\n(Puede haber más: la búsqueda se detuvo antes de terminar.)"
    return resultado


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
    if len(contenido) > MAX_CARACTERES_ESCRITURA:
        return (
            f"El contenido tiene {len(contenido)} caracteres y el máximo es "
            f"{MAX_CARACTERES_ESCRITURA}. NO digas que se guardó."
        )

    archivo = Path(ruta).expanduser().resolve()
    archivo.parent.mkdir(parents=True, exist_ok=True)

    # Si ya existía, se guarda una copia antes de pisarlo. Sobrescribir sin
    # copia es la forma más fácil de perder trabajo sin darse cuenta.
    if archivo.exists():
        shutil.copy2(archivo, _nombre_en_papelera(archivo))

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

    destino = _nombre_en_papelera(archivo)
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
        problema = _destino_final_permitido(ruta_destino)
        if problema:
            return f"No puedo dejar la copia ahí: {problema}"

    ruta_destino.parent.mkdir(parents=True, exist_ok=True)

    # Si el destino ya existe se guarda copia antes de pisarlo, igual que al
    # escribir: una copia nunca debería destruir algo sin dejar rastro.
    if ruta_destino.exists():
        shutil.copy2(ruta_destino, _nombre_en_papelera(ruta_destino))

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
        problema = _destino_final_permitido(ruta_destino)
        if problema:
            return f"No puedo dejarlo ahí: {problema}"

    ruta_destino.parent.mkdir(parents=True, exist_ok=True)

    if ruta_destino.exists():
        shutil.move(str(ruta_destino), str(_nombre_en_papelera(ruta_destino)))

    shutil.move(str(ruta_origen), str(ruta_destino))
    return f"Movido a {ruta_destino}."
