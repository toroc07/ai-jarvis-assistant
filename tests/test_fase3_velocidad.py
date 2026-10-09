"""
Pruebas de la Fase 3: hablar según llega la respuesta sin decir nada que el
agente habría corregido, y la conversión de herramientas al formato de Claude.
"""

from __future__ import annotations

from core.brain import Mensaje, herramientas_para_claude, mensajes_para_claude
from voice.frases import HabladorPorFrases, partir_en_frases


def hablador() -> tuple[HabladorPorFrases, list[str]]:
    dichas: list[str] = []
    return HabladorPorFrases(decir=dichas.append), dichas


def recibir_por_trozos(h: HabladorPorFrases, texto: str, tam: int = 7) -> None:
    for i in range(0, len(texto), tam):
        h.recibir(texto[i : i + tam])


# -- Partir en frases -------------------------------------------------------


def test_partir_separa_completas_del_resto() -> None:
    completas, resto = partir_en_frases("Hola. ¿Qué tal? Bien, gracias y")
    assert completas == ["Hola.", "¿Qué tal?"]
    assert resto == "Bien, gracias y"


def test_un_punto_sin_espacio_no_corta() -> None:
    completas, resto = partir_en_frases("Abre youtube.com y pon 3.5 minutos")
    assert completas == []


# -- Hablar por frases ------------------------------------------------------


def test_dice_cada_frase_en_cuanto_esta_completa() -> None:
    h, dichas = hablador()
    recibir_por_trozos(h, "Son las cinco. Hace sol en Madrid. Y algo más")
    h.esperar()
    assert dichas == ["Son las cinco.", "Hace sol en Madrid."]


def test_al_terminar_no_repite_lo_ya_dicho() -> None:
    h, dichas = hablador()
    texto = "Son las cinco. Hace sol en Madrid. Que lo disfrutes."
    recibir_por_trozos(h, texto)
    h.terminar(texto)
    assert dichas == ["Son las cinco.", "Hace sol en Madrid.", "Que lo disfrutes."]


def test_no_adelanta_una_llamada_a_herramienta_escrita_como_texto() -> None:
    h, dichas = hablador()
    recibir_por_trozos(h, 'Vale. abrir_url\n{"url": "https://x.com"}')
    h.terminar("Abierto.")
    assert '{"url"' not in " ".join(dichas)
    assert dichas[-1] == "Abierto."


def test_no_adelanta_marcas_internas() -> None:
    h, dichas = hablador()
    recibir_por_trozos(h, "Hasta luego. [FIN_CONVERSACION] ")
    h.terminar("Hasta luego.")
    assert all("[" not in d for d in dichas)
    assert dichas == ["Hasta luego."]


def test_no_dice_que_hizo_algo_si_no_hubo_herramientas() -> None:
    """La regla que no se rompe: nada de 'ya lo he abierto' sin haberlo hecho."""
    h, dichas = hablador()
    recibir_por_trozos(h, "He abierto YouTube. Ya está sonando. ")
    # El agente detecta la mentira y la sustituye por la disculpa.
    h.terminar("No he podido hacerlo.")
    assert "He abierto YouTube." not in dichas
    assert dichas == ["No he podido hacerlo."]


def test_tras_usar_herramientas_si_puede_confirmar_lo_hecho() -> None:
    h, dichas = hablador()
    recibir_por_trozos(h, "Voy a mirarlo. ")
    h.nueva_vuelta("abrir_url")
    recibir_por_trozos(h, "He abierto YouTube. ")
    h.terminar("He abierto YouTube.")
    assert dichas == ["Voy a mirarlo.", "He abierto YouTube."]


def test_si_la_respuesta_final_cambia_se_dice_la_buena() -> None:
    h, dichas = hablador()
    recibir_por_trozos(h, "Te lo explico. Primero haces A. ")
    h.terminar("Te lo explico. Primero haces B. Luego C.")
    assert dichas == ["Te lo explico.", "Primero haces A.", "Primero haces B.", "Luego C."]


def test_cancelado_no_dice_nada_mas() -> None:
    cancelado = False
    dichas: list[str] = []
    h = HabladorPorFrases(decir=dichas.append, cancelado=lambda: cancelado)
    recibir_por_trozos(h, "Primera. ")
    h.esperar()
    cancelado = True
    h2 = HabladorPorFrases(decir=dichas.append, cancelado=lambda: cancelado)
    recibir_por_trozos(h2, "Segunda. ")
    h2.terminar("Segunda. Tercera.")
    assert dichas == ["Primera."]


def test_avisa_al_empezar_una_sola_vez() -> None:
    avisos: list[int] = []
    h = HabladorPorFrases(decir=lambda _: None, al_empezar=lambda: avisos.append(1))
    recibir_por_trozos(h, "Una. Dos. Tres. ")
    h.esperar()
    assert avisos == [1]
    assert h.dijo_algo


# -- Claude -----------------------------------------------------------------


def test_las_herramientas_se_traducen_al_formato_de_claude() -> None:
    esquema = {
        "type": "function",
        "function": {
            "name": "leer_archivo",
            "description": "Lee un archivo.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ruta": {"type": "string", "description": "Ruta."},
                    "lineas": {"type": "integer", "requerido": False},
                },
                "required": ["ruta"],
            },
        },
    }
    [herramienta] = herramientas_para_claude([esquema])
    assert herramienta["name"] == "leer_archivo"
    assert herramienta["input_schema"]["required"] == ["ruta"]
    assert "requerido" not in herramienta["input_schema"]["properties"]["lineas"]
    # El original no se toca: lo sigue usando Ollama.
    assert "requerido" in esquema["function"]["parameters"]["properties"]["lineas"]


def test_todas_las_habilidades_reales_se_traducen() -> None:
    import core.agent  # noqa: F401  (registra todas las habilidades)
    from skills.registro import registro

    traducidas = herramientas_para_claude(registro.esquemas())
    assert len(traducidas) == len(registro.listar())
    assert all(t["input_schema"]["type"] == "object" for t in traducidas)


def test_los_mensajes_vacios_y_el_asistente_inicial_se_quitan() -> None:
    mensajes = [
        Mensaje("system", "Eres Jarvis."),
        Mensaje("assistant", "Hola, ¿qué tal?"),
        Mensaje("user", "abre youtube"),
        Mensaje("assistant", ""),
        Mensaje("user", "[resultado de abrir_url]\nAbierto"),
    ]
    assert mensajes_para_claude(mensajes) == [
        {"role": "user", "content": "abre youtube"},
        {"role": "user", "content": "[resultado de abrir_url]\nAbierto"},
    ]


def test_un_resultado_nativo_llega_a_claude_como_texto() -> None:
    mensajes = [
        Mensaje("user", "¿qué hora es?"),
        Mensaje("assistant", "", llamadas=[{"name": "hora_fecha", "arguments": {}}]),
        Mensaje("tool", "las cinco", herramienta="hora_fecha"),
    ]
    assert mensajes_para_claude(mensajes) == [
        {"role": "user", "content": "¿qué hora es?"},
        {"role": "user", "content": "[resultado de hora_fecha]\nlas cinco"},
    ]


def test_el_esfuerzo_no_se_manda_a_modelos_que_lo_rechazan() -> None:
    from core.brain import admite_esfuerzo

    assert admite_esfuerzo("claude-sonnet-5")
    assert admite_esfuerzo("claude-opus-5")
    assert not admite_esfuerzo("claude-haiku-4-5")
    assert not admite_esfuerzo("claude-sonnet-4-5")
