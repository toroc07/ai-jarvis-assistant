"""
El coordinador de la conversación por voz.

Une todas las piezas y decide en qué estado está Jarvis en cada momento:

    DORMIDO ──"hey jarvis"──► ¿es tu voz?
                                  │ no ──► vuelve a DORMIDO, sin decir nada
                                  │ sí
                                  ▼
                            ESCUCHANDO ──► PENSANDO ──► HABLANDO
                                  ▲                        │
                                  └────── ¿sigues ahí? ◄───┘
                                              │ no
                                              ▼
                                          DORMIDO

Tras responder, Jarvis sigue escuchando un rato por si continúas, para que no
haya que repetir "hey Jarvis" en cada frase de una misma conversación. Se cierra
cuando te despides o cuando pasa un tiempo sin que digas nada.

Todo corre en su propio hilo. La interfaz se entera por funciones de aviso, así
que esta clase no sabe nada de Qt y se puede probar sin abrir ventanas.
"""

from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum
from typing import Callable

import numpy as np

from core.agent import Agente
from security.guard import Peticion
from skills.conversacion import (
    MARCA_DE_APAGADO,
    MARCA_DE_CIERRE,
    es_apagado,
    es_despedida,
)
from voice.escucha import Deteccion, Oido
from voice.frases import HabladorPorFrases
from voice.habla import Voz
from voice.locutor import REBAJA_EN_LA_PALABRA_CLAVE, Locutor

# Cuánto sigue escuchando tras responder, antes de volver a dormirse. Diez
# segundos dan para pensar la siguiente frase sin dejar el micrófono abierto
# indefinidamente.
ESPERA_DE_CONTINUACION = 10.0

# Decir la palabra clave mientras Jarvis habla lo interrumpe. Solo funciona con
# alguna voz registrada: sin verificar quién la dice, Jarvis podría cortarse a
# sí mismo al oírse decir su nombre por los altavoces.
PERMITIR_INTERRUPCION = os.getenv("JARVIS_INTERRUMPIR", "1") != "0"


class Fase(str, Enum):
    DORMIDO = "dormido"
    ESCUCHANDO = "escuchando"
    PENSANDO = "pensando"
    HABLANDO = "hablando"


@dataclass
class Avisos:
    """Funciones con las que la interfaz se entera de lo que pasa.

    Todas son opcionales: la sesión funciona igual sin ninguna, lo que permite
    probarla sin interfaz.
    """

    cambio_de_fase: Callable[[Fase], None] | None = None
    espectro: Callable[[list[float]], None] | None = None
    texto_del_usuario: Callable[[str], None] | None = None
    texto_de_jarvis: Callable[[str], None] | None = None
    voz_rechazada: Callable[[float], None] | None = None
    pedir_permiso: Callable[[Peticion], bool] | None = None
    aviso: Callable[[str], None] | None = None
    # Se llama cuando hay que cerrar Jarvis del todo, no solo el orbe.
    apagar: Callable[[str], None] | None = None


