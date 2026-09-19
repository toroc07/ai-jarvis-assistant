"""
Pruebas del humor y del falso positivo que provocaba.

El fallo real: Jarvis contó chistes correctamente, pero como los chistes
incluían frases como "no sé nada de drogas" y "lo siento, no tengo vaso", el
detector de carencias las tomó por Jarvis diciendo que no sabía hacer algo y
las anotó como habilidades pendientes.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.carencias import parece_una_carencia  # noqa: E402
from skills.humor import CHISTES, PULLAS, contar_chiste, comentario_con_retranca  # noqa: E402


# Los dos chistes reales que ensuciaron la lista de pendientes.
CHISTE_INGENIERO = (
    'Un día un ingeniero, un físico y un programador fueron detenidos por la '
    'policía. El ingeniero dijo: "Soy ingeniero, no sé nada de drogas". El '
    'físico dijo: "Soy físico, no sé nada de drogas". El programador dijo: '
    '"Soy programador, y esto es un problema de hardware".'
)
CHISTE_BAR = (
    'Un hombre entra en un bar y le pide al barman: "¿Tienes un vaso de '
    'whisky?" El barman le responde: "Lo siento, no tengo vaso".'
)


class TestFalsosPositivos:
    @pytest.mark.parametrize("chiste", [CHISTE_INGENIERO, CHISTE_BAR])
    def test_un_chiste_no_es_una_carencia(self, chiste: str) -> None:
        assert not parece_una_carencia(chiste, hubo_acciones=False)

    @pytest.mark.parametrize("chiste", CHISTES)
    def test_ningun_chiste_propio_se_anota(self, chiste: str) -> None:
        """Toda la colección debe pasar el filtro."""
        assert not parece_una_carencia(chiste, hubo_acciones=False)

    @pytest.mark.parametrize("pulla", PULLAS)
    def test_las_pullas_no_se_anotan(self, pulla: str) -> None:
        assert not parece_una_carencia(pulla, hubo_acciones=False)

    def test_una_cita_larga_no_se_anota(self) -> None:
        texto = (
            "El personaje dice en la película: «Lo siento, no puedo hacer eso, "
            "Dave». Es una de las frases más conocidas del cine."
        )
        assert not parece_una_carencia(texto, hubo_acciones=False)

    def test_una_negativa_enterrada_al_final_no_cuenta(self) -> None:
        """Cuando no sabe algo lo dice de entrada, no en el párrafo cuarto."""
        texto = (
            "Una API es un conjunto de reglas que permite a los programas "
            "comunicarse entre sí. Se usa constantemente en desarrollo web y "
            "móvil para conectar servicios distintos entre ellos. Por ejemplo, "
            "cuando una aplicación del tiempo consulta los datos de una "
            "estación meteorológica. Por cierto, no tengo acceso a internet "
            "ahora mismo."
        )
        assert not parece_una_carencia(texto, hubo_acciones=False)


class TestSiguenDetectandoseLasCarenciasDeVerdad:
    """El arreglo no puede haber roto la detección legítima."""

    @pytest.mark.parametrize(
        "texto",
        [
            "No sé poner alarmas todavía.",
            "No puedo enviar mensajes de WhatsApp.",
            "No tengo forma de encender las luces de casa.",
            "Lamentablemente no dispongo de acceso a tu correo.",
        ],
    )
    def test_se_detectan(self, texto: str) -> None:
        assert parece_una_carencia(texto, hubo_acciones=False)

    def test_si_hubo_acciones_nunca_es_carencia(self) -> None:
        assert not parece_una_carencia("No sé poner alarmas.", hubo_acciones=True)


class TestChistes:
    def test_devuelve_un_chiste_de_la_coleccion(self) -> None:
        assert contar_chiste() in CHISTES

    def test_no_repite_hasta_agotar(self, tmp_path, monkeypatch) -> None:
        """Escuchar el mismo chiste dos veces seguidas mata la gracia."""
        from skills import humor

        monkeypatch.setattr(humor, "RUTA_CONTADOS", tmp_path / "contados.json")
        salidos = {humor.contar_chiste() for _ in range(len(CHISTES))}
        assert len(salidos) == len(CHISTES)

    def test_tras_agotarlos_sigue_contando(self, tmp_path, monkeypatch) -> None:
        from skills import humor

        monkeypatch.setattr(humor, "RUTA_CONTADOS", tmp_path / "contados.json")
        for _ in range(len(CHISTES) + 3):
            assert humor.contar_chiste() in CHISTES

    def test_los_chistes_son_cortos(self) -> None:
        """Se escuchan en voz alta: uno largo se hace pesado."""
        for chiste in CHISTES:
            assert len(chiste) <= 260, f"Demasiado largo: {chiste[:60]}..."

    def test_hay_variedad_suficiente(self) -> None:
        assert len(CHISTES) >= 15
        assert len(set(CHISTES)) == len(CHISTES)

    def test_la_retranca_devuelve_algo_de_la_lista(self) -> None:
        assert comentario_con_retranca() in PULLAS


class TestElReflexivoNoEsUnaCarencia:
    """«No se» reflexivo no es «no sé».

    Lo destapó una respuesta real con humor: "un robot que no se cansa de
    responder preguntas" se anotaba como habilidad pendiente, porque el patrón
    casaba con el pronombre reflexivo en lugar de con el verbo saber.
    """

    @pytest.mark.parametrize(
        "texto",
        [
            "Pues yo, como un robot que no se cansa de responder preguntas.",
            "Aquí no se permite fumar.",
            "Eso no se hace así.",
            "El archivo no se ha podido abrir porque está en uso.",
            "Se me ha estropeado el teclado y no sé por qué.",
        ],
    )
    def test_no_se_anota(self, texto: str) -> None:
        assert not parece_una_carencia(texto, hubo_acciones=False)

    @pytest.mark.parametrize(
        "texto",
        [
            "No sé poner alarmas todavía.",
            "No se poner alarmas todavia.",
            "No sé cómo hacer eso.",
            "No sé.",
            "No soy capaz de controlar la tele.",
            "No dispongo de acceso a tu correo.",
            "No tengo forma de encender las luces de casa.",
        ],
    )
    def test_las_de_verdad_siguen_detectandose(self, texto: str) -> None:
        assert parece_una_carencia(texto, hubo_acciones=False)
