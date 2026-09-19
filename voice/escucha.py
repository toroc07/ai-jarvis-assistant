"""
El oído de Jarvis: wake word y transcripción, ambos locales.

El ciclo completo es:

  1. Un hilo escucha el micrófono sin parar, en bloques de 80 ms.
  2. openWakeWord busca "hey jarvis" en cada bloque. Es un modelo diminuto,
     así que esto gasta muy poca CPU aunque corra todo el día.
  3. Al detectarlo, se verifica que la voz sea la tuya con el audio que
     acaba de sonar. Si no lo es, se ignora en silencio.
  4. Se graba lo que digas hasta que te calles.
  5. faster-whisper lo transcribe a texto en español.

Nada de esto sale de tu equipo.
"""

from __future__ import annotations

import os
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from voice.locutor import SEGUNDOS_DE_VERIFICACION
from voice.ruido import limpiar_ruido

RAIZ = Path(__file__).resolve().parent.parent
RUTA_MODELOS = RAIZ / "data" / "voz"

# openWakeWord trabaja a 16 kHz y en bloques de 1280 muestras (80 ms).
FRECUENCIA = 16000
BLOQUE = 1280

# PALABRAS DE ACTIVACIÓN DISPONIBLES
#
# openWakeWord trae estos modelos preentrenados y NO se pueden inventar otros:
# cada uno es una red entrenada con miles de grabaciones de esa frase concreta.
# Si quieres activar el asistente con otro nombre, hay que entrenar un modelo
# propio (openWakeWord publica el procedimiento) y apuntar a su archivo .onnx
# con JARVIS_PALABRA_CLAVE.
#
# Ojo: todas son en inglés, así que se pronuncian como en inglés aunque luego
# le hables en español.
PALABRAS_DISPONIBLES = {
    "hey_jarvis": "hey Jarvis",
    "alexa": "Alexa",
    "hey_mycroft": "hey Mycroft",
    "hey_rhasspy": "hey Rhasspy",
}

# Cuál se usa. Puede ser una de las de arriba o la ruta a un .onnx propio.
PALABRA = os.getenv("JARVIS_PALABRA_CLAVE", "hey_jarvis").strip() or "hey_jarvis"


def nombre_de_la_palabra() -> str:
    """Cómo se dice en voz alta la palabra configurada, para los mensajes."""
    if PALABRA in PALABRAS_DISPONIBLES:
        return PALABRAS_DISPONIBLES[PALABRA]
    return Path(PALABRA).stem.replace("_", " ")

# Confianza mínima del detector, de 0 a 1. Por debajo de 0,5 salta con ruidos
# parecidos; por encima de 0,7 hay que vocalizar demasiado.
UMBRAL_DETECCION = 0.55

# Cuánto audio previo se guarda para verificar la voz. Se toma de locutor.py
# para que sea exactamente el mismo tamaño con el que allí se calibra el umbral:
# verificar con menos audio del que se usó al calibrar rechaza al propio
# usuario, que es justo lo que pasaba cuando aquí había un segundo fijo.
SEGUNDOS_DE_CONTEXTO = SEGUNDOS_DE_VERIFICACION

# Cuándo se considera que terminaste de hablar.
SILENCIO_PARA_CORTAR = 1.2
MAXIMO_DE_GRABACION = 15.0
# Nivel por debajo del cual se considera silencio, en RMS de 0 a 1.
UMBRAL_DE_SILENCIO = 0.012

# Tope de bloques esperando a ser analizados. 25 bloques son dos segundos,
# de sobra para absorber un tiron puntual de CPU sin acumular retraso.
MAX_BLOQUES_EN_COLA = 25

# Contexto que se le da a Whisper antes de transcribir. No es una orden: es una
# muestra del vocabulario habitual, y el modelo la usa para decidir entre
# alternativas parecidas. Lleva los nombres propios que más se dicen hablándole
# a Jarvis, porque son justo donde más se equivoca: transcribió "Nirvana" como
# "Irbana" por no tener ninguna pista de que esperara un nombre de grupo.
CONTEXTO_DE_TRANSCRIPCION = (
    "Jarvis, abre YouTube, Spotify, Google, Gmail, Opera, Chrome, WhatsApp. "
    "Reproduce música de Nirvana, Metallica, Queen, The Beatles. "
    "Busca en internet, pon una canción, sube el volumen, qué hora es, "
    "cuánto espacio libre me queda, apágate, gracias."
)


@dataclass
class Deteccion:
    """Una activación válida del wake word."""

    audio_previo: np.ndarray
    confianza: float


