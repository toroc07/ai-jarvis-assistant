"""
El icono de la bandeja del sistema.

Es lo que mantiene a Jarvis vivo cuando cierras la ventana, que es la base
sobre la que se montará el wake word: sin un proceso de fondo no hay nada que
pueda estar escuchando.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from security.parada import interruptor


def _icono() -> QIcon:
    """Dibuja el icono en memoria, para no depender de un archivo externo.

    Es un anillo con un punto dentro: se distingue bien a 16 píxeles, que es
    el tamaño real al que se ve en la barra de tareas.
    """
    lienzo = QPixmap(64, 64)
    lienzo.fill(Qt.transparent)

    pintor = QPainter(lienzo)
    pintor.setRenderHint(QPainter.Antialiasing)

    pintor.setPen(QPen(QColor("#3d8f63"), 6))
    pintor.setBrush(Qt.NoBrush)
    pintor.drawEllipse(8, 8, 48, 48)

    pintor.setPen(Qt.NoPen)
    pintor.setBrush(QColor("#7aa7d8"))
    pintor.drawEllipse(24, 24, 16, 16)
    pintor.end()

    return QIcon(lienzo)


class Bandeja(QSystemTrayIcon):
    def __init__(self, ventana, voz=None) -> None:
        super().__init__(_icono())
        self.ventana = ventana
        self.voz = voz
        self.setToolTip("Jarvis — di «hey Jarvis» para hablarle")

        menu = QMenu()

        abrir = QAction("Abrir chat escrito", menu)
        abrir.triggered.connect(self.mostrar_ventana)
        menu.addAction(abrir)

        if voz is not None:
            # Abrir el orbe a mano, para hablarle sin decir la palabra clave:
            # útil si estás en una llamada, con ruido, o simplemente no quieres
            # hablarle en alto para activarlo.
            self.accion_orbe = QAction("Hablar con Jarvis", menu)
            self.accion_orbe.triggered.connect(self.abrir_orbe)
            menu.addAction(self.accion_orbe)

        if voz is not None:
            menu.addSeparator()

            self.accion_voz = QAction("Enseñarle mi voz", menu)
            self.accion_voz.triggered.connect(self.registrar_voz)
            menu.addAction(self.accion_voz)

            self.accion_olvidar = QAction("Olvidar mi voz", menu)
            self.accion_olvidar.triggered.connect(self.olvidar_voz)
            menu.addAction(self.accion_olvidar)

            # Con el orbe en pantalla, esta es la salida sin tener que hablar.
            self.accion_cerrar_orbe = QAction("Cerrar el orbe", menu)
            self.accion_cerrar_orbe.triggered.connect(voz.detener_conversacion)
            menu.addAction(self.accion_cerrar_orbe)


        menu.addSeparator()

        # La parada también desde aquí: con la ventana escondida, el botón rojo
        # no está a la vista y este menú sí.
        self.accion_parada = QAction("PARADA DE EMERGENCIA", menu)
        self.accion_parada.triggered.connect(self.parada_de_emergencia)
        menu.addAction(self.accion_parada)

        menu.addSeparator()

        salir = QAction("Apagar Jarvis", menu)
        salir.triggered.connect(self.salir)
        menu.addAction(salir)

        menu.aboutToShow.connect(self._actualizar_menu)
        self.setContextMenu(menu)
        self.activated.connect(self._al_pulsar)

    def abrir_orbe(self) -> None:
        """Empieza a escucharte sin que tengas que decir la palabra clave."""
        if self.voz is None:
            return
        if not self.voz.abrir_conversacion():
            # Ya estaba abierta: se trae al frente en lugar de no hacer nada,
            # que desde fuera parecería que el menú está roto.
            self.voz.orbe.show()
            self.voz.orbe.raise_()

    def parada_de_emergencia(self) -> None:
        if interruptor.activado:
            self.mostrar_ventana()
            return
        interruptor.activar("Lo detuviste desde el menú de la bandeja.")
        self.showMessage(
            "Jarvis detenido",
            "No ejecutará ninguna acción hasta que lo reactives.",
            _icono(),
            6000,
        )

    def _actualizar_menu(self) -> None:
        """El menú refleja el estado real: la voz y la parada."""
        self.accion_parada.setText(
            "DETENIDO — abrir para reactivar"
            if interruptor.activado
            else "PARADA DE EMERGENCIA"
        )
        if self.voz is None:
            return

        # Solo tiene sentido ofrecer lo que se puede hacer ahora mismo.
        abierta = self.voz.conversacion_abierta
        self.accion_orbe.setEnabled(not abierta)
        self.accion_cerrar_orbe.setEnabled(abierta)

        configurada = self.voz.voz_configurada
        self.accion_voz.setText(
            "Volver a enseñarle mi voz" if configurada else "Enseñarle mi voz"
        )
        self.accion_olvidar.setEnabled(configurada)

    def registrar_voz(self) -> None:
        from ui.registro_voz import DialogoRegistroVoz

        DialogoRegistroVoz(self.voz.sesion, self.ventana).exec()

    def olvidar_voz(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        respuesta = QMessageBox.question(
            self.ventana,
            "Olvidar mi voz",
            "Jarvis olvidará tu huella vocal y volverá a responder a cualquier "
            "voz que diga «hey Jarvis».\n\n¿Seguro?",
        )
        if respuesta == QMessageBox.Yes:
            self.voz.sesion.locutor.olvidar()
            self.showMessage(
                "Jarvis", "Huella vocal borrada.", _icono(), 4000
            )

    def _al_pulsar(self, motivo) -> None:
        # Doble clic es el gesto que Windows asocia a "abrir esto".
        if motivo == QSystemTrayIcon.DoubleClick:
            self.mostrar_ventana()

    def mostrar_ventana(self) -> None:
        self.ventana.show()
        self.ventana.raise_()
        self.ventana.activateWindow()

    def salir(self) -> None:
        """Apaga Jarvis del todo, no solo esconde la ventana.

        Se cierra la sesión en la memoria: la próxima vez que lo ejecutes será
        una sesión nueva, con su propio historial de acciones.
        """
        try:
            agente = self.ventana.agente
            agente.memoria.cerrar_sesion(agente.sesion, "Lo apagaste desde la bandeja.")
        except Exception:
            # No poder anotar el cierre no debe impedir apagar.
            pass

        if self.voz is not None:
            self.voz.parar()

        self.ventana.saliendo = True
        self.hide()
        QApplication.quit()
