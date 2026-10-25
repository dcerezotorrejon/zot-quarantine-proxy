"""Reenvío de peticiones OCI hacia los registros públicos (upstream), con token anónimo."""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping

import httpx

from zqp.image_ref import RepoName

from .forwarder import FORWARDED_REQUEST_HEADERS

logger = logging.getLogger(__name__)

# Base de cada registro público permitido.
REGISTRY_BASES = {
    "docker.io": "https://registry-1.docker.io",
    "ghcr.io": "https://ghcr.io",
    "quay.io": "https://quay.io",
    "registry.k8s.io": "https://registry.k8s.io",
}

# Endpoint de token anónimo (realm, service) por registro.
REGISTRY_AUTH = {
    "docker.io": ("https://auth.docker.io/token", "registry.docker.io"),
    "ghcr.io": ("https://ghcr.io/token", "ghcr.io"),
    "quay.io": ("https://quay.io/v2/auth", "quay.io"),
    "registry.k8s.io": ("https://registry.k8s.io/token", "registry.k8s.io"),
}

_TOKEN_TTL_SECONDS = 240


class UpstreamAuthError(RuntimeError):
    """No se pudo obtener el token anónimo del registro upstream."""


class UpstreamForwarder:
    """Cliente asíncrono que reenvía peticiones al registro público de origen."""

    def __init__(self) -> None:
        """Crea el cliente HTTP y la caché de tokens."""
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, read=300.0), follow_redirects=True
        )
        self._tokens: dict[tuple[str, str], tuple[str, float]] = {}

    async def send(
        self, repo: RepoName, path: str, method: str, request_headers: Mapping[str, str]
    ) -> httpx.Response:
        """Envía la petición al upstream en streaming, añadiendo el token anónimo.

        Args:
            repo: Repositorio upstream (registro más ruta).
            path: Ruta OCI en el upstream, p. ej. ``/v2/library/alpine/manifests/3.20``.
            method: Método HTTP de la petición original (GET o HEAD).
            request_headers: Cabeceras de la petición original. Solo se reenvían
                ``Accept`` y ``Range``.

        Returns:
            La respuesta del upstream, pendiente de leer (streaming).
        """
        base = REGISTRY_BASES.get(repo.registry, f"https://{repo.registry}")
        headers = {
            name: request_headers[name]
            for name in FORWARDED_REQUEST_HEADERS
            if name in request_headers
        }
        token = await self._token(repo)
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        request = self._client.build_request(method, f"{base}{path}", headers=headers)
        return await self._client.send(request, stream=True)

    async def _token(self, repo: RepoName) -> str | None:
        """Devuelve (cacheando) el token anónimo de pull para un repositorio.

        Args:
            repo: Repositorio upstream.

        Returns:
            El token, o None si el registro no tiene endpoint conocido.

        Raises:
            UpstreamAuthError: Si el endpoint deniega o no devuelve token.
        """
        auth = REGISTRY_AUTH.get(repo.registry)
        if auth is None:
            return None
        realm, service = auth
        key = (repo.registry, repo.path)
        cached = self._tokens.get(key)
        if cached is not None and cached[1] > time.time():
            return cached[0]

        scope = f"repository:{repo.path}:pull"
        response = await self._client.get(f"{realm}?service={service}&scope={scope}")
        if response.status_code != 200:
            raise UpstreamAuthError(
                f"Token denegado ({response.status_code}) para {repo.registry}/{repo.path}"
            )
        data = response.json()
        token = data.get("token") or data.get("access_token")
        if not token:
            raise UpstreamAuthError(f"No se obtuvo token de {repo.registry} para {repo.path}")
        self._tokens[key] = (token, time.time() + _TOKEN_TTL_SECONDS)
        return token

    async def close(self) -> None:
        """Cierra el cliente HTTP."""
        await self._client.aclose()
