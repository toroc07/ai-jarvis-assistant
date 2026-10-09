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

import os
import re
import threading
from pathlib import Path
from typing import Callable

import numpy as np

from core.texto import limpiar_para_hablar

RAIZ = Path(__file__).resolve().parent.parent
RUTA_VOCES = RAIZ / "data" / "voz" / "piper"

# Voz en español de Piper. 'davefx' es masculina de España y de calidad media,
# que es el equilibrio razonable: las de calidad alta pesan mucho más y en esta
# máquina generarían más lento de lo que se tarda en escucharlas.
#
# Se puede cambiar con JARVIS_VOZ_PIPER. Para probar otra, descárgala con
#   python -m voice.habla descargar es_ES-sharvard-medium
# y ponla en .env. La lista completa está en el repositorio rhasspy/piper-voices.
VOZ_PIPER = os.getenv("JARVIS_VOZ_PIPER", "es_ES-davefx-medium").strip() or "es_ES-davefx-medium"

# Tamaño del trozo que se manda al orbe cada vez. Con 1024 muestras a 22 kHz
# salen unos 46 ms, suficiente para que el anillo se vea fluido.
TROZO = 1024


class Voz:
    """Convierte texto en sonido y lo reproduce."""

    def __init__(self) -> None:
        self._piper = None
        self._sapi = None
        self._sapi_voz: str | None = None
        self._motor = "ninguno"
        self._parar = threading.Event()
        self._flujo = None
        # Un recordatorio puede sonar mientras Jarvis contesta otra cosa. Sin
        # turno, los dos escribirían a la vez en el altavoz y saldría ruido.
        self._turno = threading.Lock()

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
        # la que venga por defecto aunque suene con acento inglés. Aquí solo se
        # elige la voz: el motor que habla se crea en el hilo que habla.
        for voz in motor.getProperty("voices"):
            identificador = f"{voz.id} {voz.name}".lower()
            if "spanish" in identificador or "es-" in identificador or "español" in identificador:
                self._sapi_voz = voz.id
                break
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

        with self._turno:
            self._parar.clear()

            if self._motor == "piper":
                self._decir_con_piper(texto, al_generar_audio)
            elif self._motor == "sapi":
                self._decir_con_sapi(texto)

    def calentar(self) -> None:
        """Sintetiza una frase sin reproducirla para dejar Piper listo.

        La primera síntesis carga y optimiza el modelo y tarda bastante más que
        las siguientes. Mejor pagarla al arrancar que en la primera respuesta.
        """
        if self._motor == "piper" and self._piper is not None:
            for _ in self._piper.synthesize("Hola."):
                pass

    def _obtener_flujo(self, frecuencia: int):
        """El flujo de salida, reutilizado entre frases.

        Abrir el dispositivo de audio cuesta decenas de milisegundos, y al
        hablar frase a frase se pagaba en cada una como un silencio extra.
        """
        import sounddevice as sd

        flujo = self._flujo
        if flujo is not None and not flujo.closed and flujo.samplerate == frecuencia:
            if not flujo.active:
                flujo.start()
            return flujo

        self.cerrar_audio()
        flujo = sd.OutputStream(samplerate=frecuencia, channels=1, dtype="float32")
        flujo.start()
        self._flujo = flujo
        return flujo

    def terminar_de_sonar(self) -> None:
        """Espera a que suene lo que queda en el búfer.

        Hay que llamarlo antes de volver a escuchar: si no, el micrófono oye el
        final de la propia respuesta y lo toma por una frase tuya.
        """
        flujo = self._flujo
        if flujo is not None and flujo.active:
            try:
                flujo.stop()
            except Exception:
                self.cerrar_audio()

    def cerrar_audio(self) -> None:
        """Libera el dispositivo de salida. Se reabre solo al volver a hablar."""
        flujo, self._flujo = self._flujo, None
        if flujo is not None:
            try:
                flujo.abort()
            finally:
                flujo.close()

    def _decir_con_piper(
        self, texto: str, al_generar_audio: Callable[[np.ndarray], None] | None
    ) -> None:
        flujo = self._obtener_flujo(self._piper.config.sample_rate)

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
                    try:
                        flujo.write(parte)
                    except Exception:
                        # callar() abortó el flujo desde otro hilo: no es un
                        # error, es que le han mandado callar.
                        if self._parar.is_set():
                            break
                        raise
        except Exception:
            # Un flujo que ha fallado no se reutiliza: se abrirá uno nuevo.
            self.cerrar_audio()
            raise

    def _decir_con_sapi(self, texto: str) -> None:
        # SAPI no deja acceder al audio mientras habla, así que con este motor
        # el orbe no puede seguir la voz de verdad. Es el precio de la red de
        # seguridad; con Piper sí se mueve con el sonido real.
        #
        # SAPI es un objeto COM y solo funciona en el hilo que lo creó. Antes se
        # creaba en el hilo que prepara la voz y se usaba en el de la
        # conversación, y el respaldo se quedaba mudo. Ahora se crea aquí.
        import pyttsx3

        try:
            import comtypes

            comtypes.CoInitialize()
        except Exception:
            pass

        motor = pyttsx3.init()
        if self._sapi_voz:
            motor.setProperty("voice", self._sapi_voz)
        motor.setProperty("rate", 180)
        self._sapi = motor
        try:
            motor.say(texto)
            motor.runAndWait()
        finally:
            self._sapi = None

    def callar(self) -> None:
        """Corta lo que esté diciendo."""
        self._parar.set()
        flujo = self._flujo
        if flujo is not None:
            try:
                flujo.abort()
            except Exception:
                pass
        if self._motor == "sapi" and self._sapi is not None:
            try:
                self._sapi.stop()
            except Exception:
                pass


