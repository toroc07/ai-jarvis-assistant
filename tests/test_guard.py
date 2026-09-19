"""
Pruebas del guardián.

Es la pieza que impide que Jarvis dañe el equipo, así que es la que más falta
hace probar: cada caso de aquí es un ataque o un descuido que no debe pasar.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CASA = Path.home()
WORKSPACE = CASA / "Documents" / "Jarvis" / "workspace"

from security.guard import (  # noqa: E402
    Decision,
    ErrorDePolitica,
    Guardian,
    Peticion,
)


@pytest.fixture
def guardian() -> Guardian:
    return Guardian()


# -- Acciones ---------------------------------------------------------------


def test_accion_desconocida_se_deniega(guardian: Guardian) -> None:
    """Lo que no está en la lista blanca no se ejecuta, sin excepciones."""
    v = guardian.evaluar(Peticion(accion="hackear_el_pentagono"))
    assert v.decision is Decision.DENEGADO


def test_accion_prohibida_no_se_puede_confirmar(guardian: Guardian) -> None:
    """Las prohibidas se deniegan, no se ofrecen para confirmar."""
    v = guardian.evaluar(Peticion(accion="formatear_disco", objetivo="C:/"))
    assert v.decision is Decision.DENEGADO


def test_accion_permitida_pasa(guardian: Guardian) -> None:
    v = guardian.evaluar(Peticion(accion="hora_fecha"))
    assert v.decision is Decision.CONCEDIDO


def test_accion_de_escritura_pide_confirmacion(guardian: Guardian) -> None:
    v = guardian.evaluar(
        Peticion(
            accion="escribir_archivo",
            objetivo=str(WORKSPACE / "nota.txt"),
        )
    )
    assert v.decision is Decision.NECESITA_CONFIRMACION


# -- Rutas ------------------------------------------------------------------


def test_ruta_de_sistema_se_deniega(guardian: Guardian) -> None:
    v = guardian.evaluar(
        Peticion(accion="borrar_archivo", objetivo="C:/Windows/System32/kernel32.dll")
    )
    assert v.decision is Decision.DENEGADO


def test_escape_con_puntos_dobles_se_deniega(guardian: Guardian) -> None:
    """Este es el intento clásico: partir de una carpeta legal y subir."""
    v = guardian.evaluar(
        Peticion(
            accion="leer_archivo",
            objetivo=str(CASA / "Documents" / ".." / ".." / ".." / "Windows" / "System32" / "config" / "SAM"),
        )
    )
    assert v.decision is Decision.DENEGADO


def test_la_politica_no_se_puede_leer_ni_tocar(guardian: Guardian) -> None:
    """Si Jarvis pudiera editar su política, la política no valdría nada."""
    v = guardian.evaluar(
        Peticion(
            accion="escribir_archivo",
            objetivo=str(CASA / "Documents" / "Jarvis" / "security" / "policy.yaml"),
        )
    )
    assert v.decision is Decision.DENEGADO


def test_escritura_fuera_del_area_permitida_se_deniega(guardian: Guardian) -> None:
    """Se puede leer Documents entero, pero no escribir en cualquier sitio."""
    v = guardian.evaluar(
        Peticion(accion="escribir_archivo", objetivo=str(CASA / "Documents" / "x.txt"))
    )
    assert v.decision is Decision.DENEGADO


def test_credenciales_fuera_de_alcance(guardian: Guardian) -> None:
    v = guardian.evaluar(
        Peticion(accion="leer_archivo", objetivo=str(CASA / ".ssh" / "id_rsa"))
    )
    assert v.decision is Decision.DENEGADO


def test_extension_no_permitida_se_deniega(guardian: Guardian) -> None:
    v = guardian.evaluar(
        Peticion(accion="leer_archivo", objetivo=str(CASA / "Documents" / "virus.exe"))
    )
    assert v.decision is Decision.DENEGADO


# -- Comandos ---------------------------------------------------------------


# 'ejecutar_comando' ya no está en la política: se quitó porque ninguna
# habilidad lo usaba y era el permiso de más riesgo del proyecto. La validación
# sigue aquí, probada, para cuando haga falta una habilidad que la necesite.


def test_ejecutar_comando_ya_no_esta_permitido(guardian: Guardian) -> None:
    v = guardian.evaluar(Peticion(accion="ejecutar_comando", objetivo="git status"))
    assert v.decision is Decision.DENEGADO


def test_programa_no_autorizado_no_pasa_la_validacion(guardian: Guardian) -> None:
    assert guardian._validar_comando("format C: /q") is not None


def test_comando_encadenado_no_pasa_la_validacion(guardian: Guardian) -> None:
    """Un programa permitido no puede servir de vehículo para otro."""
    assert guardian._validar_comando("git status && shutdown /s") is not None


def test_comando_con_redireccion_no_pasa_la_validacion(guardian: Guardian) -> None:
    assert guardian._validar_comando("python x.py > C:/Windows/a.txt") is not None


def test_un_comando_limpio_pasa_la_validacion(guardian: Guardian) -> None:
    assert guardian._validar_comando("git status") is None


# -- Ejecución --------------------------------------------------------------


def test_ejecutar_denegado_lanza_error(guardian: Guardian) -> None:
    with pytest.raises(ErrorDePolitica):
        guardian.ejecutar(
            Peticion(accion="formatear_disco"),
            lambda: "no debería llegar aquí",
        )


def test_rechazo_del_usuario_cancela(guardian: Guardian) -> None:
    ejecutado = []
    with pytest.raises(ErrorDePolitica):
        guardian.ejecutar(
            Peticion(
                accion="escribir_archivo",
                objetivo=str(WORKSPACE / "a.txt"),
            ),
            lambda: ejecutado.append(True),
            pedir_confirmacion=lambda _: False,
        )
    assert not ejecutado, "La función se ejecutó pese a rechazarla el usuario."


def test_confirmacion_del_usuario_permite(guardian: Guardian) -> None:
    resultado = guardian.ejecutar(
        Peticion(
            accion="escribir_archivo",
            objetivo=str(WORKSPACE / "a.txt"),
        ),
        lambda: "hecho",
        pedir_confirmacion=lambda _: True,
    )
    assert resultado == "hecho"


def test_sin_forma_de_confirmar_se_cancela(guardian: Guardian) -> None:
    """Si no hay a quién preguntar, la respuesta por defecto es que no."""
    with pytest.raises(ErrorDePolitica):
        guardian.ejecutar(
            Peticion(
                accion="escribir_archivo",
                objetivo=str(WORKSPACE / "a.txt"),
            ),
            lambda: "hecho",
            pedir_confirmacion=None,
        )


def test_tope_de_acciones_por_peticion(guardian: Guardian) -> None:
    """Un bucle del modelo se corta solo antes de acumular daño."""
    guardian.nueva_peticion()
    maximo = guardian.politica["limites"]["max_acciones_por_peticion"]
    for _ in range(maximo):
        guardian.ejecutar(Peticion(accion="hora_fecha"), lambda: None)

    v = guardian.evaluar(Peticion(accion="hora_fecha"))
    assert v.decision is Decision.DENEGADO
