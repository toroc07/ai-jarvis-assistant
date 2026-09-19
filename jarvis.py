"""
Jarvis como aplicación de escritorio con voz.

    pythonw jarvis.py     (sin ventana de consola detrás)
    python jarvis.py      (con consola, útil para ver errores)

Normalmente Jarvis no se ve: vive en la bandeja del sistema escuchando. Al
decir "hey Jarvis" con tu voz aparece el orbe, que se mueve con el sonido
mientras habláis, y se va cuando te despides.

La ventana de chat sigue existiendo para escribir en vez de hablar, pero está
escondida salvo que la pidas desde el icono de la bandeja.
"""

from __future__ import annotations

import signal
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))


def _asegurar_salida() -> None:
    """Da a Jarvis una salida de texto real cuando corre sin consola.

    Con pythonw.exe no hay consola, así que sys.stdout y sys.stderr valen None.
    Cualquier librería que imprima algo revienta con
    "'NoneType' object has no attribute 'write'", y eso ocurre en el peor
    momento: tqdm dibuja una barra de progreso al descargar el modelo de
    verificación de voz, y el registro de voz fallaba entero por eso.

    Se redirige a un archivo en lugar de descartarlo, porque sin consola ese
    archivo es la única forma de ver qué pasó cuando algo falla.
    """
    if sys.stdout is not None and sys.stderr is not None:
        return

    registro = RAIZ / "data" / "jarvis.log"
    registro.parent.mkdir(parents=True, exist_ok=True)
    # line_buffering para que lo escrito aparezca aunque el proceso muera antes
    # de cerrar el archivo, que es justo cuando más falta hace leerlo.
    destino = open(registro, "a", encoding="utf-8", buffering=1, errors="replace")

    if sys.stdout is None:
        sys.stdout = destino
    if sys.stderr is None:
        sys.stderr = destino


_asegurar_salida()

from dotenv import load_dotenv  # noqa: E402

# Se carga antes que nada para que ANTHROPIC_API_KEY esté disponible cuando el
# cerebro compruebe qué motores tiene.
load_dotenv(RAIZ / ".env")

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QMessageBox,
    QSystemTrayIcon,
)

from core.agent import NOMBRE_ASISTENTE, Agente  # noqa: E402
from ui.bandeja import Bandeja  # noqa: E402
from ui.controlador_voz import ControladorVoz  # noqa: E402
from ui.registro_voz import DialogoRegistroVoz  # noqa: E402
from ui.ventana import Ventana  # noqa: E402


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(NOMBRE_ASISTENTE)
    # Sin esto, Qt cerraría el programa al esconderse la última ventana, que es
    # justo lo contrario de quedarse en segundo plano escuchando.
    app.setQuitOnLastWindowClosed(False)

    try:
        agente = Agente()
    except Exception as e:
        QMessageBox.critical(None, "Jarvis", f"No se pudo arrancar:\n\n{e}")
        return 1

    ventana = Ventana(agente)
    voz = ControladorVoz(agente, ventana)

    # Lo que pase por voz se escribe también en la ventana de chat, para que
    # quede historial de la conversación aunque no la estés mirando.
    voz.dijo_el_usuario.connect(ventana.anotar_del_usuario)
    voz.dijo_jarvis.connect(ventana.anotar_de_jarvis)
    voz.aviso.connect(ventana.anotar_aviso)
    def apagar(motivo: str) -> None:
        """Cierra Jarvis del todo, a diferencia de esconder la ventana.

        Cerrar la ventana deja la sesión en espera en segundo plano; esto la
        termina y cierra el proceso, así que para volver hay que ejecutarlo de
        nuevo. Se anota el cierre para que la próxima sea una sesión nueva.
        """
        agente.memoria.cerrar_sesion(agente.sesion, motivo)
        voz.parar()
        ventana.saliendo = True
        app.quit()

    voz.apagado_pedido.connect(apagar)

    # Ctrl+C en la terminal apaga Jarvis igual que hacerlo desde la bandeja.
    # Hace falta el temporizador: mientras Qt tiene el control, el intérprete de
    # Python no ejecuta nada suyo y la señal se queda esperando sin atenderse.
    # Un latido cada 300 ms le devuelve el control lo justo para procesarla.
    signal.signal(
        signal.SIGINT, lambda *_: apagar("Lo apagaste con Ctrl+C en la terminal.")
    )
    latido = QTimer()
    latido.start(300)
    latido.timeout.connect(lambda: None)
    app.latido = latido  # type: ignore[attr-defined]
    voz.escuchando.connect(
        lambda: ventana.anotar_aviso(
            f"Ya te escucho: di «{__import__('voice.escucha', fromlist=['x']).nombre_de_la_palabra()}»."
        )
    )
    voz.preparada.connect(
        lambda motor: ventana.anotar_aviso(f"Voz y transcripción listas ({motor}).")
    )

    if not QSystemTrayIcon.isSystemTrayAvailable():
        # Sin bandeja no hay segundo plano posible, así que se avisa y se sigue
        # como aplicación normal en lugar de fallar.
        QMessageBox.warning(
            None,
            "Jarvis",
            "Este sistema no tiene bandeja disponible. Jarvis funcionará, pero "
            "se cerrará del todo al cerrar la ventana.",
        )
        ventana.saliendo = True
        app.setQuitOnLastWindowClosed(True)
        ventana.show()
    else:
        bandeja = Bandeja(ventana, voz)
        bandeja.show()
        # Se guarda en la app para que el recolector de basura no se lleve el
        # icono y desaparezca de la barra de tareas.
        app.bandeja = bandeja  # type: ignore[attr-defined]

        # Si aún no sabe tu voz, se ofrece configurarla: sin huella responde a
        # cualquiera, que es justo lo que no quieres.
        if not voz.voz_configurada:
            respuesta = QMessageBox.question(
                None,
                "Jarvis",
                "Jarvis aún no conoce tu voz, así que responderá a cualquiera "
                "que diga «hey Jarvis».\n\n¿Quieres enseñársela ahora? Son "
                "cinco frases y lleva un minuto.",
            )
            if respuesta == QMessageBox.Yes:
                DialogoRegistroVoz(voz.sesion, ventana).exec()

    voz.arrancar()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
