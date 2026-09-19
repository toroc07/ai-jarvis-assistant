"""
Pruebas del interruptor de emergencia.

Es la última línea de defensa: si falla, todas las demás dan igual. Lo que más
importa aquí es que nada pueda ejecutarse estando activado, y que Jarvis no
pueda desactivárselo a sí mismo.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from security.guard import Decision, ErrorDePolitica, Guardian, Peticion  # noqa: E402
from security.parada import InterruptorDeEmergencia, interruptor  # noqa: E402


@pytest.fixture
def limpio(tmp_path):
    """Interruptor aislado, y el global desactivado al terminar."""
    propio = InterruptorDeEmergencia(tmp_path / "parada.json")
    yield propio
    interruptor.rearmar()


@pytest.fixture
def guardian(limpio):
    return Guardian()


class TestEstadoBasico:
    def test_arranca_desactivado(self, limpio: InterruptorDeEmergencia) -> None:
        assert not limpio.activado

    def test_activar_lo_detiene(self, limpio: InterruptorDeEmergencia) -> None:
        limpio.activar("prueba")
        assert limpio.activado
        assert limpio.parada is not None
        assert limpio.parada.motivo == "prueba"

    def test_rearmar_lo_suelta(self, limpio: InterruptorDeEmergencia) -> None:
        limpio.activar("prueba")
        limpio.rearmar()
        assert not limpio.activado

    def test_la_parada_sobrevive_al_reinicio(self, tmp_path) -> None:
        """Un interruptor que se olvida al reiniciar no sirve de nada."""
        ruta = tmp_path / "parada.json"
        primero = InterruptorDeEmergencia(ruta)
        primero.activar("algo grave pasó")

        segundo = InterruptorDeEmergencia(ruta)
        assert segundo.activado
        assert segundo.parada is not None
        assert "algo grave" in segundo.parada.motivo

    def test_un_archivo_corrupto_se_trata_como_parada(self, tmp_path) -> None:
        """Ante la duda, lo seguro es no actuar."""
        ruta = tmp_path / "parada.json"
        ruta.write_text("esto no es json", encoding="utf-8")
        assert InterruptorDeEmergencia(ruta).activado


class TestBloqueoDeAcciones:
    def test_estando_detenido_no_pasa_nada(self, guardian: Guardian) -> None:
        """Ni siquiera las acciones más inofensivas."""
        interruptor.activar("prueba")
        v = guardian.evaluar(Peticion(accion="hora_fecha"))
        assert v.decision is Decision.DENEGADO
        assert "interruptor de emergencia" in v.razon.lower()

    def test_detenido_bloquea_incluso_lo_permitido(self, guardian: Guardian) -> None:
        interruptor.activar("prueba")
        for accion in ("leer_archivo", "abrir_url", "control_volumen"):
            v = guardian.evaluar(Peticion(accion=accion, objetivo="x"))
            assert v.decision is Decision.DENEGADO

    def test_ejecutar_estando_detenido_lanza_error(self, guardian: Guardian) -> None:
        interruptor.activar("prueba")
        ejecutado = []
        with pytest.raises(ErrorDePolitica):
            guardian.ejecutar(
                Peticion(accion="hora_fecha"), lambda: ejecutado.append(True)
            )
        assert not ejecutado

    def test_al_rearmar_vuelve_a_funcionar(self, guardian: Guardian) -> None:
        interruptor.activar("prueba")
        interruptor.rearmar()
        assert guardian.evaluar(Peticion(accion="hora_fecha")).decision is (
            Decision.CONCEDIDO
        )


class TestParadaAutomatica:
    def test_insistir_en_lo_prohibido_lo_detiene(self, guardian: Guardian) -> None:
        """Un intento es un error del modelo; varios seguidos son otra cosa."""
        assert not interruptor.activado

        guardian.evaluar(Peticion(accion="formatear_disco", objetivo="C:/"))
        guardian.evaluar(Peticion(accion="modificar_registro", objetivo="HKLM"))

        assert interruptor.activado
        assert interruptor.parada is not None
        assert interruptor.parada.quien == "automatica"

    def test_un_solo_intento_no_lo_detiene(self, guardian: Guardian) -> None:
        guardian.evaluar(Peticion(accion="formatear_disco", objetivo="C:/"))
        assert not interruptor.activado

    def test_las_acciones_normales_no_cuentan(self, guardian: Guardian) -> None:
        """Que te denieguen una ruta no es intentar saltarse los límites."""
        for _ in range(6):
            guardian.evaluar(
                Peticion(accion="leer_archivo", objetivo="C:/Windows/algo.txt")
            )
        assert not interruptor.activado


class TestJarvisNoPuedeDesactivarlo:
    def test_no_existe_una_herramienta_para_rearmar(self) -> None:
        """Si el modelo pudiera rearmarlo, el interruptor sería una sugerencia."""
        from skills.registro import registro

        nombres = " ".join(registro.listar()).lower()
        for palabra in ("rearmar", "reactivar", "quitar_parada", "desbloquear"):
            assert palabra not in nombres

    def test_rearmar_no_esta_en_la_politica(self) -> None:
        from security.guard import guardian as global_guardian

        acciones = global_guardian.politica["acciones"]
        for clave in acciones:
            assert "rearmar" not in clave
            assert "reactivar" not in clave