class Oido:
    """Escucha el micrófono, detecta la palabra clave y transcribe."""

    def __init__(
        self,
        modelo_whisper: str = "small",
        dispositivo: int | None = None,
    ) -> None:
        self.modelo_whisper = modelo_whisper
        self.dispositivo = dispositivo

        self._detector = None
        self._clave_prediccion = PALABRA
        self._transcriptor = None
        self._escuchando = False
        self._hilo: threading.Thread | None = None

        # La cola tiene tope A PROPÓSITO. Medido en este equipo: el micrófono
        # entrega un bloque cada 80 ms y analizarlo cuesta unos 100 ms, así que
        # con una cola sin límite el retraso crece sin parar: a los cinco
        # minutos Jarvis estaría analizando audio de hace más de un minuto y
        # dejaría de responder a lo que dices ahora.
        #
        # Para detectar una palabra en directo, el audio viejo no vale nada: si
        # hay que tirar algo, se tira lo antiguo y se conserva lo reciente.
        self._audio = queue.Queue(maxsize=MAX_BLOQUES_EN_COLA)
        self._descartados = 0

        # Guarda el último segundo de audio para poder verificar la voz con
        # el propio "hey jarvis" que acaba de sonar.
        self._contexto = deque(maxlen=int(FRECUENCIA * SEGUNDOS_DE_CONTEXTO))

    # -- Carga de modelos ----------------------------------------------------

    def _obtener_detector(self):
        if self._detector is not None:
            return self._detector

        from openwakeword.model import Model

        RUTA_MODELOS.mkdir(parents=True, exist_ok=True)

        # Si PALABRA es una ruta a un .onnx propio se usa tal cual; si es uno de
        # los nombres conocidos, openWakeWord lo resuelve solo.
        modelo = PALABRA
        ruta_propia = Path(PALABRA)
        if ruta_propia.suffix == ".onnx":
            if not ruta_propia.is_file():
                raise FileNotFoundError(
                    f"No existe el modelo de palabra clave: {ruta_propia}. "
                    f"Usa uno de {sorted(PALABRAS_DISPONIBLES)} o corrige la "
                    "ruta en JARVIS_PALABRA_CLAVE."
                )
            modelo = str(ruta_propia)

        self._detector = Model(wakeword_models=[modelo], inference_framework="onnx")

        # openWakeWord nombra la predicción por el nombre del archivo, que no
        # tiene por qué coincidir con lo que se configuró. Se guarda el nombre
        # real para leer la predicción correcta.
        self._clave_prediccion = next(iter(self._detector.models), PALABRA)
        return self._detector

    def _obtener_transcriptor(self):
        if self._transcriptor is not None:
            return self._transcriptor

        from faster_whisper import WhisperModel

        # int8 en CPU: es lo que hace que la transcripción tarde un segundo en
        # lugar de cinco, a cambio de una pérdida de precisión inapreciable.
        self._transcriptor = WhisperModel(
            self.modelo_whisper,
            device="cpu",
            compute_type="int8",
            download_root=str(RUTA_MODELOS / "whisper"),
        )
        return self._transcriptor

    def precargar(self) -> None:
        """Deja los modelos listos para que la primera vez no tarde."""
        self._obtener_detector()
        self._obtener_transcriptor()

    # -- Escucha continua ----------------------------------------------------

    def escuchar(
        self,
        al_detectar: Callable[[Deteccion], None],
        al_recibir_audio: Callable[[np.ndarray], None] | None = None,
    ) -> None:
        """Arranca la escucha en segundo plano.

        'al_detectar' se llama cada vez que suena la palabra clave.
        'al_recibir_audio' recibe cada bloque, para alimentar el orbe.
        """
        if self._escuchando:
            return

        self._escuchando = True
        self._hilo = threading.Thread(
            target=self._bucle_protegido,
            args=(al_detectar, al_recibir_audio),
            daemon=True,
        )
        self._hilo.start()

    def _bucle_protegido(
        self,
        al_detectar: Callable[[Deteccion], None],
        al_recibir_audio: Callable[[np.ndarray], None] | None,
    ) -> None:
        """Envuelve el bucle para que un fallo no deje a Jarvis sordo en silencio.

        Sin esto, cualquier excepción mata el hilo sin rastro: la aplicación
        sigue abierta y aparentemente bien, pero ya no oye nada y no hay forma
        de saber por qué.
        """
        import traceback

        try:
            self._bucle(al_detectar, al_recibir_audio)
        except Exception:
            self._escuchando = False
            traceback.print_exc()
            print(
                "[escucha] El hilo de escucha se detuvo por el error anterior. "
                "Jarvis ya no responde a la palabra clave.",
                flush=True,
            )

    def parar(self) -> None:
        self._escuchando = False
        if self._hilo:
            self._hilo.join(timeout=2.0)

    def _bucle(
        self,
        al_detectar: Callable[[Deteccion], None],
        al_recibir_audio: Callable[[np.ndarray], None] | None,
    ) -> None:
        import sounddevice as sd

        detector = self._obtener_detector()
        mejor = 0.0
        ultimo_informe = time.monotonic()

        def entrada(datos, cuadros, tiempo, estado):
            if not self._escuchando:
                return
            try:
                self._audio.put_nowait(datos.copy())
            except queue.Full:
                # Cola llena: se tira el bloque más antiguo para meter el nuevo.
                # Esta función la llama el controlador de audio, así que no
                # puede bloquearse esperando sitio sin cortar la captura.
                try:
                    self._audio.get_nowait()
                    self._audio.put_nowait(datos.copy())
                    self._descartados += 1
                except (queue.Empty, queue.Full):
                    pass

        with sd.InputStream(
            samplerate=FRECUENCIA,
            blocksize=BLOQUE,
            channels=1,
            dtype="int16",
            device=self.dispositivo,
            callback=entrada,
        ):
            while self._escuchando:
                try:
                    bloque = self._audio.get(timeout=0.5)
                except queue.Empty:
                    continue

                muestras = bloque.flatten()
                self._contexto.extend(muestras)

                if al_recibir_audio is not None:
                    al_recibir_audio(muestras.astype(np.float32) / 32768.0)

                predicciones = detector.predict(muestras)
                confianza = predicciones.get(self._clave_prediccion, 0.0)

                # Se anota lo más cerca que se ha estado del umbral en el
                # último minuto. Sin esto no hay forma de distinguir "no te
                # oigo" de "te oigo pero no reconozco la palabra", que llevan a
                # arreglos completamente distintos.
                mejor = max(mejor, confianza)
                ahora = time.monotonic()
                if ahora - ultimo_informe >= 60.0:
                    aviso = ""
                    if self._descartados:
                        aviso = (
                            f"  [se descartaron {self._descartados} bloques de "
                            "audio por falta de CPU]"
                        )
                        self._descartados = 0
                    print(
                        f"[escucha] Mejor confianza del último minuto: "
                        f"{mejor:.3f} (hace falta {UMBRAL_DETECCION}){aviso}",
                        flush=True,
                    )
                    mejor = 0.0
                    ultimo_informe = ahora

                if confianza < UMBRAL_DETECCION:
                    continue

                previo = np.array(self._contexto, dtype=np.float32) / 32768.0
                al_detectar(Deteccion(audio_previo=previo, confianza=confianza))

                # Se limpia el contexto y el estado del detector para que la
                # misma activación no se dispare varias veces seguidas.
                self._contexto.clear()
                detector.reset()

    # -- Grabación de una petición -------------------------------------------

    def grabar_peticion(
        self, al_recibir_audio: Callable[[np.ndarray], None] | None = None
    ) -> np.ndarray:
        """Graba hasta que dejes de hablar y devuelve el audio.

        Corta tras 1,2 segundos de silencio, que es la pausa natural al
        terminar una frase sin cortar a quien piensa a media petición.
        """
        import sounddevice as sd

        trozos: list[np.ndarray] = []
        silencio = 0.0
        transcurrido = 0.0
        hubo_voz = False
        duracion_bloque = BLOQUE / FRECUENCIA

        cola: queue.Queue = queue.Queue()

        def entrada(datos, cuadros, tiempo, estado):
            cola.put(datos.copy())

        with sd.InputStream(
            samplerate=FRECUENCIA,
            blocksize=BLOQUE,
            channels=1,
            dtype="float32",
            device=self.dispositivo,
            callback=entrada,
        ):
            while transcurrido < MAXIMO_DE_GRABACION:
                try:
                    bloque = cola.get(timeout=1.0)
                except queue.Empty:
                    break

                muestras = bloque.flatten()
                trozos.append(muestras)
                transcurrido += duracion_bloque

                if al_recibir_audio is not None:
                    al_recibir_audio(muestras)

                nivel = float(np.sqrt(np.mean(muestras**2)))
                if nivel > UMBRAL_DE_SILENCIO:
                    hubo_voz = True
                    silencio = 0.0
                else:
                    silencio += duracion_bloque
                    # Solo se corta por silencio si antes hubo voz: si no, se
                    # espera a que empieces a hablar.
                    if hubo_voz and silencio >= SILENCIO_PARA_CORTAR:
                        break

        return np.concatenate(trozos) if trozos else np.array([], dtype=np.float32)

    # -- Transcripción -------------------------------------------------------

    def transcribir(self, audio: np.ndarray) -> str:
        """Convierte audio en texto, en español."""
        if audio.size < FRECUENCIA * 0.3:
            return ""

        # Se limpia el ruido de fondo antes de transcribir: Whisper acierta
        # bastante más con voz limpia, sobre todo en frases cortas.
        segmentos, _ = self._obtener_transcriptor().transcribe(
            limpiar_ruido(audio.astype(np.float32), FRECUENCIA),
            language="es",
            # Cinco caminos en lugar de uno. Con beam_size=1 el modelo se queda
            # con la primera opción que le parece bien, y en nombres propios se
            # equivoca: transcribió "Nirvana" como "Irbana". Explorar varias
            # alternativas cuesta décimas de segundo y acierta bastante más.
            beam_size=5,
            vad_filter=True,  # Descarta los silencios antes de transcribir.
            # El prompt inicial sesga la transcripción hacia el vocabulario que
            # de verdad usas con Jarvis. Whisper tiende a "españolizar" los
            # nombres en inglés, y darle estas palabras de contexto evita buena
            # parte de esos destrozos.
            initial_prompt=CONTEXTO_DE_TRANSCRIPCION,
            # Evita que se enganche repitiendo la misma palabra, que pasa con
            # audio corto o entrecortado.
            condition_on_previous_text=False,
        )
        return " ".join(s.text.strip() for s in segmentos).strip()
