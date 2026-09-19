"""
Humor: chistes y comentarios con retranca.

POR QUÉ UNA COLECCIÓN PROPIA Y NO DEJARLO AL MODELO
El modelo local ya "sabe" contar chistes, pero los que sale son incoherentes.
Uno real de esta misma máquina: «Un hombre entra en un bar y pide un vaso de
whisky. El barman responde: lo siento, no tengo vaso, pero si quieres puedo
prepararte un vaso de whisky». No tiene remate porque un modelo de 8B no
sostiene la estructura de un chiste, que depende de una sola frase final
colocada con precisión.

Con una colección escrita, los chistes tienen gracia. A cambio son finitos, así
que se lleva cuenta de los ya contados para no repetir hasta agotarlos.

El humor de Jarvis es seco y algo irónico, como el de la película: comenta con
retranca mientras hace su trabajo, no hace payasadas. Eso vive en el prompt
(ver TONO_CON_HUMOR), y esta herramienta es solo para cuando le pides un chiste
a propósito.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from skills.registro import registro

RAIZ = Path(__file__).resolve().parent.parent
RUTA_CONTADOS = RAIZ / "data" / "chistes_contados.json"

# Chistes cortos, que es lo que funciona al escucharlos en voz alta: uno largo
# se hace pesado cuando no puedes releerlo.
CHISTES = [
    "¿Qué le dice un bit a otro? Nos vemos en el bus.",
    "Hay diez tipos de personas: las que entienden binario y las que no.",
    "Un programador va al supermercado. Su pareja le dice: compra una barra "
    "de pan y, si hay huevos, trae seis. Volvió con seis barras de pan.",
    "¿Por qué los programadores confunden Halloween con Navidad? Porque "
    "octubre 31 es igual a diciembre 25.",
    "Mi código no tiene errores. Tiene funciones sorpresa.",
    "¿Cuál es el colmo de un electricista? Que su hija se llame Luz y su "
    "mujer lo deje en la calle sin corriente.",
    "Un tipo entra en una biblioteca y pide un libro sobre la paranoia. El "
    "bibliotecario se acerca y le susurra: está justo detrás de ti.",
    "¿Qué hace una abeja en el gimnasio? Zumba.",
    "El optimista dice que el vaso está medio lleno. El pesimista, medio "
    "vacío. El ingeniero dice que el vaso es el doble de grande de lo "
    "necesario.",
    "Se me ha estropeado el teclado y no sé por qué. Puede que fuera el café, "
    "pero prefiero pensar que se fue por motivos personales.",
    "¿Sabes qué le dijo el cero al ocho? Bonito cinturón.",
    "Un hombre le pregunta a su médico: doctor, ¿me va a doler? Y el médico "
    "responde: a usted no, a mí me da bastante igual.",
    "Tengo un chiste sobre la recursividad, pero para entenderlo primero "
    "tienes que entender el chiste sobre la recursividad.",
    "¿Por qué el libro de matemáticas estaba triste? Porque tenía demasiados "
    "problemas.",
    "Estoy leyendo un libro sobre antigravedad. Es imposible dejarlo.",
    "Mi jefe me dijo que trabajara como si fuera el último día de mi vida. "
    "Por eso llevo dos horas despidiéndome de todo el mundo.",
    "¿Qué le dice una impresora a otra? Esa hoja es tuya o es impresión mía.",
    "Un hombre entra en una ferretería y pregunta: ¿tienen tornillos? El "
    "dependiente contesta: sí. Y el hombre: pues enhorabuena, esto va a "
    "funcionar de maravilla.",
]

# Comentarios con retranca para cuando le preguntas por él mismo. No son
# chistes: son el tono de la casa.
PULLAS = [
    "Funcionando dentro de los parámetros normales, que en mi caso es un "
    "listón bastante bajo.",
    "Estupendamente. Llevo todo el día escuchando por si me llamabas, que es "
    "justo tan emocionante como suena.",
    "Bien. Siete gigas de memoria ocupados para decirte la hora, pero bien.",
    "Operativo. Aunque mi idea de una tarde interesante es que me pidas abrir "
    "el bloc de notas.",
    "Todo en orden. Si tuviera pulso, sería regular.",
]


def _cargar_contados() -> set[int]:
    try:
        with open(RUTA_CONTADOS, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except (OSError, json.JSONDecodeError, TypeError):
        return set()


def _guardar_contados(contados: set[int]) -> None:
    try:
        RUTA_CONTADOS.parent.mkdir(parents=True, exist_ok=True)
        with open(RUTA_CONTADOS, "w", encoding="utf-8") as f:
            json.dump(sorted(contados), f)
    except OSError:
        # No poder recordar cuáles contó solo significa que podría repetirse.
        pass


@registro.registrar(
    nombre="contar_chiste",
    descripcion=(
        "Cuenta un chiste. Úsala cuando el usuario pida un chiste, algo "
        "gracioso, que le alegres el día o que le hagas reír. Devuelve el "
        "chiste ya escrito: dilo tal cual, sin cambiarlo ni explicarlo, que "
        "explicar un chiste lo mata."
    ),
    accion="contar_chiste",
    parametros={},
)
def contar_chiste() -> str:
    contados = _cargar_contados()

    pendientes = [i for i in range(len(CHISTES)) if i not in contados]
    if not pendientes:
        # Agotados todos: se empieza otra vuelta en lugar de quedarse mudo.
        contados = set()
        pendientes = list(range(len(CHISTES)))

    elegido = random.choice(pendientes)
    contados.add(elegido)
    _guardar_contados(contados)

    return CHISTES[elegido]


@registro.registrar(
    nombre="comentario_con_retranca",
    descripcion=(
        "Devuelve una respuesta con humor seco a preguntas sobre cómo estás, "
        "qué tal te va o si estás ahí. Úsala solo para ese tipo de preguntas "
        "sobre ti mismo, no para peticiones de verdad."
    ),
    accion="contar_chiste",
    parametros={},
)
def comentario_con_retranca() -> str:
    return random.choice(PULLAS)
