"""
El prompt fijo debe caber holgado en el contexto del modelo local.

Si no cabe, Ollama recorta el principio sin avisar: el modelo pierde parte de
sus instrucciones y las primeras herramientas de la lista, y deja de usarlas.
Pasó de verdad (4600 tokens en un contexto de 4096) y solo se vio porque una
herramienta que funcionaba dejó de usarse al añadir otras.

La cuenta es una estimación por caracteres (medido con qwen3: ~3,9 caracteres
por token en este prompt; se usa 3,5 para ir sobrados). Debe quedar sitio para
el historial y los resultados de las herramientas, así que el prompt fijo no
puede pasar del 60 % del contexto.
"""

from __future__ import annotations

import json

from core.agent import Agente
from core.brain import CONTEXTO
from core.memory import Memoria
from skills.registro import registro

CARACTERES_POR_TOKEN = 3.5
MAXIMO_DEL_CONTEXTO = 0.6


def test_el_prompt_fijo_cabe_en_el_contexto_con_margen(tmp_path) -> None:
    agente = Agente(memoria=Memoria(tmp_path / "db"))
    caracteres = len(agente._prompt_sistema()) + len(
        json.dumps(registro.esquemas(), ensure_ascii=False)
    )
    tokens_estimados = caracteres / CARACTERES_POR_TOKEN
    assert tokens_estimados < CONTEXTO * MAXIMO_DEL_CONTEXTO, (
        f"El prompt fijo ronda los {tokens_estimados:.0f} tokens y el contexto es "
        f"de {CONTEXTO}. Acorta descripciones de herramientas o sube JARVIS_CONTEXTO."
    )
