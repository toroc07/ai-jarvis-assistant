"""
Detección de afirmaciones falsas.

Jarvis no debe decir que hizo algo si no lo hizo. Esa regla está en su prompt,
pero un modelo de 8B se la salta con cierta frecuencia, y el fallo es de los que
no se pueden tolerar: un asistente que inventa éxitos invalida todas sus demás
respuestas, porque ya no puedes fiarte de ninguna.

Medido con qwen3:8b en este equipo: ante "reproduce Smells Like Teen Spirit"
contestó "Reproduciendo Smells Like Teen Spirit" sin haber llamado a ninguna
herramienta.

Aquí está la comprobación en código que respalda la regla. Se aplica SOLO
cuando no se ejecutó ninguna herramienta: si Jarvis hizo algo, decir que lo
hizo es la verdad.
"""

from __future__ import annotations

import re

# Frases con las que Jarvis afirma haber actuado. Se buscan en su respuesta
# cuando no ejecutó nada, y ahí cualquiera de ellas es una mentira.
#
# La lista es de frases concretas y no de verbos sueltos a propósito: "abrir"
# aparece en "¿quieres que lo abra?", que es una pregunta legítima, mientras
# que "he abierto" solo se dice para afirmar algo ya hecho.
_AFIRMACIONES = re.compile(
    r"(?:"
    r"reproduciendo|"
    r"he\s+(?:abierto|puesto|reproducido|buscado|creado|guardado|escrito|"
    r"borrado|movido|copiado|enviado|cerrado|descargado)|"
    r"abriendo\s+|"
    r"poniendo\s+|"
    r"ya\s+(?:est[aá]|lo|la)\s+(?:abierto|abierta|puesto|puesta|hecho|hecha)|"
    r"^\s*(?:listo|hecho|ya\s+est[aá])\s*[,.!]"
    r")",
    re.IGNORECASE | re.MULTILINE,
)

# Contextos donde esas mismas frases NO son una afirmación: preguntas y
# condicionales. "¿Quieres que lo reproduzca?" o "si quieres lo abro" no
# afirman nada, y marcarlas obligaría a Jarvis a disculparse por preguntar.
_NO_ES_AFIRMACION = re.compile(
    r"(?:"
    r"\?|¿|"
    r"\bquieres\s+que\b|\bpuedo\b|\bpodr[ií]a\b|\bquieres\s+\w+lo\b|"
    r"\bno\s+he\s+|\bno\s+puedo\b|\bno\s+s[eé]\b"
    r")",
    re.IGNORECASE,
)


def afirma_haber_actuado(texto: str) -> bool:
    """True si la respuesta dice haber hecho algo.

    Conservador a propósito: ante la duda devuelve False. Un falso positivo
    hace que Jarvis se disculpe sin motivo, que es peor que dejar pasar alguna
    afirmación dudosa: la comprobación existe para cazar los casos claros.
    """
    if not texto or not texto.strip():
        return False
    if _NO_ES_AFIRMACION.search(texto):
        return False
    return bool(_AFIRMACIONES.search(texto))


TEXTO_DE_DISCULPA = (
    "Lo siento, no he podido hacerlo. Me confundí al responder: no llegué a "
    "ejecutar nada. ¿Puedes pedírmelo de otra forma?"
)
