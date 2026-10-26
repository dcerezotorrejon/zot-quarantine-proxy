"""Paso de retención del ciclo: poda de ``verified`` las versiones que ninguna referencia pide."""

from __future__ import annotations

import logging

import httpx

from zqp.image_ref import VERIFIED, RepoName
from zqp.skopeo import SkopeoError
from zqp.state import StateStore
from zqp.zot_api import ZotApi

logger = logging.getLogger(__name__)


class Retention:
    """Paso de retención: poda de ``verified`` las versiones que ninguna referencia pide."""

    def __init__(self, state: StateStore, zot: ZotApi, registry_host: str) -> None:
        """Crea el paso.

        Args:
            state: Base de datos de estado con las refs pedidas.
            zot: Cliente de la API de Zot.
            registry_host: Host y puerto de Zot para skopeo (sin uso en este paso).
        """
        self._state = state
        self._zot = zot
        self._registry_host = registry_host

    def run(self) -> None:
        """Aplica la retención a todos los paquetes pedidos."""
        for (registry, path), references in self._state.refs_by_repo().items():
            repo = RepoName(registry, path)
            try:
                self._apply(repo, set(references))
            except (SkopeoError, httpx.HTTPError):
                logger.exception("Retención fallida para %s", repo.upstream_image)

    def _apply(self, repo: RepoName, keep: set[str]) -> None:
        """Poda las versiones de un paquete que ninguna referencia pide.

        Args:
            repo: Repositorio upstream.
            keep: Referencias pedidas por los clientes para ese repositorio.
        """
        physical = repo.physical(VERIFIED)
        tags = self._zot.list_tags(physical)
        if not tags:
            return

        protected_digests = {self._zot.get_digest(physical, tag) for tag in keep}
        protected_digests.discard(None)

        for tag in tags:
            if tag in keep:
                continue
            digest = self._zot.get_digest(physical, tag)
            if digest is None or digest in protected_digests:
                continue
            self._zot.delete_manifest(physical, digest)
            logger.info("Retirada versión antigua %s:%s", physical, tag)
