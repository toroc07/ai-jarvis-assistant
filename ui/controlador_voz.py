"""
El puente entre la voz y la interfaz.

La sesión de voz corre en sus propios hilos y no sabe nada de Qt, que es lo que
permite probarla sin abrir ventanas. Pero el orbe solo se puede tocar desde el
hilo de la interfaz, así que cada aviso de la sesión se convierte aquí en una
señal de Qt, que es la forma segura de cruzar de un hilo a otro.

La petición de permiso cruza en sentido contrario y encima tiene que esperar
respuesta, así que usa una conexión bloqueante, igual que en la ventana de chat.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Qt, Signal, Slot

from core.agent import Agente
from security.parada import interruptor
from security.guard import Peticion
from ui.confirmacion import pedir_confirmacion
from ui.orbe import Estado, Orbe
from voice.sesion import Avisos, Fase, SesionDeVoz

# Cada fase de la conversación tiene su aspecto en el orbe.
FASE_A_ESTADO = {
    Fase.DORMIDO: Estado.DORMIDO,
    Fase.ESCUCHANDO: Estado.ESCUCHANDO,
    Fase.PENSANDO: Estado.PENSANDO,
    Fase.HABLANDO: Estado.HABLANDO,
}


class ControladorVoz(QObject):
    """Conecta la sesión de voz con el orbe y la ventana."""

    fase_cambiada = Signal(object)
    espectro_recibido = Signal(list)
    dijo_el_usuario = Signal(str)
    dijo_jarvis = Signal(str)
    voz_no_reconocida = Signal(float)
    aviso = Signal(str)
    permiso_pedido = Signal(object)
    # Se emite en cuanto puede oír la palabra clave, antes de tener el
    # resto cargado: es el momento en que ya le puedes hablar.
    escuchando = Signal()
    preparada = Signal(str)
    # Apagar Jarvis del todo, a diferencia de cerrar el orbe.
    apagado_pedido = Signal(str)

    def __init__(self, agente: Agente, ventana=None) -> None:
        super().__init__()
        self.ventana = ventana
        self.orbe = Orbe()
        self.orbe.cerrado.connect(self.detener_conversacion)

        self._permiso_concedido = False

        self.sesion = SesionDeVoz(
            agente,
            Avisos(
                cambio_de_fase=self.fase_cambiada.emit,
                espectro=self.espectro_recibido.emit,
                texto_del_usuario=self.dijo_el_usuario.emit,
                texto_de_jarvis=self.dijo_jarvis.emit,
                voz_rechazada=self.voz_no_reconocida.emit,
                pedir_permiso=self._pedir_permiso,
                aviso=self.aviso.emit,
                apagar=self.apagado_pedido.emit,
            ),
        )

        # Cada señal se atiende en el hilo de la interfaz. La del permiso es
        # bloqueante porque el hilo de voz necesita la respuesta para seguir.
        self.fase_cambiada.connect(self._al_cambiar_fase)
        self.espectro_recibido.connect(self._al_recibir_espectro)
        self.permiso_pedido.connect(
            self._al_pedir_permiso, Qt.BlockingQueuedConnection
        )

    # -- Permisos ------------------------------------------------------------

    def _pedir_permiso(self, peticion: Peticion) -> bool:
        """La llama el hilo de voz; se queda esperando la respuesta."""
        self.permiso_pedido.emit(peticion)
        return self._permiso_concedido

    @Slot(object)
    def _al_pedir_permiso(self, peticion: Peticion) -> None:
        # El orbe se aparta: el diálogo debe verse y poder pulsarse.
        estaba_visible = self.orbe.isVisible()
        if estaba_visible:
            self.orbe.hide()

        self._permiso_concedido = pedir_confirmacion(peticion, self.ventana)

        if estaba_visible:
            self.orbe.show()
            self.orbe.raise_()

    # -- Orbe ----------------------------------------------------------------

    @Slot(object)
    def _al_cambiar_fase(self, fase: Fase) -> None:
        # Estando detenido, el orbe lo muestra en rojo por encima de la fase en
        # la que esté: es la información más importante en ese momento.
        if interruptor.activado and fase is not Fase.DORMIDO:
            self.orbe.cambiar_estado(Estado.DETENIDO)
            return
        self.orbe.cambiar_estado(FASE_A_ESTADO.get(fase, Estado.DORMIDO))

    @Slot(list)
    def _al_recibir_espectro(self, bandas: list) -> None:
        self.orbe.alimentar(bandas)

    # -- Control -------------------------------------------------------------

    def arrancar(self) -> None:
        """Carga los modelos y empieza a escuchar. Tarda, así que va aparte."""
        from PySide6.QtCore import QThread

        controlador = self

        class Preparador(QThread):
            def run(self) -> None:
                try:
                    # Primero lo justo para oír: así Jarvis responde a la
                    # palabra clave en un segundo en vez de en seis.
                    controlador.sesion.empezar_a_escuchar()
                    print("[voz] Escucha activa. Di «hey Jarvis».", flush=True)
                    controlador.escuchando.emit()

                    # Y ahora lo que solo hace falta cuando ya has hablado.
                    motor = controlador.sesion.preparar_el_resto()
                    print(f"[voz] Voz y transcripción listas ({motor}).", flush=True)
                    controlador.preparada.emit(motor)
                except Exception as e:
                    # Se imprime además de avisar: el aviso va a la ventana de
                    # chat, que está escondida, así que sin esto un fallo aquí
                    # deja a Jarvis sordo sin dejar rastro en ningún sitio.
                    import traceback

                    traceback.print_exc()
                    print(
                        f"[voz] NO SE PUDO ARRANCAR LA ESCUCHA: {e}", flush=True
                    )
                    controlador.aviso.emit(f"No se pudo arrancar la voz: {e}")

        self._preparador = Preparador()
        self._preparador.start()

    def abrir_conversacion(self) -> bool:
        """Abre el orbe y empieza a escuchar, sin decir la palabra clave."""
        return self.sesion.abrir_conversacion()

    @property
    def conversacion_abierta(self) -> bool:
        return self.orbe.isVisible()

    def detener_conversacion(self) -> None:
        """Cierra el orbe y calla a Jarvis, pero sigue escuchando la palabra."""
        self.sesion.voz.callar()
        self.orbe.dormir()

    def parar(self) -> None:
        self.sesion.parar()
        self.orbe.dormir()

    @property
    def voz_configurada(self) -> bool:
        return self.sesion.locutor.configurado
