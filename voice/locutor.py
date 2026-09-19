"""
Verificación de locutor: quién está hablando.

Admite hasta tres personas registradas, cada una con su nombre. Al activarse,
se compara la voz con todos los perfiles y gana el más parecido, así el
asistente puede llamarte por tu nombre y saber a quién responde.

CÓMO FUNCIONA
Al registrarte lees unas frases; de cada una se extrae un vector de 192 números
con ECAPA-TDNN que resume cómo suena tu voz (timbre, resonancia, forma del
tracto vocal) y se guarda la media. Después, cada activación se compara contra
esa huella.

QUÉ ES Y QUÉ NO ES ESTO
Es un filtro de conveniencia: evita que lo activen personas que no están
registradas, la televisión o un vídeo de fondo. NO es autenticación. Una
grabación de buena calidad puede engañarlo, y por eso las acciones que tocan el
sistema siguen pasando por la confirmación del guardián. Nada de lo que hay
aquí debilita esa capa.
"""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from voice.ruido import limpiar_ruido, nivel_de_ruido

RAIZ = Path(__file__).resolve().parent.parent
RUTA_PERFILES = RAIZ / "data" / "voz" / "perfiles.json"
RUTA_MODELO = RAIZ / "data" / "voz" / "modelo"
RUTA_MUESTRAS = RAIZ / "data" / "voz" / "muestras"

# Huella del formato anterior, de cuando solo había una voz. Se importa sola.
RUTA_HUELLA_ANTIGUA = RAIZ / "data" / "voz" / "huella.json"
RUTA_MUESTRAS_ANTIGUAS = RAIZ / "data" / "voz" / "muestras.npz"

# Versión del método de calibración. Al cambiar cómo se calcula el umbral se
# sube este número, y un perfil guardado con una versión anterior se recalibra
# solo con sus muestras en lugar de obligar a repetir el registro.
VERSION_DE_CALIBRACION = 4

# Cuántas personas caben. Tres cubre una casa normal sin que la comparación se
# alargue: cada activación se mide contra todos los perfiles.
MAX_PERFILES = 3

# Parecido mínimo por defecto, de 0 a 1.
#
# CUIDADO CON ESTE NÚMERO. La similitud del coseno llevada al rango 0-1 comprime
# mucho las diferencias: midiendo dos timbres sintéticos completamente distintos
# sale 0,92. Un umbral bajo no filtra nada y da falsa sensación de seguridad.
# Solo es el punto de partida: al registrar una voz se sustituye por uno
# calculado con sus propias grabaciones.
UMBRAL = 0.74

# Límites del umbral calibrado. EL MÍNIMO ESTÁ MEDIDO, NO ELEGIDO: con 0,66 el
# sistema rechazaba al propio usuario, cuyas activaciones reales puntuaban 0,637
# y 0,544. Segundo y medio de audio no da para que ECAPA-TDNN discrimine tanto
# como con frases largas, y fingir lo contrario con un umbral alto no da
# seguridad: da un asistente que no responde.
UMBRAL_MINIMO = 0.50
UMBRAL_MAXIMO = 0.88

# Margen que se resta a la peor grabación. Amplio porque el registro se hace en
# un momento concreto y una voz varía bastante más que eso a lo largo del día.
MARGEN = 0.10

# Cuánto se rebaja el listón en la activación por palabra clave.
#
# Reparto deliberado del trabajo: el clip del "hey ..." dura segundo y medio y
# da parecidos bajos incluso siendo tú, así que exigir ahí el mismo listón que
# con audio largo deja fuera a todo el mundo. Se usa uno más bajo para abrir el
# orbe —una acción inofensiva— y se verifica en serio con la petición completa.
REBAJA_EN_LA_PALABRA_CLAVE = 0.12

# Duración del audio con el que se verifica. Debe coincidir con
# SEGUNDOS_DE_CONTEXTO en voice/escucha.py: el umbral se calibra con trozos de
# este tamaño, y si los dos valores se separan el umbral deja de corresponder a
# lo que realmente se mide.
SEGUNDOS_DE_VERIFICACION = 1.5

