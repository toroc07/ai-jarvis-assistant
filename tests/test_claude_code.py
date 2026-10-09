"""
Pruebas del motor que llega a Claude a través de Claude Code (la suscripción).

No se lanza Claude de verdad: se sustituye subprocess.run y se comprueba lo que
importa para la seguridad y la factura. Claude Code va sin herramientas propias,
sin la clave de la API en el entorno y fuera de cualquier proyecto. Una petición
de herramienta solo se acepta si es la respuesta entera y la herramienta existe.
"""

from __future__ import annotations

import json
import subprocess

import pytest

import core.brain as brain
from core.brain import Mensaje, ModeloClaudeCode, llamada_en_respuesta

HERRAMIENTAS = [
    {
        "type": "function",
        "function": {
            "name": "hora_fecha",
            "description": "Dice la hora.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    }
]


@pytest.fixture
def lanzamientos(monkeypatch):
    """Sustituye a Claude Code por una respuesta fija y anota cómo se lanzó."""
    vistos: list[dict] = []
    respuesta = {"texto": "Hola."}

    def falso_run(orden, **kwargs):
        vistos.append({"orden": orden, **kwargs})
        salida = json.dumps({"is_error": False, "result": respuesta["texto"]})
        return subprocess.CompletedProcess(orden, 0, stdout=salida, stderr="")

    monkeypatch.setattr(subprocess, "run", falso_run)
    monkeypatch.setattr(ModeloClaudeCode, "_ejecutable", staticmethod(lambda: "claude"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-no-debe-llegar")
    return vistos, respuesta


def conversar(modelo: ModeloClaudeCode):
    return modelo.responder(
        [Mensaje("system", "Eres Jarvis."), Mensaje("user", "¿Qué hora es?")], HERRAMIENTAS
    )


def test_claude_code_se_lanza_sin_herramientas_propias(lanzamientos) -> None:
    vistos, _ = lanzamientos
    conversar(ModeloClaudeCode())
    orden = vistos[0]["orden"]
    assert orden[orden.index("--tools") + 1] == ""
    assert "--strict-mcp-config" in orden
    # --bare obliga a usar la clave de la API: facturaría.
    assert "--bare" not in orden


def test_la_clave_de_la_api_no_llega_a_claude_code(lanzamientos) -> None:
    vistos, _ = lanzamientos
    conversar(ModeloClaudeCode())
    entorno = vistos[0]["env"]
    assert "ANTHROPIC_API_KEY" not in entorno
    assert "ANTHROPIC_AUTH_TOKEN" not in entorno


def test_se_lanza_fuera_de_cualquier_proyecto(lanzamientos) -> None:
    vistos, _ = lanzamientos
    conversar(ModeloClaudeCode())
    assert "jarvis-claude-" in vistos[0]["cwd"]


def test_las_herramientas_de_jarvis_van_en_el_prompt(lanzamientos) -> None:
    vistos, _ = lanzamientos
    conversar(ModeloClaudeCode())
    orden = vistos[0]["orden"]
    assert "--system-prompt-file" in orden
    assert "Usuario: ¿Qué hora es?" in vistos[0]["input"]


def test_una_peticion_de_herramienta_se_devuelve_como_llamada(lanzamientos) -> None:
    _, respuesta = lanzamientos
    respuesta["texto"] = '{"name": "hora_fecha", "arguments": {}}'
    r = conversar(ModeloClaudeCode())
    assert r.texto == ""
    assert r.herramientas == [{"name": "hora_fecha", "arguments": {}}]


def test_un_error_de_claude_code_se_avisa(monkeypatch) -> None:
    def falla(orden, **kwargs):
        return subprocess.CompletedProcess(
            orden, 1, stdout=json.dumps({"is_error": True, "subtype": "error"}), stderr=""
        )

    monkeypatch.setattr(subprocess, "run", falla)
    monkeypatch.setattr(ModeloClaudeCode, "_ejecutable", staticmethod(lambda: "claude"))
    with pytest.raises(brain.ErrorDeModelo):
        conversar(ModeloClaudeCode())


@pytest.mark.parametrize(
    "texto",
    [
        'Claro: {"name": "hora_fecha", "arguments": {}}',  # no es la respuesta entera
        '{"name": "borrar_todo", "arguments": {}}',  # no existe
        '{"name": "hora_fecha", "arguments": "x"}',  # argumentos mal
        "{no es json}",
    ],
)
def test_lo_que_no_es_una_llamada_valida_no_se_ejecuta(texto: str) -> None:
    assert llamada_en_respuesta(texto, {"hora_fecha"}) is None


def test_una_llamada_entre_comillas_de_codigo_vale() -> None:
    texto = '```json\n{"name": "hora_fecha", "arguments": {}}\n```'
    assert llamada_en_respuesta(texto, {"hora_fecha"}) == {"name": "hora_fecha", "arguments": {}}
