"""
El interruptor de emergencia.

Una sola función: que Jarvis deje de actuar AHORA, pase lo que pase. Se activa
de tres formas distintas a propósito, porque si una falla tiene que quedar otra:

  - Tú, desde el botón rojo de la ventana o el menú de la bandeja.
  - Tú, con Ctrl+Alt+J desde cualquier parte, sin tener que buscar la ventana.
  - El propio guardián, solo, si detecta que Jarvis insiste en saltarse sus
    límites de seguridad.

CÓMO ESTÁ HECHO Y POR QUÉ ASÍ
El estado es una bandera en memoria más un archivo en disco. La bandera corta
en el acto; el archivo hace que la parada sobreviva a un reinicio, de modo que
si Jarvis se apaga estando parado, al volver sigue parado. Un interruptor de
emergencia que se olvida al reiniciar no es un interruptor de emergencia.

La comprobación vive en el guardián, que es el único camino por el que una
habilidad llega al sistema. Así no hay nada que recordar añadir en cada
habilidad nueva: si el interruptor está activado, ninguna pasa.

Rearmarlo es SIEMPRE manual. Jarvis no puede desactivarse su propia parada, ni
por una herramienta ni pidiéndotelo: eso convertiría el interruptor en una
sugerencia.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
RUTA_PARADA = RAIZ / "data" / "parada.json"


@dataclass
class Parada:
    """Información de una parada de emergencia activa."""

    momento: str
    motivo: str
    quien: str  # "usuario" o "automatica"


class InterruptorDeEmergencia:
    def __init__(self, ruta: Path = RUTA_PARADA) -> None:
        self.ruta = ruta
        # Event de threading porque lo consultan varios hilos a la vez: el de
        # la interfaz, el de voz y el del agente.
        self._activado = threading.Event()
        self._parada: Parada | None = None
        self._avisos: list = []
        self._cargar()

    # -- Estado --------------------------------------------------------------

    @property
    def activado(self) -> bool:
        return self._activado.is_set()

    @property
    def parada(self) -> Parada | None:
        return self._parada

    def _cargar(self) -> None:
        """Recupera una parada anterior al arrancar."""
        if not self.ruta.exists():
            return
        try:
            with open(self.ruta, "r", encoding="utf-8") as f:
                datos = json.load(f)
            self._parada = Parada(**datos)
            self._activado.set()
        except (OSError, json.JSONDecodeError, TypeError):
            # Un archivo corrupto se trata como parada activa, no como ausencia
            # de parada: ante la duda, lo seguro es no actuar.
            self._parada = Parada(
                momento=datetime.now().isoformat(timespec="seconds"),
                motivo="El archivo de parada estaba dañado y no se pudo leer.",
                quien="automatica",
            )
            self._activado.set()

    def _guardar(self) -> None:
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        try:
            if self._parada is None:
                self.ruta.unlink(missing_ok=True)
                return
            with open(self.ruta, "w", encoding="utf-8") as f:
                json.dump(self._parada.__dict__, f, ensure_ascii=False, indent=2)
        except OSError:
            # Si no se puede escribir, la parada sigue activa en memoria. Se
            # pierde al reiniciar, pero no deja de funcionar ahora.
            pass

    # -- Accionamiento -------------------------------------------------------

    def activar(self, motivo: str, quien: str = "usuario") -> Parada:
        """Detiene a Jarvis. Es inmediato y no se puede deshacer solo."""
        self._parada = Parada(
            momento=datetime.now().isoformat(timespec="seconds"),
            motivo=motivo,
            quien=quien,
        )
        self._activado.set()
        self._guardar()
        self._notificar()
        return self._parada

    def rearmar(self) -> None:
        """Vuelve a permitir que Jarvis actúe.

        Solo debe llamarse desde una acción explícita tuya en la interfaz.
        Nunca desde una habilidad, ni desde nada que el modelo pueda invocar.
        """
        self._parada = None
        self._activado.clear()
        self._guardar()
        self._notificar()

    # -- Avisos --------------------------------------------------------------

    def al_cambiar(self, funcion) -> None:
        """Registra una función que se llama cuando el interruptor cambia."""
        self._avisos.append(funcion)

    def _notificar(self) -> None:
        for funcion in list(self._avisos):
            try:
                funcion(self.activado, self._parada)
            except Exception:
                # Un aviso que falle no debe impedir que la parada surta efecto.
                pass

    # -- Explicación ---------------------------------------------------------

    def explicacion(self) -> str:
        """Texto para mostrarte por qué Jarvis está detenido."""
        if not self.activado or self._parada is None:
            return "Jarvis funciona con normalidad."

        origen = (
            "Lo detuviste tú"
            if self._parada.quien == "usuario"
            else "Jarvis se detuvo solo"
        )
        return (
            f"{origen} el {self._parada.momento}.\n\n"
            f"Motivo: {self._parada.motivo}\n\n"
            "Mientras esté detenido no ejecutará ninguna acción sobre tu "
            "equipo. Seguirá conversando, pero sin tocar nada."
        )


interruptor = InterruptorDeEmergencia()
