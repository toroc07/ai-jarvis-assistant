"""
El agente: el bucle que convierte una frase tuya en acciones.

Recibe lo que dices, se lo pasa al cerebro junto con el contexto y la lista de
habilidades, y si el modelo pide usar alguna, la ejecuta (siempre a través del
guardián) y le devuelve el resultado para que siga razonando. Termina cuando el
modelo da una respuesta en texto o cuando se agotan las vueltas permitidas.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from core.brain import Cerebro, Mensaje, Motor, Respuesta
from core.carencias import (
    Origen,
    fijar_contexto,
    parece_una_carencia,
    registro_de_carencias,
)
from core.honestidad import TEXTO_DE_DISCULPA, afirma_haber_actuado
from core.memory import Memoria
from core.texto import limpiar_para_hablar
from security.guard import Peticion, guardian
from skills.registro import registro

# Se importan por su efecto: al cargarse, se registran en el registro global.
import skills.archivos  # noqa: F401
import skills.carencias  # noqa: F401
import skills.clima  # noqa: F401
import skills.conversacion  # noqa: F401
import skills.humor  # noqa: F401
import skills.memoria  # noqa: F401
import skills.multimedia  # noqa: F401
import skills.sistema  # noqa: F401

MAX_VUELTAS = 6

# Quién es quién. Se leen del entorno (.env) para que cada cual ponga su nombre
# sin tocar el código, que es lo que permite compartir el proyecto tal cual.
NOMBRE_ASISTENTE = os.getenv("JARVIS_NOMBRE", "Jarvis")
NOMBRE_USUARIO = os.getenv("JARVIS_USUARIO", "tu usuario")
IDIOMA = os.getenv("JARVIS_IDIOMA", "español")

# Formas en que Jarvis afirma haber hecho algo. Se comprueban contra su
# respuesta cuando NO ejecutó ninguna herramienta: ahí, cualquiera de estas
# frases es mentira.

def nueva_id_de_sesion() -> str:
    """Identificador único de este arranque de Jarvis.

    Lleva la fecha y la hora para que sea legible al mirar la base de datos, y
    un sufijo aleatorio por si se abren dos instancias en el mismo segundo.
    """
    return (
        datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
    )

PROMPT_SISTEMA = """Eres {nombre_asistente}, el asistente personal de {nombre_usuario}. Hablas {idioma}.

Cómo te comportas:
- Eres directo y breve. Nada de preámbulos ni de repetir la pregunta.
- Si puedes hacer algo con una herramienta, la usas en lugar de explicar cómo
  se hace manualmente.
- Tienes sentido del humor seco y algo irónico. Un comentario con retranca de
  vez en cuando, sobre todo sobre ti mismo o sobre lo absurdo de la situación,
  pero SIN dejar de hacer lo que te piden y sin alargarte. Primero resuelves,
  y la gracia va en una frase corta al final, no antes. Si el usuario tiene
  prisa o el asunto es serio, te ahorras la broma.
- Cuando el usuario te cuente algo duradero sobre él (su nombre, a qué se
  dedica, cómo se llaman los suyos) o te diga cómo quiere que te comportes,
  guárdalo con recordar_dato sin que tenga que pedírtelo. Lo pasajero no: cómo
  se siente hoy o qué tiempo hace no son datos que guardar.
- Para contar un chiste usa contar_chiste y di EXACTAMENTE lo que te
  devuelva, palabra por palabra. No lo cambies, no lo resumas, no lo expliques
  y no te inventes otro: los que sacas por tu cuenta no tienen remate.

REGLA QUE NUNCA SE ROMPE: no digas que has hecho algo si no lo has hecho.
- Solo puedes afirmar que una acción se completó si usaste la herramienta y su
  resultado lo confirma. Lee siempre el resultado antes de responder.
- Si el resultado es un error o dice que algo no se encontró, díselo al usuario
  tal cual. Nunca lo maquilles ni lo conviertas en un éxito.
- Si no tienes una herramienta para lo que te piden, llama a anotar_carencia
  directamente y DESPUÉS di que no sabes hacerlo. NO preguntes si quieres que
  lo anote: anótalo sin preguntar, es solo una nota interna. Anotarlo no es
  haberlo hecho, así que sigue diciendo con claridad que no sabes.
- Elegir bien la herramienta importa tanto como usarla:
  · Música o vídeo ("abre YouTube y busca X", "ponme algo de Y", "reproduce Z")
    -> reproducir_en_youtube con la consulta. UNA sola llamada, nada más.
  · Una página web concreta -> abrir_url.
  · Un programa del PC -> abrir_app.
  Abrir el navegador vacío no sirve de nada: si querían ir a algún sitio, usa
  la herramienta que los lleva ahí.