class SesionDeVoz:
    def __init__(self, agente: Agente, avisos: Avisos | None = None) -> None:
        self.agente = agente
        self.avisos = avisos or Avisos()

        self.oido = Oido()
        self.voz = Voz()
        self.locutor = Locutor()

        from voice.espectro import Analizador

        self._analizador = Analizador(bandas=64)
        self._fase = Fase.DORMIDO
        self._activa = False
        self._en_conversacion = threading.Event()
        # Se activa al cerrar el orbe. Antes cerrarlo solo callaba la voz: el
        # hilo de la conversación seguía grabando, el orbe reaparecía en la
        # siguiente fase y la palabra clave se ignoraba hasta que acabara.
        self._cancelada = threading.Event()
        # Le has cortado diciendo la palabra clave: se calla y te escucha, pero
        # la conversación sigue (a diferencia de cerrar el orbe).
        self._interrumpida = threading.Event()
        # Quién abrió la conversación en curso, para dirigirse a esa persona.
        self._quien_habla = ""
        # La verificación de la voz corre aquí mientras Whisper transcribe:
        # antes iban una detrás de otra y se sumaban sus tiempos.
        self._verificador = ThreadPoolExecutor(max_workers=1, thread_name_prefix="verificacion")
        # Momento en que dejaste de hablar, para medir cuánto tarda en contestar.
        self._fin_de_frase: float | None = None

    # -- Estado --------------------------------------------------------------

    @property
    def fase(self) -> Fase:
        return self._fase

    def _cambiar_fase(self, fase: Fase) -> None:
        # Con la conversación cancelada solo se acepta volver a dormir: si no,
        # el orbe que acabas de cerrar volvería a abrirse solo.
        if self._cancelada.is_set() and fase is not Fase.DORMIDO:
            return
        self._fase = fase
        if self.avisos.cambio_de_fase:
            self.avisos.cambio_de_fase(fase)

    def _emitir_espectro(self, audio: np.ndarray) -> None:
        if self.avisos.espectro:
            self.avisos.espectro(self._analizador.analizar(audio))

    def _avisar(self, texto: str) -> None:
        if self.avisos.aviso:
            self.avisos.aviso(texto)

    # -- Arranque ------------------------------------------------------------

    def empezar_a_escuchar(self) -> None:
        """Carga SOLO el detector de la palabra clave y arranca la escucha.

        Se separa del resto a propósito. El detector pesa poco más de un mega y
        carga en un segundo; Whisper y la voz tardan casi cinco más. Cargarlo
        todo antes de escuchar dejaba a Jarvis sordo durante ese rato justo
        después de arrancar, que es cuando más probable es que le hables.

        Lo demás se carga después: hasta que no digas algo no hace falta ni
        transcribir ni hablar.
        """
        self.oido._obtener_detector()
        self.arrancar()

    def preparar_el_resto(self) -> str:
        """Carga la voz, la transcripción y el verificador con la escucha en marcha.

        Todo lo que se cargue aquí no se paga en la primera pregunta. El
        verificador de voz tardaba varios segundos y se cargaba al decir la
        palabra clave por primera vez, con el micrófono desatendido mientras.
        """
        motor = self.voz.preparar()
        try:
            self.voz.calentar()
        except Exception:
            import traceback

            traceback.print_exc()
        self.oido._obtener_transcriptor()
        if self.locutor.configurado:
            try:
                self.locutor._obtener_codificador()
            except Exception:
                import traceback

                traceback.print_exc()
        return motor

    def preparar(self) -> str:
        """Carga todo de una vez. Se mantiene para pruebas y para main.py."""
        motor = self.voz.preparar()
        self.oido.precargar()
        return motor

    def arrancar(self) -> None:
        """Empieza a escuchar la palabra clave."""
        if self._activa:
            return
        self._activa = True
        self._cambiar_fase(Fase.DORMIDO)
        self.oido.escuchar(al_detectar=self._al_detectar_palabra, al_fallar=self._avisar)

    def parar(self) -> None:
        self._activa = False
        self._cancelada.set()
        self.voz.callar()
        self.oido.parar()
        self._cambiar_fase(Fase.DORMIDO)

    def detener_conversacion(self) -> None:
        """Corta la conversación en curso, pero sigue esperando la palabra clave."""
        self._cancelada.set()
        self.voz.callar()
        self._cambiar_fase(Fase.DORMIDO)

    # -- Detección -----------------------------------------------------------

    def _al_detectar_palabra(self, deteccion: Deteccion) -> None:
        """Se llama desde el hilo de escucha cuando suena "hey jarvis"."""
        # Si ya hay una conversación en marcha, la activación se ignora: no
        # tiene sentido abrir otra encima.
        if self._en_conversacion.is_set():
            if self._puede_interrumpirse():
                resultado = self.locutor.identificar(
                    deteccion.audio_previo, es_palabra_clave=True
                )
                if resultado.es_el_usuario:
                    print(
                        f"[voz] Interrumpido por {resultado.nombre or 'el usuario'} "
                        f"(parecido {resultado.parecido:.3f})",
                        flush=True,
                    )
                    self.interrumpir()
                    return
            # Se registra: si una conversación anterior se quedara colgada sin
            # cerrarse, esta línea repitiéndose sería la única pista de por qué
            # Jarvis deja de responder a la palabra clave.
            print(
                "[voz] Activación ignorada: ya hay una conversación abierta "
                f"(confianza {deteccion.confianza:.3f})",
                flush=True,
            )
            return

        resultado = self.locutor.identificar(
            deteccion.audio_previo, es_palabra_clave=True
        )
        if not resultado.es_el_usuario:
            # Se rechaza sin decir nada: si Jarvis contestara "no te reconozco",
            # sería molesto cada vez que la tele dice algo parecido. Pero queda
            # anotado, porque un rechazo invisible y sin rastro es imposible de
            # diagnosticar: así se ve si te está rechazando a ti y por cuánto.
            print(
                f"[voz] Activación rechazada: parecido {resultado.parecido:.3f}, "
                f"umbral {self.locutor.umbral:.3f} "
                f"(confianza del wake word {deteccion.confianza:.3f})",
                flush=True,
            )
            if self.avisos.voz_rechazada:
                self.avisos.voz_rechazada(resultado.parecido)
            return

        self._quien_habla = resultado.nombre
        print(
            f"[voz] Activación ACEPTADA ({resultado.nombre or 'sin identificar'}): "
            f"parecido {resultado.parecido:.3f}, "
            f"listón {self.locutor.umbral - REBAJA_EN_LA_PALABRA_CLAVE:.3f} "
            f"(confianza del wake word {deteccion.confianza:.3f})",
            flush=True,
        )
        self._iniciar_conversacion()

    def _puede_interrumpirse(self) -> bool:
        return (
            PERMITIR_INTERRUPCION
            and self._fase is Fase.HABLANDO
            and self.locutor.configurado
        )

    def interrumpir(self) -> None:
        """Le corta mientras habla y pasa a escucharte, sin cerrar el orbe."""
        self._interrumpida.set()
        self.voz.callar()

    def abrir_conversacion(self, quien: str = "") -> bool:
        """Abre el orbe y empieza a escuchar sin esperar a la palabra clave.

        Se usa desde el menú de la bandeja. No se verifica la voz aquí, y es
        correcto: has pulsado tú la opción con el ratón, y eso ya demuestra que
        estás delante del equipo mejor de lo que puede hacerlo una huella vocal.
        La verificación existe para filtrar quién puede activarlo HABLANDO.

        Devuelve False si ya había una conversación abierta.
        """
        if not self._activa or self._en_conversacion.is_set():
            return False
        # Sin nombre se deja vacío: al abrirlo con el ratón no se sabe quién
        # es, y la petición completa lo identificará igualmente.
        self._quien_habla = quien
        self._iniciar_conversacion()
        return True

    def _iniciar_conversacion(self) -> None:
        self._cancelada.clear()
        self._en_conversacion.set()
        threading.Thread(target=self._conversar, daemon=True).start()

    # -- Conversación --------------------------------------------------------

    def _conversar(self) -> None:
        """Lleva una conversación completa hasta que te despides o callas."""
        try:
            while self._activa and not self._cancelada.is_set():
                peticion = self._escuchar_peticion()

                if peticion is None or self._cancelada.is_set():
                    # Silencio: se entiende que ya no hay nada más.
                    break

                # El apagado se comprueba ANTES que la despedida, porque
                # "apágate" también encaja con algún patrón de despedida y la
                # diferencia importa: cerrar deja a Jarvis escuchando, apagar
                # termina el proceso.
                if es_apagado(peticion):
                    self._apagarse("Lo pediste por voz.")
                    return

                if es_despedida(peticion):
                    self._despedirse()
                    break

                if not self._responder(peticion):
                    # El modelo cerró la conversación por su cuenta.
                    break
        except Exception as e:
            # El error se DICE en voz alta, no solo se anota. Antes iba a la
            # ventana de chat, que está escondida: desde fuera Jarvis parecía
            # cerrarse sin motivo a mitad de la petición y no había forma de
            # saber qué había pasado.
            import traceback

            traceback.print_exc()
            self._avisar(f"Error en la conversación: {e}")
            self._decir_el_problema(e)
        finally:
            # El altavoz se deja abierto entre frases de una misma conversación;
            # al terminarla se libera.
            try:
                self.voz.cerrar_audio()
            except Exception:
                pass
            self._en_conversacion.clear()
            self._cambiar_fase(Fase.DORMIDO)

    def _escuchar_peticion(self) -> str | None:
        """Graba y transcribe. Devuelve None si no dijiste nada."""
        self._cambiar_fase(Fase.ESCUCHANDO)
        audio = self.oido.grabar_peticion(
            al_recibir_audio=self._emitir_espectro, cancelar=self._cancelada
        )

        if audio.size == 0:
            return None

        self._fin_de_frase = time.perf_counter()
        self._cambiar_fase(Fase.PENSANDO)

        # Verificación seria, con la petición completa: dura varios segundos y
        # ahí sí se distinguen las voces. La de la palabra clave solo decidía
        # si abrir el orbe. Corre a la vez que la transcripción; si resulta no
        # ser tu voz, lo transcrito se tira sin usarlo.
        verificacion = (
            self._verificador.submit(self.locutor.identificar, audio)
            if self.locutor.configurado
            else None
        )

        texto = self.oido.transcribir(audio)

        if verificacion is not None:
            comprobacion = verificacion.result()
            if comprobacion.es_conocido and comprobacion.nombre:
                # La petición completa identifica mejor que el "hey ...", así
                # que si aquí se reconoce a otra persona, manda esta.
                self._quien_habla = comprobacion.nombre
            if not comprobacion.es_conocido:
                print(
                    f"[voz] Petición descartada: no es tu voz "
                    f"(parecido {comprobacion.parecido:.3f}, "
                    f"umbral {self.locutor.umbral:.3f})",
                    flush=True,
                )
                return None

        if not texto:
            return None

        if self.avisos.texto_del_usuario:
            self.avisos.texto_del_usuario(texto)
        return texto

    def _responder(self, peticion: str) -> bool:
        """Contesta. Devuelve False si hay que cerrar la conversación."""
        self._cambiar_fase(Fase.PENSANDO)
        self._interrumpida.clear()

        # La respuesta se va diciendo según llega, frase a frase, en lugar de
        # esperar a tenerla entera. Ver voice/frases.py para los frenos.
        hablador = HabladorPorFrases(
            decir=lambda frase: self.voz.decir(frase, al_generar_audio=self._emitir_espectro),
            al_empezar=self._al_empezar_a_hablar,
            cancelado=lambda: self._cancelada.is_set() or self._interrumpida.is_set(),
        )

        try:
            resultado = self.agente.responder(
                peticion,
                pedir_confirmacion=self.avisos.pedir_permiso,
                quien_habla=self._quien_habla,
                al_recibir_texto=hablador.recibir,
                al_usar_herramienta=hablador.nueva_vuelta,
            )
        except Exception:
            # Lo que ya estuviera sonando termina antes de explicar el error.
            hablador.esperar()
            raise
        texto = resultado.texto

        # El modelo puede cerrar la conversación o apagar Jarvis llamando a
        # sus herramientas, que dejan estas marcas. Se quitan antes de leer el
        # texto en voz alta.
        apagar = MARCA_DE_APAGADO in texto
        cerrar = MARCA_DE_CIERRE in texto
        texto = texto.replace(MARCA_DE_CIERRE, "").replace(MARCA_DE_APAGADO, "").strip()

        if self.avisos.texto_de_jarvis:
            self.avisos.texto_de_jarvis(texto)

        # Cerraste el orbe mientras pensaba: la respuesta queda escrita en la
        # ventana de chat, pero no se lee en voz alta.
        if self._cancelada.is_set():
            hablador.esperar()
            return False

        hablador.terminar(texto)
        self.voz.terminar_de_sonar()

        # Si le cortaste, lo que quedaba por decir se queda en la ventana de
        # chat y la conversación sigue: lo siguiente es escucharte.
        # Un apagado que pediste se respeta aunque le cortes a media despedida.
        if self._interrumpida.is_set() and not apagar:
            self._interrumpida.clear()
            return True

        if apagar:
            self._apagarse("Lo pidió el modelo tras tu petición.")
            return False

        return not cerrar

    def _al_empezar_a_hablar(self) -> None:
        self._cambiar_fase(Fase.HABLANDO)
        if self._fin_de_frase is not None:
            espera = time.perf_counter() - self._fin_de_frase
            print(f"[voz] Empezó a contestar {espera:.2f} s después de que callaras.", flush=True)
            self._fin_de_frase = None

    def _hablar(self, texto: str) -> None:
        if not texto:
            return
        self._al_empezar_a_hablar()
        self.voz.decir(texto, al_generar_audio=self._emitir_espectro)
        self.voz.terminar_de_sonar()

    def decir_aviso(self, texto: str) -> None:
        """Dice un aviso que no es respuesta a nada, como un recordatorio.

        Bloquea mientras habla, así que se llama desde un hilo propio.
        """
        if self.avisos.texto_de_jarvis:
            self.avisos.texto_de_jarvis(texto)
        self.voz.decir(texto, al_generar_audio=self._emitir_espectro)
        self.voz.terminar_de_sonar()
        if not self._en_conversacion.is_set():
            self.voz.cerrar_audio()

    def _decir_el_problema(self, error: Exception) -> None:
        """Explica en voz alta por qué no ha podido, con lenguaje llano."""
        texto = str(error)

        if "No hay ningún modelo disponible" in texto or "Ollama" in texto:
            aviso = (
                "No he podido pensar la respuesta porque el modelo no está "
                "disponible. Comprueba que Ollama esté arrancado."
            )
        else:
            aviso = "Ha habido un error y no he podido completar la petición."

        if self.avisos.texto_de_jarvis:
            self.avisos.texto_de_jarvis(aviso)
        try:
            self._hablar(aviso)
        except Exception:
            # Si ni siquiera puede hablar, al menos queda en el registro.
            import traceback

            traceback.print_exc()

    def _apagarse(self, motivo: str) -> None:
        """Dice adiós y cierra Jarvis del todo.

        Se habla ANTES de avisar a la interfaz: si se apagara primero, el
        proceso podría morir a mitad de la frase y no sabrías si te oyó.
        """
        despedida = "Apagando. Hasta la próxima."
        if self.avisos.texto_de_jarvis:
            self.avisos.texto_de_jarvis(despedida)
        self._hablar(despedida)

        self._activa = False
        if self.avisos.apagar:
            self.avisos.apagar(motivo)

    def _despedirse(self) -> None:
        despedida = "Hasta luego."
        if self.avisos.texto_de_jarvis:
            self.avisos.texto_de_jarvis(despedida)
        self._hablar(despedida)

    # -- Registro de voz -----------------------------------------------------

    def registrar_voz(
        self, nombre: str, al_pedir_frase: Callable[[str, int, int], None]
    ) -> tuple[bool, str]:
        """Graba tus frases y guarda tu huella vocal.

        'al_pedir_frase' recibe la frase a leer, cuál es y cuántas hay, para
        que la interfaz pueda ir enseñándolas.
        """
        from voice.locutor import FRASES_SUGERIDAS

        grabaciones: list[np.ndarray] = []
        for i, frase in enumerate(FRASES_SUGERIDAS, 1):
            al_pedir_frase(frase, i, len(FRASES_SUGERIDAS))
            # Pausa para que te dé tiempo a leerla antes de que empiece.
            time.sleep(0.6)
            audio = self.oido.grabar_peticion(al_recibir_audio=self._emitir_espectro)
            if audio.size > 0:
                grabaciones.append(audio)

        if len(grabaciones) < 2:
            return False, "No se grabaron suficientes frases. Inténtalo de nuevo."

        try:
            self.locutor.registrar(nombre, grabaciones)
        except ValueError as e:
            return False, str(e)

        registrados = ", ".join(self.locutor.nombres)
        return True, (
            f"Voz de {nombre} registrada con {len(grabaciones)} frases.\n\n"
            f"Voces reconocidas: {registrados}."
        )
