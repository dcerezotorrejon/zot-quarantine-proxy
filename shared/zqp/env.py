"""Lectura de configuración desde variables de entorno."""

from __future__ import annotations

import os
import re
from urllib.parse import urlparse

_DURATION_PATTERN = re.compile(r"^(\d+)([smhd])$")
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def env_str(name: str, default: str) -> str:
    """Lee una variable de texto sin espacios sobrantes.

    Args:
        name: Nombre de la variable de entorno.
        default: Valor si la variable no existe.

    Returns:
        El valor leído o el valor por defecto.
    """
    return os.environ.get(name, default).strip()


def env_int(name: str, default: int) -> int:
    """Lee una variable entera.

    Args:
        name: Nombre de la variable de entorno.
        default: Valor si la variable no existe.

    Returns:
        El entero leído.

    Raises:
        ValueError: Si el valor no es un entero.
    """
    return int(env_str(name, str(default)))


def env_list(name: str, default: str) -> frozenset[str]:
    """Lee una lista separada por comas, sin espacios ni elementos vacíos.

    Args:
        name: Nombre de la variable de entorno.
        default: Valor si la variable no existe.

    Returns:
        Conjunto de elementos no vacíos.
    """
    items = (item.strip() for item in env_str(name, default).split(","))
    return frozenset(item for item in items if item)


def env_duration_seconds(name: str, default: str) -> int:
    """Lee una duración con sufijo y la convierte en segundos.

    Args:
        name: Nombre de la variable de entorno.
        default: Valor si la variable no existe, p. ej. ``6h``.

    Returns:
        Duración en segundos.

    Raises:
        ValueError: Si el formato no es ``30s``, ``15m``, ``6h`` o ``7d``.
    """
    raw = env_str(name, default)
    match = _DURATION_PATTERN.match(raw)
    if match is None:
        raise ValueError(f"{name}: duración inválida {raw!r} (usa 30s, 15m, 6h o 7d)")
    value, unit = match.groups()
    return int(value) * _UNIT_SECONDS[unit]


def registry_host(url: str) -> str:
    """Extrae host y puerto de una URL, en el formato que espera skopeo.

    Args:
        url: URL completa, p. ej. ``http://zot:5000``.

    Returns:
        Host y puerto, p. ej. ``zot:5000``.
    """
    parsed = urlparse(url)
    return parsed.netloc or url