# Nombre de una voz de Piper: idioma_PAÍS-nombre-calidad, p. ej. es_ES-davefx-medium.
_NOMBRE_DE_VOZ = re.compile(r"^([a-z]{2})_([A-Z]{2})-([A-Za-z0-9_]+)-(x_low|low|medium|high)$")


def url_de_voz(voz: str) -> str | None:
    """Carpeta de Hugging Face donde está una voz, o None si el nombre no vale.

    El nombre se valida entero porque acaba formando parte de una URL y de un
    nombre de archivo: nada de barras ni de '..'.
    """
    partes = _NOMBRE_DE_VOZ.match(voz)
    if not partes:
        return None
    idioma, pais, nombre, calidad = partes.groups()
    return (
        "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
        f"{idioma}/{idioma}_{pais}/{nombre}/{calidad}"
    )


def descargar_voz(voz: str = VOZ_PIPER, destino: Path = RUTA_VOCES) -> tuple[bool, str]:
    """Descarga una voz de Piper.

    Son unos 60 MB desde Hugging Face. Se hace aparte y no al instalar porque
    sin ella Jarvis sigue hablando, solo que con la voz de Windows.
    """
    import httpx

    base = url_de_voz(voz)
    if base is None:
        return False, f"'{voz}' no es un nombre de voz de Piper (ejemplo: es_ES-davefx-medium)."
    destino.mkdir(parents=True, exist_ok=True)

    for nombre in (f"{voz}.onnx", f"{voz}.onnx.json"):
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

    return True, (
        f"Voz {voz} lista en {destino}. Para usarla pon JARVIS_VOZ_PIPER={voz} "
        "en el archivo .env y reinicia Jarvis."
    )


if __name__ == "__main__":
    import sys

    if len(sys.argv) == 3 and sys.argv[1] == "descargar":
        ok, mensaje = descargar_voz(sys.argv[2])
        print(mensaje)
        raise SystemExit(0 if ok else 1)
    print("Uso: python -m voice.habla descargar <voz>   (p. ej. es_ES-sharvard-medium)")
    raise SystemExit(2)
