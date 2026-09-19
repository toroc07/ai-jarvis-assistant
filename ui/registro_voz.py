"""
El asistente que aprende tu voz.

Te pide leer cinco frases, extrae la huella de cada una y guarda la media. Las
frases son las que dirías de verdad, no trabalenguas, porque la huella debe
recoger cómo suenas al usar Jarvis y no al recitar.
"""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal, Slot
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)


class Registrador(QThread):
    """Graba las frases sin congelar el diálogo."""

    frase_pedida = Signal(str, int, int)
    terminado = Signal(bool, str)

    def __init__(self, sesion, nombre: str) -> None:
        super().__init__()
        self.sesion = sesion
        self.nombre = nombre

    def run(self) -> None:
        try:
            ok, mensaje = self.sesion.registrar_voz(
                self.nombre,
                al_pedir_frase=lambda f, i, n: self.frase_pedida.emit(f, i, n),
            )
            self.terminado.emit(ok, mensaje)
        except Exception as e:
            self.terminado.emit(False, f"Error al registrar la voz: {e}")


class DialogoRegistroVoz(QDialog):
    def __init__(self, sesion, padre=None) -> None:
        super().__init__(padre)
        self.sesion = sesion
        self.registrador: Registrador | None = None

        self.setWindowTitle("Registrar una voz")
        self.setMinimumWidth(480)

        disposicion = QVBoxLayout(self)
        disposicion.setSpacing(16)
        disposicion.setContentsMargins(24, 22, 24, 20)

        titulo = QLabel("Registrar una voz")
        titulo.setStyleSheet("font-size: 16px; font-weight: 600;")
        disposicion.addWidget(titulo)

        self.explicacion = QLabel(
            "Escribe tu nombre y lee cinco frases en voz alta. Con ellas se "
            "aprende cómo suena tu voz, para reconocerte y llamarte por tu "
            "nombre.\n\n"
            "Habla como hablas normalmente, a la distancia habitual del "
            "micrófono y en un sitio sin ruido. Cada frase se graba sola: "
            "empieza a leer cuando aparezca y calla al terminar.\n\n"
            "Esto evita que lo activen personas no registradas o la televisión, "
            "pero no es una contraseña: una grabación podría engañarlo. Por eso "
            "las acciones que tocan el equipo se seguirán preguntando."
        )
        self.explicacion.setWordWrap(True)
        self.explicacion.setStyleSheet("color: #9aa0a6;")
        disposicion.addWidget(self.explicacion)

        self.nombre = QLineEdit()
        self.nombre.setPlaceholderText("Tu nombre, por ejemplo: Carlos")
        self.nombre.setStyleSheet(
            "background: #22242a; border: 1px solid #32353d; border-radius: 8px; "
            "padding: 10px 12px; font-size: 14px;"
        )
        self.nombre.returnPressed.connect(self.empezar)
        disposicion.addWidget(self.nombre)

        self.frase = QLabel("")
        self.frase.setWordWrap(True)
        self.frase.setStyleSheet(
            "font-size: 19px; font-weight: 600; color: #5aa87a; "
            "background: rgba(61,143,99,0.10); border-radius: 8px; padding: 18px;"
        )
        self.frase.setVisible(False)
        disposicion.addWidget(self.frase)

        self.progreso = QProgressBar()
        self.progreso.setVisible(False)
        self.progreso.setTextVisible(False)
        disposicion.addWidget(self.progreso)

        self.boton = QPushButton("Empezar")
        self.boton.clicked.connect(self.empezar)
        disposicion.addWidget(self.boton)

    def empezar(self) -> None:
        nombre = self.nombre.text().strip()
        if not nombre:
            self.explicacion.setText(
                "Escribe primero un nombre: es como te llamará y cómo se "
                "distingue tu voz de las demás."
            )
            self.explicacion.setStyleSheet("color: #e8a33d;")
            self.nombre.setFocus()
            return

        self.nombre.setEnabled(False)
        self.boton.setEnabled(False)
        self.boton.setText("Grabando...")
        self.explicacion.setText(
            "Lee cada frase en voz alta en cuanto aparezca. La grabación se "
            "corta sola cuando dejas de hablar."
        )
        self.frase.setVisible(True)
        self.frase.setText("Preparando el micrófono...")
        self.progreso.setVisible(True)
        self.progreso.setRange(0, 0)  # Indeterminado mientras carga el modelo.

        self.registrador = Registrador(self.sesion, nombre)
        self.registrador.frase_pedida.connect(self.mostrar_frase)
        self.registrador.terminado.connect(self.al_terminar)
        self.registrador.start()

    @Slot(str, int, int)
    def mostrar_frase(self, frase: str, indice: int, total: int) -> None:
        self.progreso.setRange(0, total)
        self.progreso.setValue(indice - 1)
        self.frase.setText(f"{indice} de {total}\n\n«{frase}»")

    @Slot(bool, str)
    def al_terminar(self, ok: bool, mensaje: str) -> None:
        self.progreso.setRange(0, 1)
        self.progreso.setValue(1)
        self.frase.setVisible(False)
        self.explicacion.setText(mensaje)
        self.explicacion.setStyleSheet(
            "color: #5aa87a;" if ok else "color: #e8a33d;"
        )
        self.boton.setEnabled(True)
        self.nombre.setEnabled(not ok)
        self.boton.setText("Cerrar" if ok else "Reintentar")
        self.boton.clicked.disconnect()
        self.boton.clicked.connect(self.accept if ok else self.empezar)
