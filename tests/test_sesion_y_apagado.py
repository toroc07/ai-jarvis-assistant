"""
Pruebas de las sesiones, el apagado y la reproducción en YouTube.

La distinción entre cerrar y apagar es la que más fácil se rompe al tocar
código: cerrar deja a Jarvis escuchando en segundo plano y la sesión continúa;
apagar termina el proceso y la próxima vez empieza una sesión nueva.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.memory import Memoria  # noqa: E402
from skills.conversacion import (  # noqa: E402
    MARCA_DE_APAGADO,
    apagar_jarvis,
    es_apagado,
    es_despedida,
)


class TestSesiones:
    def test_cada_arranque_es_una_sesion_distinta(self) -> None:
        from core.agent import nueva_id_de_sesion

        assert nueva_id_de_sesion() != nueva_id_de_sesion()

    def test_se_anota_el_inicio(self, tmp_path) -> None:
        m = Memoria(tmp_path / "x.db")
        m.abrir_sesion("s1")
        assert m.inicio_de_sesion("s1") is not None

    def test_se_recuerdan_las_acciones_de_la_sesion(self, tmp_path) -> None:
        m = Memoria(tmp_path / "x.db")
        m.abrir_sesion("s1")
        m.registrar_accion("s1", "abrir_url", "https://youtube.com", "Abierto")
        m.registrar_accion("s1", "hora_fecha", "", "Son las 3")

        acciones = m.acciones_de_sesion("s1")
        assert len(acciones) == 2
        assert acciones[0]["habilidad"] == "abrir_url"

    def test_las_sesiones_no_se_mezclan(self, tmp_path) -> None:
        """Lo hecho en un arranque no debe aparecer en el siguiente."""
        m = Memoria(tmp_path / "x.db")
        m.abrir_sesion("s1")
        m.abrir_sesion("s2")
        m.registrar_accion("s1", "abrir_url", "a", "ok")

        assert len(m.acciones_de_sesion("s1")) == 1
        assert len(m.acciones_de_sesion("s2")) == 0

    def test_el_historial_no_se_mezcla_entre_sesiones(self, tmp_path) -> None:
        m = Memoria(tmp_path / "x.db")
        m.guardar_turno("s1", "user", "hola")
        m.guardar_turno("s2", "user", "adiós")

        assert len(m.historial("s1")) == 1
        assert m.historial("s1")[0].contenido == "hola"


APAGADOS = [
    "Jarvis apágate",
    "apágate",
    "apagate",
    "jarvis, apágate",
    "apaga el programa",
    "cierra el programa",
    "cierra jarvis",
    "termina el programa",
    "desconectate",
]

NO_APAGADOS = [
    "gracias, ya me encargo yo",
    "hasta luego",
    "adiós",
    "apaga la luz del salón",
    "apaga la música",
    "cierra la ventana del navegador",
    "qué hora es",
]


class TestApagado:
    @pytest.mark.parametrize("frase", APAGADOS)
    def test_se_reconoce_el_apagado(self, frase: str) -> None:
        assert es_apagado(frase), f"No se reconoció como apagado: {frase!r}"

    @pytest.mark.parametrize("frase", NO_APAGADOS)
    def test_no_se_apaga_por_error(self, frase: str) -> None:
        assert not es_apagado(frase), f"Se apagaría por error con: {frase!r}"

    def test_apagar_la_luz_no_apaga_jarvis(self) -> None:
        """El caso que más fácil se confunde: 'apaga' referido a otra cosa."""
        assert not es_apagado("apaga la luz")
        assert not es_apagado("apaga la tele")

    def test_despedirse_no_es_apagar(self) -> None:
        """Cerrar deja a Jarvis escuchando; apagar cierra el programa."""
        assert es_despedida("gracias, ya me encargo yo")
        assert not es_apagado("gracias, ya me encargo yo")

    def test_la_herramienta_marca_el_apagado(self) -> None:
        assert MARCA_DE_APAGADO in apagar_jarvis()


class TestYouTube:
    def test_reproducir_sin_busqueda_previa_no_miente(self) -> None:
        from skills.multimedia import limpiar_resultados, reproducir_en_youtube

        limpiar_resultados()
        resultado = reproducir_en_youtube(numero=1)
        assert "NO digas que reprodujiste" in resultado

    def test_un_numero_fuera_de_rango_no_miente(self) -> None:
        from skills import multimedia

        multimedia._ultimos_resultados = [multimedia.Video("abc12345678", "Una canción")]
        multimedia._ultima_busqueda = "prueba"
        resultado = multimedia.reproducir_en_youtube(numero=9)
        assert "NO digas que reprodujiste" in resultado
        multimedia.limpiar_resultados()

    def test_sin_consulta_ni_numero_no_miente(self) -> None:
        from skills.multimedia import limpiar_resultados, reproducir_en_youtube

        limpiar_resultados()
        resultado = reproducir_en_youtube()
        assert "NO digas que reprodujiste" in resultado

    def test_la_url_del_video_es_la_correcta(self) -> None:
        from skills.multimedia import Video

        v = Video("dQw4w9WgXcQ", "Algo")
        assert v.url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    def test_reproducir_usa_el_permiso_de_abrir_url(self) -> None:
        """No debe tener un permiso propio más laxo que abrir una web."""
        from security.guard import guardian
        from skills.registro import registro

        habilidad = registro._habilidades["reproducir_en_youtube"]
        assert habilidad.accion == "abrir_url"
        assert guardian.politica["acciones"]["abrir_url"] == "permitir"
