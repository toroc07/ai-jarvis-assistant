"""
Registro de habilidades.

Una habilidad es una función que Jarvis puede invocar. Cada una declara qué
acción de la política necesita, y el registro se encarga de que la llamada pase
por el guardián antes de ejecutarse. Una habilidad nunca debe saltarse esto.
"""

from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass
from typing import Any, Callable

from core.carencias import Origen, contexto, registro_de_carencias
from security.guard import ErrorDePolitica, Peticion, guardian

log = logging.getLogger("jarvis.habilidades")

_VERDADEROS = {"true", "1", "si", "sí", "yes"}
_FALSOS = {"false", "0", "no"}


def _convertir_tipos(argumentos: dict[str, Any], esquema: dict[str, Any]) -> dict[str, Any]:
    """Ajusta los argumentos al tipo que declara la habilidad.

    Los modelos pequeños mandan a menudo números como texto ("5" en vez de 5),
    y la habilidad fallaba con un TypeError incomprensible para el modelo.
    """
    convertidos = dict(argumentos)
    for clave, valor in argumentos.items():
        tipo = esquema.get(clave, {}).get("type")
        if valor is None or not isinstance(valor, str):
            continue
        texto = valor.strip()
        if tipo == "integer":
            try:
                convertidos[clave] = int(float(texto))
            except ValueError:
                raise ValueError(f"'{clave}' debe ser un número entero, no '{valor}'.")
        elif tipo == "number":
            try:
                convertidos[clave] = float(texto.replace(",", "."))
            except ValueError:
                raise ValueError(f"'{clave}' debe ser un número, no '{valor}'.")
        elif tipo == "boolean":
            if texto.lower() in _VERDADEROS:
                convertidos[clave] = True
            elif texto.lower() in _FALSOS:
                convertidos[clave] = False
            else:
                raise ValueError(f"'{clave}' debe ser verdadero o falso, no '{valor}'.")
    return convertidos


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
                    # 'requerido' es una marca interna (ya se traduce a la lista
                    # 'required'); enviarla al modelo solo gastaba contexto.
                    "properties": {
                        nombre: {k: v for k, v in p.items() if k != "requerido"}
                        for nombre, p in self.parametros.items()
                    },
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
            # Dos habilidades con el mismo nombre se pisarían en silencio y el
            # modelo llamaría a la que no espera. Volver a importar el mismo
            # módulo sí se tolera: es la misma función.
            previa = self._habilidades.get(nombre)
            if previa and (previa.funcion.__module__, previa.funcion.__qualname__) != (
                funcion.__module__, funcion.__qualname__
            ):
                raise ValueError(
                    f"Ya hay una habilidad llamada '{nombre}' "
                    f"({previa.funcion.__module__}.{previa.funcion.__qualname__})."
                )
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
        ignorados = set(argumentos) - set(validos)
        if ignorados:
            log.debug("'%s' ignora argumentos desconocidos: %s", nombre, sorted(ignorados))

        # Si el modelo usó otro nombre ('tiempo' en vez de 'minutos'), el
        # argumento se filtró arriba y la función fallaría con un error de
        # Python. Mejor decirle qué nombres espera para que se corrija solo.
        faltan = [
            p.name
            for p in firma.parameters.values()
            if p.default is inspect.Parameter.empty and p.name not in validos
        ]
        if faltan:
            esperados = ", ".join(habilidad.parametros) or "ninguno"
            return (
                f"Faltan argumentos para '{nombre}': {', '.join(faltan)}. Vuelve a "
                f"llamarla usando exactamente estos nombres: {esperados}."
            )

        try:
            validos = _convertir_tipos(validos, habilidad.parametros)
        except ValueError as e:
            return f"Argumentos no válidos para '{nombre}': {e}"

        def llamar() -> Any:
            # La habilidad usa la ruta que el guardián validó, ya resuelta, y
            # no la cadena que escribió el modelo: así no hay ventana entre
            # comprobar un enlace y abrirlo.
            if peticion.ruta_resuelta and habilidad.campo_objetivo in validos:
                validos[habilidad.campo_objetivo] = peticion.ruta_resuelta
            return habilidad.funcion(**validos)

        try:
            return guardian.ejecutar(peticion, llamar, pedir_confirmacion)
        except ErrorDePolitica as e:
            return f"Acción bloqueada por la política de seguridad: {e}"
        except Exception as e:
            return f"La habilidad '{nombre}' falló: {e}"


registro = Registro()
