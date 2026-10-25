"""Paso 5 del ciclo: borrar de la base de datos los paquetes sin imágenes en Zot."""

from __future__ import annotations

import logging

from zqp.image_ref import QUARANTINE, VERIFIED, RepoName
from zqp.state import StateStore
from zqp.zot_api import ZotApi

logger = logging.getLogger(__name__)


class StateCleaner:
    """Paso 5: borra de la base de datos los paquetes que ya no tienen imágenes."""

    def __init__(self, state: StateStore, zot: ZotApi) -> None:
        """Crea el paso.

        Args:
            state: Base de datos de estado.
            zot: Cliente de la API de Zot.
        """
        self._state = state
        self._zot = zot

    def run(self) -> None:
        """Elimina del estado los paquetes sin imágenes en ``verified`` ni ``quarantine``."""
        for registry, path in self._state.refs_by_repo():
            repo = RepoName(registry, path)
            if self._has_images(repo):
                continue
            self._state.delete_repo(registry, path)
            logger.info("Paquete %s eliminado de la base de datos: sin imágenes", repo.upstream_image)

    def _has_images(self, repo: RepoName) -> bool:
        """Indica si el repositorio tiene al menos un tag en ``verified`` o ``quarantine``.

        Args:
            repo: Repositorio upstream.

        Returns:
            True si hay alguna imagen en cualquiera de los dos namespaces.
        """
        return bool(
            self._zot.list_tags(repo.physical(VERIFIED))
            or self._zot.list_tags(repo.physical(QUARANTINE))
        )
