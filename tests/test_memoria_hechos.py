"""
Pruebas de lo que Jarvis recuerda de ti.

Antes de esto la tabla de hechos existía y se inyectaba en el prompt, pero
nada escribía en ella: cada sesión empezaba en blanco. Lo que más importa aquí
es que no se dupliquen datos escritos de formas distintas y que el número de
hechos no crezca sin freno, porque cada uno viaja en el prompt de TODAS las
peticiones siguientes.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.memory import Memoria  # noqa: E402
from skills import memoria as habilidad  # noqa: E402


@pytest.fixture(autouse=True)
def memoria_aislada(tmp_path, monkeypatch):
    """Cada prueba con su propia base de datos, no la tuya."""
    propia = Memoria(tmp_path / "prueba.db")
    monkeypatch.setattr(habilidad, "_memoria", propia)
    monkeypatch.setattr(habilidad, "_al_cambiar", None)
    return propia


class TestNormalizacionDeClaves:
    @pytest.mark.parametrize(
        "entrada,esperado",
        [
            ("Nombre", "nombre"),
            ("  NOMBRE  ", "nombre"),
            ("profesión", "profesion"),
            ("Mi Hermano", "mi_hermano"),
            ("día de cumpleaños", "dia_de_cumpleanos"),
            ("longitud-respuestas", "longitud_respuestas"),
        ],
    )
    def test_se_normaliza(self, entrada: str, esperado: str) -> None:
        assert habilidad.normalizar_clave(entrada) == esperado

    def test_variantes_de_lo_mismo_no_duplican(self, memoria_aislada) -> None:
        """«Nombre» y «nombre» son el mismo dato, no dos."""
        habilidad.recordar_dato("Nombre", "Ana")
        habilidad.recordar_dato("  nombre ", "Ana Andrés")
        assert len(memoria_aislada.hechos()) == 1
        assert memoria_aislada.hechos()["nombre"] == "Ana Andrés"


class TestGuardar:
    def test_se_guarda_y_se_lee(self, memoria_aislada) -> None:
        habilidad.recordar_dato("nombre", "Ana", "personal")
        assert memoria_aislada.hechos()["nombre"] == "Ana"

    def test_actualizar_avisa_del_valor_anterior(self, memoria_aislada) -> None:
        habilidad.recordar_dato("ciudad", "Madrid")
        resultado = habilidad.recordar_dato("ciudad", "Valencia")
        assert "Madrid" in resultado and "Valencia" in resultado

    def test_sin_valor_no_guarda_ni_miente(self, memoria_aislada) -> None:
        resultado = habilidad.recordar_dato("nombre", "")
        assert "NO digas que lo has guardado" in resultado
        assert memoria_aislada.hechos() == {}

    def test_una_categoria_invalida_no_rompe(self, memoria_aislada) -> None:
        habilidad.recordar_dato("x", "y", "categoria_inventada")
        assert memoria_aislada.hechos()["x"] == "y"

    def test_los_valores_largos_se_recortan(self, memoria_aislada) -> None:
        habilidad.recordar_dato("nota", "a" * 1000)
        assert len(memoria_aislada.hechos()["nota"]) <= habilidad.MAX_VALOR


class TestTope:
    def test_no_crece_sin_freno(self, memoria_aislada) -> None:
        """Cada hecho viaja en el prompt de todas las peticiones siguientes."""
        for i in range(habilidad.MAX_HECHOS):
            habilidad.recordar_dato(f"dato_{i}", f"valor {i}")

        resultado = habilidad.recordar_dato("uno_mas", "no cabe")
        assert "máximo" in resultado
        assert "NO digas que lo has guardado" in resultado
        assert len(memoria_aislada.hechos()) == habilidad.MAX_HECHOS

    def test_estando_lleno_aun_se_pueden_actualizar(self, memoria_aislada) -> None:
        """Llenar el cupo no debe impedir corregir un dato equivocado."""
        for i in range(habilidad.MAX_HECHOS):
            habilidad.recordar_dato(f"dato_{i}", f"valor {i}")

        habilidad.recordar_dato("dato_0", "valor corregido")
        assert memoria_aislada.hechos()["dato_0"] == "valor corregido"


class TestOlvidar:
    def test_se_olvida(self, memoria_aislada) -> None:
        habilidad.recordar_dato("nombre", "Ana")
        assert "Olvidado" in habilidad.olvidar_dato("nombre")
        assert memoria_aislada.hechos() == {}

    def test_olvidar_algo_que_no_existe_no_miente(self, memoria_aislada) -> None:
        resultado = habilidad.olvidar_dato("no_existe")
        assert "NO digas que lo has borrado" in resultado

    def test_se_olvida_aunque_escribas_la_clave_distinta(self, memoria_aislada) -> None:
        habilidad.recordar_dato("profesión", "ingeniero")
        assert "Olvidado" in habilidad.olvidar_dato("Profesion")


class TestConsultar:
    def test_sin_datos_lo_dice(self, memoria_aislada) -> None:
        assert "No recuerdo nada" in habilidad.consultar_datos()

    def test_los_enumera(self, memoria_aislada) -> None:
        habilidad.recordar_dato("nombre", "Ana")
        habilidad.recordar_dato("ciudad", "Madrid")
        resultado = habilidad.consultar_datos()
        assert "Ana" in resultado and "Madrid" in resultado


class TestLlegaAlPrompt:
    def test_los_hechos_entran_en_el_prompt(self, memoria_aislada) -> None:
        """De nada sirve guardar si el modelo no lo ve."""
        habilidad.recordar_dato("nombre", "Ana")
        assert "Ana" in memoria_aislada.resumen_para_prompt()

    def test_sin_hechos_el_prompt_no_lleva_seccion_vacia(self, memoria_aislada) -> None:
        assert memoria_aislada.resumen_para_prompt() == ""


class TestAvisoDeCambio:
    def test_guardar_avisa(self, memoria_aislada, monkeypatch) -> None:
        """El aviso es lo que dispara el recalentado del modelo."""
        avisos = []
        monkeypatch.setattr(habilidad, "_al_cambiar", lambda: avisos.append(1))

        habilidad.recordar_dato("nombre", "Ana")
        habilidad.olvidar_dato("nombre")
        assert len(avisos) == 2

    def test_un_aviso_que_falla_no_rompe_el_guardado(
        self, memoria_aislada, monkeypatch
    ) -> None:
        def revienta() -> None:
            raise RuntimeError("fallo del recalentado")

        monkeypatch.setattr(habilidad, "_al_cambiar", revienta)
        habilidad.recordar_dato("nombre", "Ana")
        assert memoria_aislada.hechos()["nombre"] == "Ana"
