"""
Pruebas de las habilidades de sistema.

El foco está en que Jarvis no afirme haber hecho algo que no hizo: es el fallo
que más daña la confianza, porque invalida todas sus demás respuestas.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skills.sistema import (  # noqa: E402
    _navegador_por_defecto,
    _resolver_app,
    abrir_url,
)


# -- Resolución de aplicaciones ---------------------------------------------


def test_app_inexistente_no_se_resuelve() -> None:
    assert _resolver_app("photoshop") is None


def test_app_inventada_no_se_resuelve() -> None:
    assert _resolver_app("programa_que_no_existe_123") is None


def test_alias_funciona() -> None:
    """'code' y 'vscode' deben llevar al mismo sitio."""
    assert _resolver_app("code") == _resolver_app("vscode")


def test_navegador_se_resuelve_al_predeterminado() -> None:
    """'abre el navegador' debe usar el tuyo, no uno impuesto."""
    resuelta = _resolver_app("navegador")
    if _navegador_por_defecto() is None:
        pytest.skip("No se pudo leer el navegador por defecto del registro.")
    assert resuelta is not None
    assert Path(resuelta[1]).is_file()


def test_lo_que_se_resuelve_existe_de_verdad() -> None:
    """Si se devuelve una ruta, el archivo tiene que estar ahí."""
    resuelta = _resolver_app("explorador")
    assert resuelta is not None
    assert Path(resuelta[1]).is_file()


# -- Apertura de direcciones ------------------------------------------------


def test_url_con_esquema_peligroso_se_rechaza() -> None:
    """file: llegaría a archivos locales saltándose la política de rutas."""
    resultado = abrir_url("file:///C:/Windows/System32/config/SAM")
    assert "NO digas que se abrió" in resultado


def test_javascript_se_rechaza() -> None:
    resultado = abrir_url("javascript:alert(1)")
    assert "NO digas que se abrió" in resultado


def test_url_vacia_se_rechaza() -> None:
    resultado = abrir_url("   ")
    assert "NO digas que se abrió" in resultado


# -- Honestidad en los mensajes ---------------------------------------------


def test_app_no_instalada_avisa_de_no_mentir() -> None:
    """El mensaje de fallo debe frenar al modelo, no solo informar."""
    from skills.sistema import abrir_app

    resultado = abrir_app("photoshop")
    assert "No encuentro" in resultado
    assert "NO le digas al usuario que la abriste" in resultado


def test_app_no_instalada_sugiere_alternativas() -> None:
    from skills.sistema import abrir_app

    resultado = abrir_app("photoshop")
    assert "navegador" in resultado
