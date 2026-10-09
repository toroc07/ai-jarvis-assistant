"""
Pruebas de las funciones nuevas: temporizadores, notas, PDF y Word, clima de
varios días, voces de Piper e interrumpir a Jarvis mientras habla.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta

import pytest

import skills.notas as notas
import skills.temporizadores as temporizadores
from skills.archivos import leer_archivo
from skills.clima import _proximos_dias
from voice.habla import url_de_voz


# -- Temporizadores ---------------------------------------------------------


@pytest.fixture
def avisos():
    recibidos: list[str] = []
    sonado = threading.Event()

    def avisar(texto: str) -> None:
        recibidos.append(texto)
        sonado.set()

    temporizadores.fijar_aviso(avisar)
    yield recibidos, sonado
    temporizadores.cancelar_todos()
    temporizadores.fijar_aviso(None)


def test_un_temporizador_suena_con_su_mensaje(avisos) -> None:
    recibidos, sonado = avisos
    texto = temporizadores.poner_temporizador(0.002, "sacar la ropa")  # ~0,1 s
    assert "puesto" in texto and "se pierde" in texto
    assert sonado.wait(3)
    assert recibidos == ["Recordatorio: sacar la ropa."]
    assert "No hay ningún" in temporizadores.listar_temporizadores()


def test_cancelar_evita_que_suene(avisos) -> None:
    recibidos, sonado = avisos
    temporizadores.poner_temporizador(0.003)
    [linea] = temporizadores.listar_temporizadores().splitlines()[1:]
    numero = int(linea.split(":")[0])
    assert "Cancelado" in temporizadores.cancelar_temporizador(numero)
    assert not sonado.wait(0.5)
    assert "NO digas" in temporizadores.cancelar_temporizador(numero)


@pytest.mark.parametrize("minutos", [0, -5, 60 * 25])
def test_tiempos_imposibles_se_rechazan(avisos, minutos: float) -> None:
    assert "NO digas" in temporizadores.poner_temporizador(minutos)


def test_hay_un_maximo_de_avisos_pendientes(avisos, monkeypatch) -> None:
    monkeypatch.setattr(temporizadores, "MAX_ACTIVOS", 2)
    temporizadores.poner_temporizador(10)
    temporizadores.poner_temporizador(10)
    assert "máximo" in temporizadores.poner_temporizador(10)


def test_un_recordatorio_a_una_hora_pasada_es_para_manana(avisos) -> None:
    hace_un_rato = (datetime.now() - timedelta(minutes=5)).strftime("%H:%M")
    assert "mañana" in temporizadores.poner_recordatorio(hace_un_rato, "llamar")


@pytest.mark.parametrize("hora", ["25:00", "18:61", "a las seis", ""])
def test_horas_que_no_existen_se_rechazan(avisos, hora: str) -> None:
    assert "NO digas" in temporizadores.poner_recordatorio(hora, "x")


def test_sin_interfaz_un_aviso_no_revienta(capsys) -> None:
    temporizadores.fijar_aviso(None)
    temporizadores.poner_temporizador(0.001, "probar")
    time.sleep(0.3)
    assert "probar" in capsys.readouterr().out


@pytest.mark.parametrize(
    "tiempo, minutos",
    [
        ("5 minutos", 5), ("media hora", 30), ("hora y media", 90), ("1 hora", 60),
        ("90 segundos", 1.5), ("dos horas", 120), ("cuarenta y cinco minutos", 45),
        ("1h 30min", 90), (10, 10), ("10", 10),
    ],
)
def test_el_tiempo_se_entiende_como_lo_diria_una_persona(tiempo, minutos) -> None:
    assert temporizadores.interpretar_tiempo(tiempo) == pytest.approx(minutos)


@pytest.mark.parametrize("tiempo", ["mañana", "", "todos los días"])
def test_lo_que_no_es_un_tiempo_no_se_inventa(tiempo) -> None:
    assert temporizadores.interpretar_tiempo(tiempo) is None


def test_si_el_modelo_usa_otro_nombre_se_le_dice_cual_espera() -> None:
    from skills.registro import registro

    texto = registro.invocar("poner_temporizador", {"minutos": 5})
    assert "Faltan argumentos" in texto and "tiempo" in texto


# -- Notas ------------------------------------------------------------------


@pytest.fixture
def cuaderno(tmp_path, monkeypatch):
    archivo = tmp_path / "notas.md"
    monkeypatch.setattr(notas, "ARCHIVO_NOTAS", archivo)
    return archivo


def test_las_notas_se_anaden_sin_borrar_las_anteriores(cuaderno) -> None:
    notas.tomar_nota("comprar pilas")
    notas.tomar_nota("llamar al fontanero")
    contenido = cuaderno.read_text(encoding="utf-8")
    assert "comprar pilas" in contenido and "llamar al fontanero" in contenido
    assert "2 notas" in notas.leer_notas()


def test_una_nota_no_puede_colarse_como_varias(cuaderno) -> None:
    notas.tomar_nota("uno\n- [2020-01-01 00:00] nota falsa")
    assert notas.leer_notas().count("\n- ") == 1


def test_una_nota_vacia_no_se_guarda(cuaderno) -> None:
    assert "NO digas" in notas.tomar_nota("   ")
    assert not cuaderno.exists()


def test_tomar_nota_pide_confirmacion() -> None:
    from security.guard import Decision, Peticion, guardian

    veredicto = guardian.evaluar(Peticion(accion="tomar_nota", objetivo="x"))
    assert veredicto.decision is Decision.NECESITA_CONFIRMACION


# -- PDF y Word -------------------------------------------------------------

def _pdf_con_texto(texto: str) -> bytes:
    """Un PDF válido de una página, con su tabla de referencias bien calculada."""
    contenido = f"BT /F1 18 Tf 20 100 Td ({texto}) Tj ET".encode()
    objetos = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 144]/Contents 4 0 R"
        b"/Resources<</Font<</F1 5 0 R>>>>>>",
        b"<</Length %d>>stream\n" % len(contenido) + contenido + b"\nendstream",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    salida = b"%PDF-1.4\n"
    posiciones = []
    for i, cuerpo in enumerate(objetos, 1):
        posiciones.append(len(salida))
        salida += b"%d 0 obj\n" % i + cuerpo + b"\nendobj\n"
    inicio_xref = len(salida)
    salida += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objetos) + 1)
    salida += b"".join(b"%010d 00000 n \n" % p for p in posiciones)
    salida += b"trailer<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objetos) + 1,
        inicio_xref,
    )
    return salida


def test_lee_el_texto_de_un_pdf(tmp_path) -> None:
    pytest.importorskip("pypdf")
    archivo = tmp_path / "doc.pdf"
    archivo.write_bytes(_pdf_con_texto("Hola Jarvis"))
    assert "Hola Jarvis" in leer_archivo(str(archivo))


def test_un_pdf_roto_se_dice_sin_inventar(tmp_path) -> None:
    pytest.importorskip("pypdf")
    archivo = tmp_path / "roto.pdf"
    archivo.write_bytes(b"esto no es un pdf")
    assert "NO te inventes" in leer_archivo(str(archivo))


def test_lee_parrafos_y_tablas_de_un_word(tmp_path) -> None:
    docx = pytest.importorskip("docx")
    documento = docx.Document()
    documento.add_paragraph("Informe trimestral")
    tabla = documento.add_table(rows=1, cols=2)
    tabla.rows[0].cells[0].text = "Ventas"
    tabla.rows[0].cells[1].text = "120"
    archivo = tmp_path / "informe.docx"
    documento.save(str(archivo))

    texto = leer_archivo(str(archivo))
    assert "Informe trimestral" in texto
    assert "Ventas | 120" in texto


# -- Clima ------------------------------------------------------------------


def test_la_prevision_nombra_manana_y_los_dias_siguientes() -> None:
    diario = {
        "time": ["2026-10-08", "2026-10-09", "2026-10-10"],
        "temperature_2m_max": [20, 22, 18],
        "temperature_2m_min": [10, 11, 9],
        "weather_code": [0, 63, 3],
        "precipitation_probability_max": [0, 80, 10],
    }
    lineas = _proximos_dias(diario)
    assert lineas[0].startswith("Mañana: entre 11 y 22 grados, lloviendo, 80%")
    assert lineas[1].startswith("El sábado 10: entre 9 y 18 grados, nublado")
    assert "%" not in lineas[1]


def test_un_solo_dia_no_anade_prevision() -> None:
    assert _proximos_dias({"time": ["2026-10-08"], "temperature_2m_max": [1],
                           "temperature_2m_min": [0]}) == []


# -- Voces de Piper ---------------------------------------------------------


def test_la_url_de_una_voz_se_construye_desde_su_nombre() -> None:
    assert url_de_voz("es_ES-sharvard-medium").endswith("/es/es_ES/sharvard/medium")


@pytest.mark.parametrize(
    "voz", ["../../etc", "es_ES-davefx", "es_ES-x-medium/../../", "ES_es-a-medium", ""]
)
def test_nombres_de_voz_raros_se_rechazan(voz: str) -> None:
    assert url_de_voz(voz) is None


# -- Interrumpir ------------------------------------------------------------


def _sesion_falsa(fase, configurado: bool):
    from voice.sesion import Avisos, SesionDeVoz

    sesion = SesionDeVoz.__new__(SesionDeVoz)
    sesion.avisos = Avisos()
    sesion._fase = fase
    sesion._cancelada = threading.Event()
    sesion._interrumpida = threading.Event()
    sesion.callada = False

    class Voz:
        def callar(self) -> None:
            sesion.callada = True

    class Locutor:
        pass

    sesion.voz = Voz()
    sesion.locutor = Locutor()
    sesion.locutor.configurado = configurado
    return sesion


def test_solo_se_interrumpe_mientras_habla_y_con_voz_registrada() -> None:
    from voice.sesion import Fase

    assert _sesion_falsa(Fase.HABLANDO, True)._puede_interrumpirse()
    assert not _sesion_falsa(Fase.PENSANDO, True)._puede_interrumpirse()
    # Sin voz registrada podría cortarse a sí mismo al oír su nombre.
    assert not _sesion_falsa(Fase.HABLANDO, False)._puede_interrumpirse()


def test_interrumpir_calla_sin_cerrar_la_conversacion() -> None:
    from voice.sesion import Fase

    sesion = _sesion_falsa(Fase.HABLANDO, True)
    sesion.interrumpir()
    assert sesion.callada
    assert sesion._interrumpida.is_set()
    assert not sesion._cancelada.is_set()


# -- Historial con herramientas ---------------------------------------------


def test_el_historial_recuerda_que_se_usaron_herramientas(tmp_path) -> None:
    """Sin esto el modelo imitaba turnos 'sin herramientas' y se inventaba datos."""
    from core.agent import Agente
    from core.brain import Mensaje, Motor, Respuesta
    from core.memory import Memoria

    vistos: list[list[Mensaje]] = []
    guion = [
        Respuesta("", Motor.LOCAL, "falso", [{"name": "hora_fecha", "arguments": {}}]),
        Respuesta("Son las cinco.", Motor.LOCAL, "falso"),
        Respuesta("De nada.", Motor.LOCAL, "falso"),
    ]

    class CerebroFalso:
        def responder(self, mensajes, herramientas=None, **_):
            vistos.append(list(mensajes))
            return guion.pop(0)

    agente = Agente(cerebro=CerebroFalso(), memoria=Memoria(tmp_path / "db"))
    agente.responder("¿Qué hora es?")
    agente.responder("Gracias.")

    # Orden y forma del contexto del segundo turno, sin el sistema: la pregunta,
    # la llamada del asistente en formato nativo, el resultado como 'tool', la
    # respuesta y la nueva pregunta (sin duplicar).
    pregunta, llamada, resultado, respuesta, nueva = vistos[-1][1:]
    assert (pregunta.rol, pregunta.contenido) == ("user", "¿Qué hora es?")
    assert llamada.rol == "assistant" and llamada.llamadas == [
        {"name": "hora_fecha", "arguments": {}}
    ]
    assert resultado.rol == "tool" and resultado.herramienta == "hora_fecha"
    # El resultado no lleva la cabecera de texto: es lo que el modelo imitaba.
    assert "[resultado de" not in resultado.contenido
    assert (respuesta.rol, respuesta.contenido) == ("assistant", "Son las cinco.")
    assert (nueva.rol, nueva.contenido) == ("user", "Gracias.")


def test_los_mensajes_nativos_llegan_bien_a_ollama() -> None:
    from core.brain import Mensaje, mensaje_para_ollama

    llamada = mensaje_para_ollama(
        Mensaje("assistant", "", llamadas=[{"name": "hora_fecha", "arguments": {}}])
    )
    assert llamada["tool_calls"] == [{"function": {"name": "hora_fecha", "arguments": {}}}]
    resultado = mensaje_para_ollama(Mensaje("tool", "las cinco", herramienta="hora_fecha"))
    assert resultado == {"role": "tool", "content": "las cinco", "tool_name": "hora_fecha"}