# Posiciones que se prueban dentro del audio al verificar. El "hey ..." puede
# caer en cualquier punto del trozo capturado.
VENTANAS_DE_BUSQUEDA = 3

FRASES_SUGERIDAS = [
    "Hola, soy yo",
    "Abre el navegador, por favor",
    "Qué hora es",
    "Cuánto espacio libre me queda en el disco",
    "Gracias por la ayuda, ya me encargo yo",
]


def normalizar_nombre(nombre: str) -> str:
    """Forma canónica del nombre, para comparar sin distinguir tildes ni mayúsculas."""
    sin_tildes = "".join(
        c
        for c in unicodedata.normalize("NFD", nombre.strip().lower())
        if unicodedata.category(c) != "Mn"
    )
    return " ".join(sin_tildes.split())


@dataclass
class Perfil:
    """Una persona registrada."""

    nombre: str
    huella: np.ndarray
    umbral: float = UMBRAL

    @property
    def clave(self) -> str:
        return normalizar_nombre(self.nombre)


@dataclass
class Resultado:
    """A quién se ha reconocido, si a alguien."""

    es_conocido: bool
    parecido: float
    motivo: str
    # Nombre de la persona reconocida, vacío si no se reconoció a nadie.
    nombre: str = ""

    @property
    def es_el_usuario(self) -> bool:
        """Se mantiene por compatibilidad con el código que ya lo usaba."""
        return self.es_conocido


