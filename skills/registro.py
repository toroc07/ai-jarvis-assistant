"""
Registro de habilidades.

Una habilidad es una función que Jarvis puede invocar. Cada una declara qué
acción de la política necesita, y el registro se encarga de que la llamada pase
por el guardián antes de ejecutarse. Una habilidad nunca debe saltarse esto.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable

from core.carencias import Origen, contexto, registro_de_carencias
from security.guard import ErrorDePolitica, Peticion, guardian


@dataclass
class Habilidad:
    nombre: str
    descripcion: str
    accion: str  # Clave correspondiente en policy.yaml
    funcion: Callable[..., Any]
    parametros: dict[str, Any]
    # Cómo sacar de los argumentos el "objetivo" que evaluará el guardián
    # (normalmente la ruta o el comando). Si es None, no hay objetivo concreto.
    campo_objetivo: str | None = None

    def esquema(self) -> dict[str, Any]:
        """La definición de la herramienta tal y como la espera el modelo."""
        return {
            "type": "function",
            "function": {
                "name": self.nombre,
                "description": self.descripcion,
                "parameters": {
                    "type": "object",
                    "properties": self.parametros,
                    "required": [
                        n
                        for n, p in self.parametros.items()
                        if p.get("requerido", True)
                    ],
                },
            },
        }


class Registro:
    def __init__(self) -> None:
        self._habilidades: dict[str, Habilidad] = {}

    def registrar(
        self,
        nombre: str,
        descripcion: str,
        accion: str,
        parametros: dict[str, Any],
        campo_objetivo: str | None = None,
    ) -> Callable:
        """Decorador para declarar una habilidad."""

        def decorador(funcion: Callable) -> Callable:
            self._habilidades[nombre] = Habilidad(
                nombre=nombre,
                descripcion=descripcion,
                accion=accion,
                funcion=funcion,
                parametros=parametros,
                campo_objetivo=campo_objetivo,
            )
            return funcion

        return decorador

    def esquemas(self) -> list[dict[str, Any]]:
        """Todas las herramientas, para pasárselas al modelo."""
        return [h.esquema() for h in self._habilidades.values()]

    def listar(self) -> list[str]:
        return sorted(self._habilidades)

    def invocar(
        self,
        nombre: str,
        argumentos: dict[str, Any],
        pedir_confirmacion: Callable[[Peticion], bool] | None = None,
    ) -> Any:
        """Ejecuta una habilidad pasando por el guardián.

        Devuelve el resultado, o un texto explicando el rechazo. Nunca deja
        escapar el error: el modelo debe poder leer el motivo y reaccionar.
        """
        habilidad = self._habilidades.get(nombre)
        if habilidad is None:
            # El modelo se ha inventado el nombre porque esperaba que existiera,
            # así que ese nombre describe bastante bien lo que falta. Queda
            # anotado para revisarlo y decidir si construir la habilidad.
            peticion_original, sesion = contexto()
            registro_de_carencias.anotar(
                Origen.HERRAMIENTA_INVENTADA,
                que_falta=nombre,
                peticion=peticion_original,
                sesion=sesion,
                detalle=f"El modelo la llamó con: {argumentos}",
            )
            return (
                f"No existe ninguna habilidad llamada '{nombre}'. "
                "Dile al usuario que todavía no sabes hacer eso; ha quedado "
                "anotado para añadirlo más adelante."
            )

        objetivo = ""
        if habilidad.campo_objetivo:
            objetivo = str(argumentos.get(habilidad.campo_objetivo, ""))

        peticion = Peticion(
            accion=habilidad.accion,
            objetivo=objetivo,
            detalles=argumentos,
            motivo=habilidad.descripcion,
        )

        # Se filtran los argumentos que la función realmente acepta, para que
        # un parámetro inventado por el modelo no reviente la llamada.
        firma = inspect.signature(habilidad.funcion)
        validos = {k: v for k, v in argumentos.items() if k in firma.parameters}

        try:
            return guardian.ejecutar(
                peticion,
                lambda: habilidad.funcion(**validos),
                pedir_confirmacion,
            )
        except ErrorDePolitica as e:
            return f"Acción bloqueada por la política de seguridad: {e}"
        except Exception as e:
            return f"La habilidad '{nombre}' falló: {e}"


registro = Registro()
