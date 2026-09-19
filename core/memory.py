"""
La memoria de Jarvis.

Tres capas, porque no todo lo que se recuerda vale lo mismo:

  - Conversaciones: el histórico en bruto de lo que os habéis dicho.
  - Hechos: cosas concretas sobre ti que conviene recordar entre sesiones
    ("mi jefe se llama X", "prefiero que me avises 15 minutos antes").
  - Preferencias: ajustes del propio Jarvis que tú cambias hablando.

Todo vive en un SQLite local. No sale nada de tu máquina.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

RAIZ = Path(__file__).resolve().parent.parent
RUTA_BD = RAIZ / "data" / "jarvis.db"

ESQUEMA = """
CREATE TABLE IF NOT EXISTS conversaciones (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    sesion      TEXT NOT NULL,
    rol         TEXT NOT NULL,
    contenido   TEXT NOT NULL,
    motor       TEXT,
    momento     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conv_sesion ON conversaciones(sesion, id);

CREATE TABLE IF NOT EXISTS hechos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    clave       TEXT NOT NULL UNIQUE,
    valor       TEXT NOT NULL,
    categoria   TEXT DEFAULT 'general',
    creado      TEXT NOT NULL,
    actualizado TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS preferencias (
    clave       TEXT PRIMARY KEY,
    valor       TEXT NOT NULL,
    actualizado TEXT NOT NULL
);

-- Una sesión es cada arranque completo de Jarvis. Cerrar la ventana no la
-- termina: sigue en segundo plano y la conversación continúa siendo la misma.
-- Solo apagarlo del todo cierra la sesión y abre una nueva al volver.
CREATE TABLE IF NOT EXISTS sesiones (
    id          TEXT PRIMARY KEY,
    inicio      TEXT NOT NULL,
    fin         TEXT,
    motivo_fin  TEXT
);

-- Qué ha hecho Jarvis en cada sesión. Permite que responda a "¿qué has hecho?"
-- con hechos registrados en lugar de con lo que recuerde de la conversación,
-- que es donde un modelo pequeño se inventa cosas.
CREATE TABLE IF NOT EXISTS acciones (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    sesion      TEXT NOT NULL,
    habilidad   TEXT NOT NULL,
    objetivo    TEXT,
    resultado   TEXT,
    momento     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_acciones_sesion ON acciones(sesion, id);
"""


@dataclass
class Turno:
    rol: str
    contenido: str
    momento: str
    motor: str | None = None


class Memoria:
    def __init__(self, ruta: Path = RUTA_BD) -> None:
        self.ruta = ruta
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        with self._conexion() as con:
            con.executescript(ESQUEMA)

    @contextmanager
    def _conexion(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self.ruta)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        finally:
            con.close()

    @staticmethod
    def _ahora() -> str:
        return datetime.now().isoformat(timespec="seconds")

    # -- Conversaciones ------------------------------------------------------

    def guardar_turno(
        self, sesion: str, rol: str, contenido: str, motor: str | None = None
    ) -> None:
        with self._conexion() as con:
            con.execute(
                "INSERT INTO conversaciones (sesion, rol, contenido, motor, momento) "
                "VALUES (?, ?, ?, ?, ?)",
                (sesion, rol, contenido, motor, self._ahora()),
            )

    def historial(self, sesion: str, limite: int = 20) -> list[Turno]:
        """Los últimos turnos de una sesión, en orden cronológico."""
        with self._conexion() as con:
            filas = con.execute(
                "SELECT rol, contenido, momento, motor FROM conversaciones "
                "WHERE sesion = ? ORDER BY id DESC LIMIT ?",
                (sesion, limite),
            ).fetchall()
        return [Turno(**dict(f)) for f in reversed(filas)]

    def buscar_en_conversaciones(self, texto: str, limite: int = 10) -> list[Turno]:
        with self._conexion() as con:
            filas = con.execute(
                "SELECT rol, contenido, momento, motor FROM conversaciones "
                "WHERE contenido LIKE ? ORDER BY id DESC LIMIT ?",
                (f"%{texto}%", limite),
            ).fetchall()
        return [Turno(**dict(f)) for f in filas]

    # -- Sesiones ------------------------------------------------------------

    def abrir_sesion(self, sesion: str) -> None:
        """Anota que Jarvis acaba de arrancar."""
        with self._conexion() as con:
            con.execute(
                "INSERT OR IGNORE INTO sesiones (id, inicio) VALUES (?, ?)",
                (sesion, self._ahora()),
            )

    def cerrar_sesion(self, sesion: str, motivo: str = "apagado") -> None:
        """Anota que Jarvis se apaga del todo. Cerrar la ventana no cuenta."""
        with self._conexion() as con:
            con.execute(
                "UPDATE sesiones SET fin = ?, motivo_fin = ? WHERE id = ?",
                (self._ahora(), motivo, sesion),
            )

    def registrar_accion(
        self, sesion: str, habilidad: str, objetivo: str = "", resultado: str = ""
    ) -> None:
        """Deja constancia de una acción ejecutada en esta sesión."""
        with self._conexion() as con:
            con.execute(
                "INSERT INTO acciones (sesion, habilidad, objetivo, resultado, momento) "
                "VALUES (?, ?, ?, ?, ?)",
                (sesion, habilidad, objetivo, resultado[:400], self._ahora()),
            )

    def acciones_de_sesion(self, sesion: str, limite: int = 30) -> list[dict[str, str]]:
        with self._conexion() as con:
            filas = con.execute(
                "SELECT habilidad, objetivo, resultado, momento FROM acciones "
                "WHERE sesion = ? ORDER BY id DESC LIMIT ?",
                (sesion, limite),
            ).fetchall()
        return [dict(f) for f in reversed(filas)]

    def inicio_de_sesion(self, sesion: str) -> str | None:
        with self._conexion() as con:
            fila = con.execute(
                "SELECT inicio FROM sesiones WHERE id = ?", (sesion,)
            ).fetchone()
        return fila["inicio"] if fila else None

    # -- Hechos --------------------------------------------------------------

    def recordar(self, clave: str, valor: str, categoria: str = "general") -> None:
        """Guarda un hecho sobre el usuario, o actualiza el que ya existía."""
        ahora = self._ahora()
        with self._conexion() as con:
            con.execute(
                "INSERT INTO hechos (clave, valor, categoria, creado, actualizado) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(clave) DO UPDATE SET valor = ?, actualizado = ?",
                (clave, valor, categoria, ahora, ahora, valor, ahora),
            )

    def olvidar(self, clave: str) -> bool:
        with self._conexion() as con:
            cur = con.execute("DELETE FROM hechos WHERE clave = ?", (clave,))
        return cur.rowcount > 0

    def hechos(self, categoria: str | None = None) -> dict[str, str]:
        consulta = "SELECT clave, valor FROM hechos"
        parametros: tuple[Any, ...] = ()
        if categoria:
            consulta += " WHERE categoria = ?"
            parametros = (categoria,)
        with self._conexion() as con:
            filas = con.execute(consulta, parametros).fetchall()
        return {f["clave"]: f["valor"] for f in filas}

    def resumen_para_prompt(self) -> str:
        """Los hechos formateados para inyectarlos en el prompt de sistema."""
        hechos = self.hechos()
        if not hechos:
            return ""
        lineas = [f"- {clave}: {valor}" for clave, valor in hechos.items()]
        return "Lo que sabes sobre el usuario:\n" + "\n".join(lineas)

    # -- Preferencias --------------------------------------------------------

    def preferencia(self, clave: str, por_defecto: Any = None) -> Any:
        with self._conexion() as con:
            fila = con.execute(
                "SELECT valor FROM preferencias WHERE clave = ?", (clave,)
            ).fetchone()
        if fila is None:
            return por_defecto
        try:
            return json.loads(fila["valor"])
        except json.JSONDecodeError:
            return fila["valor"]

    def fijar_preferencia(self, clave: str, valor: Any) -> None:
        with self._conexion() as con:
            con.execute(
                "INSERT INTO preferencias (clave, valor, actualizado) VALUES (?, ?, ?) "
                "ON CONFLICT(clave) DO UPDATE SET valor = ?, actualizado = ?",
                (
                    clave,
                    json.dumps(valor),
                    self._ahora(),
                    json.dumps(valor),
                    self._ahora(),
                ),
            )
