"""Envoltorio mínimo sobre la CLI `skopeo`: copias entre registros y listado de tags."""

from __future__ import annotations

import json
import subprocess

from .env import env_str

COPY_TIMEOUT_SECONDS = 1800
LIST_TIMEOUT_SECONDS = 120


class SkopeoError(RuntimeError):
    """skopeo terminó con error o no devolvió una salida válida."""


def copy(source: str, destination: str) -> None:
    """Copia una imagen al destino.

    Por defecto copia solo la plataforma del sistema (``--multi-arch system``),
    para no almacenar ni escanear arquitecturas que no se usan. Se puede cambiar
    con la variable ``SKOPEO_MULTI_ARCH`` (``system``, ``all`` o ``index-only``).

    Args:
        source: Referencia de origen sin ``docker://``, p. ej. ``docker.io/library/nginx:1.27``.
        destination: Referencia de destino sin ``docker://``.

    Raises:
        SkopeoError: Si skopeo falla o supera el tiempo límite.
    """
    multi_arch = env_str("SKOPEO_MULTI_ARCH", "system")
    _run(
        ["copy", "--multi-arch", multi_arch, f"docker://{source}", f"docker://{destination}"],
        COPY_TIMEOUT_SECONDS,
    )


def list_tags(image: str) -> list[str]:
    """Lista los tags publicados de una imagen en su registro.

    Args:
        image: Imagen sin tag, p. ej. ``docker.io/library/nginx``.

    Returns:
        Lista de tags publicados.

    Raises:
        SkopeoError: Si skopeo falla o la salida no tiene el formato esperado.
    """
    output = _run(["list-tags", f"docker://{image}"], LIST_TIMEOUT_SECONDS)
    try:
        return list(json.loads(output).get("Tags") or [])
    except (json.JSONDecodeError, AttributeError) as exc:
        raise SkopeoError(f"Respuesta inesperada de skopeo list-tags para {image}") from exc


def _run(args: list[str], timeout: int) -> str:
    command = [env_str("SKOPEO_BIN", "skopeo"), *args]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise SkopeoError(f"skopeo superó el tiempo límite: {args[0]}") from exc
    if result.returncode != 0:
        raise SkopeoError(result.stderr.strip() or f"skopeo {args[0]} falló")
    return result.stdout
