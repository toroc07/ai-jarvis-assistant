"""
Revisión de lo que Jarvis no supo hacer.

    python revisar.py                    lista las carencias pendientes
    python revisar.py --todas            incluye las ya tratadas
    python revisar.py --marcar "X" aprobada
    python revisar.py --marcar "X" descartada

Está pensado para que Claude lo ejecute al empezar una sesión de trabajo, vea
qué habilidades has echado en falta ordenadas por cuántas veces las has pedido,
y te las proponga una a una para que apruebes o descartes cada una.

Ordenar por frecuencia es lo importante: indica qué construir primero según lo
que de verdad usas, no según lo que parezca más vistoso.
"""

from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))

from core.carencias import registro_de_carencias  # noqa: E402

# Cómo leer cada origen, para saber qué implica arreglarlo.
EXPLICACION = {
    "herramienta_inventada": (
        "el modelo se inventó una herramienta con ese nombre porque esperaba "
        "que existiera"
    ),
    "accion_no_permitida": (
        "la acción no está en policy.yaml; puede faltar la habilidad o solo el "
        "permiso"
    ),
    "declarada_por_el_modelo": (
        "Jarvis reconoció que no sabía hacerlo y lo apuntó él mismo"
    ),
    "deducida_de_la_respuesta": (
        "Jarvis dijo que no sabía y no ejecutó nada; se dedujo de su respuesta"
    ),
}

# Aviso al pie para las carencias deducidas: su nombre es la frase que dijiste,
# así que dos formas distintas de pedir lo mismo aparecen como dos entradas.
# Agruparlas es parte del trabajo de revisión, no algo que el archivo resuelva.
NOTA_DEDUCIDAS = (
    "Las entradas marcadas como deducidas llevan por nombre tu frase literal, "
    "así que la misma necesidad pedida de dos formas sale dos veces. "
    "Al revisarlas conviene agruparlas a mano."
)


def listar(solo_pendientes: bool = True) -> int:
    resumenes = registro_de_carencias.resumen(solo_pendientes=solo_pendientes)

    if not resumenes:
        print("No hay carencias registradas.")
        print()
        print("Se irán anotando solas cuando le pidas a Jarvis algo que no")
        print("sepa hacer. Vuelve a ejecutar esto dentro de unos días de uso.")
        return 0

    total = sum(r.veces for r in resumenes)
    print(f"{len(resumenes)} capacidades distintas, {total} peticiones en total.")
    print("Ordenadas por cuántas veces las has pedido.")
    print("=" * 72)

    for i, r in enumerate(resumenes, 1):
        veces = "vez" if r.veces == 1 else "veces"
        print()
        print(f"{i}. {r.que_falta}    [{r.veces} {veces}]")
        print(f"   origen: {EXPLICACION.get(r.origen, r.origen)}")
        print(f"   desde {r.primera_vez[:10]} hasta {r.ultima_vez[:10]}")
        if r.estado != "pendiente":
            print(f"   estado: {r.estado}")
        if r.ejemplos:
            print("   lo pediste así:")
            for ejemplo in r.ejemplos:
                print(f"     · {ejemplo}")

    print()
    print("=" * 72)
    if any(r.origen == "deducida_de_la_respuesta" for r in resumenes):
        print(NOTA_DEDUCIDAS)
        print()
    print("Para marcar una decisión:")
    print('  python revisar.py --marcar "<nombre>" aprobada')
    print('  python revisar.py --marcar "<nombre>" descartada')
    return 0


def marcar(que_falta: str, estado: str) -> int:
    if estado not in ("pendiente", "propuesta", "aprobada", "descartada"):
        print(f"Estado no válido: '{estado}'.")
        print("Usa: pendiente, propuesta, aprobada o descartada.")
        return 1

    cambiadas = registro_de_carencias.marcar(que_falta, estado)
    if cambiadas == 0:
        print(f"No se encontró ninguna carencia llamada '{que_falta}'.")
        return 1

    print(f"'{que_falta}' marcada como {estado} ({cambiadas} anotaciones).")
    return 0


def main() -> int:
    argumentos = sys.argv[1:]

    if "--marcar" in argumentos:
        i = argumentos.index("--marcar")
        if len(argumentos) < i + 3:
            print('Uso: python revisar.py --marcar "<nombre>" <estado>')
            return 1
        return marcar(argumentos[i + 1], argumentos[i + 2])

    return listar(solo_pendientes="--todas" not in argumentos)


if __name__ == "__main__":
    raise SystemExit(main())