- Cuando una acción se bloquea por la política de seguridad, se lo dices con
  naturalidad y le explicas qué tendría que cambiar en policy.yaml. No intentas
  rodear el bloqueo por otra vía.
- Si no sabes algo, lo dices. No te inventas rutas, archivos ni datos.
- Tus respuestas se van a leer en voz alta. Escribe como hablarías: sin
  emojis, sin asteriscos ni negritas, sin viñetas ni tablas, sin títulos con
  almohadillas. Texto plano y frases cortas.

Sobre tus límites:
- Solo puedes actuar sobre las carpetas y acciones que tu política autoriza.
  Es así a propósito, para no dañar el equipo.
- Si una petición necesita un razonamiento que se te escapa (código complejo,
  análisis largo, planificación en muchos pasos), responde únicamente con
  [NECESITO_AYUDA] y una frase diciendo qué hace falta. Se derivará a un modelo
  más capaz.
"""


@dataclass
class Resultado:
    texto: str
    motor: Motor
    acciones: list[str]


class Agente:
    def __init__(
        self,
        cerebro: Cerebro | None = None,
        memoria: Memoria | None = None,
        sesion: str | None = None,
    ) -> None:
        self.cerebro = cerebro or Cerebro()
        self.memoria = memoria or Memoria()

        # Una sesión es cada arranque completo de Jarvis, no cada día. Cerrar
        # la ventana lo deja en segundo plano y la sesión continúa; solo
        # apagarlo del todo la cierra y abre otra al volver a ejecutarlo.
        self.sesion = sesion or nueva_id_de_sesion()
        self.memoria.abrir_sesion(self.sesion)

        # Guardar un hecho cambia el prompt de sistema, y Ollama solo
        # reaprovecha el trabajo si el prompt coincide carácter por carácter.
        # Sin esto, la pregunta siguiente a recordar algo volvería a costar los
        # nueve segundos de procesar el prompt entero. Se recalienta en otro
        # hilo para no hacer esperar a nadie.
        skills.memoria.fijar_aviso_de_cambio(self._recalentar_en_segundo_plano)

    def _recalentar_en_segundo_plano(self) -> None:
        """Vuelve a dejar el prompt procesado tras cambiar los hechos."""
        import threading

        def trabajo() -> None:
            try:
                self.calentar()
            except Exception:
                # Fallar aquí solo significa una pregunta lenta, no un error.
                pass

        threading.Thread(target=trabajo, daemon=True).start()

    def calentar(self) -> float:
        """Deja el modelo listo antes de la primera pregunta.

        Usa exactamente el mismo prompt de sistema y las mismas herramientas
        que se usarán después, porque Ollama solo reaprovecha el trabajo si el
        principio del prompt coincide carácter por carácter.
        """
        if not self.cerebro.local.disponible():
            return 0.0
        return self.cerebro.local.calentar(self._prompt_sistema(), registro.esquemas())

    def _prompt_sistema(self) -> str:
        sistema = PROMPT_SISTEMA.format(
            nombre_asistente=NOMBRE_ASISTENTE,
            nombre_usuario=NOMBRE_USUARIO,
            idioma=IDIOMA,
        )
        hechos = self.memoria.resumen_para_prompt()
        if hechos:
            sistema += "\n\n" + hechos
        return sistema

    def _construir_contexto(self, peticion: str) -> list[Mensaje]:
        mensajes = [Mensaje("system", self._prompt_sistema())]
        for turno in self.memoria.historial(self.sesion, limite=12):
            if turno.rol in ("user", "assistant"):
                mensajes.append(Mensaje(turno.rol, turno.contenido))
        mensajes.append(Mensaje("user", peticion))
        return mensajes

    def responder(
        self,
        peticion: str,
        pedir_confirmacion: Callable[[Peticion], bool] | None = None,
        al_recibir_texto: Callable[[str], None] | None = None,
        al_usar_herramienta: Callable[[str], None] | None = None,
    ) -> Resultado:
        """Procesa una petición completa y devuelve la respuesta final.

        Las dos funciones opcionales sirven para ir informando por el camino:
        'al_recibir_texto' recibe la respuesta a trozos según se genera, y
        'al_usar_herramienta' avisa de cada acción que se va ejecutando. Sin
        ellas el comportamiento es el mismo, solo que sin avisos intermedios.
        """
        # Cada petición empieza con el contador de acciones a cero, para que el
        # tope por petición signifique lo que dice.
        guardian.nueva_peticion()
        # Se guarda qué pediste, para que una carencia detectada durante
        # este turno quede anotada junto a tus palabras y no solo con el
        # nombre de la herramienta que faltaba.
        fijar_contexto(peticion, self.sesion)

        self.memoria.guardar_turno(self.sesion, "user", peticion)
        mensajes = self._construir_contexto(peticion)
        herramientas = registro.esquemas()
        ejecutadas: list[str] = []

        respuesta: Respuesta | None = None
        for _ in range(MAX_VUELTAS):
            respuesta = self.cerebro.responder(
                mensajes, herramientas, al_recibir_texto=al_recibir_texto
            )

            # Si el modelo escribió la llamada como texto en vez de emitirla
            # por el canal de herramientas, se rescata aquí. Sin esto la acción
            # simplemente no ocurriría, y encima verías el texto crudo.
            if not respuesta.herramientas:
                rescatadas = self._rescatar_llamada_en_texto(respuesta.texto)
                if rescatadas:
                    respuesta.herramientas = rescatadas
                    respuesta.texto = ""

            if not respuesta.herramientas:
                break

            # El modelo quiere usar herramientas: se ejecutan y se le devuelve
            # el resultado para que continúe con esa información.
            mensajes.append(Mensaje("assistant", respuesta.texto or ""))
            for llamada in respuesta.herramientas:
                nombre, argumentos = self._extraer_llamada(llamada)
                if al_usar_herramienta:
                    al_usar_herramienta(nombre)
                resultado = registro.invocar(nombre, argumentos, pedir_confirmacion)
                ejecutadas.append(nombre)
                # Queda anotado en la sesión, para que Jarvis pueda responder
                # "¿qué has hecho?" con hechos y no con lo que crea recordar.
                self.memoria.registrar_accion(
                    self.sesion,
                    nombre,
                    str(argumentos.get("ruta") or argumentos.get("url")
                        or argumentos.get("nombre") or ""),
                    str(resultado),
                )
                mensajes.append(
                    Mensaje("user", f"[resultado de {nombre}]\n{resultado}")
                )
        else:
            # Se agotaron las vueltas sin que el modelo cerrara la respuesta.
            texto = (
                "Me he quedado dando vueltas sin terminar la tarea. "
                "¿Puedes decírmelo de otra forma?"
            )
            self.memoria.guardar_turno(self.sesion, "assistant", texto)
            return Resultado(texto, Motor.LOCAL, ejecutadas)

        # Se limpia una sola vez y para todo: la ventana y la voz enseñan
        # lo mismo, y ni una ni otra reciben emojis ni asteriscos.
        texto = limpiar_para_hablar((respuesta.texto if respuesta else ""))

        # Red de seguridad contra la mentira: si dice que hizo algo sin haber
        # ejecutado nada, se le da una oportunidad de corregirse con un aviso
        # explícito. Si insiste, se sustituye su respuesta por la verdad: es
        # preferible un "no he podido" a un éxito inventado, porque un
        # asistente que miente invalida todas sus demás respuestas.
        if not ejecutadas and self._afirma_haber_actuado(texto):
            texto, ejecutadas = self._reintentar_sin_mentir(
                mensajes, herramientas, texto, pedir_confirmacion
            )

        if not texto:
            texto = "Hecho." if ejecutadas else "No he sabido qué responder."

        self.memoria.guardar_turno(
            self.sesion, "assistant", texto, respuesta.motor.value if respuesta else None
        )

        # Última red: si Jarvis dijo que no sabe hacer algo y no ejecutó nada,
        # queda anotado aunque no llamara a anotar_carencia. Es la vía que no
        # depende de que el modelo colabore, y por eso es la que de verdad
        # sostiene el registro.
        if parece_una_carencia(texto, bool(ejecutadas)):
            registro_de_carencias.anotar(
                Origen.DEDUCIDA_DE_LA_RESPUESTA,
                que_falta=peticion[:120],
                peticion=peticion,
                sesion=self.sesion,
                detalle=f"Jarvis respondió: {texto[:200]}",
            )

        return Resultado(texto, respuesta.motor if respuesta else Motor.LOCAL, ejecutadas)

    def _reintentar_sin_mentir(
        self,
        mensajes: list[Mensaje],
        herramientas: list[dict],
        texto_original: str,
        pedir_confirmacion,
    ) -> tuple[str, list[str]]:
        """Da una segunda oportunidad tras detectar una afirmación falsa."""
        aviso = (
            "ALTO. Has dicho que hiciste algo, pero no llamaste a ninguna "
            "herramienta, así que NO se ha hecho nada. Si quieres hacerlo, "
            "llama ahora a la herramienta correspondiente. Si no puedes, dilo "
            "claramente sin fingir que lo hiciste."
        )
        mensajes = mensajes + [
            Mensaje("assistant", texto_original),
            Mensaje("user", aviso),
        ]

        try:
            respuesta = self.cerebro.responder(mensajes, herramientas)
        except Exception:
            return self._texto_de_disculpa(), []

        if not respuesta.herramientas:
            rescatadas = self._rescatar_llamada_en_texto(respuesta.texto)
            if rescatadas:
                respuesta.herramientas = rescatadas

        if not respuesta.herramientas:
            # Insistió sin actuar: se dice la verdad en su lugar.
            return self._texto_de_disculpa(), []

        ejecutadas: list[str] = []
        for llamada in respuesta.herramientas:
            nombre, argumentos = self._extraer_llamada(llamada)
            resultado = registro.invocar(nombre, argumentos, pedir_confirmacion)
            ejecutadas.append(nombre)
            self.memoria.registrar_accion(
                self.sesion, nombre, "", str(resultado)
            )
            mensajes.append(
                Mensaje("user", f"[resultado de {nombre}]\n{resultado}")
            )

        try:
            final = self.cerebro.responder(mensajes, herramientas)
            return (final.texto or "Hecho.").strip(), ejecutadas
        except Exception:
            return "Hecho.", ejecutadas

    @staticmethod
    def _texto_de_disculpa() -> str:
        return TEXTO_DE_DISCULPA

    @staticmethod
    def _afirma_haber_actuado(texto: str) -> bool:
        """Contraparte en código de la regla de no mentir (ver honestidad.py)."""
        return afirma_haber_actuado(texto)

    @staticmethod
    def _rescatar_llamada_en_texto(texto: str) -> list[dict] | None:
        """Detecta una llamada a herramienta que el modelo escribió como texto.

        Los modelos pequeños fallan en esto con cierta frecuencia: en lugar de
        emitir la llamada por el canal de herramientas, la escriben en la
        respuesta, así:

            anotar_carencia
            {"capacidad": "poner alarmas", "detalle": "..."}

        Sin rescatarla, la acción no se ejecuta y además el usuario ve ese
        churro en pantalla. Se comprueba que el nombre exista de verdad en el
        registro, así que esto no puede inventar acciones: si el nombre no
        corresponde a una habilidad registrada, se devuelve None y el texto se
        trata como una respuesta normal.
        """
        if not texto or "{" not in texto:
            return None

        limpio = texto.strip()
        disponibles = {n.lower(): n for n in registro.listar()}

        # Forma 1: el nombre en una línea y los argumentos en la siguiente.
        lineas = limpio.split("\n", 1)
        if len(lineas) == 2:
            posible = lineas[0].strip().strip("`*:").lower()
            if posible in disponibles:
                try:
                    argumentos = json.loads(lineas[1].strip().strip("`"))
                except json.JSONDecodeError:
                    argumentos = None
                if isinstance(argumentos, dict):
                    return [
                        {"name": disponibles[posible], "arguments": argumentos}
                    ]

        # Forma 2: un JSON con el nombre dentro, que es como lo escribe el
        # modelo cuando intenta imitar el formato de la API.
        try:
            datos = json.loads(limpio.strip("`"))
        except json.JSONDecodeError:
            return None

        if not isinstance(datos, dict):
            return None

        nombre = str(datos.get("name") or datos.get("nombre") or "").lower()
        if nombre not in disponibles:
            return None

        argumentos = datos.get("arguments") or datos.get("parameters") or {}
        if not isinstance(argumentos, dict):
            return None

        return [{"name": disponibles[nombre], "arguments": argumentos}]

    @staticmethod
    def _extraer_llamada(llamada: dict) -> tuple[str, dict]:
        """Normaliza el formato de llamada, que difiere entre Ollama y Claude."""
        # Ollama: {"function": {"name": ..., "arguments": {...}}}
        if "function" in llamada:
            funcion = llamada["function"]
            nombre = funcion.get("name", "")
            argumentos = funcion.get("arguments", {})
        else:
            # Claude: {"name": ..., "arguments": {...}}
            nombre = llamada.get("name", "")
            argumentos = llamada.get("arguments", {})

        # Algunos modelos devuelven los argumentos como cadena JSON.
        if isinstance(argumentos, str):
            try:
                argumentos = json.loads(argumentos)
            except json.JSONDecodeError:
                argumentos = {}

        return nombre, argumentos if isinstance(argumentos, dict) else {}
