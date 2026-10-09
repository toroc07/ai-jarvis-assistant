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
    from security.parada import interruptor

    monkeypatch.setattr(
        registro_de_carencias, "ruta", tmp_path / "carencias.jsonl"
    )
    monkeypatch.setattr(guardian, "ruta_registro", tmp_path / "audit.log")

    # Las pruebas que crean su propio Guardian() también escribían en el
    # security/audit.log real, mezclando ataques de prueba con el registro de
    # verdad. Todo guardián creado durante una prueba usa el directorio temporal.
    from security.guard import Guardian

    original_init = Guardian.__init__

    def init_con_registro_temporal(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self.ruta_registro = tmp_path / "audit.log"

    monkeypatch.setattr(Guardian, "__init__", init_con_registro_temporal)

    # El interruptor global guarda su estado en data/parada.json. Sin esto, una
    # prueba que lo active o lo rearme tocaría la parada REAL del usuario: podía
    # borrarla, o dejar a Jarvis parado en el siguiente arranque.
    monkeypatch.setattr(interruptor, "ruta", tmp_path / "parada.json")
    estado_previo = (interruptor._parada, interruptor._activado.is_set())
    interruptor._parada = None
    interruptor._activado.clear()
    yield
    interruptor._parada, activado = estado_previo
    if activado:
        interruptor._activado.set()
    else:
        interruptor._activado.clear()
