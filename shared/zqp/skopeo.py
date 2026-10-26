"""Envoltorio mínimo sobre la CLI `skopeo`: copias entre registros e inspección de digests."""

from __future__ import annotations

import subprocess

from .env import env_str

COPY_TIMEOUT_SECONDS = 1800
LIST_TIMEOUT_SECONDS = 120


class SkopeoError(RuntimeError):
    """skopeo terminó con error o no devolvió una salida válida."""


def copy(source: str, destination: str) -> None:
    """Copia una imagen al destino.

    Por defecto copia todas las arquitecturas (``--multi-arch all``), para que el
    digest del índice que devuelve ``inspect_digest`` coincida con lo que queda
    almacenado en Zot. Se puede cambiar con la variable ``SKOPEO_MULTI_ARCH``,
    pero solo se recomienda ``all``: ``system`` o ``index-only`` rompen la
    correlación entre el digest inspeccionado y el almacenado.

    Args:
        source: Referencia de origen sin ``docker://``, p. ej. ``docker.io/library/nginx:1.27``.
        destination: Referencia de destino sin ``docker://``.

    Raises:
        SkopeoError: Si skopeo falla o supera el tiempo límite.
    """
    multi_arch = env_str("SKOPEO_MULTI_ARCH", "all")
    _run(
        ["copy", "--multi-arch", multi_arch, f"docker://{source}", f"docker://{destination}"],
        COPY_TIMEOUT_SECONDS,
    )


def export_dir(image_with_tag: str, dest_dir: str) -> None:
    """Exporta una imagen a un directorio en layout OCI, para escanear sus capas.

    Extrae solo la plataforma del sistema (``--multi-arch system``), para que
    ``umoci`` pueda desempaquetar un manifest único aunque la imagen sea multi-arch.

    Args:
        image_with_tag: Imagen con tag, p. ej. ``zot:5000/quarantine/...:sha256-...``.
        dest_dir: Directorio destino. Se crea en él un layout OCI etiquetado ``latest``.

    Raises:
        SkopeoError: Si skopeo falla.
    """
    _run(
        ["copy", "--multi-arch", "system", f"docker://{image_with_tag}", f"oci:{dest_dir}:latest"],
        COPY_TIMEOUT_SECONDS,
    )


def inspect_digest(image_with_tag: str) -> str:
    """Devuelve el digest del manifest de una imagen con tag.

    Args:
        image_with_tag: Referencia completa con tag, p. ej.
            ``docker.io/library/nginx:latest``.

    Returns:
        Digest ``sha256:...`` del manifest (o del índice, si la imagen es multi-arch).

    Raises:
        SkopeoError: Si skopeo falla (p. ej. el tag no existe) o la salida no es
            un digest válido.
    """
    output = _run(
        ["inspect", "--format", "{{.Digest}}", f"docker://{image_with_tag}"],
        LIST_TIMEOUT_SECONDS,
    )
    digest = output.strip()
    if not digest.startswith("sha256:"):
        raise SkopeoError(f"Respuesta inesperada de skopeo inspect para {image_with_tag}")
    return digest


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
