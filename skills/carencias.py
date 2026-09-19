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
        "Anota que el usuario ha pedido algo que no sabes hacer, para que se "
        "estudie añadirlo más adelante. Úsala SIEMPRE que te pidan una acción "
        "para la que no tienes herramienta: poner alarmas, leer el correo, "
        "controlar luces, enviar mensajes, o cualquier otra cosa que no esté "
        "en tu lista. Anótala primero y después dile al usuario con naturalidad "
        "que todavía no sabes hacerlo pero que ha quedado apuntado. No la uses "
        "si sí tienes una herramienta para ello, ni para preguntas de "
        "conocimiento que puedes responder hablando."
    ),
    accion="anotar_carencia",
    parametros={
        "capacidad": {
            "type": "string",
            "description": (
                "Qué capacidad falta, en pocas palabras y en infinitivo. "
                "Por ejemplo: 'poner alarmas y temporizadores', 'leer el "
                "correo', 'encender luces inteligentes'."
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
