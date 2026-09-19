"""
Limpieza del texto que escribe el modelo.

Un modelo de chat escribe para pantalla: mete emojis, negritas con asteriscos,
viñetas y enlaces. Todo eso, dicho por un sintetizador, suena mal o directamente
se lee: Piper pronuncia los asteriscos, y los emojis se convierten en pausas
raras o en su nombre completo ("cara sonriente con ojos de risa").

Pedirlo en el prompt ayuda pero no basta: el modelo vuelve a meterlos en cuanto
se despista. Esto lo limpia antes de hablar, así que da igual lo que escriba.
"""

from __future__ import annotations

import re

# Rangos de emoji y símbolos decorativos. Se quitan enteros porque ninguno
# aporta nada dicho en voz alta.
_EMOJIS = re.compile(
    "["
    "\U0001f300-\U0001faff"  # pictogramas, emoticonos, objetos, símbolos
    "\U00002600-\U000027bf"  # símbolos varios y dingbats
    "\U0001f1e6-\U0001f1ff"  # banderas
    "\U00002190-\U000021ff"  # flechas
    "\U0000fe00-\U0000fe0f"  # selectores de variación
    "\U0001f000-\U0001f0ff"  # fichas de mahjong y cartas
    "\U00002b00-\U00002bff"  # símbolos y pictogramas varios
    "\U00002190-\U000021ff"
    "\U0000200d"  # unión de emojis
    "]+",
    flags=re.UNICODE,
)

# Marcas de Markdown. Se quita el símbolo y se deja el texto que envolvía.
_NEGRITA = re.compile(r"\*{1,3}([^*]+)\*{1,3}")
_SUBRAYADO = re.compile(r"_{1,3}([^_]+)_{1,3}")
_CODIGO = re.compile(r"`{1,3}([^`]+)`{1,3}")
_ENLACE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_TITULO = re.compile(r"^#{1,6}\s*", flags=re.MULTILINE)
_VINETA = re.compile(r"^\s*[-*+•]\s+", flags=re.MULTILINE)

# Espacios y saltos sobrantes que quedan tras limpiar.
_ESPACIOS = re.compile(r"[ \t]{2,}")
_SALTOS = re.compile(r"\n{3,}")


def limpiar_para_hablar(texto: str) -> str:
    """Deja el texto listo para el sintetizador de voz."""
    if not texto:
        return ""

    limpio = _ENLACE.sub(r"\1", texto)  # El texto del enlace, no la dirección.
    limpio = _CODIGO.sub(r"\1", limpio)
    limpio = _NEGRITA.sub(r"\1", limpio)
    limpio = _SUBRAYADO.sub(r"\1", limpio)
    limpio = _TITULO.sub("", limpio)
    limpio = _VINETA.sub("", limpio)
    limpio = _EMOJIS.sub("", limpio)

    limpio = _ESPACIOS.sub(" ", limpio)
    limpio = _SALTOS.sub("\n\n", limpio)

    # Un texto que era solo emojis se queda vacío tras limpiarlo, y hacer hablar
    # al sintetizador con una cadena vacía no tiene sentido.
    return limpio.strip()
