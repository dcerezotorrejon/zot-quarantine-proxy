"""Punto de entrada HTTP del proxy: expone la API OCI Distribution hacia los clientes."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from zqp.image_ref import QUARANTINE, VERIFIED, ImageRef, InvalidImageName, RepoName
from zqp.skopeo import SkopeoError
from zqp.state import StateStore
from zqp.zot_api import ZotApi

from .config import Settings
from .forwarder import ZotForwarder, to_streaming_response
from .service import ImageNotFound, ImageService, UpstreamNotAllowed

logger = logging.getLogger(__name__)

DISTRIBUTION_HEADERS = {"Docker-Distribution-API-Version": "registry/2.0"}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Prepara los servicios al arrancar y los libera al parar.

    Args:
        app: Aplicación FastAPI a la que se añaden ``service`` y ``forwarder``.

    Yields:
        Control a la aplicación mientras está en marcha.
    """
    settings = Settings.from_env()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    zot = ZotApi(settings.zot_url)
    state = StateStore(settings.state_dir / "state.sqlite3")
    app.state.service = ImageService(settings, zot, state)
    app.state.forwarder = ZotForwarder(settings.zot_url)
    logger.info("Proxy listo; Zot en %s", settings.zot_url)
    try:
        yield
    finally:
        await app.state.forwarder.close()
        zot.close()


app = FastAPI(
    title="zot-quarantine-proxy",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


def oci_error(status: int, code: str, message: str) -> JSONResponse:
    """Construye una respuesta de error con el formato de la API OCI.

    Args:
        status: Código HTTP.
        code: Código de error OCI, p. ej. ``MANIFEST_UNKNOWN``.
        message: Texto legible para el cliente.

    Returns:
        Respuesta JSON ``{"errors": [...]}``.
    """
    body = {"errors": [{"code": code, "message": message}]}
    return JSONResponse(body, status_code=status, headers=DISTRIBUTION_HEADERS)


def _register_error(exc_type: type[Exception], status: int, code: str) -> None:
    """Asocia una excepción a una respuesta de error OCI.

    Args:
        exc_type: Tipo de excepción a traducir.
        status: Código HTTP que se devuelve.
        code: Código de error OCI que se devuelve.
    """

    async def handler(_request: Request, exc: Exception) -> JSONResponse:
        if status >= 500:
            logger.error("%s: %s", code, exc)
        return oci_error(status, code, str(exc))

    app.add_exception_handler(exc_type, handler)


_register_error(InvalidImageName, 400, "NAME_INVALID")
_register_error(UpstreamNotAllowed, 403, "DENIED")
_register_error(ImageNotFound, 404, "MANIFEST_UNKNOWN")
_register_error(SkopeoError, 502, "UNKNOWN")
_register_error(httpx.TransportError, 503, "UNAVAILABLE")


@app.get("/v2/")
async def api_version() -> Response:
    """Indica al cliente que el servidor habla la API OCI Distribution (``GET /v2/``)."""
    return JSONResponse({}, headers=DISTRIBUTION_HEADERS)


@app.api_route("/v2/{name:path}/manifests/{reference}", methods=["GET", "HEAD"])
async def get_manifest(name: str, reference: str, request: Request) -> Response:
    """Sirve el manifest de una imagen desde la copia que corresponda.

    Args:
        name: Repositorio tal y como llega en la ruta, p. ej. ``docker.io/library/nginx``.
        reference: Tag o digest pedido.
        request: Petición entrante, para acceder al servicio y al reenvío.

    Returns:
        Manifest reenviado desde Zot, en streaming.
    """
    ref = ImageRef.from_route(name, reference)
    local = await run_in_threadpool(request.app.state.service.locate, ref)
    path = f"/v2/{ref.repo.physical(local.namespace)}/manifests/{local.reference}"
    upstream = await request.app.state.forwarder.send(request.method, path, request.headers)
    return to_streaming_response(upstream)


@app.api_route("/v2/{name:path}/blobs/{digest}", methods=["GET", "HEAD"])
async def get_blob(name: str, digest: str, request: Request) -> Response:
    """Sirve un blob por digest, buscándolo en ``verified`` y después en ``quarantine``.

    Args:
        name: Repositorio tal y como llega en la ruta.
        digest: Digest del blob, ``sha256:...``.
        request: Petición entrante.

    Returns:
        Blob reenviado desde Zot, o error ``BLOB_UNKNOWN`` si no existe en ninguno.
    """
    repo = RepoName.from_name(name)
    request.app.state.service.ensure_allowed(repo)
    forwarder = request.app.state.forwarder
    # Los blobs son direccionables por digest: se buscan primero en verified y luego en quarantine.
    for namespace in (VERIFIED, QUARANTINE):
        path = f"/v2/{repo.physical(namespace)}/blobs/{digest}"
        upstream = await forwarder.send(request.method, path, request.headers)
        if upstream.status_code != 404:
            return to_streaming_response(upstream)
        await upstream.aclose()
    return oci_error(404, "BLOB_UNKNOWN", f"El blob {digest} no existe")


@app.get("/v2/{name:path}/tags/list")
async def list_tags(name: str, request: Request) -> Response:
    """Lista los tags verificados de un repositorio (``GET /v2/<name>/tags/list``).

    Args:
        name: Repositorio tal y como llega en la ruta.
        request: Petición entrante.

    Returns:
        JSON con el nombre y los tags ordenados.
    """
    repo = RepoName.from_name(name)
    tags = await run_in_threadpool(request.app.state.service.verified_tags, repo)
    return JSONResponse({"name": name, "tags": sorted(tags)}, headers=DISTRIBUTION_HEADERS)
