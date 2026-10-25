"""Estado persistente compartido por proxy y updater (SQLite).

- `refs`: referencias de tag solicitadas por los clientes (qué mantener fresco).
- `quarantine`: cuándo entró cada tag en cuarentena (para contar los días).
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS refs (
    registry TEXT NOT NULL,
    path TEXT NOT NULL,
    reference TEXT NOT NULL,
    last_requested INTEGER NOT NULL,
    PRIMARY KEY (registry, path, reference)
);
CREATE TABLE IF NOT EXISTS quarantine (
    registry TEXT NOT NULL,
    path TEXT NOT NULL,
    tag TEXT NOT NULL,
    quarantined_at INTEGER NOT NULL,
    PRIMARY KEY (registry, path, tag)
);
"""


@dataclass(frozen=True)
class QuarantineRecord:
    """Una imagen en cuarentena y el momento en que entró."""

    registry: str
    path: str
    tag: str
    quarantined_at: int


class StateStore:
    """Acceso a la base de datos de estado. Cada operación usa su propia conexión."""

    def __init__(self, db_path: Path) -> None:
        """Abre o crea la base de datos y aplica el esquema.

        Args:
            db_path: Ruta del fichero SQLite. Su directorio se crea si no existe.
        """
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        with self._connect() as connection:
            connection.executescript(_SCHEMA)

    def upsert_ref(self, registry: str, path: str, reference: str) -> None:
        """Registra una petición de tag y actualiza su última fecha de uso.

        Args:
            registry: Registro upstream, p. ej. ``docker.io``.
            path: Ruta del repositorio, p. ej. ``library/nginx``.
            reference: Tag pedido por el cliente.
        """
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO refs (registry, path, reference, last_requested)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (registry, path, reference)
                DO UPDATE SET last_requested = excluded.last_requested
                """,
                (registry, path, reference, _now()),
            )

    def refs_by_repo(self) -> dict[tuple[str, str], list[str]]:
        """Agrupa los tags pedidos por repositorio.

        Returns:
            Diccionario ``(registry, path) -> [tags]``, ordenado por nombre.
        """
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT registry, path, reference FROM refs ORDER BY registry, path, reference"
            ).fetchall()
        grouped: dict[tuple[str, str], list[str]] = {}
        for row in rows:
            grouped.setdefault((row["registry"], row["path"]), []).append(row["reference"])
        return grouped

    def delete_ref(self, registry: str, path: str, reference: str) -> None:
        """Elimina una referencia pedida del estado.

        Args:
            registry: Registro upstream.
            path: Ruta del repositorio.
            reference: Tag pedido que se deja de conservar.
        """
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM refs WHERE registry = ? AND path = ? AND reference = ?",
                (registry, path, reference),
            )

    def delete_repo(self, registry: str, path: str) -> None:
        """Elimina un paquete completo del estado: refs y cuarentena.

        Args:
            registry: Registro upstream del paquete.
            path: Ruta del repositorio.
        """
        with self._connect() as connection:
            connection.execute("DELETE FROM refs WHERE registry = ? AND path = ?", (registry, path))
            connection.execute(
                "DELETE FROM quarantine WHERE registry = ? AND path = ?", (registry, path)
            )

    def add_quarantine(self, registry: str, path: str, tag: str, quarantined_at: int) -> None:
        """Registra una imagen en cuarentena. Si ya existe, no cambia su fecha de entrada.

        Args:
            registry: Registro upstream.
            path: Ruta del repositorio.
            tag: Tag concreto descargado.
            quarantined_at: Instante de entrada, en segundos epoch.
        """
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO quarantine (registry, path, tag, quarantined_at)
                VALUES (?, ?, ?, ?)
                """,
                (registry, path, tag, quarantined_at),
            )

    def list_quarantine(self) -> list[QuarantineRecord]:
        """Lista todas las imágenes en cuarentena.

        Returns:
            Registros de cuarentena, en el orden de la tabla.
        """
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT registry, path, tag, quarantined_at FROM quarantine"
            ).fetchall()
        return [
            QuarantineRecord(row["registry"], row["path"], row["tag"], row["quarantined_at"])
            for row in rows
        ]

    def remove_quarantine(self, registry: str, path: str, tag: str) -> None:
        """Quita una imagen de la cuarentena (tras promoverla o purgarla).

        Args:
            registry: Registro upstream.
            path: Ruta del repositorio.
            tag: Tag a quitar.
        """
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM quarantine WHERE registry = ? AND path = ? AND tag = ?",
                (registry, path, tag),
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Abre una conexión con transacción; confirma al salir o revierte si hay error."""
        connection = sqlite3.connect(self._db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            with connection:  # commit al salir con éxito, rollback si falla
                yield connection
        finally:
            connection.close()


def _now() -> int:
    return int(time.time())
