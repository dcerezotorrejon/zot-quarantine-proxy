"""Estado persistente compartido por proxy y updater (SQLite).

- `refs`: referencias de tag solicitadas por los clientes (qué mantener fresco).
- `quarantine`: un reloj de cuarentena por digest (inmutable).
- `quarantine_refs`: qué tag de cliente satisface cada digest en cuarentena.
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
    digest TEXT NOT NULL,
    quarantined_at INTEGER NOT NULL,
    PRIMARY KEY (registry, path, digest)
);
CREATE TABLE IF NOT EXISTS quarantine_refs (
    registry TEXT NOT NULL,
    path TEXT NOT NULL,
    digest TEXT NOT NULL,
    reference TEXT NOT NULL,
    PRIMARY KEY (registry, path, digest, reference)
);
"""


@dataclass(frozen=True)
class QuarantineRecord:
    """Un digest en cuarentena y el momento en que entró."""

    registry: str
    path: str
    digest: str
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
        """Elimina un paquete completo del estado: refs, cuarentena y sus referencias.

        Args:
            registry: Registro upstream del paquete.
            path: Ruta del repositorio.
        """
        with self._connect() as connection:
            connection.execute("DELETE FROM refs WHERE registry = ? AND path = ?", (registry, path))
            connection.execute(
                "DELETE FROM quarantine_refs WHERE registry = ? AND path = ?", (registry, path)
            )
            connection.execute(
                "DELETE FROM quarantine WHERE registry = ? AND path = ?", (registry, path)
            )

    def add_quarantine(
        self, registry: str, path: str, digest: str, reference: str, quarantined_at: int
    ) -> None:
        """Registra un digest en cuarentena y la referencia de cliente que satisface.

        Si el digest ya estaba en cuarentena, se conserva su fecha de entrada
        original y solo se añade la nueva referencia.

        Args:
            registry: Registro upstream.
            path: Ruta del repositorio.
            digest: Digest del manifest, p. ej. ``sha256:abc…``.
            reference: Tag de cliente que debe apuntar a ese digest al promover.
            quarantined_at: Instante de entrada, en segundos epoch.
        """
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO quarantine (registry, path, digest, quarantined_at) "
                "VALUES (?, ?, ?, ?)",
                (registry, path, digest, quarantined_at),
            )
            connection.execute(
                "INSERT OR IGNORE INTO quarantine_refs (registry, path, digest, reference) "
                "VALUES (?, ?, ?, ?)",
                (registry, path, digest, reference),
            )

    def list_quarantine(self) -> list[QuarantineRecord]:
        """Lista todos los digests en cuarentena.

        Returns:
            Registros de cuarentena, en el orden de la tabla.
        """
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT registry, path, digest, quarantined_at FROM quarantine"
            ).fetchall()
        return [
            QuarantineRecord(row["registry"], row["path"], row["digest"], row["quarantined_at"])
            for row in rows
        ]

    def quarantine_refs(self, registry: str, path: str, digest: str) -> list[str]:
        """Lista las referencias de cliente asociadas a un digest en cuarentena.

        Args:
            registry: Registro upstream.
            path: Ruta del repositorio.
            digest: Digest del manifest.

        Returns:
            Referencias (tags de cliente) que apuntarán a ese digest al promover.
        """
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT reference FROM quarantine_refs "
                "WHERE registry = ? AND path = ? AND digest = ? ORDER BY reference",
                (registry, path, digest),
            ).fetchall()
        return [row["reference"] for row in rows]

    def quarantine_refs_by_repo(self) -> dict[tuple[str, str], list[tuple[str, str]]]:
        """Agrupa las asociaciones ``(digest, reference)`` por repositorio.

        Returns:
            Diccionario ``(registry, path) -> [(digest, reference), ...]``.
        """
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT registry, path, digest, reference FROM quarantine_refs "
                "ORDER BY registry, path, digest, reference"
            ).fetchall()
        grouped: dict[tuple[str, str], list[tuple[str, str]]] = {}
        for row in rows:
            grouped.setdefault((row["registry"], row["path"]), []).append(
                (row["digest"], row["reference"])
            )
        return grouped

    def remove_quarantine_ref(self, registry: str, path: str, digest: str, reference: str) -> None:
        """Quita la asociación de una referencia con un digest.

        No borra la fila de cuarentena del digest aunque se quede sin referencias.

        Args:
            registry: Registro upstream.
            path: Ruta del repositorio.
            digest: Digest del manifest.
            reference: Referencia de cliente a desasociar.
        """
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM quarantine_refs "
                "WHERE registry = ? AND path = ? AND digest = ? AND reference = ?",
                (registry, path, digest, reference),
            )

    def remove_quarantine(self, registry: str, path: str, digest: str) -> None:
        """Quita un digest de la cuarentena y todas sus referencias.

        Args:
            registry: Registro upstream.
            path: Ruta del repositorio.
            digest: Digest del manifest a quitar.
        """
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM quarantine_refs WHERE registry = ? AND path = ? AND digest = ?",
                (registry, path, digest),
            )
            connection.execute(
                "DELETE FROM quarantine WHERE registry = ? AND path = ? AND digest = ?",
                (registry, path, digest),
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
