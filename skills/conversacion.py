"""
Cerrar la conversación.

Jarvis tiene que entender cuándo has terminado con él, y la gente no termina
diciendo "salir": dice "gracias, ya me encargo yo" o "nada más". Se resuelve por
dos caminos que se complementan:

  - Una lista de frases habituales, que acierta al instante y sin gastar
    modelo. Cubre lo que se dice el 90% de las veces.
  - Una herramienta que el propio modelo invoca cuando entiende que te
    despides con palabras que no están en esa lista. Es lo que cubre el resto.

Hacen falta las dos. Solo con la lista, "bueno, te dejo que tengo prisa" no se
reconocería. Solo con el modelo, un fallo suyo te dejaría con el orbe en
pantalla sin saber cómo echarlo.
"""

from __future__ import annotations

import re

from skills.registro import registro

# Frases con las que la gente cierra de verdad una conversación en español.
# Se comparan sobre el texto sin signos ni tildes, así que van sin ellos.
_PATRONES_DE_DESPEDIDA = [
    r"\b(gracias|grac[ií]as)\b.*\b(ya (me )?(encargo|ocupo)|nada m[aá]s|es todo|eso es todo)\b",
    r"\bya (me )?(encargo|ocupo)\b",
    r"\b(nada|algo) m[aá]s\b.*\bgracias\b",
    r"^\s*(gracias|muchas gracias|vale gracias|ok gracias)\s*$",
    r"\b(eso es todo|es todo por ahora|no necesito nada m[aá]s)\b",
    r"\b(ad[ií]os|hasta luego|hasta pronto|nos vemos|chao|chau)\b",
    r"\b(d[eé]jalo|olv[ií]dalo|da igual|no importa)\b.*\b(gracias)?\b$",
    r"\b(cierra|ci[eé]rrate|desaparece|vete|l[aá]rgate|silencio|c[aá]llate)\b",
    r"\b(ya est[aá]|listo|perfecto)\b.*\b(gracias)\b",
    r"\bte dejo\b",
    r"\bhasta (ma[ñn]ana|la pr[oó]xima)\b",
]

_COMPILADOS = [re.compile(p, re.IGNORECASE) for p in _PATRONES_DE_DESPEDIDA]

# Marca que el agente reconoce para saber que hay que cerrar el orbe.
MARCA_DE_CIERRE = "[FIN_CONVERSACION]"

# Marca para apagar Jarvis del todo. Es distinto de cerrar: cerrar deja la
# sesión en espera en segundo plano, apagar termina el proceso y hay que volver
# a ejecutarlo a mano.
MARCA_DE_APAGADO = "[APAGAR_JARVIS]"

# Frases para apagarlo del todo. Son más explícitas que las de cerrar porque la
# consecuencia es mayor: tras esto hay que arrancarlo a mano.
_PATRONES_DE_APAGADO = [
    r"\b(jarvis[, ]+)?ap[aá]gate\b",
    r"\bap[aá]ga(te|lo)\b.*\b(del todo|completamente|ya)\b",
    r"\b(apaga|cierra) (el )?(programa|jarvis|la aplicaci[oó]n)\b",
    r"\bs[aá]l(te)? del programa\b",
    r"\b(termina|finaliza) (el )?(programa|proceso)\b",
    r"\bdesconectate\b",
    r"\bhasta ma[ñn]ana jarvis\b.*\bap[aá]gate\b",
]

_COMPILADOS_APAGADO = [re.compile(p, re.IGNORECASE) for p in _PATRONES_DE_APAGADO]


def _normalizar(texto: str) -> str:
    """Quita tildes y signos para que las frases coincidan igual sin ellos."""
    reemplazos = str.maketrans("áéíóúÁÉÍÓÚüÜ", "aeiouAEIOUuU")
    limpio = texto.translate(reemplazos).lower()
    return re.sub(r"[¿?¡!.,;:]", " ", limpio).strip()


