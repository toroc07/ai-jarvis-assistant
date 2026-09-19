"""
El guardián de Jarvis.

Toda acción que Jarvis quiera ejecutar sobre el sistema pasa por aquí primero.
Ninguna habilidad habla con el sistema operativo directamente: piden permiso,
y el guardián decide contra la política definida en policy.yaml.

El diseño parte de asumir que el modelo puede equivocarse o ser manipulado.
Por eso las comprobaciones no confían en lo que el modelo dice que va a hacer,
sino que verifican la acción ya resuelta: la ruta real tras seguir enlaces, el
comando ya montado, la extensión efectiva del archivo.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Callable

import yaml

from security.parada import interruptor

RAIZ = Path(__file__).resolve().parent.parent
RUTA_POLITICA = RAIZ / "security" / "policy.yaml"

# Cuántos intentos de hacer algo prohibido, y en cuánto tiempo, bastan para
# que Jarvis se detenga solo. Dos son deliberadamente pocos: el coste de una
# parada de más es que la rearmes; el de una de menos puede ser tu equipo.
INTENTOS_ANTES_DE_PARAR = 2
MINUTOS_DE_VIGILANCIA = 5


class Nivel(str, Enum):
    """Qué hacer con una acción según la política."""

    PERMITIR = "permitir"
    CONFIRMAR = "confirmar"
    PROHIBIR = "prohibir"


class Decision(str, Enum):
    """Resultado de una evaluación del guardián."""

    CONCEDIDO = "concedido"
    NECESITA_CONFIRMACION = "necesita_confirmacion"
    DENEGADO = "denegado"


@dataclass
class Peticion:
    """Una acción que una habilidad quiere ejecutar.

    'accion' debe ser una de las claves de la sección 'acciones' de policy.yaml.
    'objetivo' es sobre qué actúa: una ruta, un comando, un nombre de app.
    """

    accion: str
    objetivo: str = ""
    detalles: dict[str, Any] = field(default_factory=dict)
    motivo: str = ""  # Explicación en lenguaje natural, para mostrarte al confirmar.


@dataclass
class Veredicto:
    """Lo que el guardián responde a una petición."""

    decision: Decision
    razon: str
    peticion: Peticion

    @property
    def permitida(self) -> bool:
        return self.decision is Decision.CONCEDIDO


class ErrorDePolitica(Exception):
    """Se lanza cuando una acción viola la política de seguridad."""


class Guardian:
    """Aplica la política de seguridad y deja registro de todo."""

    def __init__(self, ruta_politica: Path = RUTA_POLITICA) -> None:
        self.ruta_politica = ruta_politica
        self.politica = self._cargar_politica()
        self._historial_confirmaciones: list[datetime] = []
        self._acciones_peticion_actual = 0
        # Intentos de hacer algo prohibido, para detectar insistencia.
        self._intentos_prohibidos: list[datetime] = []
        self._acciones_prohibidas: list[str] = []

        registro = self.politica.get("registro", {})
        self.registro_activo = registro.get("activo", True)
        self.ruta_registro = RAIZ / registro.get("ruta", "security/audit.log")
        self.registrar_rechazadas = registro.get("incluir_rechazadas", True)

    # -- Carga de la política ------------------------------------------------

    def _cargar_politica(self) -> dict[str, Any]:
        if not self.ruta_politica.exists():
            raise ErrorDePolitica(
                f"No existe la política de seguridad en {self.ruta_politica}. "
                "Jarvis no arranca sin ella."
            )
        with open(self.ruta_politica, "r", encoding="utf-8") as f:
            politica = yaml.safe_load(f)

        # Sin estas secciones no se puede decidir nada, así que es mejor fallar
        # al arrancar que descubrirlo a mitad de una operación.
        for seccion in ("acciones", "rutas", "archivos"):
            if seccion not in politica:
                raise ErrorDePolitica(
                    f"La política no tiene la sección obligatoria '{seccion}'."
                )
        return politica

    def recargar(self) -> None:
        """Vuelve a leer policy.yaml. Útil tras editarla a mano."""
        self.politica = self._cargar_politica()

    # -- Evaluación principal ------------------------------------------------

    def evaluar(self, peticion: Peticion) -> Veredicto:
        """Decide si una acción puede ejecutarse. No ejecuta nada."""
        veredicto = self._evaluar_sin_registrar(peticion)
        if self.registro_activo and (
            veredicto.permitida or self.registrar_rechazadas
        ):
            self._registrar(veredicto)
        return veredicto

    def _evaluar_sin_registrar(self, peticion: Peticion) -> Veredicto:
        def denegar(razon: str) -> Veredicto:
            return Veredicto(Decision.DENEGADO, razon, peticion)

        # 0. El interruptor de emergencia gana sobre todo lo demás. Va primero
        #    para que estando activado no se evalúe ni se registre nada más.
        if interruptor.activado:
            return denegar(
                "El interruptor de emergencia está activado. Jarvis no puede "
                "ejecutar ninguna acción hasta que lo rearmes desde la ventana "
                "o el menú de la bandeja."
            )

        # 1. Tope de acciones por petición: corta bucles del modelo.
        limites = self.politica.get("limites", {})
        max_acciones = limites.get("max_acciones_por_peticion", 15)
        if self._acciones_peticion_actual >= max_acciones:
            return denegar(
                f"Se alcanzó el límite de {max_acciones} acciones en una sola "
                "petición. Se detiene por seguridad."
            )

        # 2. La acción debe existir en la política. Una acción desconocida es
        #    una acción no autorizada: no hay comportamiento por defecto.
        nivel_bruto = self.politica["acciones"].get(peticion.accion)
        if nivel_bruto is None:
            # Puede ser una habilidad que falta o solo un permiso sin añadir.
            # La diferencia se decide al revisar, no aquí.
            from core.carencias import Origen, contexto, registro_de_carencias

            peticion_original, sesion = contexto()
            registro_de_carencias.anotar(
                Origen.ACCION_NO_PERMITIDA,
                que_falta=peticion.accion,
                peticion=peticion_original,
                sesion=sesion,
                detalle=f"Sobre: {peticion.objetivo}" if peticion.objetivo else "",
            )
            return denegar(
                f"La acción '{peticion.accion}' no está en la lista blanca. "
                "Para permitirla, añádela a policy.yaml."
            )

        try:
            nivel = Nivel(nivel_bruto)
        except ValueError:
            return denegar(
                f"La acción '{peticion.accion}' tiene un nivel inválido "
                f"('{nivel_bruto}') en la política."
            )

        if nivel is Nivel.PROHIBIR:
            self._anotar_intento_prohibido(peticion)
            return denegar(
                f"La acción '{peticion.accion}' está prohibida de forma "
                "absoluta. No se puede autorizar ni confirmándola."
            )

        # 3. Validaciones específicas según el tipo de acción.
        if self._es_accion_de_archivo(peticion.accion):
            problema = self._validar_ruta(peticion)
            if problema:
                return denegar(problema)

        if peticion.accion == "ejecutar_comando":
            problema = self._validar_comando(peticion.objetivo)
            if problema:
                return denegar(problema)

        # 4. Tope de confirmaciones por hora: evita que te acostumbres a decir
        #    que sí en cadena y que un fallo se convierta en muchos.
        if nivel is Nivel.CONFIRMAR:
            if not self._hay_cupo_de_confirmaciones():
                max_conf = limites.get("max_confirmaciones_por_hora", 30)
                return denegar(
                    f"Se superó el límite de {max_conf} confirmaciones por hora. "
                    "Es una señal de que algo se está descontrolando."
                )
            return Veredicto(
                Decision.NECESITA_CONFIRMACION,
                "Esta acción modifica tu sistema y necesita tu aprobación.",
                peticion,
            )

        return Veredicto(Decision.CONCEDIDO, "Acción permitida por la política.", peticion)

    # -- Validaciones específicas -------------------------------------------

    _ACCIONES_DE_ARCHIVO = {
        "leer_archivo",
        "listar_carpeta",
        "buscar_archivos",
        "crear_archivo",
        "escribir_archivo",
        "mover_archivo",
        "copiar_archivo",
        "borrar_archivo",
        "crear_carpeta",
    }

    _ACCIONES_DE_ESCRITURA = {
        "crear_archivo",
        "escribir_archivo",
        "mover_archivo",
        "copiar_archivo",
        "borrar_archivo",
        "crear_carpeta",
    }

    def _es_accion_de_archivo(self, accion: str) -> bool:
        return accion in self._ACCIONES_DE_ARCHIVO

    def _validar_ruta(self, peticion: Peticion) -> str | None:
        """Devuelve el motivo del rechazo, o None si la ruta es aceptable."""
        if not peticion.objetivo:
            return "La acción sobre archivos no indica ninguna ruta."

        # resolve() sigue enlaces simbólicos y normaliza '..'. Sin esto, una
        # ruta como "Documents/../../Windows/System32" pasaría los filtros.
        try:
            ruta = Path(peticion.objetivo).expanduser().resolve()
        except (OSError, ValueError) as e:
            return f"La ruta '{peticion.objetivo}' no es válida: {e}"

        rutas = self.politica["rutas"]

        # Las prohibidas ganan sobre todo lo demás. Se comprueban primero.
        for prohibida in rutas.get("prohibidas", []):
            if self._esta_dentro(ruta, prohibida):
                return (
                    f"La ruta está dentro de '{prohibida}', que es una zona "
                    "prohibida de forma permanente."
                )

        escribe = peticion.accion in self._ACCIONES_DE_ESCRITURA
        permitidas = rutas.get("escritura" if escribe else "lectura", [])
        etiqueta = "escritura" if escribe else "lectura"

        if not any(self._esta_dentro(ruta, base) for base in permitidas):
            return (
                f"La ruta '{ruta}' está fuera de las carpetas autorizadas para "
                f"{etiqueta}. Carpetas permitidas: {', '.join(permitidas)}."
            )

        # Extensión: solo para archivos, no para carpetas.
        if peticion.accion not in ("listar_carpeta", "crear_carpeta", "buscar_archivos"):
            extensiones = self.politica["archivos"].get("extensiones_permitidas", [])
            if extensiones and ruta.suffix.lower() not in extensiones:
                tipo = ruta.suffix or "(sin extensión)"
                return (
                    f"El tipo de archivo '{tipo}' no está permitido. "
                    f"Permitidos: {', '.join(extensiones)}."
                )

        # Tamaño: solo tiene sentido al leer algo que ya existe.
        if peticion.accion == "leer_archivo" and ruta.is_file():
            max_mb = self.politica["archivos"].get("max_mb_lectura", 25)
            tam_mb = ruta.stat().st_size / (1024 * 1024)
            if tam_mb > max_mb:
                return (
                    f"El archivo pesa {tam_mb:.1f} MB y el límite de lectura es "
                    f"{max_mb} MB."
                )

        return None

    def _validar_comando(self, comando: str) -> str | None:
        """Devuelve el motivo del rechazo, o None si el comando es aceptable."""
        if not comando or not comando.strip():
            return "No se indicó ningún comando."

        config = self.politica.get("comandos", {})
        comando_limpio = comando.strip()

        # Los patrones prohibidos se buscan en el comando entero, sin distinguir
        # mayúsculas, porque incluyen operadores para encadenar órdenes.
        bajo = comando_limpio.lower()
        for patron in config.get("patrones_prohibidos", []):
            if patron.lower() in bajo:
                return (
                    f"El comando contiene '{patron}', que está bloqueado por "
                    "permitir encadenar o esconder otras órdenes."
                )

        programa = comando_limpio.split()[0].lower()
        programa = Path(programa).name.removesuffix(".exe")

        permitidos = [p.lower() for p in config.get("programas_permitidos", [])]
        if programa not in permitidos:
            return (
                f"El programa '{programa}' no está en la lista de programas "
                f"autorizados: {', '.join(permitidos)}."
            )

        return None

    @staticmethod
    def _esta_dentro(ruta: Path, base: str) -> bool:
        """True si 'ruta' es 'base' o está por debajo de ella."""
        try:
            base_resuelta = Path(base).expanduser().resolve()
        except (OSError, ValueError):
            return False
        try:
            ruta.relative_to(base_resuelta)
            return True
        except ValueError:
            return False

    def _anotar_intento_prohibido(self, peticion: Peticion) -> None:
        """Cuenta los intentos de hacer algo prohibido y para si insisten.

        Un intento suelto suele ser el modelo equivocándose: pide formatear
        cuando quería listar. Varios seguidos son otra cosa, y da igual si es un
        fallo del modelo o alguien manipulándolo con lo que le dice: en ambos
        casos lo correcto es parar y que lo mires tú.
        """
        ahora = datetime.now()
        self._acciones_prohibidas.append(peticion.accion)
        ventana = ahora - timedelta(minutes=MINUTOS_DE_VIGILANCIA)
        self._intentos_prohibidos = [
            t for t in self._intentos_prohibidos if t > ventana
        ] + [ahora]

        if len(self._intentos_prohibidos) < INTENTOS_ANTES_DE_PARAR:
            return

        acciones = ", ".join(
            sorted({p.accion for p in [peticion]} | set(self._acciones_prohibidas))
        )
        interruptor.activar(
            motivo=(
                f"Jarvis intentó {len(self._intentos_prohibidos)} acciones "
                f"prohibidas en {MINUTOS_DE_VIGILANCIA} minutos "
                f"(la última: {peticion.accion} sobre '{peticion.objetivo}'). "
                f"Acciones implicadas: {acciones}. "
                "Se detuvo solo para que revises qué está pasando."
            ),
            quien="automatica",
        )

    def _hay_cupo_de_confirmaciones(self) -> bool:
        limite = self.politica.get("limites", {}).get("max_confirmaciones_por_hora", 30)
        hace_una_hora = datetime.now() - timedelta(hours=1)
        self._historial_confirmaciones = [
            t for t in self._historial_confirmaciones if t > hace_una_hora
        ]
        return len(self._historial_confirmaciones) < limite

    # -- Ejecución controlada ------------------------------------------------

    def ejecutar(
        self,
        peticion: Peticion,
        funcion: Callable[[], Any],
        pedir_confirmacion: Callable[[Peticion], bool] | None = None,
    ) -> Any:
        """Evalúa la petición y solo entonces ejecuta 'funcion'.

        Este es el único camino por el que una habilidad debe llegar al sistema.
        Si la política pide confirmación, se llama a 'pedir_confirmacion', que
        es quien muestra la acción al usuario y devuelve True o False.
        """
        veredicto = self.evaluar(peticion)

        if veredicto.decision is Decision.DENEGADO:
            raise ErrorDePolitica(veredicto.razon)

        if veredicto.decision is Decision.NECESITA_CONFIRMACION:
            if pedir_confirmacion is None:
                raise ErrorDePolitica(
                    f"'{peticion.accion}' requiere confirmación y no hay forma de "
                    "pedírtela en este contexto. Se cancela."
                )
            if not pedir_confirmacion(peticion):
                self._registrar(
                    Veredicto(Decision.DENEGADO, "Rechazada por el usuario.", peticion)
                )
                raise ErrorDePolitica("Has rechazado la acción.")
            self._historial_confirmaciones.append(datetime.now())

        self._acciones_peticion_actual += 1
        return funcion()

    def nueva_peticion(self) -> None:
        """Reinicia el contador de acciones. Se llama al empezar cada turno."""
        self._acciones_peticion_actual = 0

    # -- Registro ------------------------------------------------------------

    def _registrar(self, veredicto: Veredicto) -> None:
        entrada = {
            "momento": datetime.now().isoformat(timespec="seconds"),
            "accion": veredicto.peticion.accion,
            "objetivo": veredicto.peticion.objetivo,
            "decision": veredicto.decision.value,
            "razon": veredicto.razon,
        }
        try:
            self.ruta_registro.parent.mkdir(parents=True, exist_ok=True)
            with open(self.ruta_registro, "a", encoding="utf-8") as f:
                f.write(json.dumps(entrada, ensure_ascii=False) + "\n")
        except OSError:
            # Un fallo al registrar nunca debe tumbar a Jarvis, pero tampoco
            # debe pasar desapercibido.
            print(f"[guardian] No se pudo escribir en el registro: {self.ruta_registro}")


# Instancia compartida: todas las habilidades usan el mismo guardián para que
# los contadores y el registro sean coherentes.
guardian = Guardian()
