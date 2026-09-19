"""
Limpieza de ruido de fondo.

El ruido de una habitación (ventilador, nevera, tráfico, el propio portátil) es
bastante constante: cambia poco de un instante a otro. La voz no. Eso permite
separarlos sin modelos ni dependencias nuevas.

El método es resta espectral, que es el clásico para esto:

  1. Se mide el espectro de los tramos más silenciosos, que son los que solo
     tienen ruido. Eso da el "perfil" del ruido de tu sala.
  2. Ese perfil se resta del espectro de todo el audio.
  3. Se reconstruye la señal.

Se aplica antes de extraer la huella vocal y antes de transcribir, de modo que
tanto la verificación como Whisper trabajen sobre voz más limpia. En una sala
silenciosa apenas cambia nada; en una ruidosa es la diferencia entre que te
reconozca y que no.
"""

from __future__ import annotations

import numpy as np

# Tamaño de la ventana de análisis, en muestras. 512 a 16 kHz son 32 ms, que es
# el estándar para voz: bastante corto para seguir los cambios de un fonema y
# bastante largo para tener resolución en frecuencia.
VENTANA = 512
SALTO = VENTANA // 4

# Cuánto ruido se resta. Por encima de 1 se resta de más, lo que limpia mejor
# pero deja un burbujeo metálico que confunde a los modelos. Este valor está
# medido, no elegido a ojo: con 1,5 la señal salía peor que sin limpiar.
FACTOR = 1.0

# Suelo por debajo del cual no se baja, como fracción de la señal original.
# Sin él, restar de más deja huecos de silencio absoluto que suenan peor que el
# ruido y desestabilizan la huella vocal.
SUELO = 0.15

# Percentil de cada banda que se toma como ruido. Bajo a propósito: el ruido de
# fondo es lo que está presente incluso en los instantes más apagados de cada
# frecuencia. Subirlo se empieza a comer la voz.
PERCENTIL_DE_RUIDO = 15


def _ventanas(senal: np.ndarray) -> np.ndarray:
    """Parte la señal en ventanas solapadas."""
    if senal.size < VENTANA:
        return np.empty((0, VENTANA), dtype=np.float32)
    n = 1 + (senal.size - VENTANA) // SALTO
    indices = np.arange(VENTANA)[None, :] + SALTO * np.arange(n)[:, None]
    return senal[indices]


def perfil_de_ruido(senal: np.ndarray) -> np.ndarray | None:
    """Estima el ruido de fondo banda por banda, o None si no se puede.

    Se toma un percentil bajo de cada banda de frecuencia POR SEPARADO a lo
    largo del tiempo, en lugar de quedarse con los fotogramas más silenciosos
    enteros. La diferencia importa: con fotogramas enteros, si la persona habla
    sin pausas, los más "flojos" siguen siendo voz y restarlos se come la voz.
    Banda a banda no pasa, porque hasta en mitad de una frase cada frecuencia
    concreta tiene instantes apagados: ahí es donde asoma el ruido de fondo.

    Es el método conocido como estadística de mínimos, y es lo que hace que la
    limpieza funcione con habla real y no solo con grabaciones con silencios.
    """
    trozos = _ventanas(senal)
    if trozos.shape[0] < 4:
        return None

    ventana = np.hanning(VENTANA).astype(np.float32)
    espectros = np.abs(np.fft.rfft(trozos * ventana, axis=1))

    return np.percentile(espectros, PERCENTIL_DE_RUIDO, axis=0)


def limpiar_ruido(senal: np.ndarray, frecuencia: int = 16000) -> np.ndarray:
    """Quita el ruido de fondo constante de un audio.

    Si algo no encaja (audio muy corto, todo silencio), devuelve la señal tal
    cual: es mejor trabajar con audio sucio que con audio destrozado.
    """
    muestras = np.asarray(senal, dtype=np.float32).flatten()
    if muestras.size < VENTANA * 4:
        return muestras

    ruido = perfil_de_ruido(muestras)
    if ruido is None:
        return muestras

    trozos = _ventanas(muestras)
    ventana = np.hanning(VENTANA).astype(np.float32)
    espectros = np.fft.rfft(trozos * ventana, axis=1)

    magnitud = np.abs(espectros)
    fase = np.angle(espectros)

    # Se resta el ruido de la magnitud, nunca de la fase: la fase no lleva
    # información de ruido separable y tocarla emborrona la voz.
    limpia = np.maximum(magnitud - FACTOR * ruido[None, :], SUELO * magnitud)

    reconstruidos = np.fft.irfft(limpia * np.exp(1j * fase), n=VENTANA, axis=1)
    reconstruidos = reconstruidos * ventana

    # Se vuelven a sumar las ventanas solapadas, dividiendo por cuántas veces
    # se ha sumado cada muestra para no subir el volumen en el solape.
    salida = np.zeros(muestras.size, dtype=np.float32)
    pesos = np.zeros(muestras.size, dtype=np.float32)
    for i in range(reconstruidos.shape[0]):
        desde = i * SALTO
        salida[desde : desde + VENTANA] += reconstruidos[i]
        pesos[desde : desde + VENTANA] += ventana**2

    # El umbral de los pesos importa más de lo que parece. En los extremos de
    # la señal solo se ha sumado media ventana, así que el peso es diminuto y
    # dividir por él dispara la amplitud: medido, el audio salía con picos de
    # 8,2 cuando la entrada no pasaba de 1. Con un suelo razonable, donde no
    # hay solapamiento suficiente se conserva la señal original en lugar de
    # inventar una amplificada.
    util = pesos > 0.05 * float(pesos.max() or 1.0)
    salida[util] /= pesos[util]
    salida[~util] = muestras[~util]

    # Red de seguridad: la limpieza nunca debe subir el volumen. Si tras
    # reconstruir el pico supera al original, se reescala. Un audio más fuerte
    # que el de entrada satura al reproducirlo y descoloca a los modelos.
    pico_original = float(np.abs(muestras).max())
    pico_nuevo = float(np.abs(salida).max())
    if pico_original > 0 and pico_nuevo > pico_original:
        salida *= pico_original / pico_nuevo

    # Si la limpieza se ha llevado casi toda la señal, algo ha salido mal y es
    # preferible el audio original.
    if float(np.abs(salida).max()) < 0.05 * float(np.abs(muestras).max()):
        return muestras

    return salida.astype(np.float32)


def nivel_de_ruido(senal: np.ndarray) -> float:
    """Cuánto ruido de fondo hay, de 0 (silencio) a 1 (muy ruidoso).

    Sirve para avisarte al registrar la voz de que el sitio no es bueno, en vez
    de dejar que la huella salga mal y descubrirlo después.
    """
    perfil = perfil_de_ruido(np.asarray(senal, dtype=np.float32).flatten())
    if perfil is None:
        return 0.0
    energia = float(perfil.mean()) / VENTANA
    db = 20 * np.log10(energia + 1e-10)
    return float(np.clip((db + 70) / 50, 0.0, 1.0))
