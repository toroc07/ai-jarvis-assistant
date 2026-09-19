"""
La política no debe conceder permisos que nadie usa.

Un permiso abierto "por si acaso" es superficie de ataque gratis, y en una
lista blanca contradice el propósito de la lista. Esta prueba es la que impide
que vuelva a acumularse: si alguien añade una acción a policy.yaml sin la
habilidad correspondiente, o borra una habilidad y deja su permiso, falla.

Llegó a haber seis a la vez, uno de ellos 'ejecutar_comando', que era el de más
riesgo de todo el proyecto.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core.agent  # noqa: E402,F401  (carga todas las habilidades)
from security.guard import guardian  # noqa: E402
from skills.registro import registro  # noqa: E402


def _acciones_de_la_politica() -> dict[str, str]:
    return guardian.politica["acciones"]


def _acciones_usadas() -> set[str]:
    return {h.accion for h in registro._habilidades.values()}


def test_ninguna_accion_permitida_se_queda_sin_habilidad() -> None:
    """Todo permiso concedido debe tener algo que lo use."""
    acciones = _acciones_de_la_politica()
    concedidas = {a for a, nivel in acciones.items() if nivel != "prohibir"}

    huerfanas = sorted(concedidas - _acciones_usadas())
    assert not huerfanas, (
        "Estas acciones están permitidas en policy.yaml pero ninguna habilidad "
        f"las usa: {huerfanas}. Impleméntalas o quítalas de la política."
    )


def test_ninguna_habilidad_usa_una_accion_inexistente() -> None:
    """El fallo contrario: una habilidad que nunca podría ejecutarse."""
    acciones = _acciones_de_la_politica()
    inventadas = sorted(_acciones_usadas() - set(acciones))
    assert not inventadas, (
        f"Estas habilidades piden acciones que no están en policy.yaml: "
        f"{inventadas}. Añádelas o corrige el nombre."
    )


def test_ninguna_habilidad_usa_una_accion_prohibida() -> None:
    """Una habilidad sobre una acción prohibida no se ejecutaría jamás."""
    acciones = _acciones_de_la_politica()
    prohibidas = {a for a, nivel in acciones.items() if nivel == "prohibir"}
    conflictos = sorted(_acciones_usadas() & prohibidas)
    assert not conflictos, (
        f"Estas habilidades piden acciones prohibidas: {conflictos}."
    )


class TestLoQueSeQuitoSigueFuera:
    """Las tres que se eliminaron no deben volver sin una habilidad detrás."""

    def test_ejecutar_comando_no_esta_concedido(self) -> None:
        """Era el permiso de más riesgo y no lo usaba nadie."""
        assert "ejecutar_comando" not in _acciones_de_la_politica()

    def test_domotica_no_esta_concedida(self) -> None:
        assert "domotica" not in _acciones_de_la_politica()

    def test_crear_archivo_no_esta_concedido(self) -> None:
        """Redundante: escribir_archivo ya crea."""
        assert "crear_archivo" not in _acciones_de_la_politica()


class TestLaValidacionDeComandosSigueLista:
    """Quitar el permiso no debe tirar la lógica, que está probada y sirve.

    Si algún día hace falta una habilidad que ejecute comandos, la validación
    tiene que seguir ahí: es la parte difícil y la que más cuesta rehacer bien.
    """

    def test_se_rechazan_los_programas_no_autorizados(self) -> None:
        assert guardian._validar_comando("format C: /q") is not None

    def test_se_rechaza_encadenar_ordenes(self) -> None:
        assert guardian._validar_comando("git status && shutdown /s") is not None

    def test_se_rechaza_la_redireccion(self) -> None:
        assert guardian._validar_comando("python x.py > C:/Windows/a.txt") is not None

    def test_un_comando_limpio_pasa_la_validacion(self) -> None:
        assert guardian._validar_comando("git status") is None
