"""Origen desde el que se sirve una referencia: almacén verificado o registro upstream."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ServeSource(str, Enum):
    """De dónde sale la imagen que se sirve."""

    VERIFIED = "verified"  # almacén local (Zot), namespace verified
    UPSTREAM = "upstream"  # registro público de origen


@dataclass(frozen=True)
class ServeTarget:
    """Origen y referencia concreta con la que se sirve una petición."""

    source: ServeSource
    reference: str
