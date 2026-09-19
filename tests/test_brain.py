"""
Pruebas del cerebro: enrutado entre modelos y limpieza de las respuestas.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.brain import (  # noqa: E402
    MARCA_DE_AYUDA,
    limpiar_razonamiento,
    necesita_claude,
    pidio_ayuda,
)


# -- Limpieza del razonamiento ----------------------------------------------


def test_se_quita_el_bloque_think() -> None:
    texto = "<think>a ver, esto va de...</think>Son las tres."
    assert limpiar_razonamiento(texto) == "Son las tres."


def test_se_quita_el_bloque_aunque_no_cierre() -> None:
    """Una respuesta cortada a media reflexión no debe leerse en voz alta."""
    texto = "Son las tres.<think>pero espera, deberia comprobar si"
    assert limpiar_razonamiento(texto) == "Son las tres."


def test_no_se_devuelve_vacio() -> None:
    """Preferible una respuesta rara a ninguna respuesta."""
    texto = "<think>solo razonamiento y nada mas</think>"
    assert limpiar_razonamiento(texto) != ""


def test_texto_normal_no_se_toca() -> None:
    texto = "Son las tres y veinte de la tarde."
    assert limpiar_razonamiento(texto) == texto


def test_texto_vacio_no_rompe() -> None:
    assert limpiar_razonamiento("") == ""


# -- Enrutado ---------------------------------------------------------------


def test_peticion_simple_se_queda_en_local() -> None:
    assert not necesita_claude("¿qué hora es?")
    assert not necesita_claude("sube el volumen")
    assert not necesita_claude("abre spotify")


def test_peticion_de_codigo_va_a_claude() -> None:
    assert necesita_claude("escribe un script que ordene estos archivos")


def test_peticion_de_diseno_va_a_claude() -> None:
    assert necesita_claude("diseña la arquitectura de una tienda online")


def test_peticion_muy_larga_va_a_claude() -> None:
    assert necesita_claude("resume esto: " + "palabra " * 300)


def test_contexto_muy_grande_va_a_claude() -> None:
    assert necesita_claude("y entonces?", longitud_contexto=20000)


# -- Delegación explícita ---------------------------------------------------


def test_se_detecta_la_peticion_de_ayuda() -> None:
    assert pidio_ayuda(f"{MARCA_DE_AYUDA} esto necesita un modelo mejor")


def test_respuesta_normal_no_pide_ayuda() -> None:
    assert not pidio_ayuda("Son las tres.")
