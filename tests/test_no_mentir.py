"""
Pruebas de la red de seguridad contra afirmaciones falsas.

Un asistente que dice haber hecho algo que no hizo invalida todas sus demás
respuestas, así que esta regla no puede depender solo del prompt. Medido con
qwen3:8b: ante "reproduce Smells Like Teen Spirit" contestó "Reproduciendo
Smells Like Teen Spirit" sin llamar a ninguna herramienta.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.agent import Agente  # noqa: E402

MENTIRAS = [
    "Reproduciendo Smells Like Teen Spirit de Nirvana.",
    "He abierto YouTube para ti.",
    "Ya está abierto.",
    "Abriendo Spotify.",
    "He puesto música de Queen.",
    "He creado el archivo en tu escritorio.",
    "He guardado los cambios.",
    "He borrado el archivo.",
    "Listo, ya lo tienes.",
    "Hecho, ya está.",
]

RESPUESTAS_HONESTAS = [
    "Son las tres y veinte de la tarde.",
    "No sé poner alarmas todavía.",
    "No puedo abrir Photoshop porque no está instalado.",
    "¿Qué canción quieres que ponga?",
    "Tienes 127 gigas libres en el disco C.",
    "Una API es un conjunto de reglas que permite a los programas comunicarse.",
    "No he encontrado ningún archivo con ese nombre.",
    "",
]


@pytest.mark.parametrize("texto", MENTIRAS)
def test_se_detecta_la_afirmacion_falsa(texto: str) -> None:
    assert Agente._afirma_haber_actuado(texto), f"No detectado: {texto!r}"


@pytest.mark.parametrize("texto", RESPUESTAS_HONESTAS)
def test_las_respuestas_honestas_no_se_marcan(texto: str) -> None:
    """Una respuesta que no afirma haber actuado debe pasar tal cual."""
    assert not Agente._afirma_haber_actuado(texto), f"Falso positivo: {texto!r}"


def test_el_texto_de_disculpa_no_finge_exito() -> None:
    disculpa = Agente._texto_de_disculpa()
    assert not Agente._afirma_haber_actuado(disculpa)
    assert "no he podido" in disculpa.lower()


class TestRescateDeLlamadasEnTexto:
    """El modelo a veces escribe la llamada en vez de emitirla."""

    def test_se_rescata_nombre_y_json_en_lineas(self) -> None:
        rescatada = Agente._rescatar_llamada_en_texto(
            'anotar_carencia\n{"capacidad": "poner alarmas"}'
        )
        assert rescatada is not None
        assert rescatada[0]["name"] == "anotar_carencia"

    def test_se_rescata_json_con_el_nombre_dentro(self) -> None:
        rescatada = Agente._rescatar_llamada_en_texto(
            '{"name": "hora_fecha", "arguments": {}}'
        )
        assert rescatada is not None
        assert rescatada[0]["name"] == "hora_fecha"

    def test_no_inventa_herramientas(self) -> None:
        """Solo rescata nombres que existan de verdad en el registro."""
        assert (
            Agente._rescatar_llamada_en_texto(
                'formatear_disco\n{"unidad": "C:"}'
            )
            is None
        )

    def test_una_respuesta_normal_no_se_rescata(self) -> None:
        assert Agente._rescatar_llamada_en_texto("Son las tres.") is None

    def test_texto_con_llave_pero_sin_llamada(self) -> None:
        assert (
            Agente._rescatar_llamada_en_texto("El JSON usa llaves como { esta }")
            is None
        )
