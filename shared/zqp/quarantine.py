"""Gestión de la cuarentena de Zot: copia de digests y limpieza (usada por proxy y updater)."""

from __future__ import annotations

import logging
import time

from . import skopeo
from .image_ref import QUARANTINE, VERIFIED, RepoName, digest_to_tag, zot_reference
from .state import StateStore
from .zot_api import ZotApi

logger = logging.getLogger(__name__)


def ensure_quarantined(
    zot: ZotApi,
    registry_host: str,
    state: StateStore,
    repo: RepoName,
    reference: str,
    digest: str,
    now: int | None = None,
) -> None:
    """Asegura que un digest esté en cuarentena y registrado bajo una referencia.

    El digest se guarda en ``quarantine/`` bajo un tag derivado de él mismo
    (``sha256-<hex>``), para que cada digest tenga su propio reloj. Si el digest
    ya está en Zot, solo se registra el estado; si no, se copia del upstream.

    Al copiar un digest nuevo se obtiene su fecha de build (``Created``) desde la
    copia local, que conserva el ``created`` del config del origen. Ese instante
    marca el inicio del plazo de cuarentena; si no se puede obtener, el plazo
    cuenta desde la entrada en cuarentena.

    Args:
        zot: Cliente de la API de Zot.
        registry_host: Host y puerto de Zot para skopeo.
        state: Base de datos de estado.
        repo: Repositorio upstream.
        reference: Tag de cliente que motiva la cuarentena.
        digest: Digest del manifest a cuarentenar.
        now: Instante de entrada, en segundos epoch. Por defecto, ahora.

    Raises:
        SkopeoError: Si la copia falla. En ese caso no se registra nada.
    """
    tag = digest_to_tag(digest)
    published_at = None
    if zot.get_digest(repo.physical(QUARANTINE), tag) is None:
        source = f"{repo.upstream_image}:{reference}"
        destination = zot_reference(registry_host, repo.physical(QUARANTINE), tag)
        logger.info("Descargando %s a cuarentena (digest %s)", source, digest)
        skopeo.copy(source, destination)
        published_at = skopeo.inspect_created(destination)
        if published_at is None:
            logger.debug(
                "Sin fecha de build para %s@%s; el plazo contará desde la entrada",
                repo.upstream_image,
                digest,
            )
    state.add_quarantine(
        repo.registry, repo.path, digest, reference, now or int(time.time()), published_at
    )


def remove_from_quarantine(zot: ZotApi, state: StateStore, repo: RepoName, digest: str) -> None:
    """Borra un digest de la cuarentena: su manifest en Zot y sus filas de estado.

    Args:
        zot: Cliente de la API de Zot.
        state: Base de datos de estado.
        repo: Repositorio upstream.
        digest: Digest del manifest a eliminar.
    """
    zot.delete_manifest(repo.physical(QUARANTINE), digest)
    state.remove_quarantine(repo.registry, repo.path, digest)


def promote_to_verified(
    zot: ZotApi, registry_host: str, state: StateStore, repo: RepoName, digest: str
) -> None:
    """Copia un digest de cuarentena a ``verified`` bajo cada ref y lo quita de cuarentena.

    Args:
        zot: Cliente de la API de Zot.
        registry_host: Host y puerto de Zot para skopeo.
        state: Base de datos de estado.
        repo: Repositorio upstream.
        digest: Digest del manifest a promover.
    """
    references = state.quarantine_refs(repo.registry, repo.path, digest)
    tag = digest_to_tag(digest)
    for reference in references:
        skopeo.copy(
            zot_reference(registry_host, repo.physical(QUARANTINE), tag),
            zot_reference(registry_host, repo.physical(VERIFIED), reference),
        )
    remove_from_quarantine(zot, state, repo, digest)
