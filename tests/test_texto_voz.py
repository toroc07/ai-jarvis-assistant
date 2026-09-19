"""
Pruebas de la limpieza de texto para voz.

El modelo escribe para pantalla: mete emojis, negritas con asteriscos y
viñetas. Dicho por Piper, eso suena mal o directamente se lee. Pedirlo en el
prompt ayuda pero no basta, así que se limpia antes de hablar.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voice.texto import limpiar_para_hablar  # noqa: E402


class TestEmojis:
    @pytest.mark.parametrize(
        "texto,esperado",
        [
            ("Son las once. \U0001f552", "Son las once."),
            ("Muy gracioso \U0001f604", "Muy gracioso"),
            ("Listo ✅ ya está", "Listo ya está"),
            ("\U0001f916 Soy Jarvis", "Soy Jarvis"),
        ],
    )
    def test_se_quitan(self, texto: str, esperado: str) -> None:
        assert limpiar_para_hablar(texto) == esperado

    def test_un_texto_solo_de_emojis_queda_vacio(self) -> None:
        """Hacer hablar al sintetizador con nada no tiene sentido."""
        assert limpiar_para_hablar("\U0001fa9d\U0001f528") == ""


class TestMarkdown:
    @pytest.mark.parametrize(
        "texto,esperado",
        [
            ("**Listo**, ya está", "Listo, ya está"),
            ("*Pues aquí, esperando*", "Pues aquí, esperando"),
            ("Usa `abrir_url` para eso", "Usa abrir_url para eso"),
            ("Mira [YouTube](https://youtube.com)", "Mira YouTube"),
            ("# Título\nY el texto", "Título\nY el texto"),
            ("- Primero\n- Segundo", "Primero\nSegundo"),
        ],
    )
    def test_se_quitan_las_marcas(self, texto: str, esperado: str) -> None:
        assert limpiar_para_hablar(texto) == esperado

    def test_del_enlace_se_dice_el_texto_no_la_direccion(self) -> None:
        """Leer una URL en voz alta es insoportable."""
        limpio = limpiar_para_hablar("Abre [el vídeo](https://youtu.be/abc123)")
        assert "youtu.be" not in limpio
        assert "el vídeo" in limpio


class TestNoDestruyeElTextoNormal:
    @pytest.mark.parametrize(
        "texto",
        [
            "Son las tres y veinte de la tarde.",
            "¿Qué canción quieres que ponga?",
            "Tienes 127 gigas libres en el disco C.",
            "No sé poner alarmas todavía, pero lo he apuntado.",
            "¿Por qué el libro de matemáticas estaba triste? "
            "Porque tenía demasiados problemas.",
        ],
    )
    def test_el_texto_plano_pasa_igual(self, texto: str) -> None:
        assert limpiar_para_hablar(texto) == texto

    def test_los_signos_de_puntuacion_se_conservan(self) -> None:
        """Piper los necesita para entonar y hacer pausas."""
        texto = "Hola, ¿qué tal? Bien; gracias. ¡Genial!"
        assert limpiar_para_hablar(texto) == texto

    def test_los_acentos_y_la_enie_se_conservan(self) -> None:
        texto = "El año que viene añadiré más funciones, según vayas pidiendo."
        assert limpiar_para_hablar(texto) == texto

    def test_texto_vacio_no_rompe(self) -> None:
        assert limpiar_para_hablar("") == ""
        assert limpiar_para_hablar("   ") == ""


def test_el_caso_real_que_lo_motivo() -> None:
    """Respuesta real de Jarvis, con emoji y asteriscos a la vez."""
    real = "*Pues aquí, encendido y esperando órdenes. ¿Y tú?* \U0001f60f"
    assert limpiar_para_hablar(real) == "Pues aquí, encendido y esperando órdenes. ¿Y tú?"
