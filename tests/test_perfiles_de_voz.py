"""
Pruebas de los perfiles de voz.

Antes solo cabía una persona. Ahora caben tres, cada una con su nombre, y el
asistente sabe a quién responde. Lo que más importa aquí es que los perfiles no
se mezclen ni se dupliquen, y que quedarse sin ninguno devuelva el sistema a un
estado explicable en lugar de a uno roto.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voice import locutor as mod  # noqa: E402
from voice.locutor import MAX_PERFILES, Locutor, Perfil, normalizar_nombre  # noqa: E402


def _huella(semilla: int) -> np.ndarray:
    """Una huella cualquiera, reproducible, sin cargar el modelo real."""
    rng = np.random.default_rng(semilla)
    return rng.normal(size=192).astype(np.float32)


@pytest.fixture
def locutor_vacio(tmp_path, monkeypatch) -> Locutor:
    """Un locutor aislado, que no toca los perfiles reales del equipo."""
    monkeypatch.setattr(mod, "RUTA_PERFILES", tmp_path / "perfiles.json")
    monkeypatch.setattr(mod, "RUTA_MUESTRAS", tmp_path / "muestras")
    monkeypatch.setattr(mod, "RUTA_HUELLA_ANTIGUA", tmp_path / "no-existe.json")
    l = Locutor()
    l.perfiles = []
    return l


class TestNombres:
    @pytest.mark.parametrize(
        "entrada,esperado",
        [
            ("Carlos", "carlos"),
            ("  CARLOS  ", "carlos"),
            ("José", "jose"),
            ("Ana María", "ana maria"),
        ],
    )
    def test_se_normalizan(self, entrada: str, esperado: str) -> None:
        assert normalizar_nombre(entrada) == esperado

    def test_dos_formas_del_mismo_nombre_son_la_misma_persona(
        self, locutor_vacio: Locutor
    ) -> None:
        """«José» y «jose» no deben ocupar dos de las tres plazas."""
        locutor_vacio.perfiles = [Perfil("José", _huella(1), 0.7)]
        assert locutor_vacio.buscar("jose") is not None
        assert locutor_vacio.buscar("  JOSE ") is not None


class TestCupoDeTresPlazas:
    def test_caben_tres(self, locutor_vacio: Locutor) -> None:
        for i in range(MAX_PERFILES):
            locutor_vacio.perfiles.append(Perfil(f"Persona {i}", _huella(i), 0.7))
        assert not locutor_vacio.hay_sitio
        assert len(locutor_vacio.nombres) == MAX_PERFILES

    def test_con_sitio_libre_lo_dice(self, locutor_vacio: Locutor) -> None:
        locutor_vacio.perfiles = [Perfil("Ana", _huella(1), 0.7)]
        assert locutor_vacio.hay_sitio

    def test_una_cuarta_voz_se_rechaza(self, locutor_vacio: Locutor) -> None:
        for i in range(MAX_PERFILES):
            locutor_vacio.perfiles.append(Perfil(f"Persona {i}", _huella(i), 0.7))

        with pytest.raises(ValueError, match="Olvida una"):
            locutor_vacio.registrar("Cuarta", [np.zeros(16000, dtype=np.float32)] * 2)

    def test_registrar_de_nuevo_a_alguien_no_gasta_plaza(
        self, locutor_vacio: Locutor, monkeypatch
    ) -> None:
        """Rehacer tu perfil te sustituye, no te duplica."""
        monkeypatch.setattr(Locutor, "_extraer", lambda self, a, f=16000: _huella(7))
        monkeypatch.setattr(
            Locutor, "_parecidos_en_trozos", lambda self, g, m, f: [0.8, 0.85]
        )

        audio = [np.ones(32000, dtype=np.float32)] * 3
        locutor_vacio.registrar("Ana", audio)
        locutor_vacio.registrar("Ana", audio)
        assert locutor_vacio.nombres == ["Ana"]


class TestIdentificacion:
    def test_se_reconoce_a_la_persona_correcta(
        self, locutor_vacio: Locutor, monkeypatch
    ) -> None:
        """Con varias registradas debe ganar la que de verdad se parece."""
        de_ana, de_luis = _huella(1), _huella(2)
        locutor_vacio.perfiles = [
            Perfil("Ana", de_ana, 0.6),
            Perfil("Luis", de_luis, 0.6),
        ]
        # La voz entrante es exactamente la huella de Luis.
        monkeypatch.setattr(Locutor, "_extraer", lambda self, a, f=16000: de_luis)

        resultado = locutor_vacio.identificar(np.ones(32000, dtype=np.float32))
        assert resultado.es_conocido
        assert resultado.nombre == "Luis"

    def test_una_voz_desconocida_se_rechaza(
        self, locutor_vacio: Locutor, monkeypatch
    ) -> None:
        locutor_vacio.perfiles = [Perfil("Ana", _huella(1), 0.95)]
        monkeypatch.setattr(Locutor, "_extraer", lambda self, a, f=16000: _huella(99))

        resultado = locutor_vacio.identificar(np.ones(32000, dtype=np.float32))
        assert not resultado.es_conocido
        assert resultado.nombre == ""

    def test_sin_nadie_registrado_se_acepta_a_cualquiera(
        self, locutor_vacio: Locutor
    ) -> None:
        """Mejor abierto y explicable que mudo sin motivo."""
        assert locutor_vacio.identificar(np.ones(32000, dtype=np.float32)).es_conocido

    def test_la_palabra_clave_usa_un_liston_mas_bajo(
        self, locutor_vacio: Locutor, monkeypatch
    ) -> None:
        """Segundo y medio de audio da parecidos bajos incluso siendo tú."""
        huella = _huella(1)
        # Umbral justo por encima de lo que dará la comparación consigo misma
        # menos la rebaja, para que solo pase en el modo permisivo.
        locutor_vacio.perfiles = [Perfil("Ana", huella, 1.05)]
        monkeypatch.setattr(Locutor, "_extraer", lambda self, a, f=16000: huella)

        audio = np.ones(32000, dtype=np.float32)
        assert not locutor_vacio.identificar(audio).es_conocido
        assert locutor_vacio.identificar(audio, es_palabra_clave=True).es_conocido


class TestOlvidar:
    def test_se_olvida_a_una_persona(self, locutor_vacio: Locutor) -> None:
        locutor_vacio.perfiles = [
            Perfil("Ana", _huella(1), 0.7),
            Perfil("Luis", _huella(2), 0.7),
        ]
        assert locutor_vacio.olvidar("Ana")
        assert locutor_vacio.nombres == ["Luis"]

    def test_olvidar_a_alguien_que_no_esta_no_rompe(
        self, locutor_vacio: Locutor
    ) -> None:
        assert not locutor_vacio.olvidar("Nadie")

    def test_se_olvida_aunque_escribas_el_nombre_distinto(
        self, locutor_vacio: Locutor
    ) -> None:
        locutor_vacio.perfiles = [Perfil("José", _huella(1), 0.7)]
        assert locutor_vacio.olvidar("jose")

    def test_quedarse_sin_perfiles_deja_el_sistema_abierto(
        self, locutor_vacio: Locutor
    ) -> None:
        """No es un fallo: es el estado inicial, y conviene que sea explicable."""
        locutor_vacio.perfiles = [Perfil("Ana", _huella(1), 0.7)]
        locutor_vacio.olvidar("Ana")

        assert not locutor_vacio.configurado
        assert locutor_vacio.identificar(np.ones(32000, dtype=np.float32)).es_conocido


class TestCompatibilidad:
    def test_verificar_sigue_funcionando(self, locutor_vacio: Locutor) -> None:
        """El nombre anterior del método no debe romperse de golpe."""
        resultado = locutor_vacio.verificar(np.ones(32000, dtype=np.float32))
        assert resultado.es_el_usuario == resultado.es_conocido

    def test_se_importa_la_huella_del_formato_antiguo(
        self, tmp_path, monkeypatch
    ) -> None:
        """Quien ya tuviera su voz registrada no debe repetirlo por el cambio."""
        import json

        antigua = tmp_path / "huella.json"
        antigua.write_text(
            json.dumps({"huella": _huella(5).tolist(), "umbral": 0.61}),
            encoding="utf-8",
        )
        monkeypatch.setattr(mod, "RUTA_HUELLA_ANTIGUA", antigua)
        monkeypatch.setattr(mod, "RUTA_PERFILES", tmp_path / "perfiles.json")
        monkeypatch.setattr(mod, "RUTA_MUESTRAS", tmp_path / "muestras")
        monkeypatch.setattr(mod, "RUTA_MUESTRAS_ANTIGUAS", tmp_path / "no-existe.npz")
        monkeypatch.setenv("JARVIS_USUARIO", "Ana")

        l = Locutor()
        assert l.nombres == ["Ana"]
        assert l.perfiles[0].umbral == 0.61


class TestPersistencia:
    def test_los_perfiles_sobreviven_al_reinicio(
        self, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.setattr(mod, "RUTA_PERFILES", tmp_path / "perfiles.json")
        monkeypatch.setattr(mod, "RUTA_MUESTRAS", tmp_path / "muestras")
        monkeypatch.setattr(mod, "RUTA_HUELLA_ANTIGUA", tmp_path / "no-existe.json")

        primero = Locutor()
        primero.perfiles = [Perfil("Ana", _huella(1), 0.65)]
        primero._guardar()

        segundo = Locutor()
        assert segundo.nombres == ["Ana"]
        assert segundo.perfiles[0].umbral == 0.65

    def test_un_archivo_corrupto_no_rompe_el_arranque(
        self, tmp_path, monkeypatch
    ) -> None:
        ruta = tmp_path / "perfiles.json"
        ruta.write_text("esto no es json", encoding="utf-8")
        monkeypatch.setattr(mod, "RUTA_PERFILES", ruta)
        monkeypatch.setattr(mod, "RUTA_HUELLA_ANTIGUA", tmp_path / "no-existe.json")

        assert Locutor().perfiles == []
