"""Paso 4 del ciclo: alias flotantes/parciales y retención de versiones antiguas en verified."""

from __future__ import annotations

import logging

import httpx

from zqp import skopeo
from zqp.image_ref import VERIFIED, RepoName, zot_reference
from zqp.skopeo import SkopeoError
from zqp.state import StateStore
from zqp.versions import TagSpec, alias_targets, parse_tag, parse_version, tags_to_keep
from zqp.zot_api import ZotApi

logger = logging.getLogger(__name__)


class Retention:
    """Paso 4: mantiene los alias (``latest``, ``1.27``…) y poda versiones que nadie pide."""

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
        """Aplica alias y retención a todos los paquetes pedidos."""
        for (registry, path), references in self._state.refs_by_repo().items():
            repo = RepoName(registry, path)
            specs = [parse_tag(reference) for reference in references]
            try:
                self._apply(repo, specs)
            except (SkopeoError, httpx.HTTPError):
                logger.exception("Retención fallida para %s", repo.upstream_image)

    def _apply(self, repo: RepoName, specs: list[TagSpec]) -> None:
        """Actualiza alias y poda las versiones de un paquete.

        Args:
            repo: Repositorio upstream.
            specs: Referencias pedidas para ese repositorio.
        """
        physical = repo.physical(VERIFIED)
        tags = self._zot.list_tags(physical)
        if not tags:
            return

        for alias, target in alias_targets(tags, specs).items():
            self._point_alias(physical, alias, target)

        tags = self._zot.list_tags(physical)
        self._prune(physical, tags, specs)

    def _point_alias(self, physical: str, alias: str, target: str) -> None:
        """Hace que un alias apunte al mismo contenido que su tag concreto.

        No copia nada si el alias ya apunta al digest correcto.

        Args:
            physical: Ruta física del repositorio en ``verified``.
            alias: Nombre del alias, p. ej. ``latest``.
            target: Tag concreto, p. ej. ``2.1.0``.
        """
        wanted = self._zot.get_digest(physical, target)
        if wanted is None or self._zot.get_digest(physical, alias) == wanted:
            return
        skopeo.copy(
            zot_reference(self._registry_host, physical, target),
            zot_reference(self._registry_host, physical, alias),
        )
        logger.info("Alias %s:%s -> %s", physical, alias, target)

    def _prune(self, physical: str, tags: list[str], specs: list[TagSpec]) -> None:
        """Borra las versiones semver que ninguna referencia pide.

        Nunca borra tags no semver (p. ej. ``alpine``) ni digests que sigan
        referenciados por un tag protegido.

        Args:
            physical: Ruta física del repositorio en ``verified``.
            tags: Tags existentes en ese repositorio.
            specs: Referencias pedidas para ese repositorio.
        """
        keep = tags_to_keep(tags, specs)
        protected = keep | {tag for tag in tags if parse_version(tag) is None}
        protected_digests = {self._zot.get_digest(physical, tag) for tag in protected}
        protected_digests.discard(None)

        for tag in tags:
            if tag in protected:
                continue
            digest = self._zot.get_digest(physical, tag)
            if digest is None or digest in protected_digests:
                continue
            self._zot.delete_manifest(physical, digest)
            logger.info("Retirada versión antigua %s:%s", physical, tag)
