"""Paso de limpieza del ciclo: borra del estado lo que ya no existe en Zot."""

from __future__ import annotations

import logging

from zqp.image_ref import QUARANTINE, VERIFIED, RepoName
from zqp.state import StateStore
from zqp.versions import parse_tag, pick_tag
from zqp.zot_api import ZotApi

logger = logging.getLogger(__name__)


class StateCleaner:
    """Reconcilia la base de datos con Zot.

    Un paquete sin imágenes en ``verified`` ni ``quarantine`` se borra entero del
    estado. Si el paquete aún tiene imágenes, se quitan las entradas de cuarentena
    y las refs cuyo tag ya no existe en Zot.
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
                logger.info("Paquete %s eliminado de la base de datos: sin imágenes", repo.upstream_image)
                continue
            self._prune_quarantine(repo, quarantined)
            self._prune_refs(repo, references, [*verified, *quarantined])

    def _prune_quarantine(self, repo: RepoName, quarantined: list[str]) -> None:
        """Quita del estado las entradas de cuarentena cuyo tag ya no está en Zot.

        Args:
            repo: Repositorio upstream.
            quarantined: Tags que Zot tiene actualmente en ``quarantine``.
        """
        for record in self._state.list_quarantine():
            if (record.registry, record.path) != (repo.registry, repo.path):
                continue
            if record.tag not in quarantined:
                self._state.remove_quarantine(repo.registry, repo.path, record.tag)
                logger.info(
                    "Quitada de la base de datos la cuarentena de %s:%s",
                    repo.upstream_image,
                    record.tag,
                )

    def _prune_refs(self, repo: RepoName, references: list[str], tags: list[str]) -> None:
        """Quita del estado las refs que ya no cumplen ningún tag de Zot.

        Args:
            repo: Repositorio upstream.
            references: Tags pedidos por los clientes para ese repositorio.
            tags: Tags que Zot tiene actualmente en ``verified`` y ``quarantine``.
        """
        for reference in references:
            if pick_tag(parse_tag(reference), tags) is None:
                self._state.delete_ref(repo.registry, repo.path, reference)
                logger.info("Quitada de la base de datos la ref %s:%s", repo.upstream_image, reference)
