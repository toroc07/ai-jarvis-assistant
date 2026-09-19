"""
El botón de pánico y el diálogo para rearmarlo.

El botón está siempre visible en la ventana de chat, en rojo y sin pedir
confirmación: un interruptor de emergencia que pregunta "¿seguro?" no sirve
para una emergencia. Pulsarlo detiene a Jarvis en el acto.

Rearmarlo sí pide confirmación, y enseña por qué se detuvo, porque ahí el
riesgo va en la otra dirección: volver a soltarlo sin mirar qué pasó.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from security.parada import interruptor

ESTILO_ARMADO = """
QPushButton {
    background: #7a1f1f; border: 1px solid #a83232; border-radius: 8px;
    padding: 9px 14px; font-size: 13px; font-weight: 700; color: #ffd9d9;
}
QPushButton:hover { background: #a02828; border-color: #d04040; }
"""

ESTILO_DETENIDO = """
QPushButton {
    background: #c0392b; border: 1px solid #ff6b6b; border-radius: 8px;
    padding: 9px 14px; font-size: 13px; font-weight: 700; color: white;
}
QPushButton:hover { background: #d94436; }
"""


class BotonDeParada(QPushButton):
    """Botón rojo que detiene a Jarvis, y lo rearma cuando está detenido."""

    def __init__(self, padre=None) -> None:
        super().__init__(padre)
        self.clicked.connect(self._pulsado)
        interruptor.al_cambiar(lambda *_: self._refrescar())
        self._refrescar()

    def _refrescar(self) -> None:
        if interruptor.activado:
            self.setText("DETENIDO — pulsa para reactivar")
            self.setStyleSheet(ESTILO_DETENIDO)
            self.setToolTip(interruptor.explicacion())
        else:
            self.setText("PARADA DE EMERGENCIA")
            self.setStyleSheet(ESTILO_ARMADO)
            self.setToolTip(
                "Detiene a Jarvis al instante: dejará de ejecutar cualquier "
                "acción sobre tu equipo.\nTambién con Ctrl+Alt+J desde "
                "cualquier parte."
            )

    def _pulsado(self) -> None:
        if interruptor.activado:
            self._rearmar()
        else:
            # Sin confirmación a propósito: si hay que parar, hay que parar ya.
            interruptor.activar("Lo detuviste desde el botón de emergencia.")

    def _rearmar(self) -> None:
        dialogo = DialogoRearmar(self.window())
        if dialogo.exec() == QDialog.Accepted:
            interruptor.rearmar()


class DialogoRearmar(QDialog):
    """Enseña por qué se detuvo antes de dejar que Jarvis vuelva a actuar."""

    def __init__(self, padre=None) -> None:
        super().__init__(padre)
        self.setWindowTitle("Reactivar Jarvis")
        self.setMinimumWidth(480)
        self.setModal(True)

        disposicion = QVBoxLayout(self)
        disposicion.setSpacing(14)
        disposicion.setContentsMargins(22, 20, 22, 18)

        titulo = QLabel("Jarvis está detenido")
        titulo.setStyleSheet("font-size: 16px; font-weight: 600; color: #ff8080;")
        disposicion.addWidget(titulo)

        explicacion = QLabel(interruptor.explicacion())
        explicacion.setWordWrap(True)
        explicacion.setTextInteractionFlags(Qt.TextSelectableByMouse)
        explicacion.setStyleSheet(
            "background: rgba(255,80,80,0.08); border-radius: 6px; padding: 12px;"
        )
        disposicion.addWidget(explicacion)

        aviso = QLabel(
            "Si Jarvis se detuvo solo, conviene mirar security/audit.log antes "
            "de reactivarlo: ahí está qué intentó hacer exactamente."
        )
        aviso.setWordWrap(True)
        aviso.setStyleSheet("color: #9aa0a6;")
        disposicion.addWidget(aviso)

        botones = QDialogButtonBox()
        cancelar = botones.addButton("Seguir detenido", QDialogButtonBox.RejectRole)
        reactivar = botones.addButton("Reactivar", QDialogButtonBox.AcceptRole)

        # Por defecto, seguir detenido: reactivar debe ser deliberado.
        cancelar.setDefault(True)
        reactivar.setAutoDefault(False)

        botones.accepted.connect(self.accept)
        botones.rejected.connect(self.reject)
        disposicion.addWidget(botones)
