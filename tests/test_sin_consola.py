"""
Pruebas de los fallos que solo aparecen con la aplicación empaquetada.

Los dos que se arreglaron aquí no se veían al desarrollar y rompían el registro
de voz en el uso real:

  - Con pythonw.exe no hay consola, así que sys.stdout y sys.stderr valen None.
    La barra de progreso de tqdm, al descargar el modelo de verificación,
    reventaba con "'NoneType' object has no attribute 'write'".
  - SpeechBrain usa enlaces simbólicos al guardar el modelo, y crearlos en
    Windows exige permisos de administrador.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jarvis  # noqa: E402
from voice import locutor  # noqa: E402


class TestSalidaSinConsola:
    def test_se_crea_una_salida_cuando_no_hay(self) -> None:
        """Sin consola, algo tiene que poder escribir sin reventar."""
        antes_out, antes_err = sys.stdout, sys.stderr
        try:
            sys.stdout = None
            sys.stderr = None
            jarvis._asegurar_salida()

            assert sys.stdout is not None
            assert sys.stderr is not None
            # La prueba de fuego: que se pueda escribir, que es lo que hacía
            # tqdm cuando fallaba.
            sys.stdout.write("")
            sys.stderr.write("")
        finally:
            sys.stdout, sys.stderr = antes_out, antes_err

    def test_no_se_toca_la_salida_si_ya_existe(self) -> None:
        """Con consola, no debe redirigirse nada a un archivo."""
        antes = sys.stdout
        jarvis._asegurar_salida()
        assert sys.stdout is antes


class TestUmbralDeVoz:
    def test_el_umbral_por_defecto_es_exigente(self) -> None:
        """Un umbral bajo no filtra nada y da falsa sensación de seguridad.

        Con la similitud del coseno llevada al rango 0-1, dos timbres muy
        distintos llegan a puntuar 0,92, así que cualquier umbral por debajo de
        0,6 aceptaría prácticamente a cualquiera.
        """
        assert locutor.UMBRAL >= 0.70

    def test_el_umbral_calibrado_no_puede_bajar_de_lo_seguro(self) -> None:
        """El suelo está MEDIDO, no elegido.

        Este test afirmaba 0,60 y esa suposición era justamente la que dejaba
        al usuario fuera: sus activaciones reales puntuaban 0,637 y 0,544 con un
        umbral pegado al suelo de 0,66. Con segundo y medio de audio,
        ECAPA-TDNN no discrimina tanto como con frases largas, y un umbral alto
        ahí no da seguridad: da un asistente que no responde.

        El suelo sigue existiendo para que una calibración con mucho ruido no
        deje a Jarvis abierto a cualquiera.
        """
        assert locutor.UMBRAL_MINIMO >= 0.45

    def test_la_palabra_clave_es_mas_permisiva_que_la_peticion(self) -> None:
        """El reparto del trabajo entre las dos comprobaciones.

        Abrir el orbe es inofensivo, así que ahí el listón baja. La verificación
        que decide si Jarvis ACTÚA se hace con la petición completa, que dura
        varios segundos y sí permite distinguir voces.
        """
        assert 0 < locutor.REBAJA_EN_LA_PALABRA_CLAVE < 0.25
        efectivo = locutor.UMBRAL_MINIMO - locutor.REBAJA_EN_LA_PALABRA_CLAVE
        # Ni con la rebaja debe caer a un nivel que acepte cualquier cosa.
        assert efectivo >= 0.30

    def test_los_limites_del_umbral_son_coherentes(self) -> None:
        assert locutor.UMBRAL_MINIMO < locutor.UMBRAL_MAXIMO
        assert locutor.UMBRAL_MINIMO <= locutor.UMBRAL <= locutor.UMBRAL_MAXIMO

    def test_sin_huella_no_se_bloquea_al_usuario(self) -> None:
        """Sin configurar, Jarvis debe responder en vez de quedarse mudo."""
        import numpy as np

        l = locutor.Locutor()
        l._huella = None
        resultado = l.verificar(np.zeros(16000, dtype=np.float32))
        assert resultado.es_el_usuario

    def test_un_audio_muy_corto_se_rechaza(self) -> None:
        """Medio segundo no da para una huella fiable; el parecido sale al azar."""
        import numpy as np

        l = locutor.Locutor()
        l._huella = np.ones(192, dtype=np.float32)
        resultado = l.verificar(np.zeros(1000, dtype=np.float32))
        assert not resultado.es_el_usuario
        assert "corto" in resultado.motivo


class TestCoherenciaDeLaVerificacion:
    """La calibración y la verificación deben medir lo mismo.

    Este fue un fallo real y silencioso: el umbral se calibraba comparando
    frases enteras de unos tres segundos, pero al activarse Jarvis solo tiene
    el segundo escaso que dura "hey Jarvis". Con menos audio la huella sale
    menos estable y el parecido baja, así que el umbral quedaba alto y
    rechazaba al propio usuario. Medido en el equipo: parecido 0,741 contra un
    umbral de 0,760, rechazado por diecinueve milésimas.
    """

    def test_la_ventana_de_escucha_coincide_con_la_de_calibracion(self) -> None:
        from voice import escucha

        assert escucha.SEGUNDOS_DE_CONTEXTO == locutor.SEGUNDOS_DE_VERIFICACION

    def test_hay_audio_suficiente_para_una_huella_estable(self) -> None:
        """Con menos de un segundo el parecido sale casi al azar."""
        assert locutor.SEGUNDOS_DE_VERIFICACION >= 1.2

    def test_el_margen_no_deja_el_umbral_al_limite(self) -> None:
        assert locutor.MARGEN > 0
