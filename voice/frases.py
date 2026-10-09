"""
Leer la respuesta en voz alta según se genera, frase a frase.

Sin esto Jarvis esperaba a tener la respuesta entera antes de abrir la boca:
con el modelo local eso son varios segundos de silencio en cada pregunta. Aquí
cada frase completa se manda a la voz en cuanto llega.

El riesgo de adelantarse es decir algo que luego el agente habría corregido:
una llamada a herramienta escrita como texto, una marca interna o, sobre todo,
afirmar que se hizo algo que no se hizo. Por eso hay dos frenos:

  - Si aparece algo que no es lenguaje natural ('{', '[', '<', '`'), se deja de
    adelantar en esa vuelta.
  - Si una frase dice haber hecho algo y todavía no se ha ejecutado ninguna
    herramienta, también.

Lo que no se adelantó se dice al final a partir de la respuesta definitiva, que
ya ha pasado por todas las comprobaciones del agente.
"""

from __future__ import annotations

import queue
import re
import threading
from typing import Callable

from core.honestidad import afirma_haber_actuado
from core.texto import limpiar_para_hablar

# Fin de frase: puntuación de cierre seguida de espacio o salto de línea. Un
# punto sin espacio detrás ("3.5", "youtube.com") no corta.
_FIN_DE_FRASE = re.compile(r"(?<=[.!?…])\s+|\n+")

# Lo que delata que el modelo no está hablando para el usuario.
_SENALES_DE_NO_HABLA = ("{", "[", "<", "`")


def partir_en_frases(texto: str) -> tuple[list[str], str]:
    """Separa las frases completas del trozo que aún no ha terminado."""
    partes = _FIN_DE_FRASE.split(texto)
    if not partes:
        return [], ""
    completas = [p.strip() for p in partes[:-1] if p.strip()]
    return completas, partes[-1]


def _normalizar(frase: str) -> str:
    return " ".join(frase.lower().split())


class HabladorPorFrases:
    """Recibe la respuesta a trozos y la va diciendo por frases.

    'decir' es la función que habla (bloquea hasta terminar la frase).
    'al_empezar' se llama justo antes de la primera frase, para cambiar el
    orbe a "hablando" y medir cuánto tardó en empezar.
    'cancelado' indica si cerraste el orbe: a partir de ahí no se dice nada más.
    """

    def __init__(
        self,
        decir: Callable[[str], None],
        al_empezar: Callable[[], None] | None = None,
        cancelado: Callable[[], bool] | None = None,
    ) -> None:
        self._decir = decir
        self._al_empezar = al_empezar
        self._cancelado = cancelado or (lambda: False)

        self._cola: queue.Queue[str | None] = queue.Queue()
        self._hilo: threading.Thread | None = None
        self._empezado = False
        self._lock = threading.Lock()

        self._pendiente = ""
        self._dichas_en_vuelta: list[str] = []
        self._retenido = False
        self._hubo_herramientas = False

    # -- Entrada -------------------------------------------------------------

    def recibir(self, trozo: str) -> None:
        """Un trozo de la respuesta según la genera el modelo."""
        with self._lock:
            if self._retenido or self._cancelado():
                return
            self._pendiente += trozo
            if any(s in self._pendiente for s in _SENALES_DE_NO_HABLA):
                self._retenido = True
                return

            completas, self._pendiente = partir_en_frases(self._pendiente)
            for frase in completas:
                limpia = limpiar_para_hablar(frase)
                if not limpia:
                    continue
                if not self._hubo_herramientas and afirma_haber_actuado(limpia):
                    self._retenido = True
                    return
                self._dichas_en_vuelta.append(limpia)
                self._encolar(limpia)

    def nueva_vuelta(self, _herramienta: str = "") -> None:
        """El modelo pasó a usar herramientas: su próxima respuesta es otra.

        Lo dicho hasta aquí ya no forma parte de la respuesta final, así que se
        olvida para compararla al terminar. Y desde ahora sí hay acciones hechas,
        así que afirmar algo ya no es sospechoso por sí solo.
        """
        with self._lock:
            self._hubo_herramientas = True
            self._pendiente = ""
            self._dichas_en_vuelta = []
            self._retenido = False

    # -- Cierre --------------------------------------------------------------

    def terminar(self, texto_final: str) -> None:
        """Dice lo que falte de la respuesta definitiva y espera a acabar.

        Se saltan las frases del principio que ya se dijeron tal cual. Desde la
        primera que no coincide se dice todo lo demás: si el agente cambió la
        respuesta (por ejemplo, la sustituyó por una disculpa), se oye la buena.
        """
        with self._lock:
            completas, resto = partir_en_frases(limpiar_para_hablar(texto_final))
            frases = completas + ([resto.strip()] if resto.strip() else [])

            saltar = 0
            for dicha, final in zip(self._dichas_en_vuelta, frases):
                if _normalizar(dicha) != _normalizar(final):
                    break
                saltar += 1

            for frase in frases[saltar:]:
                self._encolar(frase)

        self.esperar()

    def esperar(self) -> None:
        """Bloquea hasta que se haya dicho todo lo encolado."""
        if self._hilo is not None:
            self._cola.put(None)
            self._hilo.join()
            self._hilo = None

    @property
    def dijo_algo(self) -> bool:
        return self._empezado

    # -- Interno -------------------------------------------------------------

    def _encolar(self, frase: str) -> None:
        if self._hilo is None:
            self._hilo = threading.Thread(target=self._consumir, daemon=True)
            self._hilo.start()
        self._cola.put(frase)

    def _consumir(self) -> None:
        while True:
            frase = self._cola.get()
            if frase is None:
                return
            if self._cancelado():
                continue
            if not self._empezado:
                self._empezado = True
                if self._al_empezar is not None:
                    self._al_empezar()
            self._decir(frase)
