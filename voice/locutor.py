"""
Verificación de locutor: que Jarvis solo te haga caso a ti.

Funciona comparando huellas vocales. Al configurarlo grabas unas frases, se
extrae de cada una un vector de 192 números que resume cómo suena tu voz
(timbre, resonancia, forma del tracto vocal) y se guarda la media. Después,
cada vez que alguien dice "Jarvis", se extrae la huella de ese audio y se mide
su parecido con la tuya. Si no llega al umbral, se ignora sin decir nada.

QUÉ ES Y QUÉ NO ES ESTO
Es un filtro de conveniencia: evita que te lo activen tu familia, la tele o un
vídeo de fondo. NO es autenticación. Una grabación de buena calidad de tu voz
puede engañarlo, y por eso las acciones que tocan el sistema siguen pasando por
la confirmación del guardián. Nada de lo que hay aquí debilita esa capa.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from voice.ruido import limpiar_ruido, nivel_de_ruido

RAIZ = Path(__file__).resolve().parent.parent
RUTA_HUELLA = RAIZ / "data" / "voz" / "huella.json"
RUTA_MODELO = RAIZ / "data" / "voz" / "modelo"
RUTA_MUESTRAS = RAIZ / "data" / "voz" / "muestras.npz"

# Versión del método de calibración. Al cambiar cómo se calcula el umbral se
# sube este número, y una huella guardada con una versión anterior se
# recalibra sola con las muestras del registro en lugar de obligarte a
# repetirlo.
VERSION_DE_CALIBRACION = 3

# Parecido mínimo por defecto para aceptar una voz como tuya, de 0 a 1.
#
# CUIDADO CON ESTE NÚMERO. La similitud del coseno llevada al rango 0-1 comprime
# mucho las diferencias: midiendo dos timbres sintéticos completamente distintos
# sale 0,92. Un umbral bajo aquí no filtra nada y da falsa sensación de
# seguridad, que es peor que no tener filtro.
#
# 0,74 corresponde a un coseno de 0,48, por encima del punto de trabajo habitual
# de ECAPA-TDNN. Es deliberadamente exigente porque el error que molesta es el
# falso positivo: que Jarvis salte con otra voz es peor que tener que repetir
# "hey Jarvis" una vez.
#
# Este valor es solo el punto de partida: al registrar tu voz se sustituye por
# uno calculado a partir de tus propias grabaciones.
UMBRAL = 0.74

# Límites dentro de los que puede moverse el umbral calibrado. Impiden que una
# sesión de registro con mucho ruido deje a Jarvis abierto a cualquiera, o que
# una demasiado uniforme lo deje tan exigente que ni tú puedas activarlo.
#
# EL MÍNIMO ESTÁ MEDIDO, NO ELEGIDO. Con 0,66 el sistema rechazaba al propio
# usuario: sus activaciones reales puntuaban 0,637 y 0,544 contra un umbral que
# ya estaba pegado al suelo. Un segundo y medio de "hey Jarvis" da una huella
# poco estable, y ahí ECAPA-TDNN simplemente no discrimina tanto como con audio
# largo. Fingir lo contrario con un umbral alto no da seguridad: da un asistente
# que no responde.
UMBRAL_MINIMO = 0.50
UMBRAL_MAXIMO = 0.88

# Margen que se resta a tu peor grabación para fijar el umbral. Da holgura para
# cuando hables más bajo, más rápido o estés resfriado. Es amplio porque el
# registro se hace en un momento concreto y tu voz varía bastante más que eso
# a lo largo del día.
MARGEN = 0.10

# Cuánto se rebaja el umbral en la activación por palabra clave respecto al de
# una petición completa.
#
# Aquí hay un reparto deliberado del trabajo. El clip del "hey Jarvis" es corto
# y da un parecido bajo incluso siendo tú, así que exigir en ese punto el mismo
# listón que con audio largo te deja fuera. Se usa un listón más bajo para abrir
# el orbe —una acción inofensiva: solo aparece un círculo en pantalla— y se
# verifica en serio con la petición completa, que dura varios segundos y sí
# permite distinguir voces. Quien no sea tú verá el orbe abrirse y nada más.
REBAJA_EN_LA_PALABRA_CLAVE = 0.12

# Duración del audio con el que se verifica una activación. Debe coincidir con
# SEGUNDOS_DE_CONTEXTO en voice/escucha.py: el umbral se calibra con trozos de
# este tamaño, así que si los dos valores se separan, el umbral deja de
# corresponder a lo que realmente se mide y vuelve a rechazar al usuario.
SEGUNDOS_DE_VERIFICACION = 1.5

# Cuántas posiciones distintas se prueban dentro del audio capturado al
# verificar. Más posiciones dan más oportunidades de encajar con la voz,
# pero cada una cuesta una pasada por el modelo: tres es el equilibrio entre
# reconocerte a la primera y no tardar en abrir el orbe.
VENTANAS_DE_BUSQUEDA = 3

# Cuántas frases se piden al configurar. Con menos, la huella recoge demasiado
# el tono concreto de ese momento y falla cuando hablas más bajo o resfriado.
FRASES_DE_REGISTRO = 5

FRASES_SUGERIDAS = [
    "Hola Jarvis, soy yo",
    "Jarvis, abre el navegador",
    "Qué hora es",
    "Jarvis, cuánto espacio libre me queda",
    "Gracias por la ayuda, ya me encargo yo",
]


@dataclass
class Resultado:
    es_el_usuario: bool
    parecido: float
    motivo: str


class Locutor:
    """Extrae y compara huellas vocales."""

    def __init__(self, umbral: float = UMBRAL) -> None:
        self.umbral = umbral
        self._codificador = None
        self._huella: np.ndarray | None = None
        self._cargar_huella()

    # -- Modelo --------------------------------------------------------------

    def _obtener_codificador(self):
        """Carga ECAPA-TDNN la primera vez que hace falta.

        Se hace tarde y no al arrancar porque son unos 80 MB y varios segundos:
        si nunca configuras la verificación, no hay motivo para pagarlos.
        """
        if self._codificador is not None:
            return self._codificador

        try:
            from speechbrain.inference.speaker import EncoderClassifier
            from speechbrain.utils.fetching import LocalStrategy
        except ImportError as e:
            raise RuntimeError(
                "Falta speechbrain. Instálalo con: pip install speechbrain torch"
            ) from e

        RUTA_MODELO.parent.mkdir(parents=True, exist_ok=True)
        self._codificador = EncoderClassifier.from_hparams(
            source="speechbrain/spkrec-ecapa-voxceleb",
            savedir=str(RUTA_MODELO),
            run_opts={"device": "cpu"},
            # COPY en lugar de la estrategia por defecto, que son enlaces
            # simbólicos: en Windows crearlos exige permisos de administrador o
            # el modo desarrollador, y sin ellos la carga falla con
            # "El cliente no dispone de un privilegio requerido". Copiar gasta
            # unos megas más de disco y funciona siempre.
            local_strategy=LocalStrategy.COPY,
        )
        return self._codificador

    # -- Huellas -------------------------------------------------------------

    def _extraer(self, audio: np.ndarray, frecuencia: int = 16000) -> np.ndarray:
        """Convierte un trozo de audio en su huella vocal."""
        import torch

        muestras = limpiar_ruido(audio.astype(np.float32).flatten(), frecuencia)

        # Se normaliza el volumen para que hablar más alto o más bajo no cambie
        # la huella: interesa el timbre, no cuánto levantas la voz.
        pico = float(np.abs(muestras).max())
        if pico > 0:
            muestras = muestras / pico

        with torch.no_grad():
            tensor = torch.from_numpy(muestras).unsqueeze(0)
            huella = self._obtener_codificador().encode_batch(tensor)

        return huella.squeeze().cpu().numpy()

    @staticmethod
    def _parecido(a: np.ndarray, b: np.ndarray) -> float:
        """Similitud del coseno, llevada al rango 0-1.

        Da 1 si las huellas apuntan en la misma dirección y 0 si son opuestas.
        Compara la forma del vector y no su tamaño, que es justo lo que se
        quiere: el tamaño depende del volumen, la dirección de quién habla.
        """
        na, nb = np.linalg.norm(a), np.linalg.norm(b)
        if na == 0 or nb == 0:
            return 0.0
        coseno = float(np.dot(a, b) / (na * nb))
        return (coseno + 1) / 2

    # -- Registro ------------------------------------------------------------

    @property
    def configurado(self) -> bool:
        return self._huella is not None

    def registrar(self, grabaciones: list[np.ndarray], frecuencia: int = 16000) -> None:
        """Guarda tu huella vocal a partir de varias grabaciones.

        Se promedian las huellas de todas las frases: así la referencia recoge
        cómo suenas en general y no cómo sonabas en una frase concreta.
        """
        if len(grabaciones) < 2:
            raise ValueError(
                "Hacen falta al menos 2 grabaciones para una huella fiable."
            )

        huellas = [self._extraer(g, frecuencia) for g in grabaciones]
        media = np.mean(huellas, axis=0)

        # Si tus propias frases enteras no se parecen entre sí, algo va mal
        # (ruido, micrófono lejos) y la verificación fallaría constantemente.
        parecidos_largos = [self._parecido(h, media) for h in huellas]
        if min(parecidos_largos) < UMBRAL_MINIMO:
            raise ValueError(
                f"Tus grabaciones varían demasiado entre sí (la peor da "
                f"{min(parecidos_largos):.2f}). Suele ser ruido de fondo o "
                "hablar a distintas distancias del micrófono. Repítelo en un "
                "sitio silencioso."
            )

        # EL UMBRAL SE CALIBRA CON TROZOS CORTOS, NO CON LAS FRASES ENTERAS.
        #
        # Esto importa más de lo que parece. Al activarse, Jarvis solo tiene el
        # segundo escaso que dura "hey Jarvis", y con tan poco audio la huella
        # sale menos estable y el parecido baja. Calibrar con frases de tres
        # segundos y verificar con una de uno es comparar cosas distintas: el
        # umbral quedaba alto y rechazaba al propio usuario por centésimas.
        parecidos = self._parecidos_en_trozos(grabaciones, media, frecuencia)
        if not parecidos:
            parecidos = parecidos_largos

        peor = min(parecidos)
        self.umbral = float(np.clip(peor - MARGEN, UMBRAL_MINIMO, UMBRAL_MAXIMO))

        self._huella = media
        self._guardar_muestras(grabaciones, frecuencia)
        self._guardar_huella()

    def _guardar_muestras(
        self, grabaciones: list[np.ndarray], frecuencia: int
    ) -> None:
        """Guarda las grabaciones del registro, para poder recalibrar después.

        Es lo que evita tener que repetir el registro cada vez que cambia la
        forma de calcular el umbral. Ya pasó dos veces: un ajuste del método
        dejaba la huella con un umbral que ya no correspondía, y la única
        salida era volver a grabar las cinco frases. Con las muestras
        guardadas, recalibrar es instantáneo y no molesta a nadie.

        El audio es tuyo y se queda en tu disco, igual que la huella.
        """
        try:
            RUTA_MUESTRAS.parent.mkdir(parents=True, exist_ok=True)
            np.savez_compressed(
                RUTA_MUESTRAS,
                frecuencia=frecuencia,
                **{f"g{i}": g for i, g in enumerate(grabaciones)},
            )
        except Exception:
            # Sin muestras, recalibrar exigirá repetir el registro. Molesto,
            # pero no es motivo para tumbar un registro que ya salió bien.
            pass

    def _cargar_muestras(self) -> tuple[list[np.ndarray], int] | None:
        if not RUTA_MUESTRAS.exists():
            return None
        try:
            datos = np.load(RUTA_MUESTRAS)
            frecuencia = int(datos["frecuencia"])
            grabaciones = [
                datos[c] for c in sorted(datos.files) if c.startswith("g")
            ]
        except Exception:
            return None
        return (grabaciones, frecuencia) if len(grabaciones) >= 2 else None

    def recalibrar(self) -> bool:
        """Recalcula el umbral con las grabaciones guardadas del registro.

        Devuelve False si no hay muestras y hay que repetir el registro a mano.
        """
        muestras = self._cargar_muestras()
        if muestras is None:
            return False
        try:
            self.registrar(*muestras)
        except ValueError:
            return False
        return True

    def _parecidos_en_trozos(
        self, grabaciones: list[np.ndarray], media: np.ndarray, frecuencia: int
    ) -> list[float]:
        """Parecidos de trozos cortos contra la huella media.

        Imita la situación real de verificación: poco audio, el que cabe en un
        "hey Jarvis". Se recorren las grabaciones en ventanas del mismo tamaño
        que usa la detección.
        """
        largo = int(frecuencia * SEGUNDOS_DE_VERIFICACION)
        salto = max(1, largo // 2)
        parecidos: list[float] = []

        for grabacion in grabaciones:
            muestras = grabacion.flatten()
            if muestras.size < largo:
                continue
            for inicio in range(0, muestras.size - largo + 1, salto):
                trozo = muestras[inicio : inicio + largo]
                # Los trozos de silencio entre palabras no dicen nada sobre tu
                # voz y hundirían el umbral sin motivo.
                if float(np.sqrt(np.mean(trozo**2))) < 0.01:
                    continue
                try:
                    parecidos.append(
                        self._parecido(self._extraer(trozo, frecuencia), media)
                    )
                except Exception:
                    continue

        return parecidos

    def _guardar_huella(self) -> None:
        if self._huella is None:
            return
        RUTA_HUELLA.parent.mkdir(parents=True, exist_ok=True)
        with open(RUTA_HUELLA, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "huella": self._huella.tolist(),
                    "umbral": self.umbral,
                    "version": VERSION_DE_CALIBRACION,
                },
                f,
            )

    def _cargar_huella(self) -> None:
        if not RUTA_HUELLA.exists():
            return
        try:
            with open(RUTA_HUELLA, "r", encoding="utf-8") as f:
                datos = json.load(f)
            self._huella = np.array(datos["huella"], dtype=np.float32)
            self.umbral = datos.get("umbral", self.umbral)
            version = datos.get("version", 0)
        except (OSError, json.JSONDecodeError, KeyError):
            # Una huella corrupta se ignora: mejor pedir que la configures de
            # nuevo que arrancar con una referencia sin sentido.
            self._huella = None
            return

        # Una huella calibrada con un método anterior lleva un umbral que ya no
        # corresponde, y eso se traduce en rechazarte a ti. Si hay muestras
        # guardadas se recalibra sola, sin molestarte.
        if version < VERSION_DE_CALIBRACION and not self.recalibrar():
            # Sin muestras no se puede recalcular, pero tampoco hace falta
            # obligarte a repetir el registro: los umbrales antiguos se
            # quedaban pegados a su suelo, así que no llevaban información
            # tuya. Traerlos al rango actual es equivalente, no un apaño.
            self.umbral = float(
                np.clip(self.umbral, UMBRAL_MINIMO, UMBRAL_MAXIMO)
            )
            if self.umbral > UMBRAL_MINIMO + 0.1:
                # Un umbral claramente por encima del suelo sí venía de tus
                # grabaciones, así que solo se le da la holgura del margen
                # nuevo en lugar de bajarlo del todo.
                self.umbral = max(UMBRAL_MINIMO, self.umbral - (MARGEN - 0.05))
            else:
                self.umbral = UMBRAL_MINIMO
            self._guardar_huella()

    def olvidar(self) -> None:
        """Borra tu huella. Jarvis vuelve a responder a cualquier voz."""
        self._huella = None
        RUTA_HUELLA.unlink(missing_ok=True)

    # -- Verificación --------------------------------------------------------

    def verificar(
        self,
        audio: np.ndarray,
        frecuencia: int = 16000,
        es_palabra_clave: bool = False,
    ) -> Resultado:
        """Comprueba si un audio es tu voz.

        Con 'es_palabra_clave' se aplica un listón más bajo, porque ese clip
        dura segundo y medio y da parecidos bajos incluso siendo tú. Lo que se
        decide ahí es solo si abrir el orbe; la verificación seria se hace
        después con la petición completa.
        """
        if self._huella is None:
            # Sin huella configurada se acepta a cualquiera: es preferible a
            # que Jarvis quede mudo sin explicación hasta que lo configures.
            return Resultado(True, 1.0, "No hay huella configurada.")

        # Menos de medio segundo no da para una huella fiable, y aceptarla
        # sería peor que rechazarla porque el parecido sale casi al azar.
        if audio.size < frecuencia * 0.5:
            return Resultado(False, 0.0, "Audio demasiado corto para verificar.")

        try:
            parecido = self._mejor_parecido(audio, frecuencia)
        except Exception as e:
            # Un fallo del modelo no debe dejarte sin asistente: se acepta y se
            # explica, en lugar de bloquear el acceso por un error técnico.
            return Resultado(True, 0.0, f"No se pudo verificar la voz: {e}")

        listones = self.umbral - (
            REBAJA_EN_LA_PALABRA_CLAVE if es_palabra_clave else 0.0
        )

        if parecido >= listones:
            return Resultado(True, parecido, f"Voz reconocida ({parecido:.2f}).")
        return Resultado(
            False, parecido, f"La voz no es la tuya ({parecido:.2f} < {listones:.2f})."
        )

    def _mejor_parecido(self, audio: np.ndarray, frecuencia: int) -> float:
        """El mejor parecido entre varias posiciones dentro del audio.

        El "hey Jarvis" puede caer en cualquier punto del trozo capturado, y
        parte de ese trozo suele ser silencio o el final de otra frase. Medir
        solo el bloque entero mezcla la voz con lo que la rodea y baja el
        parecido sin motivo, que es lo que hacía falsos rechazos.

        Se prueban varias posiciones y se queda la mejor. No es hacer trampa
        para colar a otro: una voz distinta puntúa bajo en todas las posiciones,
        porque lo que se compara es el timbre y ese no cambia al desplazar la
        ventana.
        """
        muestras = np.asarray(audio, dtype=np.float32).flatten()
        entero = self._parecido(self._extraer(muestras, frecuencia), self._huella)

        # Con poco audio no hay margen para desplazar nada.
        largo = int(frecuencia * SEGUNDOS_DE_VERIFICACION * 0.7)
        if muestras.size <= largo:
            return entero

        mejor = entero
        posiciones = np.linspace(0, muestras.size - largo, VENTANAS_DE_BUSQUEDA)
        for inicio in posiciones.astype(int):
            trozo = muestras[inicio : inicio + largo]
            # Un trozo casi mudo no dice nada de tu voz; medirlo solo gasta
            # tiempo y puede dar un parecido engañoso.
            if float(np.sqrt(np.mean(trozo**2))) < 0.01:
                continue
            try:
                mejor = max(
                    mejor, self._parecido(self._extraer(trozo, frecuencia), self._huella)
                )
            except Exception:
                continue

        return mejor
