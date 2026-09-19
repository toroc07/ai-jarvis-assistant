"""
El orbe: la cara visible de Jarvis cuando le hablas.

Ventana redonda, sin marco y siempre encima, que aparece al llamarlo y se va
cuando termináis. El diseño busca el aire del Jarvis de Iron Man: anillos
concéntricos, arcos que giran a distintas velocidades, marcas de retícula y un
núcleo que respira. Nada de eso es decoración pegada encima del espectro: el
espectro es la capa principal y el resto gira alrededor.

Lo que se ve, de fuera hacia dentro:

    · halo            resplandor que crece con el volumen
    · arco exterior   gira despacio en un sentido, con muescas
    · retícula        marcas cada 15 grados, las de 90 más largas
    · ESPECTRO        las 64 barras de frecuencia, simétricas hacia dentro
    · arco interior   gira más rápido en sentido contrario
    · núcleo          late, y su brillo sigue la energía del audio

Los arcos giran en sentidos opuestos a propósito: es lo que da sensación de
mecanismo vivo en lugar de imagen fija con cosas moviéndose.
"""

from __future__ import annotations

import math
from enum import Enum

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QPainter,
    QPen,
    QRadialGradient,
)
from PySide6.QtWidgets import QApplication, QWidget

# Número de barras del espectro. 64 da una curva suave sin que se emborrone.
BANDAS = 64

# La ventana es grande porque el halo necesita sitio para difuminarse; el
# dibujo real ocupa bastante menos que el lienzo.
LADO = 340
RADIO_ESPECTRO = 74
ALTURA_MAXIMA = 40
RADIO_ARCO_EXTERIOR = 132
RADIO_RETICULA = 116
RADIO_ARCO_INTERIOR = 56


class Estado(str, Enum):
    DORMIDO = "dormido"
    ESCUCHANDO = "escuchando"
    PENSANDO = "pensando"
    HABLANDO = "hablando"
    DETENIDO = "detenido"  # Interruptor de emergencia activado.


# Color principal de cada estado. Cian para ti, azul para él: la paleta fría de
# las interfaces de la película. El rojo queda reservado para la parada de
# emergencia, y por eso no se usa en ningún otro estado.
COLORES = {
    Estado.ESCUCHANDO: QColor("#3fe0d0"),
    Estado.PENSANDO: QColor("#5f7d99"),
    Estado.HABLANDO: QColor("#4aa8ff"),
    Estado.DETENIDO: QColor("#ff4444"),
    Estado.DORMIDO: QColor("#3fe0d0"),
}

ETIQUETAS = {
    Estado.ESCUCHANDO: "ESCUCHANDO",
    Estado.PENSANDO: "PROCESANDO",
    Estado.HABLANDO: "",
    Estado.DETENIDO: "DETENIDO",
    Estado.DORMIDO: "",
}


