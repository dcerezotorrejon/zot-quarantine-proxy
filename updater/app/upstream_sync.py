"""Pasos 1 y 2 del ciclo: detectar versiones nuevas y purgar lo retirado upstream."""

from __future__ import annotations

import logging

import httpx

from zqp import skopeo
from zqp.image_ref import QUARANTINE, VERIFIED, RepoName
from zqp.quarantine import copy_to_quarantine, remove_from_quarantine
from zqp.skopeo import SkopeoError
from zqp.state import StateStore
from zqp.versions import TagKind, parse_tag, pick_tag
from zqp.zot_api import ZotApi

logger = logging.getLogger(__name__)


class UpstreamSync:
    """Pasos 1 y 2: detecta versiones nuevas en el upstream y purga las retiradas."""

    def __init__(self, state: StateStore, zot: ZotApi, registry_host: str) -> None:
        """Crea el paso.

        Args:
            state: Base de datos de estado con las refs pedidas.
            zot: Cliente de la API de Zot.
            registry_host: Host y puerto de Zot para skopeo.
        """
        self._state = state
        self._zot = zot
        self._registry_host = registry_host

    def run(self) -> None:
        """Sincroniza todos los paquetes pedidos. Un fallo en uno no detiene los demás."""
        for (registry, path), references in self._state.refs_by_repo().items():
            repo = RepoName(registry, path)
            try:
                self._sync_repo(repo, references)
            except (SkopeoError, httpx.HTTPError):
                logger.exception("Sincronización fallida para %s", repo.upstream_image)

    def _sync_repo(self, repo: RepoName, references: list[str]) -> None:
        """Descarga a cuarentena la versión más reciente de cada ref que aún no esté.

        Args:
            repo: Repositorio upstream.
            references: Tags pedidos por los clientes para ese repositorio.
        """
        upstream_tags = skopeo.list_tags(repo.upstream_image)
        verified = self._zot.list_tags(repo.physical(VERIFIED))
        quarantined = self._zot.list_tags(repo.physical(QUARANTINE))

        for reference in references:
            spec = parse_tag(reference)
            target = spec.reference if spec.kind is TagKind.EXACT else pick_tag(spec, upstream_tags)
            if target is None or target not in upstream_tags:
                continue
            if target in verified or target in quarantined:
                continue
            logger.info("Nueva versión detectada para %s: %s", repo.upstream_image, target)
            copy_to_quarantine(self._registry_host, self._state, repo, target)

        self._purge_retired(repo, upstream_tags)

    def _purge_retired(self, repo: RepoName, upstream_tags: list[str]) -> None:
        """Quita de cuarentena los tags que el upstream ya no publica."""
        quarantine_repo = repo.physical(QUARANTINE)
        for tag in self._zot.list_tags(quarantine_repo):
            if tag in upstream_tags:
                continue
            remove_from_quarantine(self._zot, self._state, repo, tag)
            logger.info("Purgado %s:%s de cuarentena: ya no está publicado", repo.upstream_image, tag)
