"""
La voz de Jarvis.

Dos motores, y se usa el mejor que esté disponible:

  - Piper: voz neuronal local, suena natural y no necesita internet. Es el
    preferido, pero hay que descargar el modelo de voz en español.
  - SAPI: las voces que Windows ya trae. Suenan peor pero están siempre y no
    hay nada que descargar, así que sirven de red de seguridad.

Mientras habla se va emitiendo el audio hacia el orbe, y por eso el anillo se
mueve con lo que dice en lugar de fingir una animación.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

import numpy as np

from voice.texto import limpiar_para_hablar

RAIZ = Path(__file__).resolve().parent.parent
RUTA_VOCES = RAIZ / "data" / "voz" / "piper"

# Voz en español de Piper. 'davefx' es masculina de España y de calidad media,
# que es el equilibrio razonable: las de calidad alta pesan mucho más y en esta
# máquina generarían más lento de lo que se tarda en escucharlas.
VOZ_PIPER = "es_ES-davefx-medium"

# Tamaño del trozo que se manda al orbe cada vez. Con 1024 muestras a 22 kHz
# salen unos 46 ms, suficiente para que el anillo se vea fluido.
TROZO = 1024


class Voz:
    """Convierte texto en sonido y lo reproduce."""

    def __init__(self) -> None:
        self._piper = None
        self._sapi = None
        self._motor = "ninguno"
        self._parar = threading.Event()

    # -- Selección de motor --------------------------------------------------

    def preparar(self) -> str:
        """Carga el mejor motor disponible y devuelve cuál se va a usar."""
        if self._cargar_piper():
            self._motor = "piper"
        elif self._cargar_sapi():
            self._motor = "sapi"
        else:
            self._motor = "ninguno"
        return self._motor

    @property
    def motor(self) -> str:
        return self._motor

    def _cargar_piper(self) -> bool:
        try:
            from piper import PiperVoice
        except ImportError:
            return False

        modelo = RUTA_VOCES / f"{VOZ_PIPER}.onnx"
        if not modelo.exists():
            return False

        try:
            self._piper = PiperVoice.load(str(modelo))
            return True
        except Exception:
            return False

    def _cargar_sapi(self) -> bool:
        try:
            import pyttsx3
        except ImportError:
            return False

        try:
            motor = pyttsx3.init()
        except Exception:
            return False

        # Se busca una voz en español entre las instaladas; si no hay, se usa
        # la que venga por defecto aunque suene con acento inglés.
        for voz in motor.getProperty("voices"):
            identificador = f"{voz.id} {voz.name}".lower()
            if "spanish" in identificador or "es-" in identificador or "español" in identificador:
                motor.setProperty("voice", voz.id)
                break

        motor.setProperty("rate", 180)
        self._sapi = motor
        return True

    # -- Habla ---------------------------------------------------------------

    def decir(
        self,
        texto: str,
        al_generar_audio: Callable[[np.ndarray], None] | None = None,
    ) -> None:
        """Dice un texto en voz alta.

        'al_generar_audio' recibe el sonido por trozos según se reproduce, que
        es lo que alimenta el espectrómetro del orbe.
        """
        # Se limpia antes de sintetizar: emojis y marcas de Markdown suenan
        # mal o se leen literalmente. Da igual lo que escriba el modelo.
        texto = limpiar_para_hablar(texto)
        if not texto:
            return

        self._parar.clear()

        if self._motor == "piper":
            self._decir_con_piper(texto, al_generar_audio)
        elif self._motor == "sapi":
            self._decir_con_sapi(texto)

    def _decir_con_piper(
        self, texto: str, al_generar_audio: Callable[[np.ndarray], None] | None
    ) -> None:
        import sounddevice as sd

        flujo = sd.OutputStream(
            samplerate=self._piper.config.sample_rate, channels=1, dtype="float32"
        )
        flujo.start()

        try:
            # synthesize() va devolviendo AudioChunk por frases, así que Jarvis
            # empieza a hablar antes de haber sintetizado todo el texto.
            for trozo in self._piper.synthesize(texto):
                if self._parar.is_set():
                    break

                muestras = trozo.audio_float_array

                # Se reproduce y se avisa en partes pequeñas para que el orbe
                # se mueva a la vez que suena, no después.
                for i in range(0, muestras.size, TROZO):
                    if self._parar.is_set():
                        break
                    parte = muestras[i : i + TROZO]
                    if al_generar_audio is not None:
                        al_generar_audio(parte)
                    flujo.write(parte)
        finally:
            flujo.stop()
            flujo.close()

    def _decir_con_sapi(self, texto: str) -> None:
        # SAPI no deja acceder al audio mientras habla, así que con este motor
        # el orbe no puede seguir la voz de verdad. Es el precio de la red de
        # seguridad; con Piper sí se mueve con el sonido real.
        self._sapi.say(texto)
        self._sapi.runAndWait()

    def callar(self) -> None:
        """Corta lo que esté diciendo."""
        self._parar.set()
        if self._motor == "sapi" and self._sapi is not None:
            try:
                self._sapi.stop()
            except Exception:
                pass


def descargar_voz(destino: Path = RUTA_VOCES) -> tuple[bool, str]:
    """Descarga el modelo de voz de Piper en español.

    Son unos 63 MB desde Hugging Face. Se hace aparte y no al instalar porque
    sin él Jarvis sigue hablando, solo que con la voz de Windows.
    """
    import httpx

    base = (
        "https://huggingface.co/rhasspy/piper-voices/resolve/main/es/es_ES/"
        "davefx/medium"
    )
    destino.mkdir(parents=True, exist_ok=True)

    for nombre in (f"{VOZ_PIPER}.onnx", f"{VOZ_PIPER}.onnx.json"):
        archivo = destino / nombre
        if archivo.exists():
            continue
        try:
            with httpx.stream(
                "GET", f"{base}/{nombre}", follow_redirects=True, timeout=180.0
            ) as r:
                r.raise_for_status()
                with open(archivo, "wb") as f:
                    for trozo in r.iter_bytes():
                        f.write(trozo)
        except Exception as e:
            archivo.unlink(missing_ok=True)
            return False, f"No se pudo descargar {nombre}: {e}"

    return True, f"Voz {VOZ_PIPER} lista en {destino}."