class Orbe(QWidget):
    """Ventana circular con espectrómetro. Se mueve arrastrándola."""

    cerrado = Signal()

    def __init__(self) -> None:
        super().__init__()

        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool  # Con Tool no aparece en la barra de tareas ni en Alt+Tab.
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(LADO, LADO)

        self._estado = Estado.DORMIDO
        self._niveles = [0.0] * BANDAS
        self._objetivo = [0.0] * BANDAS
        self._fase = 0.0
        self._giro_exterior = 0.0
        self._giro_interior = 0.0
        self._arrastre: QPointF | None = None

        # 60 cuadros por segundo: el movimiento se ve continuo.
        self._animacion = QTimer(self)
        self._animacion.setInterval(16)
        self._animacion.timeout.connect(self._avanzar)

        self._colocar_abajo_derecha()

    # -- Colocación ----------------------------------------------------------

    def _colocar_abajo_derecha(self) -> None:
        """Esquina inferior derecha, encima de la barra de tareas."""
        pantalla = QApplication.primaryScreen()
        if pantalla is None:
            return
        area = pantalla.availableGeometry()
        self.move(area.right() - LADO - 16, area.bottom() - LADO - 16)

    # -- Estado --------------------------------------------------------------

    @property
    def estado(self) -> Estado:
        return self._estado

    def cambiar_estado(self, estado: Estado) -> None:
        self._estado = estado

        if estado is Estado.DORMIDO:
            self._animacion.stop()
            self.hide()
            return

        if not self.isVisible():
            self.show()
            self.raise_()
        if not self._animacion.isActive():
            self._animacion.start()

        # Al dejar de haber audio, las barras deben caer en lugar de quedarse
        # congeladas en la última forma.
        if estado in (Estado.PENSANDO, Estado.DETENIDO):
            self._objetivo = [0.0] * BANDAS

    def dormir(self) -> None:
        self.cambiar_estado(Estado.DORMIDO)

    # -- Entrada de audio ----------------------------------------------------

    def alimentar(self, espectro: list[float]) -> None:
        """Recibe las bandas de frecuencia del audio actual, de 0 a 1.

        Lo llama quien captura el micrófono o reproduce la voz. El orbe solo
        dibuja: no sabe de dónde sale el sonido ni le hace falta.
        """
        if not espectro:
            return

        # Se adapta cualquier número de bandas al que dibuja el orbe, para que
        # quien llama no tenga que conocer este detalle.
        if len(espectro) == BANDAS:
            self._objetivo = [max(0.0, min(1.0, v)) for v in espectro]
            return

        paso = len(espectro) / BANDAS
        self._objetivo = [
            max(0.0, min(1.0, espectro[min(int(i * paso), len(espectro) - 1)]))
            for i in range(BANDAS)
        ]

    def silencio(self) -> None:
        self._objetivo = [0.0] * BANDAS

    @property
    def _energia(self) -> float:
        return sum(self._niveles) / BANDAS

    # -- Animación -----------------------------------------------------------

    def _avanzar(self) -> None:
        self._fase += 0.045

        # Sentidos opuestos y velocidades distintas: es lo que hace que parezca
        # un mecanismo y no una imagen girando entera.
        self._giro_exterior = (self._giro_exterior + 0.22) % 360
        self._giro_interior = (self._giro_interior - 0.55) % 360

        # Las barras se acercan a su objetivo en lugar de saltar. Suben rápido
        # para que un golpe de voz se note, y bajan despacio para que el anillo
        # no parpadee entre sílabas.
        for i, objetivo in enumerate(self._objetivo):
            actual = self._niveles[i]
            factor = 0.55 if objetivo > actual else 0.14
            self._niveles[i] = actual + (objetivo - actual) * factor

        self.update()

    # -- Dibujo --------------------------------------------------------------

    def paintEvent(self, evento) -> None:
        pintor = QPainter(self)
        pintor.setRenderHint(QPainter.Antialiasing)

        centro = QPointF(LADO / 2, LADO / 2)
        color = COLORES[self._estado]

        self._dibujar_halo(pintor, centro, color)
        self._dibujar_arco(
            pintor, centro, color, RADIO_ARCO_EXTERIOR, self._giro_exterior,
            grosor=2.2, tramos=3, largo=74, muescas=True,
        )
        self._dibujar_reticula(pintor, centro, color)
        self._dibujar_espectro(pintor, centro, color)
        self._dibujar_arco(
            pintor, centro, color, RADIO_ARCO_INTERIOR, self._giro_interior,
            grosor=1.6, tramos=2, largo=56, muescas=False,
        )
        self._dibujar_nucleo(pintor, centro, color)
        self._dibujar_etiqueta(pintor, centro, color)

    def _dibujar_halo(self, pintor: QPainter, centro: QPointF, color: QColor) -> None:
        """Resplandor exterior que crece con el volumen."""
        radio = RADIO_ARCO_EXTERIOR + 22 + self._energia * 18

        gradiente = QRadialGradient(centro, radio)
        transparente = QColor(color.red(), color.green(), color.blue(), 0)
        interno = QColor(color)
        interno.setAlpha(int(30 + self._energia * 55))

        gradiente.setColorAt(0.0, transparente)
        gradiente.setColorAt(0.45, interno)
        gradiente.setColorAt(1.0, transparente)

        pintor.setPen(Qt.NoPen)
        pintor.setBrush(QBrush(gradiente))
        pintor.drawEllipse(centro, radio, radio)

    def _dibujar_arco(
        self,
        pintor: QPainter,
        centro: QPointF,
        color: QColor,
        radio: float,
        giro: float,
        grosor: float,
        tramos: int,
        largo: int,
        muescas: bool,
    ) -> None:
        """Anillo partido en tramos que giran."""
        marco = QRectF(
            centro.x() - radio, centro.y() - radio, radio * 2, radio * 2
        )

        trazo = QColor(color)
        trazo.setAlpha(int(110 + self._energia * 90))
        pintor.setPen(QPen(trazo, grosor, Qt.SolidLine, Qt.FlatCap))
        pintor.setBrush(Qt.NoBrush)

        # Qt mide los ángulos en dieciseisavos de grado.
        for i in range(tramos):
            inicio = int((giro + i * (360 / tramos)) * 16)
            pintor.drawArc(marco, inicio, largo * 16)

        if not muescas:
            return

        # Muescas cortas al principio de cada tramo, como los indicadores de
        # los paneles de la película.
        pintor.setPen(QPen(trazo, grosor, Qt.SolidLine, Qt.RoundCap))
        for i in range(tramos):
            angulo = math.radians(giro + i * (360 / tramos))
            for desplazamiento in (-6, -3):
                a = angulo + math.radians(desplazamiento)
                cos_a, sin_a = math.cos(a), -math.sin(a)
                pintor.drawLine(
                    QPointF(centro.x() + cos_a * (radio - 7),
                            centro.y() + sin_a * (radio - 7)),
                    QPointF(centro.x() + cos_a * (radio + 7),
                            centro.y() + sin_a * (radio + 7)),
                )

    def _dibujar_reticula(self, pintor: QPainter, centro: QPointF, color: QColor) -> None:
        """Marcas fijas cada 15 grados; las de los ejes, más largas."""
        for grados in range(0, 360, 15):
            angulo = math.radians(grados)
            cos_a, sin_a = math.cos(angulo), -math.sin(angulo)

            principal = grados % 90 == 0
            largo = 11 if principal else 5

            trazo = QColor(color)
            trazo.setAlpha(150 if principal else 70)
            pintor.setPen(QPen(trazo, 1.8 if principal else 1.0))
            pintor.drawLine(
                QPointF(centro.x() + cos_a * RADIO_RETICULA,
                        centro.y() + sin_a * RADIO_RETICULA),
                QPointF(centro.x() + cos_a * (RADIO_RETICULA - largo),
                        centro.y() + sin_a * (RADIO_RETICULA - largo)),
            )

    def _dibujar_espectro(self, pintor: QPainter, centro: QPointF, color: QColor) -> None:
        """Las barras de frecuencia, hacia fuera y hacia dentro.

        La simetría no es un adorno: hace que el anillo se lea como una onda
        alrededor de un eje, que es lo que se reconoce como espectro, en lugar
        de como púas saliendo de un círculo.
        """
        if self._estado in (Estado.PENSANDO, Estado.DETENIDO):
            # Sin audio no hay nada que representar: se dibuja el anillo base
            # para que el orbe no quede hueco.
            trazo = QColor(color)
            trazo.setAlpha(60)
            pintor.setPen(QPen(trazo, 1.4))
            pintor.setBrush(Qt.NoBrush)
            pintor.drawEllipse(centro, RADIO_ESPECTRO, RADIO_ESPECTRO)
            return

        for i, nivel in enumerate(self._niveles):
            angulo = (i / BANDAS) * 2 * math.pi - math.pi / 2
            largo = nivel * ALTURA_MAXIMA
            if largo < 0.8:
                continue

            cos_a, sin_a = math.cos(angulo), math.sin(angulo)

            tono = QColor(color)
            tono.setAlpha(int(110 + nivel * 145))
            pintor.setPen(QPen(tono, 2.4, Qt.SolidLine, Qt.RoundCap))
            pintor.drawLine(
                QPointF(centro.x() + cos_a * RADIO_ESPECTRO,
                        centro.y() + sin_a * RADIO_ESPECTRO),
                QPointF(centro.x() + cos_a * (RADIO_ESPECTRO + largo),
                        centro.y() + sin_a * (RADIO_ESPECTRO + largo)),
            )

            # Reflejo hacia dentro, más corto y tenue.
            tono.setAlpha(int(45 + nivel * 65))
            pintor.setPen(QPen(tono, 1.8, Qt.SolidLine, Qt.RoundCap))
            pintor.drawLine(
                QPointF(centro.x() + cos_a * RADIO_ESPECTRO,
                        centro.y() + sin_a * RADIO_ESPECTRO),
                QPointF(centro.x() + cos_a * (RADIO_ESPECTRO - largo * 0.45),
                        centro.y() + sin_a * (RADIO_ESPECTRO - largo * 0.45)),
            )

    def _dibujar_nucleo(self, pintor: QPainter, centro: QPointF, color: QColor) -> None:
        """Punto central. Late despacio mientras piensa, para no parecer colgado."""
        if self._estado is Estado.PENSANDO:
            pulso = (math.sin(self._fase * 2.2) + 1) / 2
            radio = 15 + pulso * 8
            alfa = int(110 + pulso * 110)
        elif self._estado is Estado.DETENIDO:
            # Parpadeo marcado: tiene que verse que algo va mal, no decorar.
            pulso = (math.sin(self._fase * 5.0) + 1) / 2
            radio = 17.0
            alfa = int(90 + pulso * 150)
        else:
            radio = 16 + self._energia * 10
            alfa = int(170 + self._energia * 70)

        # Degradado del centro hacia fuera: da volumen en lugar de un disco
        # plano, que es lo que hace que parezca una esfera.
        gradiente = QRadialGradient(centro, radio)
        centro_color = QColor(255, 255, 255, min(255, alfa + 45))
        borde = QColor(color)
        borde.setAlpha(alfa)
        gradiente.setColorAt(0.0, centro_color)
        gradiente.setColorAt(0.55, borde)
        gradiente.setColorAt(1.0, QColor(color.red(), color.green(), color.blue(), 0))

        pintor.setPen(Qt.NoPen)
        pintor.setBrush(QBrush(gradiente))
        pintor.drawEllipse(centro, radio, radio)

    def _dibujar_etiqueta(self, pintor: QPainter, centro: QPointF, color: QColor) -> None:
        """Texto breve bajo el orbe diciendo qué está haciendo."""
        texto = ETIQUETAS.get(self._estado, "")
        if not texto:
            return

        fuente = QFont("Consolas", 8)
        # Si Consolas no estuviera, Qt elige otra monoespaciada en lugar de
        # dibujar cuadraditos. El espaciado entre letras es lo que da el aire
        # de panel técnico.
        fuente.setStyleHint(QFont.Monospace)
        fuente.setLetterSpacing(QFont.AbsoluteSpacing, 2.4)
        pintor.setFont(fuente)

        trazo = QColor(color)
        trazo.setAlpha(165)
        pintor.setPen(QPen(trazo))
        pintor.drawText(
            QRectF(0, centro.y() + RADIO_ARCO_EXTERIOR - 26, LADO, 18),
            Qt.AlignHCenter | Qt.AlignVCenter,
            texto,
        )

    # -- Interacción ---------------------------------------------------------

    def mousePressEvent(self, evento) -> None:
        if evento.button() == Qt.LeftButton:
            self._arrastre = evento.globalPosition() - self.frameGeometry().topLeft()
        elif evento.button() == Qt.RightButton:
            # Clic derecho para echarlo si molesta, sin tener que hablar.
            self.cerrado.emit()

    def mouseMoveEvent(self, evento) -> None:
        if self._arrastre is not None and evento.buttons() & Qt.LeftButton:
            self.move((evento.globalPosition() - self._arrastre).toPoint())

    def mouseReleaseEvent(self, evento) -> None:
        self._arrastre = None
