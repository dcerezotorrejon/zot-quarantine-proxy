"""Paso de sincronización con el upstream: copia a cuarentena los digests pedidos y purga lo retirado."""

from __future__ import annotations

import logging

import httpx

from zqp import skopeo
from zqp.image_ref import VERIFIED, RepoName
from zqp.quarantine import ensure_quarantined, remove_from_quarantine
from zqp.skopeo import SkopeoError
from zqp.state import StateStore
from zqp.zot_api import ZotApi

logger = logging.getLogger(__name__)


class UpstreamSync:
    """Sincroniza con el upstream los digests de las referencias pedidas y purga lo retirado.

    Para cada referencia pedida resuelve el digest actual en el registro público y,
    si no está ya verificado, lo deja en cuarentena. Después quita de cuarentena los
    digests que el upstream ya no publica bajo ninguna referencia.
    """

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
        """Cuarentena el digest actual de cada referencia que aún no esté verificado.

        Args:
            repo: Repositorio upstream.
            references: Tags pedidos por los clientes para ese repositorio.
        """
        upstream_digests = {
            reference: self._resolve_digest(repo, reference) for reference in references
        }

        for reference, digest in upstream_digests.items():
            if digest is None:
                continue
            if self._zot.get_digest(repo.physical(VERIFIED), reference) == digest:
                continue
            ensure_quarantined(self._zot, self._registry_host, self._state, repo, reference, digest)

        self._purge_stale(repo, upstream_digests)

    def _resolve_digest(self, repo: RepoName, reference: str) -> str | None:
        """Resuelve el digest actual de una referencia en el upstream.

        Args:
            repo: Repositorio upstream.
            reference: Tag pedido por el cliente.

        Returns:
            El digest ``sha256:...`` actual, o None si el tag ya no existe en el upstream.
        """
        try:
            return skopeo.inspect_digest(f"{repo.upstream_image}:{reference}")
        except SkopeoError:
            return None

    def _purge_stale(self, repo: RepoName, upstream_digests: dict[str, str | None]) -> None:
        """Quita de cuarentena los digests que el upstream ya no publica.

        Args:
            repo: Repositorio upstream.
            upstream_digests: Digest actual por referencia en el upstream (None si desapareció).
        """
        pairs = self._state.quarantine_refs_by_repo().get((repo.registry, repo.path), [])
        for digest, reference in pairs:
            if upstream_digests.get(reference) == digest:
                continue
            self._state.remove_quarantine_ref(repo.registry, repo.path, digest, reference)
            if not self._state.quarantine_refs(repo.registry, repo.path, digest):
                remove_from_quarantine(self._zot, self._state, repo, digest)
                logger.info(
                    "Purgado %s@%s de cuarentena: ya no lo publica el upstream",
                    repo.upstream_image,
                    digest,
                )
