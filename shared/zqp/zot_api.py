"""Cliente síncrono de la API OCI de Zot para listar, consultar y borrar."""

from __future__ import annotations

import httpx

MANIFEST_ACCEPT = ", ".join(
    [
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    ]
)


class ZotApi:
    """Cliente síncrono de la API OCI de Zot."""

    def __init__(self, base_url: str, timeout: float = 30.0) -> None:
        """Crea el cliente HTTP.

        Args:
            base_url: URL base de Zot, p. ej. ``http://zot:5000``.
            timeout: Tiempo máximo por petición, en segundos.
        """
        self._client = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)

    def close(self) -> None:
        """Cierra las conexiones abiertas."""
        self._client.close()

    def ping(self) -> bool:
        """Comprueba si Zot responde.

        Returns:
            True si ``/v2/`` devuelve 200; False si no responde o da error.
        """
        try:
            return self._client.get("/v2/").status_code == 200
        except httpx.HTTPError:
            return False

    def list_tags(self, repo: str) -> list[str]:
        """Lista los tags de un repositorio.

        Args:
            repo: Ruta física del repositorio, p. ej. ``verified/docker.io/library/nginx``.

        Returns:
            Tags del repositorio; lista vacía si el repositorio no existe.

        Raises:
            httpx.HTTPStatusError: Si Zot responde con un error distinto de 404.
        """
        response = self._client.get(f"/v2/{repo}/tags/list")
        if response.status_code == 404:
            return []
        response.raise_for_status()
        return list(response.json().get("tags") or [])

    def get_digest(self, repo: str, reference: str) -> str | None:
        """Obtiene el digest del manifest de un tag.

        Args:
            repo: Ruta física del repositorio.
            reference: Tag o digest.

        Returns:
            El digest ``sha256:...``, o None si no existe.

        Raises:
            httpx.HTTPStatusError: Si Zot responde con un error distinto de 404.
        """
        response = self._client.head(
            f"/v2/{repo}/manifests/{reference}", headers={"Accept": MANIFEST_ACCEPT}
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.headers.get("Docker-Content-Digest")

    def delete_manifest(self, repo: str, digest: str) -> None:
        """Borra un manifest por digest. Se pierden también todos sus tags en ese repositorio.

        Args:
            repo: Ruta física del repositorio.
            digest: Digest del manifest a borrar.

        Raises:
            httpx.HTTPStatusError: Si Zot responde con un error distinto de 202 o 404.
        """
        response = self._client.delete(f"/v2/{repo}/manifests/{digest}")
        if response.status_code in (202, 404):
            return
        response.raise_for_status()
