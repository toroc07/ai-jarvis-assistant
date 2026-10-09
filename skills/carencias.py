"""
La herramienta con la que Jarvis admite que algo se le escapa.

Es la tercera vía de detección y la más importante, porque cubre el caso más
común: que ni siquiera intente nada. Si le pides "ponme una alarma a las ocho",
Jarvis no tiene nada parecido, así que no llamará a ninguna herramienta
inventada ni chocará con el guardián. Simplemente te dirá que no puede, y sin
esto esa petición se perdería.

Anotarla es una acción inofensiva —escribe una línea en un archivo propio— así
que la política la permite sin preguntar. Si pidiera confirmación, tendrías que
aprobar un diálogo cada vez que Jarvis no sabe algo, y acabarías apagándolo.
"""

from __future__ import annotations

from core.carencias import Origen, contexto, registro_de_carencias
from skills.registro import registro


@registro.registrar(
    nombre="anotar_carencia",
    descripcion=(
        "Anota una acción que te piden y para la que NO tienes ninguna "
        "herramienta (leer el correo, controlar luces, enviar mensajes...). "
        "Anótala y luego di con naturalidad que aún no sabes hacerlo. No la "
        "uses si alguna herramienta sirve, ni para preguntas que puedes "
        "responder hablando."
    ),
    accion="anotar_carencia",
    parametros={
        "capacidad": {
            "type": "string",
            "description": (
                "Qué capacidad falta, en pocas palabras y en infinitivo. "
                # Los ejemplos deben ser cosas que de verdad no sabe hacer: el
                # modelo los lee como una lista de lo que NO puede hacer. Aquí
                # ponía "poner alarmas y temporizadores", y cuando los hubo se
                # negaba a usarlos e inventaba una herramienta con ese nombre.
                "Por ejemplo: 'leer el correo', 'encender luces inteligentes'."
            ),
        },
        "detalle": {
            "type": "string",
            "description": "Qué pedía exactamente el usuario, si aporta algo.",
            "requerido": False,
        },
    },
    campo_objetivo="capacidad",
)
def anotar_carencia(capacidad: str, detalle: str = "") -> str:
    capacidad = (capacidad or "").strip()
    if not capacidad:
        return "No me has dicho qué capacidad falta."

    peticion, sesion = contexto()
    registro_de_carencias.anotar(
        Origen.DECLARADA_POR_EL_MODELO,
        que_falta=capacidad,
        peticion=peticion,
        sesion=sesion,
        detalle=detalle,
    )
    # El mensaje de vuelta le recuerda al modelo que no debe fingir que lo hizo:
    # anotar la carencia no es cumplir la petición.
    return (
        f"Anotado que falta la capacidad de {capacidad}. "
        "Dile al usuario que todavía NO sabes hacerlo y que queda apuntado "
        "para añadirlo. NO digas que lo has hecho."
    )
