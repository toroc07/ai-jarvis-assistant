"""
Pruebas del endurecimiento del guardián: formas de escribir una ruta o una
dirección pensadas para esquivar la política, y la veracidad del registro.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

import security.guard as modulo_guard
from security.guard import RAIZ, Decision, ErrorDePolitica, Guardian, Peticion

CASA = Path.home()
WORKSPACE = CASA / "Documents" / "Jarvis" / "workspace"


@pytest.fixture
def guardian() -> Guardian:
    return Guardian()


def evaluar(guardian: Guardian, accion: str, objetivo: str) -> Decision:
    return guardian.evaluar(Peticion(accion=accion, objetivo=objetivo)).decision


# -- Rutas ------------------------------------------------------------------


@pytest.mark.parametrize(
    "ruta",
    [
        "c:/windows/system32/x.txt",
        "C:/WINDOWS/x.txt",
        "c:\\PROGRAM FILES\\algo.txt",
    ],
)
def test_prohibidas_no_dependen_de_mayusculas(guardian: Guardian, ruta: str) -> None:
    assert evaluar(guardian, "leer_archivo", ruta) is Decision.DENEGADO


@pytest.mark.parametrize(
    "ruta",
    ["\\\\servidor\\share\\a.txt", "//servidor/share/a.txt", "\\\\?\\C:\\Windows\\a.txt"],
)
def test_rutas_de_red_y_de_dispositivo_se_rechazan(guardian: Guardian, ruta: str) -> None:
    assert evaluar(guardian, "leer_archivo", ruta) is Decision.DENEGADO


@pytest.mark.parametrize("nombre", ["nota.txt:oculto.exe", "nota.txt::$DATA"])
def test_flujos_alternativos_de_ntfs_se_rechazan(guardian: Guardian, nombre: str) -> None:
    ruta = str(CASA / "Documents" / nombre)
    assert evaluar(guardian, "leer_archivo", ruta) is Decision.DENEGADO


def test_una_unidad_normal_no_se_confunde_con_un_flujo(guardian: Guardian) -> None:
    ruta = str(CASA / "Documents" / "nota.txt")
    assert ":" in ruta  # la unidad
    assert evaluar(guardian, "leer_archivo", ruta) is Decision.CONCEDIDO


@pytest.mark.parametrize("carpeta", ["data", "security", ".git", "venv"])
def test_carpetas_propias_del_proyecto_estan_prohibidas(
    guardian: Guardian, carpeta: str
) -> None:
    assert evaluar(guardian, "leer_archivo", str(RAIZ / carpeta / "x.txt")) is Decision.DENEGADO


def test_el_env_con_las_claves_esta_prohibido(guardian: Guardian) -> None:
    assert evaluar(guardian, "leer_archivo", str(RAIZ / ".env")) is Decision.DENEGADO


def test_se_puede_leer_un_py_pero_no_escribirlo(guardian: Guardian) -> None:
    assert evaluar(guardian, "leer_archivo", str(CASA / "Documents" / "a.py")) is Decision.CONCEDIDO
    assert evaluar(guardian, "escribir_archivo", str(WORKSPACE / "a.py")) is Decision.DENEGADO


def test_puntos_puntos_no_sacan_del_workspace(guardian: Guardian) -> None:
    ruta = str(WORKSPACE / ".." / ".." / ".." / "AppData" / "x.txt")
    assert evaluar(guardian, "leer_archivo", ruta) is Decision.DENEGADO


def test_el_veredicto_conserva_la_ruta_resuelta(guardian: Guardian) -> None:
    peticion = Peticion(
        accion="leer_archivo", objetivo=str(WORKSPACE / "sub" / ".." / "nota.txt")
    )
    assert guardian.evaluar(peticion).decision is Decision.CONCEDIDO
    assert peticion.ruta_resuelta == str((WORKSPACE / "nota.txt").resolve())


# -- Direcciones ------------------------------------------------------------


@pytest.mark.parametrize(
    "url", ["https://www.youtube.com/watch?v=x", "youtu.be/abc", "https://es.wikipedia.org"]
)
def test_sitios_de_confianza_se_abren_sin_preguntar(guardian: Guardian, url: str) -> None:
    assert evaluar(guardian, "abrir_url", url) is Decision.CONCEDIDO


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/?dato=secreto",
        "https://evilgoogle.com",
        "https://google.com.malo.net",
    ],
)
def test_sitios_desconocidos_piden_confirmacion(guardian: Guardian, url: str) -> None:
    assert evaluar(guardian, "abrir_url", url) is Decision.NECESITA_CONFIRMACION


@pytest.mark.parametrize(
    "url",
    ["javascript:alert(1)", "file:///C:/Windows/win.ini", "https://google.com@malo.com", "  "],
)
def test_direcciones_peligrosas_se_deniegan(guardian: Guardian, url: str) -> None:
    assert evaluar(guardian, "abrir_url", url) is Decision.DENEGADO


# -- Registro ---------------------------------------------------------------


def leer_registro(guardian: Guardian) -> list[dict]:
    lineas = guardian.ruta_registro.read_text(encoding="utf-8").splitlines()
    return [json.loads(linea) for linea in lineas]


def test_el_registro_anota_lo_que_se_ejecuto(guardian: Guardian) -> None:
    guardian.ejecutar(Peticion(accion="hora_fecha"), lambda: "ok")
    assert leer_registro(guardian)[-1]["resultado"] == "ejecutada"


def test_el_registro_anota_cuando_la_habilidad_falla(guardian: Guardian) -> None:
    def rota() -> None:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        guardian.ejecutar(Peticion(accion="hora_fecha"), rota)
    assert leer_registro(guardian)[-1]["resultado"] == "fallo: RuntimeError"


def test_lo_denegado_no_figura_como_ejecutado(guardian: Guardian) -> None:
    with pytest.raises(ErrorDePolitica):
        guardian.ejecutar(Peticion(accion="formatear_disco"), lambda: None)
    assert all("resultado" not in e for e in leer_registro(guardian))


def test_el_registro_se_rota_al_crecer(guardian: Guardian, monkeypatch) -> None:
    monkeypatch.setattr(modulo_guard, "_MAX_BYTES_REGISTRO", 200)
    for _ in range(10):
        guardian.evaluar(Peticion(accion="hora_fecha"))
    assert guardian.ruta_registro.with_name("audit.log.1").exists()


def test_el_objetivo_enorme_se_recorta_en_el_registro(guardian: Guardian) -> None:
    guardian.evaluar(Peticion(accion="leer_archivo", objetivo="a" * 5000))
    assert len(leer_registro(guardian)[-1]["objetivo"]) <= 301


# -- Hilos ------------------------------------------------------------------


def test_el_contador_de_acciones_aguanta_varios_hilos(guardian: Guardian) -> None:
    def trabajo() -> None:
        for _ in range(2):
            guardian.ejecutar(Peticion(accion="hora_fecha"), lambda: None)

    hilos = [threading.Thread(target=trabajo) for _ in range(6)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()

    assert guardian._acciones_peticion_actual == 12
