"""
Notas rápidas: "apunta que tengo que comprar pilas".

Se añaden al final de workspace/notas.md, una por línea y con la fecha. Nunca se
sobrescribe nada: solo se añade. Aun así cada nota se confirma antes de
guardarla, porque escribe en tu disco.
"""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

from skills.registro import registro

ARCHIVO_NOTAS = Path(__file__).resolve().parent.parent / "workspace" / "notas.md"
MAX_NOTA = 500
NOTAS_A_LEER = 15

_lock = threading.Lock()


@registro.registrar(
    nombre="tomar_nota",
    descripcion=(
        "ES LA HERRAMIENTA CORRECTA para 'apunta que...', 'toma nota de...', "
        "'anota...' o 'añade a la lista...'. Guarda la nota en el cuaderno del "
        "usuario. Sí sabes apuntar notas: úsala."
    ),
    accion="tomar_nota",
    parametros={"texto": {"type": "string", "description": "Lo que hay que apuntar."}},
    campo_objetivo="texto",
)
def tomar_nota(texto: str) -> str:
    # Una nota es una línea: los saltos de línea se aplanan para que no pueda
    # colarse como varias, ni romper el formato del archivo.
    nota = " ".join((texto or "").split())[:MAX_NOTA]
    if not nota:
        return "No me has dicho qué apuntar. NO digas que lo has apuntado."

    marca = datetime.now().strftime("%Y-%m-%d %H:%M")
    with _lock:
        ARCHIVO_NOTAS.parent.mkdir(parents=True, exist_ok=True)
        nuevo = not ARCHIVO_NOTAS.exists()
        with open(ARCHIVO_NOTAS, "a", encoding="utf-8") as f:
            if nuevo:
                f.write("# Notas\n\n")
            f.write(f"- [{marca}] {nota}\n")
    return f"Apuntado: «{nota}»."


@registro.registrar(
    nombre="leer_notas",
    descripcion="Lee las últimas notas apuntadas. Úsala para '¿qué tengo apuntado?'.",
    accion="leer_notas",
    parametros={},
)
def leer_notas() -> str:
    if not ARCHIVO_NOTAS.exists():
        return "No hay ninguna nota apuntada todavía."
    lineas = [
        linea.strip()
        for linea in ARCHIVO_NOTAS.read_text(encoding="utf-8").splitlines()
        if linea.startswith("- ")
    ]
    if not lineas:
        return "No hay ninguna nota apuntada todavía."
    ultimas = lineas[-NOTAS_A_LEER:]
    cabecera = (
        f"Las últimas {len(ultimas)} de {len(lineas)} notas:"
        if len(lineas) > len(ultimas)
        else f"{len(lineas)} nota{'s' if len(lineas) != 1 else ''}:"
    )
    return cabecera + "\n" + "\n".join(ultimas)
