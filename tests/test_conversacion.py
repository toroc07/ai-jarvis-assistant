"""
Pruebas del cierre de conversación.

El equilibrio importa en las dos direcciones: cerrar cuando no debe interrumpe
una petición a medias, y no cerrar cuando debe te deja el orbe en pantalla.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skills.conversacion import MARCA_DE_CIERRE, es_despedida, terminar_conversacion  # noqa: E402


DESPEDIDAS = [
    "Gracias por la ayuda, ya me encargo yo",
    "gracias, ya me ocupo",
    "nada más, gracias",
    "Gracias",
    "muchas gracias",
    "vale gracias",
    "eso es todo",
    "es todo por ahora",
    "no necesito nada más",
    "adiós",
    "adios",
    "hasta luego",
    "nos vemos",
    "chao",
    "te dejo",
    "hasta mañana",
    "ciérrate",
    "cállate",
    "vete",
    "ya está, gracias",
]


PETICIONES = [
    "abre youtube",
    "qué hora es",
    "gracias a qué se debe que el cielo sea azul",
    "busca el archivo de gracias.txt en mis documentos",
    "cómo se dice adiós en japonés",
    "escribe un correo de despedida para mi jefe explicando que me voy",
    "dime algo",
    "sube el volumen",
    "",
    "   ",
]


@pytest.mark.parametrize("frase", DESPEDIDAS)
def test_se_reconocen_las_despedidas(frase: str) -> None:
    assert es_despedida(frase), f"No se reconoció como despedida: {frase!r}"


@pytest.mark.parametrize("frase", PETICIONES)
def test_no_se_cierra_con_peticiones_normales(frase: str) -> None:
    assert not es_despedida(frase), f"Se cerró por error con: {frase!r}"


def test_una_peticion_larga_nunca_es_despedida() -> None:
    """Una frase larga con 'gracias' dentro sigue siendo una petición."""
    larga = (
        "oye gracias por lo de antes pero ahora necesito que busques todos "
        "los archivos pdf que tengo en la carpeta de documentos"
    )
    assert not es_despedida(larga)


def test_la_herramienta_marca_el_cierre() -> None:
    resultado = terminar_conversacion("Hasta luego, Ana.")
    assert MARCA_DE_CIERRE in resultado
    assert "Hasta luego, Ana." in resultado


def test_la_herramienta_tiene_despedida_por_defecto() -> None:
    assert "Hasta luego" in terminar_conversacion()
