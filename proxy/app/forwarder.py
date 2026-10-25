"""Reenvío en streaming de peticiones OCI hacia Zot."""

from __future__ import annotations

from collections.abc import Mapping

import httpx
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

HOP_BY_HOP_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
    }
)
FORWARDED_REQUEST_HEADERS = ("accept", "range")


class ZotForwarder:
    """Cliente asíncrono que reenvía peticiones a Zot."""

    def __init__(self, base_url: str) -> None:
        """Crea el cliente HTTP.

        Args:
            base_url: URL base de Zot, p. ej. ``http://zot:5000``.
        """
        self._client = httpx.AsyncClient(
            base_url=base_url, timeout=httpx.Timeout(30.0, read=300.0)
        )

    async def send(
        self, method: str, path: str, request_headers: Mapping[str, str]
    ) -> httpx.Response:
        """Envía la petición a Zot sin leer el cuerpo: la respuesta se hace en streaming.

        Args:
            method: Método HTTP de la petición original (GET o HEAD).
            path: Ruta en Zot, p. ej. ``/v2/verified/.../manifests/1.27``.
            request_headers: Cabeceras de la petición original. Solo se reenvían
                ``Accept`` y ``Range``.

        Returns:
            La respuesta de Zot, pendiente de leer. Quien la use debe cerrarla.
        """
        headers = {
            name: request_headers[name]
            for name in FORWARDED_REQUEST_HEADERS
            if name in request_headers
        }
        request = self._client.build_request(method, path, headers=headers)
        return await self._client.send(request, stream=True)

    async def close(self) -> None:
        """Cierra el cliente HTTP."""
        await self._client.aclose()


def to_streaming_response(upstream: httpx.Response) -> StreamingResponse:
    """Convierte la respuesta de Zot en la respuesta del proxy, sin copiar el cuerpo.

    Quita las cabeceras de salto (``hop-by-hop``) y cierra la respuesta de Zot al
    terminar de enviar el cuerpo.

    Args:
        upstream: Respuesta recibida de Zot.

    Returns:
        Respuesta en streaming con el mismo estado y las cabeceras válidas.
    """
    headers = {
        name: value
        for name, value in upstream.headers.items()
        if name.lower() not in HOP_BY_HOP_HEADERS
    }
    return StreamingResponse(
        upstream.aiter_raw(),
        status_code=upstream.status_code,
        headers=headers,
        background=BackgroundTask(upstream.aclose),
    )
