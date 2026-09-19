"""
El diálogo que te pide permiso.

Es la única forma que tiene Jarvis de ejecutar acciones que modifican tu
sistema, así que está escrito para que sepas exactamente qué estás aprobando:
enseña la acción, sobre qué actúa y para qué, sin resumirlo ni suavizarlo.

Dos decisiones deliberadas:
  - El botón por defecto es "No". Si apruebas por inercia dándole al intro,
    la respuesta es que no.
  - Las acciones destructivas se marcan en rojo y con aviso explícito, porque
    no deberían parecerse a las inofensivas.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QLabel,
    QVBoxLayout,
)

from security.guard import Peticion

# Acciones que quitan o sobrescriben algo. Se avisan de forma distinta porque
# el coste de aprobarlas sin leer no es el mismo.
ACCIONES_DESTRUCTIVAS = {
    "borrar_archivo",
    "escribir_archivo",
    "mover_archivo",
    "ejecutar_comando",
}

# Cómo se llama cada acción en un español que se entienda de un vistazo.
NOMBRES = {
    "escribir_archivo": "Escribir en un archivo",
    "crear_archivo": "Crear un archivo",
    "borrar_archivo": "Retirar un archivo",
    "mover_archivo": "Mover un archivo",
    "copiar_archivo": "Copiar un archivo",
    "crear_carpeta": "Crear una carpeta",
    "ejecutar_comando": "Ejecutar un comando",
    "domotica": "Controlar un dispositivo de casa",
}


class DialogoConfirmacion(QDialog):
    def __init__(self, peticion: Peticion, padre=None) -> None:
        super().__init__(padre)
        self.peticion = peticion
        self.destructiva = peticion.accion in ACCIONES_DESTRUCTIVAS

        self.setWindowTitle("Jarvis pide permiso")
        self.setMinimumWidth(460)
        # Modal: no se puede seguir usando la ventana sin contestar, para que
        # una petición de permiso no se quede olvidada por detrás.
        self.setModal(True)

        disposicion = QVBoxLayout(self)
        disposicion.setSpacing(14)
        disposicion.setContentsMargins(22, 20, 22, 18)

        titulo = QLabel(NOMBRES.get(peticion.accion, peticion.accion))
        titulo.setStyleSheet("font-size: 15px; font-weight: 600;")
        disposicion.addWidget(titulo)

        if peticion.objetivo:
            disposicion.addWidget(self._recuadro(peticion.objetivo))

        if peticion.motivo:
            motivo = QLabel(peticion.motivo)
            motivo.setWordWrap(True)
            motivo.setStyleSheet("color: #9aa0a6;")
            disposicion.addWidget(motivo)

        if self.destructiva:
            aviso = QLabel(self._texto_del_aviso())
            aviso.setWordWrap(True)
            aviso.setStyleSheet(
                "color: #e8a33d; background: rgba(232,163,61,0.10); "
                "border-radius: 6px; padding: 9px 11px;"
            )
            disposicion.addWidget(aviso)

        botones = QDialogButtonBox()
        self.boton_no = botones.addButton("No permitir", QDialogButtonBox.RejectRole)
        self.boton_si = botones.addButton("Permitir", QDialogButtonBox.AcceptRole)

        # El foco arranca en "No": aprobar tiene que ser un acto deliberado.
        self.boton_no.setDefault(True)
        self.boton_no.setAutoDefault(True)
        self.boton_si.setAutoDefault(False)

        botones.accepted.connect(self.accept)
        botones.rejected.connect(self.reject)
        disposicion.addWidget(botones)

    def _recuadro(self, texto: str) -> QFrame:
        """El objetivo, en monoespaciado y sin recortar, para poder leerlo."""
        marco = QFrame()
        marco.setFrameShape(QFrame.NoFrame)
        marco.setStyleSheet(
            "background: rgba(255,255,255,0.05); border-radius: 6px;"
        )
        interior = QVBoxLayout(marco)
        interior.setContentsMargins(11, 9, 11, 9)

        etiqueta = QLabel(texto)
        etiqueta.setWordWrap(True)
        etiqueta.setTextInteractionFlags(Qt.TextSelectableByMouse)
        etiqueta.setStyleSheet("font-family: Consolas, monospace; font-size: 12px;")
        interior.addWidget(etiqueta)
        return marco

    def _texto_del_aviso(self) -> str:
        if self.peticion.accion == "borrar_archivo":
            return (
                "El archivo se moverá a la papelera de Jarvis, no se borrará "
                "del disco. Podrás recuperarlo."
            )
        if self.peticion.accion == "escribir_archivo":
            return (
                "Si el archivo ya existe, se guardará una copia del contenido "
                "anterior antes de reemplazarlo."
            )
        if self.peticion.accion == "ejecutar_comando":
            return "Se ejecutará este comando en tu equipo. Léelo antes de aceptar."
        return "Esta acción modifica tu sistema."


def pedir_confirmacion(peticion: Peticion, padre=None) -> bool:
    """Muestra el diálogo y devuelve si el usuario lo autorizó.

    Debe llamarse desde el hilo de la interfaz. El agente corre en otro hilo,
    así que la ventana se encarga de reenviar la llamada aquí.
    """
    dialogo = DialogoConfirmacion(peticion, padre)
    return dialogo.exec() == QDialog.Accepted
