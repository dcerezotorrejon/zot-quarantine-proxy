"""Inserción de imágenes en la cuarentena de Zot (usada por proxy y updater)."""

from __future__ import annotations

import logging
import time

from . import skopeo
from .image_ref import QUARANTINE, RepoName, zot_reference
from .state import StateStore
from .zot_api import ZotApi

logger = logging.getLogger(__name__)


def copy_to_quarantine(registry_host: str, state: StateStore, repo: RepoName, tag: str) -> None:
    """Descarga un tag del registro upstream a ``quarantine/`` y lo registra en el estado.

    Args:
        registry_host: Host y puerto de Zot, p. ej. ``zot:5000``.
        state: Base de datos de estado donde se anota la entrada.
        repo: Repositorio upstream.
        tag: Tag concreto a descargar.

    Raises:
        SkopeoError: Si la copia falla. En ese caso no se registra nada.
    """
    source = f"{repo.upstream_image}:{tag}"
    destination = zot_reference(registry_host, repo.physical(QUARANTINE), tag)
    logger.info("Descargando %s a cuarentena", source)
    skopeo.copy(source, destination)
    state.add_quarantine(repo.registry, repo.path, tag, int(time.time()))


def remove_from_quarantine(zot: ZotApi, state: StateStore, repo: RepoName, tag: str) -> None:
    """Borra un tag de ``quarantine/`` en Zot y lo quita del registro de cuarentena.

    Args:
        zot: Cliente de la API de Zot.
        state: Base de datos de estado.
        repo: Repositorio upstream.
        tag: Tag a eliminar de la cuarentena.
    """
    quarantine_repo = repo.physical(QUARANTINE)
    digest = zot.get_digest(quarantine_repo, tag)
    if digest is not None:
        zot.delete_manifest(quarantine_repo, digest)
    state.remove_quarantine(repo.registry, repo.path, tag)
