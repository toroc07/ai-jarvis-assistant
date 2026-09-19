"""
Convierte audio en las barras que dibuja el orbe.

Se toma la transformada de Fourier del trozo de audio y se reparte en bandas.
Dos detalles importan para que el anillo se vea bien:

  - Las bandas se reparten en escala logarítmica, no lineal. El oído percibe
    la frecuencia así, y con reparto lineal casi todas las barras caerían en
    los agudos, que apenas tienen energía en una voz: el anillo se movería solo
    por un lado.
  - El nivel se mide en decibelios, también por eso: en escala lineal la voz
    normal daría barras diminutas y solo un grito llenaría el anillo.
"""

from __future__ import annotations

import numpy as np

# Rango útil para voz humana. Por debajo hay ruido de sala y por encima apenas
# queda energía, así que incluirlo solo añadiría barras planas.
FREC_MIN = 80.0
FREC_MAX = 8000.0

# Suelo y techo en decibelios, medidos sobre el espectro ya normalizado.
#
# Están calibrados con la voz real de Piper, no elegidos a ojo: sin normalizar
# la transformada por el tamaño de la ventana, una voz normal se salía de
# escala y el anillo aparecía con todas las barras al máximo, sin moverse.
DB_MIN = -68.0
DB_MAX = -22.0


class Analizador:
    """Convierte bloques de audio en niveles de 0 a 1 por banda."""

    def __init__(self, bandas: int = 64, frecuencia: int = 16000) -> None:
        self.bandas = bandas
        self.frecuencia = frecuencia
        self._bordes: np.ndarray | None = None
        self._tamano_ventana = 0

    def _calcular_bordes(self, n: int) -> np.ndarray:
        """Dónde empieza y acaba cada banda, repartidas logarítmicamente."""
        frecuencias = np.fft.rfftfreq(n, 1 / self.frecuencia)
        limites = np.logspace(
            np.log10(FREC_MIN), np.log10(FREC_MAX), self.bandas + 1
        )
        # searchsorted traduce cada límite en hercios al índice del espectro.
        return np.searchsorted(frecuencias, limites)

    def analizar(self, audio: np.ndarray) -> list[float]:
        """Devuelve un nivel de 0 a 1 por banda a partir de audio mono."""
        if audio.size == 0:
            return [0.0] * self.bandas

        muestras = audio.astype(np.float32).flatten()

        # La ventana de Hann evita los chasquidos que aparecen al cortar el
        # audio en bloques y que se verían como picos falsos en el anillo.
        ventana = np.hanning(muestras.size)

        # Dividir por el tamaño de la ventana deja la escala independiente del
        # tamaño del bloque. Sin esto, los valores crecen con el número de
        # muestras y el resultado se sale del rango de decibelios esperado.
        espectro = np.abs(np.fft.rfft(muestras * ventana)) / (muestras.size / 2)

        if self._bordes is None or self._tamano_ventana != muestras.size:
            self._bordes = self._calcular_bordes(muestras.size)
            self._tamano_ventana = muestras.size

        niveles: list[float] = []
        for i in range(self.bandas):
            desde, hasta = self._bordes[i], self._bordes[i + 1]
            if hasta <= desde:
                # Banda más estrecha que la resolución del espectro: se toma el
                # valor más cercano en lugar de dejar un hueco en el anillo.
                hasta = desde + 1
            trozo = espectro[desde:hasta]
            energia = float(np.mean(trozo)) if trozo.size else 0.0

            # +1e-10 evita el logaritmo de cero en el silencio absoluto.
            db = 20 * np.log10(energia + 1e-10)
            nivel = (db - DB_MIN) / (DB_MAX - DB_MIN)
            niveles.append(float(np.clip(nivel, 0.0, 1.0)))

        return niveles


def volumen(audio: np.ndarray) -> float:
    """Volumen general de 0 a 1, para decidir si alguien está hablando."""
    if audio.size == 0:
        return 0.0
    rms = float(np.sqrt(np.mean(audio.astype(np.float32) ** 2)))
    db = 20 * np.log10(rms + 1e-10)
    return float(np.clip((db - DB_MIN) / (DB_MAX - DB_MIN), 0.0, 1.0))
