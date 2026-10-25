"""Nombres y referencias de imagen tal y como llegan por la API OCI."""

from __future__ import annotations

from dataclasses import dataclass

VERIFIED = "verified"
QUARANTINE = "quarantine"
DEFAULT_REGISTRY = "docker.io"


class InvalidImageName(ValueError):
    """El nombre de imagen recibido no se puede interpretar."""


@dataclass(frozen=True)
class RepoName:
    """Repositorio upstream, p. ej. registry='docker.io', path='library/nginx'."""

    registry: str
    path: str

    @classmethod
    def from_name(cls, name: str) -> RepoName:
        """Interpreta el nombre de la ruta OCI con la convención de Docker/Podman.

        El primer segmento es un registro solo si contiene ``.`` o ``:`` o es
        ``localhost``; si no, el registro es ``docker.io``. En ``docker.io``, un
        repo sin ``/`` pertenece a ``library/``.

        Args:
            name: Nombre tal y como llega en la ruta, p. ej. ``nginx`` o
                ``ghcr.io/owner/app``.

        Returns:
            El repositorio con registro y ruta normalizados.

        Raises:
            InvalidImageName: Si el nombre está vacío o tiene barras mal colocadas.
        """
        if not name or name.startswith("/") or name.endswith("/") or "//" in name:
            raise InvalidImageName(f"Nombre de imagen inválido: {name!r}")

        first, separator, rest = name.partition("/")
        if separator and _looks_like_registry(first):
            registry, path = first, rest
        else:
            registry, path = DEFAULT_REGISTRY, name

        if registry == DEFAULT_REGISTRY and "/" not in path:
            path = f"library/{path}"
        return cls(registry, path)

    @property
    def upstream_image(self) -> str:
        """Imagen en el registro público, p. ej. ``docker.io/library/nginx``."""
        return f"{self.registry}/{self.path}"

    def physical(self, namespace: str) -> str:
        """Nombre del repositorio dentro de Zot.

        Args:
            namespace: ``verified`` o ``quarantine``.

        Returns:
            Ruta física, p. ej. ``verified/docker.io/library/nginx``.
        """
        return f"{namespace}/{self.registry}/{self.path}"


@dataclass(frozen=True)
class ImageRef:
    """Petición concreta: repositorio más tag o digest."""

    repo: RepoName
    reference: str

    @classmethod
    def from_route(cls, name: str, reference: str) -> ImageRef:
        """Crea la referencia a partir de los parámetros de la ruta HTTP.

        Args:
            name: Nombre del repositorio tal y como llega en la ruta.
            reference: Tag o digest pedido.

        Returns:
            La petición normalizada.

        Raises:
            InvalidImageName: Si el nombre o la referencia no son válidos.
        """
        if not reference:
            raise InvalidImageName("La referencia (tag o digest) está vacía")
        return cls(RepoName.from_name(name), reference)

    @property
    def is_digest(self) -> bool:
        return ":" in self.reference


def zot_reference(registry_host: str, physical_repo: str, tag: str) -> str:
    """Construye la referencia completa que usa skopeo para hablar con Zot.

    Args:
        registry_host: Host y puerto de Zot, p. ej. ``zot:5000``.
        physical_repo: Ruta física del repositorio dentro de Zot.
        tag: Tag de la imagen.

    Returns:
        Referencia tipo ``zot:5000/verified/docker.io/library/nginx:1.27``.
    """
    return f"{registry_host}/{physical_repo}:{tag}"


def _looks_like_registry(segment: str) -> bool:
    return "." in segment or ":" in segment or segment == "localhost"
