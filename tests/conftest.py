"""
Configuración común de las pruebas.

Lo importante que hay aquí: las pruebas NO deben tocar los archivos reales de
Jarvis. Sin esto, cada ejecución de los tests metía en tu lista de pendientes
carencias inventadas por las propias pruebas —"hackear_el_pentagono" entre
ellas—, y esa lista dejaba de servir para decidir qué construir.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def no_tocar_los_datos_reales(tmp_path, monkeypatch):
    """Redirige a un directorio temporal todo lo que las pruebas escriben."""
    from core.carencias import registro_de_carencias
    from security.guard import guardian

    monkeypatch.setattr(
        registro_de_carencias, "ruta", tmp_path / "carencias.jsonl"
    )
    monkeypatch.setattr(guardian, "ruta_registro", tmp_path / "audit.log")
    yield
