"""
Pruebas de los fallos corregidos en la revisión: memoria con varios hilos,
argumentos mal tipados, búsquedas que se colaban en carpetas prohibidas,
papelera que pisaba archivos y el orbe que reaparecía tras cerrarlo.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

import skills.archivos as archivos
from core.brain import modelo_descargado
from core.memory import Memoria
from security.guard import guardian
from skills.registro import Registro, _convertir_tipos

CASA = Path.home()
WORKSPACE = CASA / "Documents" / "Jarvis" / "workspace"


# -- Memoria ----------------------------------------------------------------


@pytest.fixture
def memoria(tmp_path) -> Memoria:
    return Memoria(tmp_path / "jarvis.db")


def test_buscar_trata_los_comodines_de_sql_como_texto(memoria: Memoria) -> None:
    memoria.guardar_turno("s", "user", "subió un 100% el precio")
    memoria.guardar_turno("s", "user", "subió un 1000 el precio")
    memoria.guardar_turno("s", "user", "mi_archivo.txt")
    memoria.guardar_turno("s", "user", "miXarchivo.txt")

    assert [t.contenido for t in memoria.buscar_en_conversaciones("100%")] == [
        "subió un 100% el precio"
    ]
    assert [t.contenido for t in memoria.buscar_en_conversaciones("mi_archivo")] == [
        "mi_archivo.txt"
    ]


def test_varios_hilos_escriben_sin_bloquear_la_base(memoria: Memoria) -> None:
    errores: list[Exception] = []

    def escribir(n: int) -> None:
        try:
            for i in range(20):
                memoria.guardar_turno(f"s{n}", "user", f"mensaje {i}")
                memoria.historial(f"s{n}")
        except Exception as e:  # pragma: no cover - es lo que se comprueba
            errores.append(e)

    hilos = [threading.Thread(target=escribir, args=(n,)) for n in range(6)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()

    assert errores == []
    assert len(memoria.historial("s0", limite=100)) == 20


# -- Registro de habilidades ------------------------------------------------


def test_los_numeros_en_texto_se_convierten() -> None:
    esquema = {"pasos": {"type": "integer"}, "nivel": {"type": "number"}}
    assert _convertir_tipos({"pasos": "5", "nivel": "0,5"}, esquema) == {
        "pasos": 5,
        "nivel": 0.5,
    }


def test_los_booleanos_en_texto_se_convierten() -> None:
    esquema = {"activo": {"type": "boolean"}}
    assert _convertir_tipos({"activo": "sí"}, esquema) == {"activo": True}
    assert _convertir_tipos({"activo": "false"}, esquema) == {"activo": False}


def test_un_numero_imposible_da_un_error_legible() -> None:
    with pytest.raises(ValueError, match="pasos"):
        _convertir_tipos({"pasos": "muchos"}, {"pasos": {"type": "integer"}})


def test_dos_habilidades_distintas_no_pueden_llamarse_igual() -> None:
    registro = Registro()

    @registro.registrar("duplicada", "una", "hora_fecha", {})
    def primera() -> str:
        return "1"

    with pytest.raises(ValueError, match="duplicada"):

        @registro.registrar("duplicada", "otra", "hora_fecha", {})
        def segunda() -> str:
            return "2"


def test_un_argumento_mal_tipado_no_revienta_la_habilidad() -> None:
    registro = Registro()

    @registro.registrar(
        "contar", "cuenta", "hora_fecha", {"veces": {"type": "integer"}}
    )
    def contar(veces: int) -> str:
        return "x" * veces

    assert registro.invocar("contar", {"veces": "3"}) == "xxx"
    assert "no válidos" in registro.invocar("contar", {"veces": "tres"})


# -- Archivos ---------------------------------------------------------------


def test_la_papelera_no_pisa_archivos_del_mismo_nombre(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(archivos, "PAPELERA", tmp_path / "papelera")
    a = archivos._nombre_en_papelera(Path("nota.txt"))
    b = archivos._nombre_en_papelera(Path("nota.txt"))
    assert a != b
    assert a.suffix == ".txt"


def test_leer_un_archivo_enorme_se_recorta(tmp_path) -> None:
    archivo = tmp_path / "grande.txt"
    archivo.write_text("a" * (archivos.MAX_CARACTERES_LECTURA * 2), encoding="utf-8")
    texto = archivos.leer_archivo(str(archivo))
    assert "recortado" in texto
    assert len(texto) < archivos.MAX_CARACTERES_LECTURA + 300


def test_escribir_demasiado_no_guarda_nada(tmp_path) -> None:
    archivo = tmp_path / "x.txt"
    texto = archivos.escribir_archivo(str(archivo), "a" * (archivos.MAX_CARACTERES_ESCRITURA + 1))
    assert "NO digas" in texto
    assert not archivo.exists()


@pytest.fixture
def arbol(tmp_path, monkeypatch) -> Path:
    """Una carpeta legible con una subcarpeta prohibida y un venv."""
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "factura.pdf").write_text("x")
    (tmp_path / "secreta").mkdir()
    (tmp_path / "secreta" / "factura_oculta.pdf").write_text("x")
    (tmp_path / "venv" / "lib").mkdir(parents=True)
    (tmp_path / "venv" / "lib" / "factura_venv.pdf").write_text("x")

    rutas = guardian.politica["rutas"]
    monkeypatch.setitem(rutas, "lectura", [str(tmp_path)])
    monkeypatch.setitem(rutas, "prohibidas", [str(tmp_path / "secreta")])
    return tmp_path


def test_buscar_no_entra_en_carpetas_prohibidas_ni_en_venv(arbol: Path) -> None:
    resultado = archivos.buscar_archivos(str(arbol), "factura*")
    assert "factura.pdf" in resultado
    assert "factura_oculta" not in resultado
    assert "factura_venv" not in resultado


@pytest.mark.parametrize("patron", ["../*.pdf", "..\\*", "C:/Windows/*", "sub/*.pdf"])
def test_buscar_rechaza_patrones_que_son_rutas(arbol: Path, patron: str) -> None:
    assert "solo un nombre" in archivos.buscar_archivos(str(arbol), patron)


def test_buscar_tiene_un_tope_de_resultados(arbol: Path, monkeypatch) -> None:
    monkeypatch.setattr(archivos, "MAX_RESULTADOS_BUSQUEDA", 3)
    for i in range(10):
        (arbol / "docs" / f"nota{i}.txt").write_text("x")
    resultado = archivos.buscar_archivos(str(arbol), "nota*")
    assert resultado.startswith("Encontrados 3:")


def test_copiar_a_una_carpeta_valida_el_archivo_final() -> None:
    assert archivos._destino_final_permitido(WORKSPACE / "nota.txt") is None
    assert archivos._destino_final_permitido(WORKSPACE / "script.py") is not None


# -- Cerebro ----------------------------------------------------------------


def test_el_modelo_debe_coincidir_entero() -> None:
    assert modelo_descargado("qwen3:8b", ["qwen3:8b"])
    assert not modelo_descargado("qwen3:8b", ["qwen3:0.6b"])
    assert modelo_descargado("qwen3", ["qwen3:latest"])
    assert not modelo_descargado("qwen3", ["qwen3:8b"])


# -- Conversación de voz ----------------------------------------------------


def test_cerrar_el_orbe_impide_que_vuelva_a_abrirse_solo() -> None:
    from voice.sesion import Avisos, Fase, SesionDeVoz

    fases: list[Fase] = []
    sesion = SesionDeVoz.__new__(SesionDeVoz)
    sesion.avisos = Avisos(cambio_de_fase=fases.append)
    sesion._fase = Fase.ESCUCHANDO
    sesion._cancelada = threading.Event()

    class VozCallada:
        def callar(self) -> None:
            pass

    sesion.voz = VozCallada()
    sesion.detener_conversacion()
    sesion._cambiar_fase(Fase.PENSANDO)
    sesion._cambiar_fase(Fase.HABLANDO)

    assert fases == [Fase.DORMIDO]
    assert sesion.fase is Fase.DORMIDO