# Arranques de pregunta. Quien pregunta no se está despidiendo, por mucho que
# la frase contenga una palabra de despedida: "cómo se dice adiós en japonés"
# es una petición, no un adiós.
_ES_PREGUNTA = re.compile(
    r"^\s*(que|qué|como|cómo|cual|cuál|cuando|cuándo|donde|dónde|quien|quién|"
    r"cuanto|cuánto|cuanta|cuánta|por que|por qué|para que|para qué|"
    r"puedes|podrias|podrías|sabes|dime|dame|busca|abre|cierra el|pon|"
    r"escribe|crea|borra|mueve|copia|explica|traduce|calcula)\b"
)


def es_despedida(texto: str) -> bool:
    """True si la frase suena a que el usuario da por terminada la charla.

    Deliberadamente conservador: es preferible que alguna despedida rara se
    escape (y tengas que decirlo de otra forma) a cerrar el orbe en mitad de
    una petición porque la frase contenía la palabra "gracias".
    """
    if not texto or not texto.strip():
        return False

    normalizado = _normalizar(texto)

    # Una frase larga rara vez es solo una despedida: suele ser una petición
    # que casualmente incluye una fórmula de cortesía.
    if len(normalizado.split()) > 12:
        return False

    # Una pregunta o una orden nunca cierran la conversación, aunque contengan
    # una palabra de despedida como parte de lo que se pregunta.
    if _ES_PREGUNTA.match(normalizado):
        return False

    return any(p.search(normalizado) for p in _COMPILADOS)


def es_apagado(texto: str) -> bool:
    """True si el usuario pide apagar Jarvis del todo, no solo cerrar el orbe.

    Se comprueba ANTES que es_despedida, porque "apágate" también encaja con
    algunos patrones de despedida y la diferencia importa: cerrar deja a Jarvis
    escuchando en segundo plano, apagar termina el proceso.
    """
    if not texto or not texto.strip():
        return False

    normalizado = _normalizar(texto)
    if len(normalizado.split()) > 10:
        return False

    return any(p.search(normalizado) for p in _COMPILADOS_APAGADO)


@registro.registrar(
    nombre="apagar_jarvis",
    descripcion=(
        "Apaga Jarvis por completo: cierra el programa y deja de escuchar. "
        "Para volver a usarlo hay que ejecutarlo a mano. Úsala SOLO cuando el "
        "usuario pida apagarlo explícitamente, con frases como 'Jarvis "
        "apágate' o 'cierra el programa'. NO la uses para despedidas normales "
        "como 'gracias, ya me encargo yo': para eso está terminar_conversacion."
    ),
    accion="apagar_jarvis",
    parametros={
        "despedida": {
            "type": "string",
            "description": "Frase breve de despedida antes de apagarse.",
            "requerido": False,
        }
    },
)
def apagar_jarvis(despedida: str = "") -> str:
    texto = despedida.strip() or "Apagando. Hasta la próxima."
    return f"{MARCA_DE_APAGADO} {texto}"


@registro.registrar(
    nombre="terminar_conversacion",
    descripcion=(
        "Cierra la conversación por voz y deja a Jarvis en segundo plano. "
        "Úsala cuando el usuario dé a entender que ya no necesita nada más, "
        "por ejemplo 'gracias, ya me encargo yo', 'nada más por ahora' o "
        "'te dejo'. No la uses si sigue pidiendo cosas."
    ),
    accion="terminar_conversacion",
    parametros={
        "despedida": {
            "type": "string",
            "description": "Frase breve de despedida para decirle al usuario.",
            "requerido": False,
        }
    },
)
def terminar_conversacion(despedida: str = "") -> str:
    # El agente detecta esta marca en el resultado y cierra el orbe. La
    # habilidad no toca la interfaz directamente: solo informa.
    texto = despedida.strip() or "Hasta luego."
    return f"{MARCA_DE_CIERRE} {texto}"
