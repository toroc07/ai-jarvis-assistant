"""
Pruebas de la limpieza de ruido.

La que más importa es la de la amplitud: un fallo en la reconstrucción hacía
que el audio saliera ocho veces más fuerte de lo que entraba, y un audio
saturado descoloca tanto a Whisper como a la verificación de voz.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voice.ruido import limpiar_ruido, nivel_de_ruido, perfil_de_ruido  # noqa: E402

FS = 16000


def voz_con_pausas(dur: float = 2.0) -> np.ndarray:
    """Habla sintética con armónicos y pausas, como el habla real."""
    t = np.linspace(0, dur, int(FS * dur), endpoint=False)
    f0 = 130 + 25 * np.sin(2 * np.pi * 0.7 * t)
    fase = 2 * np.pi * np.cumsum(f0) / FS
    s = sum((1.0 / k) * np.sin(k * fase) for k in range(1, 9))
    env = np.zeros_like(t)
    for inicio in np.arange(0, dur, 0.55):
        a, b = int(inicio * FS), int((inicio + 0.32) * FS)
        env[a : min(b, len(env))] = 1.0
    env = np.convolve(env, np.hanning(600) / 300, mode="same")
    return (s * env * 0.3).astype(np.float32)


class TestAmplitud:
    def test_no_amplifica_la_senal(self) -> None:
        """Limpiar nunca debe subir el volumen.

        Medido con voz real de Piper: la reconstrucción sacaba picos de 8,2
        partiendo de una señal que no pasaba de 1,0. Dividir por los pesos del
        solapamiento en los extremos, donde son diminutos, disparaba la
        amplitud.
        """
        voz = voz_con_pausas()
        limpia = limpiar_ruido(voz, FS)
        assert np.abs(limpia).max() <= np.abs(voz).max() * 1.01

    def test_no_amplifica_audio_ruidoso(self) -> None:
        rng = np.random.default_rng(0)
        sucio = voz_con_pausas() + rng.normal(0, 0.08, int(FS * 2)).astype(np.float32)
        limpia = limpiar_ruido(sucio, FS)
        assert np.abs(limpia).max() <= np.abs(sucio).max() * 1.01

    def test_no_amplifica_audio_a_volumen_alto(self) -> None:
        """El caso real: Piper saca audio casi a plena escala."""
        voz = voz_con_pausas()
        fuerte = (voz / np.abs(voz).max() * 0.99).astype(np.float32)
        limpia = limpiar_ruido(fuerte, FS)
        assert np.abs(limpia).max() <= 1.01


class TestRobustez:
    def test_un_audio_vacio_no_rompe(self) -> None:
        assert limpiar_ruido(np.array([], dtype=np.float32), FS).size == 0

    def test_un_audio_muy_corto_se_devuelve_igual(self) -> None:
        corto = np.ones(100, dtype=np.float32)
        assert np.array_equal(limpiar_ruido(corto, FS), corto)

    def test_el_silencio_no_rompe(self) -> None:
        silencio = np.zeros(FS, dtype=np.float32)
        assert not np.any(np.isnan(limpiar_ruido(silencio, FS)))

    def test_no_devuelve_nan_ni_infinitos(self) -> None:
        limpia = limpiar_ruido(voz_con_pausas(), FS)
        assert np.all(np.isfinite(limpia))

    def test_conserva_la_longitud(self) -> None:
        voz = voz_con_pausas()
        assert limpiar_ruido(voz, FS).size == voz.size

    def test_sin_perfil_posible_devuelve_none(self) -> None:
        assert perfil_de_ruido(np.zeros(200, dtype=np.float32)) is None


class TestMedicionDeRuido:
    def test_el_silencio_da_cero(self) -> None:
        assert nivel_de_ruido(np.zeros(FS, dtype=np.float32)) == 0.0

    def test_mas_ruido_da_mas_nivel(self) -> None:
        rng = np.random.default_rng(1)
        voz = voz_con_pausas()
        poco = nivel_de_ruido(voz + rng.normal(0, 0.005, voz.size).astype(np.float32))
        mucho = nivel_de_ruido(voz + rng.normal(0, 0.12, voz.size).astype(np.float32))
        assert mucho > poco
