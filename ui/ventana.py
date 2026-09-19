"""
La ventana de chat de Jarvis.

El modelo tarda segundos en responder, así que todo el trabajo ocurre en un
hilo aparte: si corriera en el hilo de la interfaz, la ventana se congelaría
en cada pregunta y Windows la marcaría como "no responde".

Ese hilo tiene un problema propio: cuando una acción necesita tu permiso, el
diálogo debe abrirse en el hilo de la interfaz y el hilo de trabajo tiene que
esperar la respuesta. Se resuelve con una conexión bloqueante de Qt, que es la
forma correcta de pedir algo al hilo principal y esperar a que conteste.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QFont, QKeySequence, QShortcut, QTextCursor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.agent import Agente
from security.guard import Peticion
from security.parada import interruptor
from ui.confirmacion import pedir_confirmacion
from ui.emergencia import BotonDeParada

ESTILO = """
QMainWindow, QWidget { background: #17181c; color: #e6e6e6; }
QTextEdit {
    background: #17181c; border: none; font-size: 14px;
    selection-background-color: #2f6f4f;
}
QLineEdit {
    background: #22242a; border: 1px solid #32353d; border-radius: 9px;
    padding: 11px 14px; font-size: 14px; color: #e6e6e6;
}
QLineEdit:focus { border-color: #3d8f63; }
QPushButton {
    background: #2f6f4f; border: none; border-radius: 9px;
    padding: 11px 20px; font-size: 14px; font-weight: 600; color: white;
}
QPushButton:hover { background: #3d8f63; }
QPushButton:disabled { background: #2a2c33; color: #6b6f78; }
QDialog { background: #1e2026; color: #e6e6e6; }
QDialog QPushButton { padding: 8px 18px; font-weight: 500; }
"""


class Trabajador(QThread):
    """Ejecuta una petición del agente sin bloquear la interfaz."""

    texto_parcial = Signal(str)
    herramienta_usada = Signal(str)
    terminado = Signal(object)
    fallo = Signal(str)
    # Se conecta con BlockingQueuedConnection: al emitirla, este hilo se queda
    # esperando a que el hilo de la interfaz muestre el diálogo y responda.
    permiso_pedido = Signal(object)

    def __init__(self, agente: Agente, peticion: str) -> None:
        super().__init__()
        self.agente = agente
        self.peticion = peticion
        self._permiso_concedido = False

    def _pedir_permiso(self, peticion: Peticion) -> bool:
        self.permiso_pedido.emit(peticion)
        return self._permiso_concedido

    def responder_permiso(self, concedido: bool) -> None:
        """La llama el hilo de la interfaz antes de desbloquear a este."""
        self._permiso_concedido = concedido

    def run(self) -> None:
        try:
            resultado = self.agente.responder(
                self.peticion,
                pedir_confirmacion=self._pedir_permiso,
                al_recibir_texto=self.texto_parcial.emit,
                al_usar_herramienta=self.herramienta_usada.emit,
            )
            self.terminado.emit(resultado)
        except Exception as e:
            self.fallo.emit(str(e))


class Calentador(QThread):
    """Prepara el modelo nada más abrir la ventana.

    Sin esto, los casi 9 segundos que cuesta procesar por primera vez las
    definiciones de las herramientas los pagarías tú en tu primera pregunta.
    Hecho aquí, se pagan mientras lees el saludo.
    """

    listo = Signal(float)

    def __init__(self, agente: Agente) -> None:
        super().__init__()
        self.agente = agente

    def run(self) -> None:
        try:
            self.listo.emit(self.agente.calentar())
        except Exception:
            # Un fallo aquí solo significa que la primera pregunta irá lenta.
            self.listo.emit(0.0)


class Ventana(QMainWindow):
    def __init__(self, agente: Agente) -> None:
        super().__init__()
        self.agente = agente
        self.trabajador: Trabajador | None = None
        self._hubo_texto_en_streaming = False
        # Se pone a True solo al salir de verdad, para distinguir cerrar la
        # ventana (que esconde) de cerrar Jarvis (que termina el proceso).
        self.saliendo = False

        self.setWindowTitle("Jarvis")
        self.resize(720, 560)
        self.setStyleSheet(ESTILO)
        self._construir()
        self._saludar()
        self._calentar()

    # -- Construcción de la interfaz ----------------------------------------

    def _construir(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        disposicion = QVBoxLayout(central)
        disposicion.setContentsMargins(18, 14, 18, 16)
        disposicion.setSpacing(12)

        self.conversacion = QTextEdit()
        self.conversacion.setReadOnly(True)
        self.conversacion.setFont(QFont("Segoe UI", 10))
        disposicion.addWidget(self.conversacion)

        self.estado = QLabel("")
        self.estado.setStyleSheet("color: #7d838d; font-size: 12px;")
        disposicion.addWidget(self.estado)

        fila = QHBoxLayout()
        fila.setSpacing(10)
        self.entrada = QLineEdit()
        self.entrada.setPlaceholderText("Escribe a Jarvis...")
        self.entrada.returnPressed.connect(self.enviar)
        fila.addWidget(self.entrada)

        self.boton = QPushButton("Enviar")
        self.boton.clicked.connect(self.enviar)
        fila.addWidget(self.boton)
        disposicion.addLayout(fila)

        # El botón de emergencia va siempre visible, no escondido en un menú:
        # en una emergencia no se busca por menús.
        self.parada = BotonDeParada(self)
        disposicion.addWidget(self.parada)

        # Ctrl+Alt+J detiene a Jarvis sin tener que buscar la ventana. Es
        # ApplicationShortcut para que funcione desde cualquier ventana suya,
        # incluido el orbe.
        atajo = QShortcut(QKeySequence("Ctrl+Alt+J"), self)
        atajo.setContext(Qt.ApplicationShortcut)
        atajo.activated.connect(self._parada_por_atajo)

        # Escapar esconde la ventana en lugar de cerrarla: Jarvis sigue vivo.
        QShortcut(QKeySequence("Esc"), self, self.hide)

        # Contador de segundos mientras Jarvis trabaja.
        self._segundos = 0
        self._paso = ""
        self._cronometro = QTimer(self)
        self._cronometro.setInterval(1000)
        self._cronometro.timeout.connect(self._tic)

        self.entrada.setFocus()

    def _parada_por_atajo(self) -> None:
        if interruptor.activado:
            return
        interruptor.activar("Lo detuviste con Ctrl+Alt+J.")
        self._escribir_sistema(
            "PARADA DE EMERGENCIA activada. Jarvis no ejecutará ninguna acción "
            "hasta que lo reactives con el botón rojo."
        )
        self.show()
        self.raise_()
        self.activateWindow()

    def _tic(self) -> None:
        self._segundos += 1
        self.estado.setText(f"{self._paso}... {self._segundos} s")

    def _saludar(self) -> None:
        estado = self.agente.cerebro.estado()
        if estado["local_disponible"]:
            motor = f"modelo local {estado['modelo_local']}"
        elif estado["claude_disponible"]:
            motor = f"{estado['modelo_claude']} (sin modelo local)"
        else:
            motor = "ningún modelo disponible"

        extra = " y Claude para lo complejo" if estado["claude_disponible"] else ""
        self._escribir_sistema(f"Jarvis en marcha con {motor}{extra}.")

        if not estado["local_disponible"] and not estado["claude_disponible"]:
            self._escribir_sistema(
                "No hay ningún modelo disponible. Comprueba que Ollama esté "
                "arrancado."
            )
            self.entrada.setEnabled(False)
            self.boton.setEnabled(False)

    def _calentar(self) -> None:
        """Arranca la preparación del modelo en segundo plano."""
        if not self.agente.cerebro.local.disponible():
            return

        self.estado.setText("preparando el modelo...")
        # Se puede escribir mientras tanto: la pregunta esperará su turno.
        self.calentador = Calentador(self.agente)
        self.calentador.listo.connect(self._al_calentar)
        self.calentador.start()

    @Slot(float)
    def _al_calentar(self, segundos: float) -> None:
        if segundos > 0:
            self.estado.setText(f"listo (preparado en {segundos:.0f} s)")
        else:
            self.estado.setText("")

    # -- Escritura en la conversación ---------------------------------------

    def _añadir(self, html: str) -> None:
        self.conversacion.moveCursor(QTextCursor.End)
        self.conversacion.insertHtml(html)
        self.conversacion.moveCursor(QTextCursor.End)
        self.conversacion.ensureCursorVisible()

    def _escribir_sistema(self, texto: str) -> None:
        self._añadir(
            f'<p style="color:#7d838d;font-size:12px;margin:4px 0 12px 0;">'
            f"{texto}</p>"
        )

    def _escribir_usuario(self, texto: str) -> None:
        self._añadir(
            f'<p style="margin:14px 0 4px 0;">'
            f'<span style="color:#5aa87a;font-weight:600;">tú</span><br>'
            f"{self._escapar(texto)}</p>"
        )

    def _empezar_respuesta(self) -> None:
        self._añadir(
            '<p style="margin:12px 0 4px 0;">'
            '<span style="color:#7aa7d8;font-weight:600;">jarvis</span></p>'
        )

    @staticmethod
    def _escapar(texto: str) -> str:
        return (
            texto.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        )

    # -- Anotaciones desde la voz -------------------------------------------
    # Lo que ocurre hablando se escribe también aquí, para que quede historial
    # de la conversación aunque la ventana estuviera escondida.

    @Slot(str)
    def anotar_del_usuario(self, texto: str) -> None:
        self._escribir_usuario(texto)

    @Slot(str)
    def anotar_de_jarvis(self, texto: str) -> None:
        self._empezar_respuesta()
        self._añadir(self._escapar(texto).replace("\n", "<br>"))
        self._escribir_sistema("por voz")

    @Slot(str)
    def anotar_aviso(self, texto: str) -> None:
        self._escribir_sistema(texto)

    # -- Ciclo de una petición ----------------------------------------------

    def enviar(self) -> None:
        peticion = self.entrada.text().strip()
        if not peticion or (self.trabajador and self.trabajador.isRunning()):
            return

        self.entrada.clear()
        self._escribir_usuario(peticion)
        self._bloquear_entrada(True)
        self._hubo_texto_en_streaming = False
        self._empezar_respuesta()

        # Una acción con herramienta necesita dos vueltas al modelo y puede
        # pasar de diez segundos. Sin un contador corriendo no se distingue de
        # que se haya colgado.
        self._paso = "pensando"
        self._cronometro.start()
        self._segundos = 0
        self.estado.setText("pensando... 0 s")

        self.trabajador = Trabajador(self.agente, peticion)
        self.trabajador.texto_parcial.connect(self.al_llegar_texto)
        self.trabajador.herramienta_usada.connect(self.al_usar_herramienta)
        self.trabajador.terminado.connect(self.al_terminar)
        self.trabajador.fallo.connect(self.al_fallar)
        # Bloqueante a propósito: el hilo de trabajo espera tu respuesta.
        self.trabajador.permiso_pedido.connect(
            self.al_pedir_permiso, Qt.BlockingQueuedConnection
        )
        self.trabajador.start()

    @Slot(str)
    def al_llegar_texto(self, trozo: str) -> None:
        """Cada trozo aparece en cuanto se genera, sin esperar al final."""
        if not self._hubo_texto_en_streaming:
            self._paso = "escribiendo"
            self._hubo_texto_en_streaming = True
        self._añadir(self._escapar(trozo).replace("\n", "<br>"))

    @Slot(str)
    def al_usar_herramienta(self, nombre: str) -> None:
        self._paso = f"usando {nombre}"

    @Slot(object)
    def al_pedir_permiso(self, peticion: Peticion) -> None:
        """Se ejecuta en el hilo de la interfaz mientras el otro espera."""
        # Se para el contador: ese tiempo lo gastas tú leyendo, no Jarvis.
        corriendo = self._cronometro.isActive()
        self._cronometro.stop()
        self.estado.setText("esperando tu permiso...")

        # Si la ventana está escondida se trae al frente: una petición de
        # permiso invisible bloquearía a Jarvis sin que supieras por qué.
        if not self.isVisible():
            self.show()
        self.raise_()
        self.activateWindow()

        concedido = pedir_confirmacion(peticion, self)
        if self.trabajador:
            self.trabajador.responder_permiso(concedido)

        if not concedido:
            self._escribir_sistema("Acción no autorizada.")
        if corriendo:
            self._cronometro.start()

    @Slot(object)
    def al_terminar(self, resultado) -> None:
        # Si no hubo streaming (por ejemplo porque respondió Claude, o porque
        # el texto venía junto a llamadas a herramientas), se escribe entero.
        if not self._hubo_texto_en_streaming:
            self._añadir(self._escapar(resultado.texto).replace("\n", "<br>"))

        detalle = resultado.motor.value
        if resultado.acciones:
            detalle += " · " + ", ".join(resultado.acciones)
        self._escribir_sistema(detalle)
        self._cronometro.stop()
        self._bloquear_entrada(False)
        self.estado.setText("")

    @Slot(str)
    def al_fallar(self, error: str) -> None:
        self._escribir_sistema(f"Error: {error}")
        self._cronometro.stop()
        self._bloquear_entrada(False)
        self.estado.setText("")

    def _bloquear_entrada(self, bloqueada: bool) -> None:
        self.entrada.setEnabled(not bloqueada)
        self.boton.setEnabled(not bloqueada)
        if not bloqueada:
            self.entrada.setFocus()

    # -- Cierre --------------------------------------------------------------

    def closeEvent(self, evento) -> None:
        """Cerrar la ventana esconde Jarvis; no lo termina.

        Es lo que pediste: que siga en segundo plano para poder activarlo por
        voz. Para salir de verdad se usa el menú del icono de la bandeja.
        """
        if self.saliendo:
            evento.accept()
            return
        evento.ignore()
        self.hide()
