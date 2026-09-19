"""
El tiempo.

Usa Open-Meteo, que es gratis y no pide clave de API. Eso importa en este
proyecto: cualquiera puede clonarlo y tener el clima funcionando sin registrarse
en ningún sitio ni pegar credenciales en un archivo.

Son dos llamadas: una para convertir el nombre de la ciudad en coordenadas y
otra para el tiempo. La ciudad por defecto se guarda como un hecho más en la
memoria, así que basta con decirlo una vez.
"""

from __future__ import annotations

from skills.registro import registro

GEOCODIFICACION = "https://geocoding-api.open-meteo.com/v1/search"
PRONOSTICO = "https://api.open-meteo.com/v1/forecast"

# Los códigos que devuelve Open-Meteo, traducidos a algo que se pueda decir en
# voz alta. La lista completa está en su documentación; aquí van agrupados por
# lo que de verdad cambia lo que te pondrías para salir.
TIEMPO = {
    0: "despejado",
    1: "casi despejado",
    2: "parcialmente nublado",
    3: "nublado",
    45: "con niebla",
    48: "con niebla helada",
    51: "con llovizna ligera",
    53: "con llovizna",
    55: "con llovizna intensa",
    56: "con llovizna helada",
    57: "con llovizna helada intensa",
    61: "con lluvia ligera",
    63: "lloviendo",
    65: "lloviendo con fuerza",
    66: "con lluvia helada",
    67: "con lluvia helada intensa",
    71: "nevando ligeramente",
    73: "nevando",
    75: "nevando con fuerza",
    77: "con granizo pequeño",
    80: "con chubascos ligeros",
    81: "con chubascos",
    82: "con chubascos fuertes",
    85: "con nevadas ligeras",
    86: "con nevadas fuertes",
    95: "con tormenta",
    96: "con tormenta y granizo",
    99: "con tormenta fuerte y granizo",
}


def _describir(codigo: int) -> str:
    return TIEMPO.get(codigo, "con el cielo raro")


def _buscar_ciudad(nombre: str) -> tuple[float, float, str] | None:
    """Convierte un nombre de ciudad en coordenadas."""
    import httpx

    try:
        r = httpx.get(
            GEOCODIFICACION,
            params={"name": nombre, "count": 1, "language": "es"},
            timeout=15.0,
        )
        r.raise_for_status()
        resultados = r.json().get("results")
    except Exception:
        return None

    if not resultados:
        return None

    sitio = resultados[0]
    # Se devuelve también el país para que Jarvis pueda decir "Valencia, España"
    # y tú detectes si ha cogido la ciudad equivocada.
    etiqueta = sitio["name"]
    if sitio.get("country"):
        etiqueta += f", {sitio['country']}"
    return sitio["latitude"], sitio["longitude"], etiqueta


def _ciudad_guardada() -> str:
    """La ciudad que Jarvis recuerda de ti, si se la has dicho alguna vez."""
    from skills.memoria import _memoria

    hechos = _memoria.hechos()
    return hechos.get("ciudad") or hechos.get("ubicacion") or ""


@registro.registrar(
    nombre="consultar_clima",
    descripcion=(
        "Dice qué tiempo hace. Úsala cuando pregunten por el tiempo, la "
        "temperatura, si llueve o si hace falta abrigo. Si no dicen la ciudad, "
        "omite el parámetro y se usará la que el usuario tenga guardada."
    ),
    accion="clima",
    parametros={
        "ciudad": {
            "type": "string",
            "description": "Ciudad de la que consultar el tiempo.",
            "requerido": False,
        }
    },
    campo_objetivo="ciudad",
)
def consultar_clima(ciudad: str = "") -> str:
    import httpx

    ciudad = (ciudad or "").strip() or _ciudad_guardada()
    if not ciudad:
        return (
            "No sé de qué ciudad. Pregúntale al usuario dónde está y, cuando te "
            "lo diga, guárdalo con recordar_dato usando la clave 'ciudad'. "
            "NO te inventes el tiempo."
        )

    sitio = _buscar_ciudad(ciudad)
    if sitio is None:
        return f"No encuentro ninguna ciudad llamada '{ciudad}'. NO te inventes el tiempo."

    lat, lon, etiqueta = sitio

    try:
        r = httpx.get(
            PRONOSTICO,
            params={
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,apparent_temperature,weather_code",
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                "forecast_days": 1,
                "timezone": "auto",
            },
            timeout=15.0,
        )
        r.raise_for_status()
        datos = r.json()
    except Exception as e:
        return f"No he podido consultar el tiempo: {e}. NO te lo inventes."

    ahora = datos.get("current", {})
    hoy = datos.get("daily", {})

    temperatura = ahora.get("temperature_2m")
    sensacion = ahora.get("apparent_temperature")
    codigo = ahora.get("weather_code", -1)

    if temperatura is None:
        return "La respuesta del servicio del tiempo vino vacía. NO te lo inventes."

    partes = [f"En {etiqueta} hay {temperatura:.0f} grados y está {_describir(codigo)}"]

    # La sensación térmica solo se menciona si difiere de verdad: repetir el
    # mismo número dos veces suena raro dicho en voz alta.
    if sensacion is not None and abs(sensacion - temperatura) >= 2:
        partes.append(f", aunque la sensación es de {sensacion:.0f}")

    maxima = (hoy.get("temperature_2m_max") or [None])[0]
    minima = (hoy.get("temperature_2m_min") or [None])[0]
    if maxima is not None and minima is not None:
        partes.append(f". Hoy entre {minima:.0f} y {maxima:.0f} grados")

    lluvia = (hoy.get("precipitation_probability_max") or [None])[0]
    if lluvia is not None and lluvia >= 30:
        partes.append(f", con un {lluvia:.0f} por ciento de probabilidad de lluvia")

    return "".join(partes) + "."