class Locutor:
    """Registra e identifica voces."""

    def __init__(self) -> None:
        self._codificador = None
        self.perfiles: list[Perfil] = []
        self._cargar()

    # -- Modelo --------------------------------------------------------------

    def _obtener_codificador(self):
        """Carga ECAPA-TDNN la primera vez que hace falta.

        Se hace tarde porque son unos 80 MB y varios segundos: si nadie
        configura la verificación, no hay motivo para pagarlos.
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
            # simbólicos: en Windows crearlos exige permisos de administrador, y
            # sin ellos la carga falla con "El cliente no dispone de un
            # privilegio requerido".
            local_strategy=LocalStrategy.COPY,
        )
        return self._codificador

    # -- Huellas -------------------------------------------------------------

    def _extraer(self, audio: np.ndarray, frecuencia: int = 16000) -> np.ndarray:
        import torch

        muestras = limpiar_ruido(audio.astype(np.float32).flatten(), frecuencia)

        # Se normaliza el volumen para que hablar más alto o más bajo no cambie
        # la huella: interesa el timbre, no cuánto se levanta la voz.
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

        Compara la dirección de los vectores y no su tamaño, que es lo que se
        quiere: el tamaño depende del volumen, la dirección de quién habla.
        """
        na, nb = np.linalg.norm(a), np.linalg.norm(b)
        if na == 0 or nb == 0:
            return 0.0
        return (float(np.dot(a, b) / (na * nb)) + 1) / 2

    # -- Consulta ------------------------------------------------------------

    @property
    def configurado(self) -> bool:
        return bool(self.perfiles)

    @property
    def nombres(self) -> list[str]:
        return [p.nombre for p in self.perfiles]

    @property
    def hay_sitio(self) -> bool:
        return len(self.perfiles) < MAX_PERFILES

    def buscar(self, nombre: str) -> Perfil | None:
        clave = normalizar_nombre(nombre)
        return next((p for p in self.perfiles if p.clave == clave), None)

    # -- Registro ------------------------------------------------------------

    def registrar(
        self,
        nombre: str,
        grabaciones: list[np.ndarray],
        frecuencia: int = 16000,
    ) -> Perfil:
        """Guarda la huella vocal de una persona.

        Si el nombre ya existe, se sustituye su perfil en lugar de añadir otro.
        """
        nombre = nombre.strip()
        if not nombre:
            raise ValueError("Hace falta un nombre para el perfil.")

        if len(grabaciones) < 2:
            raise ValueError(
                "Hacen falta al menos 2 grabaciones para una huella fiable."
            )

        existente = self.buscar(nombre)
        if existente is None and not self.hay_sitio:
            raise ValueError(
                f"Ya hay {MAX_PERFILES} voces registradas "
                f"({', '.join(self.nombres)}). Olvida una antes de añadir otra."
            )

        huellas = [self._extraer(g, frecuencia) for g in grabaciones]
        media = np.mean(huellas, axis=0)

        # Si las frases de la misma persona no se parecen entre sí, algo va mal
        # (ruido, micrófono lejos) y la verificación fallaría constantemente.
        parecidos_largos = [self._parecido(h, media) for h in huellas]
        if min(parecidos_largos) < UMBRAL_MINIMO:
            raise ValueError(
                f"Las grabaciones varían demasiado entre sí (la peor da "
                f"{min(parecidos_largos):.2f}). Suele ser ruido de fondo o "
                "hablar a distintas distancias del micrófono. Repítelo en un "
                "sitio silencioso."
            )

        # EL UMBRAL SE CALIBRA CON TROZOS CORTOS, NO CON LAS FRASES ENTERAS.
        # Al activarse solo hay el segundo y medio que dura el "hey ...", y con
        # tan poco audio la huella sale menos estable y el parecido baja.
        # Calibrar con frases de tres segundos y verificar con una de uno es
        # comparar cosas distintas: el umbral quedaba alto y rechazaba a la
        # propia persona por centésimas.
        parecidos = self._parecidos_en_trozos(grabaciones, media, frecuencia)
        peor = min(parecidos) if parecidos else min(parecidos_largos)
        umbral = float(np.clip(peor - MARGEN, UMBRAL_MINIMO, UMBRAL_MAXIMO))

        perfil = Perfil(nombre=nombre, huella=media, umbral=umbral)
        if existente is not None:
            self.perfiles[self.perfiles.index(existente)] = perfil
        else:
            self.perfiles.append(perfil)

        self._guardar_muestras(perfil.clave, grabaciones, frecuencia)
        self._guardar()
        return perfil

    def _parecidos_en_trozos(
        self, grabaciones: list[np.ndarray], media: np.ndarray, frecuencia: int
    ) -> list[float]:
        """Parecidos de trozos cortos contra la huella media.

        Imita la situación real de verificación: poco audio, el que cabe en un
        "hey ...". Se recorren las grabaciones en ventanas del mismo tamaño que
        usa la detección.
        """
        largo = int(frecuencia * SEGUNDOS_DE_VERIFICACION)
        salto = max(1, largo // 2)
        parecidos: list[float] = []

        for grabacion in grabaciones:
            muestras = np.asarray(grabacion).flatten()
            if muestras.size < largo:
                continue
            for inicio in range(0, muestras.size - largo + 1, salto):
                trozo = muestras[inicio : inicio + largo]
                # Los trozos de silencio entre palabras no dicen nada de la voz
                # y hundirían el umbral sin motivo.
                if float(np.sqrt(np.mean(trozo**2))) < 0.01:
                    continue
                try:
                    parecidos.append(
                        self._parecido(self._extraer(trozo, frecuencia), media)
                    )
                except Exception:
                    continue

        return parecidos

    def olvidar(self, nombre: str) -> bool:
        """Borra el perfil de una persona."""
        perfil = self.buscar(nombre)
        if perfil is None:
            return False

        self.perfiles.remove(perfil)
        (RUTA_MUESTRAS / f"{perfil.clave}.npz").unlink(missing_ok=True)
        self._guardar()
        return True

    def olvidar_todo(self) -> None:
        """Borra todos los perfiles. El asistente vuelve a responder a cualquiera."""
        for perfil in list(self.perfiles):
            (RUTA_MUESTRAS / f"{perfil.clave}.npz").unlink(missing_ok=True)
        self.perfiles = []
        self._guardar()

    # -- Identificación ------------------------------------------------------

    def identificar(
        self,
        audio: np.ndarray,
        frecuencia: int = 16000,
        es_palabra_clave: bool = False,
    ) -> Resultado:
        """Dice a cuál de las personas registradas corresponde una voz.

        Con 'es_palabra_clave' se aplica un listón más bajo, porque ese clip
        dura segundo y medio y da parecidos bajos incluso siendo la persona
        correcta. Ahí solo se decide si abrir el orbe; la verificación seria se
        hace después con la petición completa.
        """
        if not self.perfiles:
            # Sin nadie registrado se acepta a cualquiera: es preferible a que
            # el asistente quede mudo sin explicación hasta que lo configuren.
            return Resultado(True, 1.0, "No hay ninguna voz registrada.")

        # Menos de medio segundo no da para una huella fiable, y aceptarla sería
        # peor que rechazarla porque el parecido sale casi al azar.
        if audio.size < frecuencia * 0.5:
            return Resultado(False, 0.0, "Audio demasiado corto para verificar.")

        try:
            mejor_perfil, mejor_parecido = self._mejor_coincidencia(audio, frecuencia)
        except Exception as e:
            # Un fallo del modelo no debe dejar a nadie sin asistente.
            return Resultado(True, 0.0, f"No se pudo verificar la voz: {e}")

        if mejor_perfil is None:
            return Resultado(False, 0.0, "No se pudo comparar con ningún perfil.")

        liston = mejor_perfil.umbral - (
            REBAJA_EN_LA_PALABRA_CLAVE if es_palabra_clave else 0.0
        )

        if mejor_parecido >= liston:
            return Resultado(
                True,
                mejor_parecido,
                f"Voz de {mejor_perfil.nombre} ({mejor_parecido:.2f}).",
                nombre=mejor_perfil.nombre,
            )

        return Resultado(
            False,
            mejor_parecido,
            f"Voz no reconocida ({mejor_parecido:.2f} < {liston:.2f}).",
        )

    def verificar(
        self,
        audio: np.ndarray,
        frecuencia: int = 16000,
        es_palabra_clave: bool = False,
    ) -> Resultado:
        """Nombre anterior de identificar(). Se mantiene por compatibilidad."""
        return self.identificar(audio, frecuencia, es_palabra_clave)

    def _mejor_coincidencia(
        self, audio: np.ndarray, frecuencia: int
    ) -> tuple[Perfil | None, float]:
        """El perfil que más se parece, probando varias posiciones del audio.

        El "hey ..." puede caer en cualquier punto del trozo capturado, y parte
        de ese trozo suele ser silencio o el final de otra frase. Medir solo el
        bloque entero mezcla la voz con lo que la rodea y baja el parecido sin
        motivo. No es hacer trampa: una voz distinta puntúa bajo en todas las
        posiciones, porque lo que se compara es el timbre y ese no cambia al
        desplazar la ventana.
        """
        muestras = np.asarray(audio, dtype=np.float32).flatten()
        candidatos = [self._extraer(muestras, frecuencia)]

        largo = int(frecuencia * SEGUNDOS_DE_VERIFICACION * 0.7)
        if muestras.size > largo:
            for inicio in np.linspace(
                0, muestras.size - largo, VENTANAS_DE_BUSQUEDA
            ).astype(int):
                trozo = muestras[inicio : inicio + largo]
                if float(np.sqrt(np.mean(trozo**2))) < 0.01:
                    continue
                try:
                    candidatos.append(self._extraer(trozo, frecuencia))
                except Exception:
                    continue

        mejor_perfil: Perfil | None = None
        mejor_parecido = 0.0
        for perfil in self.perfiles:
            for huella in candidatos:
                parecido = self._parecido(huella, perfil.huella)
                if parecido > mejor_parecido:
                    mejor_parecido, mejor_perfil = parecido, perfil

        return mejor_perfil, mejor_parecido

    # -- Persistencia --------------------------------------------------------

    def _guardar(self) -> None:
        RUTA_PERFILES.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(RUTA_PERFILES, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "version": VERSION_DE_CALIBRACION,
                        "perfiles": [
                            {
                                "nombre": p.nombre,
                                "huella": p.huella.tolist(),
                                "umbral": p.umbral,
                            }
                            for p in self.perfiles
                        ],
                    },
                    f,
                )
        except OSError:
            pass

    def _cargar(self) -> None:
        if not RUTA_PERFILES.exists():
            self._importar_formato_antiguo()
            return

        try:
            with open(RUTA_PERFILES, "r", encoding="utf-8") as f:
                datos = json.load(f)
            version = datos.get("version", 0)
            self.perfiles = [
                Perfil(
                    nombre=p["nombre"],
                    huella=np.array(p["huella"], dtype=np.float32),
                    umbral=p.get("umbral", UMBRAL),
                )
                for p in datos.get("perfiles", [])
            ]
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            # Un archivo corrupto se ignora: mejor pedir que se configure de
            # nuevo que arrancar con referencias sin sentido.
            self.perfiles = []
            return

        if version < VERSION_DE_CALIBRACION:
            self._recalibrar_todo()

    def _importar_formato_antiguo(self) -> None:
        """Convierte la huella única de versiones anteriores en un perfil.

        Sin esto, quien ya tuviera su voz registrada tendría que repetirlo solo
        porque el formato cambió, y eso ya pasó un par de veces en este
        proyecto. El audio es suyo y está en su disco; solo hay que leerlo.
        """
        if not RUTA_HUELLA_ANTIGUA.exists():
            return

        try:
            with open(RUTA_HUELLA_ANTIGUA, "r", encoding="utf-8") as f:
                datos = json.load(f)
            huella = np.array(datos["huella"], dtype=np.float32)
        except (OSError, json.JSONDecodeError, KeyError):
            return

        import os

        nombre = os.getenv("JARVIS_USUARIO", "").strip() or "Principal"
        self.perfiles = [
            Perfil(nombre=nombre, huella=huella, umbral=datos.get("umbral", UMBRAL))
        ]

        # Las muestras antiguas se mueven al formato nuevo, para poder
        # recalibrar más adelante sin repetir el registro.
        if RUTA_MUESTRAS_ANTIGUAS.exists():
            try:
                RUTA_MUESTRAS.mkdir(parents=True, exist_ok=True)
                destino = RUTA_MUESTRAS / f"{normalizar_nombre(nombre)}.npz"
                destino.write_bytes(RUTA_MUESTRAS_ANTIGUAS.read_bytes())
            except OSError:
                pass

        self._guardar()

    # -- Muestras y recalibración -------------------------------------------

    def _guardar_muestras(
        self, clave: str, grabaciones: list[np.ndarray], frecuencia: int
    ) -> None:
        """Guarda las grabaciones del registro, para poder recalibrar después.

        Es lo que evita repetir el registro cada vez que cambia la forma de
        calcular el umbral. Ya pasó dos veces: un ajuste del método dejaba la
        huella con un umbral que ya no correspondía, y la única salida era
        volver a grabar. El audio es tuyo y se queda en tu disco.
        """
        try:
            RUTA_MUESTRAS.mkdir(parents=True, exist_ok=True)
            np.savez_compressed(
                RUTA_MUESTRAS / f"{clave}.npz",
                frecuencia=frecuencia,
                **{f"g{i}": np.asarray(g) for i, g in enumerate(grabaciones)},
            )
        except Exception:
            pass

    def _cargar_muestras(self, clave: str) -> tuple[list[np.ndarray], int] | None:
        archivo = RUTA_MUESTRAS / f"{clave}.npz"
        if not archivo.exists():
            return None
        try:
            datos = np.load(archivo)
            frecuencia = int(datos["frecuencia"])
            grabaciones = [datos[c] for c in sorted(datos.files) if c.startswith("g")]
        except Exception:
            return None
        return (grabaciones, frecuencia) if len(grabaciones) >= 2 else None

    def _recalibrar_todo(self) -> None:
        """Recalcula los umbrales con las muestras guardadas de cada perfil."""
        for perfil in list(self.perfiles):
            muestras = self._cargar_muestras(perfil.clave)
            if muestras is None:
                # Sin muestras no se puede recalcular, pero tampoco hace falta
                # obligar a repetir el registro: los umbrales antiguos se
                # quedaban pegados a su suelo, así que traerlos al rango actual
                # es equivalente.
                perfil.umbral = float(
                    np.clip(perfil.umbral, UMBRAL_MINIMO, UMBRAL_MAXIMO)
                )
                continue
            try:
                self.registrar(perfil.nombre, *muestras)
            except ValueError:
                continue
        self._guardar()
