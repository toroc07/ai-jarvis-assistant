"""
Pruebas del registro de lo que Jarvis no sabe hacer.

Lo que más importa aquí es que las tres vías de detección funcionen y que el
archivo aguante: una carencia perdida es una habilidad que nunca se construye,
y una excepción al anotarla rompería la conversación por intentar apuntar algo
para más tarde.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.carencias import (  # noqa: E402
    Origen,
    RegistroDeCarencias,
    fijar_contexto,
    registro_de_carencias,
)


@pytest.fixture
def reg(tmp_path) -> RegistroDeCarencias:
    return RegistroDeCarencias(tmp_path / "carencias.jsonl")


@pytest.fixture
def aislado(tmp_path, monkeypatch):
    """Redirige el registro global a un archivo temporal."""
    monkeypatch.setattr(
        registro_de_carencias, "ruta", tmp_path / "carencias.jsonl"
    )
    fijar_contexto("", "")
    return registro_de_carencias


class TestEscrituraYLectura:
    def test_se_anota_y_se_lee(self, reg: RegistroDeCarencias) -> None:
        reg.anotar(Origen.DECLARADA_POR_EL_MODELO, "poner alarmas", "ponme una alarma")
        todas = reg.todas()
        assert len(todas) == 1
        assert todas[0].que_falta == "poner alarmas"
        assert todas[0].peticion == "ponme una alarma"

    def test_un_archivo_que_no_existe_no_rompe(self, tmp_path) -> None:
        assert RegistroDeCarencias(tmp_path / "no-existe.jsonl").todas() == []

    def test_una_linea_rota_no_invalida_el_resto(self, reg: RegistroDeCarencias) -> None:
        reg.anotar(Origen.DECLARADA_POR_EL_MODELO, "alarmas", "x")
        with open(reg.ruta, "a", encoding="utf-8") as f:
            f.write("esto no es json\n")
        reg.anotar(Origen.DECLARADA_POR_EL_MODELO, "correo", "y")

        assert len(reg.todas()) == 2

    def test_anotar_nunca_lanza_excepcion(self, tmp_path) -> None:
        """Perder una anotación es aceptable; romper la conversación no."""
        imposible = RegistroDeCarencias(tmp_path / "x" / "\0" / "mal.jsonl")
        imposible.anotar(Origen.DECLARADA_POR_EL_MODELO, "algo", "x")

    def test_los_textos_largos_se_recortan(self, reg: RegistroDeCarencias) -> None:
        reg.anotar(Origen.DECLARADA_POR_EL_MODELO, "a" * 500, "b" * 2000)
        c = reg.todas()[0]
        assert len(c.que_falta) <= 120
        assert len(c.peticion) <= 500


class TestResumen:
    def test_se_agrupan_las_repeticiones(self, reg: RegistroDeCarencias) -> None:
        for peticion in ("ponme una alarma", "alarma a las 8", "despiértame"):
            reg.anotar(Origen.DECLARADA_POR_EL_MODELO, "poner alarmas", peticion)

        resumen = reg.resumen()
        assert len(resumen) == 1
        assert resumen[0].veces == 3
        assert len(resumen[0].ejemplos) == 3

    def test_lo_mas_pedido_va_primero(self, reg: RegistroDeCarencias) -> None:
        """El orden es lo que hace útil el archivo: qué construir antes."""
        reg.anotar(Origen.DECLARADA_POR_EL_MODELO, "leer el correo", "lee mi correo")
        for _ in range(4):
            reg.anotar(Origen.DECLARADA_POR_EL_MODELO, "poner alarmas", "alarma")

        resumen = reg.resumen()
        assert resumen[0].que_falta == "poner alarmas"
        assert resumen[0].veces == 4

    def test_se_distingue_por_origen(self, reg: RegistroDeCarencias) -> None:
        """La misma palabra por vías distintas implica arreglos distintos."""
        reg.anotar(Origen.HERRAMIENTA_INVENTADA, "poner_alarma", "x")
        reg.anotar(Origen.ACCION_NO_PERMITIDA, "poner_alarma", "x")
        assert len(reg.resumen()) == 2


class TestEstados:
    def test_marcar_como_aprobada(self, reg: RegistroDeCarencias) -> None:
        reg.anotar(Origen.DECLARADA_POR_EL_MODELO, "poner alarmas", "x")
        assert reg.marcar("poner alarmas", "aprobada") == 1
        assert reg.todas()[0].estado == "aprobada"

    def test_lo_tratado_desaparece_de_los_pendientes(
        self, reg: RegistroDeCarencias
    ) -> None:
        reg.anotar(Origen.DECLARADA_POR_EL_MODELO, "poner alarmas", "x")
        reg.anotar(Origen.DECLARADA_POR_EL_MODELO, "leer el correo", "y")
        reg.marcar("poner alarmas", "descartada")

        pendientes = [r.que_falta for r in reg.resumen(solo_pendientes=True)]
        assert pendientes == ["leer el correo"]
        assert len(reg.resumen(solo_pendientes=False)) == 2

    def test_marcar_algo_inexistente_no_cambia_nada(
        self, reg: RegistroDeCarencias
    ) -> None:
        reg.anotar(Origen.DECLARADA_POR_EL_MODELO, "alarmas", "x")
        assert reg.marcar("otra cosa", "aprobada") == 0


class TestDeteccionAutomatica:
    def test_una_herramienta_inventada_se_anota(self, aislado) -> None:
        """La vía más fuerte: el modelo esperaba que existiera."""
        from skills.registro import registro

        fijar_contexto("ponme una alarma a las ocho", "s1")
        resultado = registro.invocar("poner_alarma", {"hora": "08:00"})

        assert "No existe ninguna habilidad" in resultado
        anotadas = aislado.todas()
        assert len(anotadas) == 1
        assert anotadas[0].que_falta == "poner_alarma"
        assert anotadas[0].peticion == "ponme una alarma a las ocho"
        assert anotadas[0].origen == Origen.HERRAMIENTA_INVENTADA.value

    def test_el_mensaje_frena_al_modelo(self, aislado) -> None:
        """No basta con anotarlo: el modelo no debe fingir que lo hizo."""
        from skills.registro import registro

        resultado = registro.invocar("enviar_whatsapp", {"a": "mamá"})
        assert "todavía no sabes hacer eso" in resultado

    def test_una_accion_fuera_de_la_politica_se_anota(self, aislado) -> None:
        from security.guard import Guardian, Peticion

        fijar_contexto("enciende la luz del salón", "s1")
        Guardian().evaluar(Peticion(accion="encender_luz", objetivo="salón"))

        anotadas = aislado.todas()
        assert len(anotadas) == 1
        assert anotadas[0].origen == Origen.ACCION_NO_PERMITIDA.value

    def test_el_modelo_puede_anotarla_el_mismo(self, aislado) -> None:
        """La vía que cubre el caso más común: que ni lo intente."""
        from skills.carencias import anotar_carencia

        fijar_contexto("ponme una alarma a las ocho", "s1")
        resultado = anotar_carencia("poner alarmas y temporizadores")

        assert "NO digas que lo has hecho" in resultado
        anotadas = aislado.todas()
        assert len(anotadas) == 1
        assert anotadas[0].que_falta == "poner alarmas y temporizadores"

    def test_anotar_sin_capacidad_no_guarda_basura(self, aislado) -> None:
        from skills.carencias import anotar_carencia

        anotar_carencia("")
        assert aislado.todas() == []


class TestNoSeAnotaLoQueSiFunciona:
    def test_una_habilidad_que_existe_no_se_anota(self, aislado) -> None:
        from skills.registro import registro

        registro.invocar("hora_fecha", {})
        assert aislado.todas() == []

    def test_un_rechazo_por_ruta_no_es_una_carencia(self, aislado) -> None:
        """Que te denieguen una carpeta no es que falte una habilidad."""
        from security.guard import Guardian, Peticion

        Guardian().evaluar(
            Peticion(accion="leer_archivo", objetivo="C:/Windows/algo.txt")
        )
        assert aislado.todas() == []
