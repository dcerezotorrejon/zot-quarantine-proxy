"""Caso de uso del proxy: decide de dónde servir cada imagen y completa la caché."""

from __future__ import annotations

import logging
import time

from zqp import skopeo
from zqp.image_ref import QUARANTINE, VERIFIED, ImageRef, RepoName
from zqp.state import StateStore
from zqp.versions import TagKind, TagSpec, parse_tag, pick_tag
from zqp.zot_api import ZotApi

from .config import Settings
from .resolver import LocalCopy, find_local_copy

logger = logging.getLogger(__name__)


class UpstreamNotAllowed(Exception):
    """El registro pedido no está en la allowlist."""


class ImageNotFound(Exception):
    """La imagen o el tag no existen ni en Zot ni en el registro upstream."""


class ImageService:
    """Decide qué copia local se sirve y completa la caché cuando falta."""

    def __init__(self, settings: Settings, zot: ZotApi, state: StateStore) -> None:
        """Crea el servicio.

        Args:
            settings: Configuración del proxy (allowlist, URL de Zot).
            zot: Cliente de la API de Zot.
            state: Base de datos de estado.
        """
        self._settings = settings
        self._zot = zot
        self._state = state

    def ensure_allowed(self, repo: RepoName) -> None:
        """Comprueba que el registro upstream está en la allowlist.

        Args:
            repo: Repositorio pedido.

        Raises:
            UpstreamNotAllowed: Si el registro no está permitido.
        """
        if repo.registry not in self._settings.upstream_registries:
            raise UpstreamNotAllowed(f"El registro '{repo.registry}' no está permitido")

    def verified_tags(self, repo: RepoName) -> list[str]:
        """Lista los tags verificados de un repositorio.

        Args:
            repo: Repositorio pedido.

        Returns:
            Tags disponibles en ``verified/``.

        Raises:
            UpstreamNotAllowed: Si el registro no está permitido.
        """
        self.ensure_allowed(repo)
        return self._zot.list_tags(repo.physical(VERIFIED))

    def locate(self, ref: ImageRef) -> LocalCopy:
        """Devuelve la copia que se debe servir, pidiéndola a Zot si no está local.

        Registra la petición, busca primero en ``verified`` y después en
        ``quarantine``. Si no hay ninguna copia, delega la descarga en Zot, que la
        sirve en streaming mientras la guarda en cuarentena.

        Args:
            ref: Petición del cliente (repositorio más tag o digest).

        Returns:
            Namespace y referencia de la copia a servir.

        Raises:
            UpstreamNotAllowed: Si el registro no está permitido.
            ImageNotFound: Si ningún tag del upstream cumple la petición.
            SkopeoError: Si no se puede consultar la lista de tags del upstream.
        """
        self.ensure_allowed(ref.repo)
        if ref.is_digest:
            return self._locate_digest(ref)

        self._state.upsert_ref(ref.repo.registry, ref.repo.path, ref.reference)
        spec = parse_tag(ref.reference)

        local = self._find_local(ref.repo, spec)
        if local is not None:
            return local
        return self._delegate_to_zot(ref.repo, spec)

    def _find_local(self, repo: RepoName, spec: TagSpec) -> LocalCopy | None:
        return find_local_copy(
            spec,
            self._zot.list_tags(repo.physical(VERIFIED)),
            self._zot.list_tags(repo.physical(QUARANTINE)),
        )

    def _delegate_to_zot(self, repo: RepoName, spec: TagSpec) -> LocalCopy:
        """Resuelve el tag concreto y deja que Zot descargue la imagen en streaming.

        La descarga empieza cuando el cliente pide el manifest a ``quarantine/``: Zot
        la sincroniza bajo demanda y la sirve mientras la recibe. Aquí solo se
        resuelve la versión y se registra la entrada de cuarentena.

        Args:
            repo: Repositorio upstream.
            spec: Referencia pedida, ya clasificada.

        Returns:
            Copia a servir en ``quarantine/`` con el tag concreto.

        Raises:
            ImageNotFound: Si ningún tag del upstream cumple la petición.
        """
        if spec.kind is TagKind.EXACT:
            target = spec.reference
        else:
            target = pick_tag(spec, skopeo.list_tags(repo.upstream_image))
        if target is None:
            raise ImageNotFound(f"Ningún tag de {repo.upstream_image} cumple '{spec.reference}'")

        self._state.add_quarantine(repo.registry, repo.path, target, int(time.time()))
        logger.info("Pedido %s:%s a Zot para descarga en streaming", repo.upstream_image, target)
        return LocalCopy(QUARANTINE, target)

    def _locate_digest(self, ref: ImageRef) -> LocalCopy:
        """Los digests solo se sirven si ya existen en Zot (no se pueden descargar a ciegas)."""
        for namespace in (VERIFIED, QUARANTINE):
            if self._zot.get_digest(ref.repo.physical(namespace), ref.reference) is not None:
                return LocalCopy(namespace, ref.reference)
        raise ImageNotFound(f"El digest {ref.reference} no está en Zot")
