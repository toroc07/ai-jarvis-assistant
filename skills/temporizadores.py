"""
Temporizadores y recordatorios.

"Avísame en diez minutos" o "recuérdame a las seis que llame a mamá". Viven en
la memoria del proceso: si apagas Jarvis del todo se pierden, y la habilidad se
lo dice al modelo para que no prometa lo contrario. Cerrar la ventana no los
borra, porque Jarvis sigue en la bandeja.

Cuando vence uno, se llama a la función de aviso que haya fijado la interfaz
(que lo dice en voz alta y lo muestra en la bandeja). No tocan el sistema: solo
la propia memoria de Jarvis.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable

from skills.registro import registro

# Límites contra un modelo que se embale: decenas de temporizadores o uno a
# tres semanas vista no son un uso real de un asistente de escritorio.
MAX_ACTIVOS = 20
MAX_HORAS = 24
MAX_MENSAJE = 200

_aviso: Callable[[str], None] | None = None
_lock = threading.Lock()
_activos: dict[int, "Temporizador"] = {}
_siguiente = 1


@dataclass
class Temporizador:
    numero: int
    mensaje: str
    vence: datetime
    _hilo: threading.Timer | None = field(default=None, repr=False)


def fijar_aviso(funcion: Callable[[str], None] | None) -> None:
    """Qué hacer cuando vence uno. Lo fija la interfaz al arrancar."""
    global _aviso
    _aviso = funcion


def _sonar(numero: int) -> None:
    with _lock:
        temporizador = _activos.pop(numero, None)
    if temporizador is None:
        return
    texto = f"Recordatorio: {temporizador.mensaje}." if temporizador.mensaje else (
        "Se ha acabado el temporizador."
    )
    if _aviso is not None:
        try:
            _aviso(texto)
            return
        except Exception:
            import traceback

            traceback.print_exc()
    print(f"[temporizador] {texto}", flush=True)


def _programar(vence: datetime, mensaje: str) -> Temporizador | str:
    """Crea el temporizador, o devuelve el motivo por el que no se puede."""
    global _siguiente

    segundos = (vence - datetime.now()).total_seconds()
    if segundos <= 0:
        return "Esa hora ya ha pasado. NO digas que lo has programado."
    if segundos > MAX_HORAS * 3600:
        return (
            f"Solo puedo programar avisos para las próximas {MAX_HORAS} horas. "
            "NO digas que lo has programado."
        )

    with _lock:
        if len(_activos) >= MAX_ACTIVOS:
            return (
                f"Ya hay {MAX_ACTIVOS} avisos pendientes, que es el máximo. "
                "NO digas que lo has programado."
            )
        numero = _siguiente
        _siguiente += 1
        temporizador = Temporizador(numero, mensaje.strip()[:MAX_MENSAJE], vence)
        hilo = threading.Timer(segundos, _sonar, args=(numero,))
        hilo.daemon = True
        temporizador._hilo = hilo
        _activos[numero] = temporizador
        hilo.start()
    return temporizador


def _cuando(vence: datetime) -> str:
    falta = vence - datetime.now()
    minutos = max(0, round(falta.total_seconds() / 60))
    if minutos < 1:
        cuanto = "menos de un minuto"
    elif minutos < 60:
        cuanto = f"{minutos} minuto{'s' if minutos != 1 else ''}"
    else:
        horas, resto = divmod(minutos, 60)
        cuanto = f"{horas} h {resto} min" if resto else f"{horas} h"
    return f"a las {vence.strftime('%H:%M')} (dentro de {cuanto})"


_NOTA_VOLATIL = " Si apagas Jarvis del todo antes, el aviso se pierde."


_NUMEROS = {
    "un": 1, "una": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
    "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "quince": 15,
    "veinte": 20, "treinta": 30, "cuarenta": 40, "cuarenta y cinco": 45,
}
_UNIDADES = {"segundo": 1 / 60, "seg": 1 / 60, "minuto": 1, "min": 1, "hora": 60, "h": 60}


def interpretar_tiempo(tiempo: object) -> float | None:
    """Convierte lo que diga el modelo ('5 minutos', 'media hora', 90) en minutos.

    Los modelos pequeños no respetan el formato pedido: tan pronto mandan 5
    como "5 minutos" o "media hora". Se acepta todo lo razonable en vez de
    fallar con un error que el modelo no sabe corregir.
    """
    if isinstance(tiempo, (int, float)):
        return float(tiempo)
    texto = " ".join(str(tiempo or "").lower().replace(",", ".").split())
    if not texto:
        return None
    if texto in ("media hora", "1/2 hora"):
        return 30.0
    if texto in ("un cuarto de hora", "cuarto de hora"):
        return 15.0
    if texto in ("hora y media", "una hora y media"):
        return 90.0

    total = 0.0
    encontrado = False
    numeros = "|".join(sorted(_NUMEROS, key=len, reverse=True))
    patron = rf"\b(\d+(?:\.\d+)?|{numeros})\s*([a-z]+)?"
    for cantidad, unidad in re.findall(patron, texto):
        valor = float(cantidad) if cantidad[0].isdigit() else float(_NUMEROS[cantidad])
        factor = 1.0
        if unidad:
            factor = next((f for u, f in _UNIDADES.items() if unidad.startswith(u)), None)
            if factor is None:
                continue
        total += valor * factor
        encontrado = True
    if "y media" in texto and encontrado:
        total += 30 if "hora" in texto else 0.5
    return total if encontrado else None


@registro.registrar(
    nombre="poner_temporizador",
    descripcion=(
        "ES LA HERRAMIENTA CORRECTA para 'avísame en 10 minutos', 'pon un "
        "temporizador de media hora' o 'recuérdame en una hora que saque la "
        "ropa'. Sí sabes poner temporizadores: úsala."
    ),
    accion="temporizador",
    parametros={
        "tiempo": {
            "type": "string",
            "description": "Cuánto esperar, tal cual: '5 minutos', 'media hora', '1 hora'.",
        },
        "mensaje": {
            "type": "string",
            "description": "Qué recordar al sonar, si lo dijo. Si no, déjalo vacío.",
            "requerido": False,
        },
    },
)
def poner_temporizador(tiempo: str, mensaje: str = "") -> str:
    minutos = interpretar_tiempo(tiempo)
    if minutos is None or minutos <= 0:
        return (
            f"No entiendo '{tiempo}' como un tiempo. Usa algo como '5 minutos'. "
            "NO digas que lo has puesto."
        )
    resultado = _programar(datetime.now() + timedelta(minutes=minutos), mensaje or "")
    if isinstance(resultado, str):
        return resultado
    return f"Temporizador {resultado.numero} puesto: sonará {_cuando(resultado.vence)}.{_NOTA_VOLATIL}"


_HORA = re.compile(r"^\s*(\d{1,2})(?:[:.h](\d{2}))?\s*$")


@registro.registrar(
    nombre="poner_recordatorio",
    descripcion=(
        "Programa un aviso a una hora concreta del día. Úsala para 'recuérdame "
        "a las seis que llame a mamá'. Si esa hora ya pasó hoy, será mañana."
    ),
    accion="temporizador",
    parametros={
        "hora": {
            "type": "string",
            "description": "Hora en formato 24 horas, como '18:00' o '7:30'.",
        },
        "mensaje": {"type": "string", "description": "Qué hay que recordar."},
    },
)
def poner_recordatorio(hora: str, mensaje: str) -> str:
    coincidencia = _HORA.match(hora or "")
    if not coincidencia:
        return f"No entiendo la hora '{hora}'. Usa el formato 18:30. NO digas que lo has programado."
    horas, minutos = int(coincidencia.group(1)), int(coincidencia.group(2) or 0)
    if horas > 23 or minutos > 59:
        return f"'{hora}' no es una hora válida. NO digas que lo has programado."

    ahora = datetime.now()
    vence = ahora.replace(hour=horas, minute=minutos, second=0, microsecond=0)
    if vence <= ahora:
        vence += timedelta(days=1)

    resultado = _programar(vence, mensaje or "")
    if isinstance(resultado, str):
        return resultado
    dia = "mañana " if vence.date() != ahora.date() else ""
    return f"Recordatorio {resultado.numero} programado: sonará {dia}{_cuando(vence)}.{_NOTA_VOLATIL}"


@registro.registrar(
    nombre="listar_temporizadores",
    descripcion=(
        "ES LA HERRAMIENTA CORRECTA para '¿qué temporizadores tengo?', '¿qué "
        "recordatorios hay?' o '¿cuánto le queda al temporizador?'. Los "
        "temporizadores solo los conoce esta herramienta: no respondas sin usarla."
    ),
    accion="temporizador",
    parametros={},
)
def listar_temporizadores() -> str:
    with _lock:
        pendientes = sorted(_activos.values(), key=lambda t: t.vence)
    if not pendientes:
        return "No hay ningún temporizador ni recordatorio pendiente."
    lineas = [
        f"{t.numero}: {_cuando(t.vence)}" + (f", «{t.mensaje}»" if t.mensaje else "")
        for t in pendientes
    ]
    return "Pendientes:\n" + "\n".join(lineas)


@registro.registrar(
    nombre="cancelar_temporizador",
    descripcion=(
        "Cancela un temporizador o recordatorio por su número. Si no sabes el "
        "número, usa antes listar_temporizadores."
    ),
    accion="temporizador",
    parametros={"numero": {"type": "integer", "description": "Número del aviso."}},
)
def cancelar_temporizador(numero: int) -> str:
    with _lock:
        temporizador = _activos.pop(numero, None)
    if temporizador is None:
        return f"No hay ningún aviso pendiente con el número {numero}. NO digas que lo has cancelado."
    if temporizador._hilo is not None:
        temporizador._hilo.cancel()
    return f"Cancelado el aviso {numero}."


def cancelar_todos() -> None:
    """Para las pruebas y para el apagado."""
    with _lock:
        pendientes = list(_activos.values())
        _activos.clear()
    for t in pendientes:
        if t._hilo is not None:
            t._hilo.cancel()
