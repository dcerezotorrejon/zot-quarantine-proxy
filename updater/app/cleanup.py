"""Paso de limpieza del ciclo: borra del estado lo que ya no existe en Zot."""

from __future__ import annotations

import logging

from zqp.image_ref import QUARANTINE, VERIFIED, RepoName, digest_to_tag
from zqp.state import StateStore
from zqp.zot_api import ZotApi

logger = logging.getLogger(__name__)


class StateCleaner:
    """Reconcilia la base de datos con Zot.

    Un paquete sin imágenes en ``verified`` ni ``quarantine`` se borra entero del
    estado. Si el paquete aún tiene imágenes, se quitan las entradas de cuarentena
    cuyo digest ya no está en Zot y las refs que no apuntan a ningún tag existente.
    """

    def __init__(self, state: StateStore, zot: ZotApi) -> None:
        """Crea el paso.

        Args:
            state: Base de datos de estado.
            zot: Cliente de la API de Zot.
        """
        self._state = state
        self._zot = zot

    def run(self) -> None:
        """Elimina del estado los paquetes, cuarentenas y refs que ya no existen en Zot."""
        for (registry, path), references in self._state.refs_by_repo().items():
            repo = RepoName(registry, path)
            verified = self._zot.list_tags(repo.physical(VERIFIED))
            quarantined = self._zot.list_tags(repo.physical(QUARANTINE))
            if not verified and not quarantined:
                self._state.delete_repo(registry, path)
                logger.info(
                    "Paquete %s eliminado de la base de datos: sin imágenes",
                    repo.upstream_image,
                )
                continue
            self._prune_quarantine(repo, quarantined)
            self._prune_refs(repo, references, verified, quarantined)

    def _prune_quarantine(self, repo: RepoName, quarantined_tags: list[str]) -> None:
        """Quita del estado las entradas de cuarentena cuyo digest ya no está en Zot.

        Args:
            repo: Repositorio upstream.
            quarantined_tags: Tags ``q-<hex>`` que Zot tiene en ``quarantine``.
        """
        present = set(quarantined_tags)
        for record in self._state.list_quarantine():
            if (record.registry, record.path) != (repo.registry, repo.path):
                continue
            if digest_to_tag(record.digest) not in present:
                self._state.remove_quarantine(repo.registry, repo.path, record.digest)
                logger.info(
                    "Quitada de la base de datos la cuarentena de %s@%s",
                    repo.upstream_image,
                    record.digest,
                )

    def _prune_refs(
        self,
        repo: RepoName,
        references: list[str],
        verified: list[str],
        quarantined_tags: list[str],
    ) -> None:
        """Quita del estado las refs que no apuntan a ningún tag existente en Zot.

        Args:
            repo: Repositorio upstream.
            references: Tags pedidos por los clientes para ese repositorio.
            verified: Tags que Zot tiene actualmente en ``verified``.
            quarantined_tags: Tags ``q-<hex>`` que Zot tiene en ``quarantine``.
        """
        active = set(verified)
        quarantine_tags = set(quarantined_tags)
        pairs = self._state.quarantine_refs_by_repo().get((repo.registry, repo.path), [])
        for digest, reference in pairs:
            if digest_to_tag(digest) in quarantine_tags:
                active.add(reference)
        for reference in references:
            if reference not in active:
                self._state.delete_ref(repo.registry, repo.path, reference)
                logger.info("Quitada de la base de datos la ref %s:%s", repo.upstream_image, reference)
