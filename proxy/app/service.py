"""Caso de uso del proxy: decide de dónde servir cada imagen y completa la cuarentena en segundo plano."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from zqp import quarantine, skopeo
from zqp.image_ref import QUARANTINE, VERIFIED, ImageRef, RepoName
from zqp.state import StateStore
from zqp.versions import TagKind, TagSpec, parse_tag, pick_tag
from zqp.zot_api import ZotApi

from .config import Settings
from .resolver import ServeSource, ServeTarget

logger = logging.getLogger(__name__)


class UpstreamNotAllowed(Exception):
    """El registro pedido no está en la allowlist."""


class ImageNotFound(Exception):
    """La imagen o el tag no existen ni en Zot ni en el registro upstream."""


def _spawn(callback: Callable[[], None]) -> None:
    """Lanza una tarea en un hilo daemon para no bloquear la petición."""
    threading.Thread(target=callback, daemon=True).start()


class ImageService:
    """Decide de dónde se sirve cada imagen y programa la copia a cuarentena.

    Solo se sirve desde local lo que está en ``verified``. Si no está, la imagen
    se sirve desde el registro upstream y, en segundo plano, se copia a
    ``quarantine`` para el ciclo de escaneo y promoción.
    """

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
        self._registry_host = settings.zot_registry_host
        self._in_flight: set[tuple[str, str, str]] = set()
        self._lock = threading.Lock()

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

    def locate(self, ref: ImageRef) -> ServeTarget:
        """Devuelve de dónde se debe servir una referencia.

        Busca en ``verified``; si no está, resuelve el tag concreto y lo sirve del
        upstream, programando además su copia a ``quarantine`` en segundo plano.

        Args:
            ref: Petición del cliente (repositorio más tag o digest).

        Returns:
            Origen (``verified`` o ``upstream``) y referencia concreta a servir.

        Raises:
            UpstreamNotAllowed: Si el registro no está permitido.
            ImageNotFound: Si ningún tag del upstream cumple la petición.
        """
        self.ensure_allowed(ref.repo)
        if ref.is_digest:
            return self._locate_digest(ref)

        self._state.upsert_ref(ref.repo.registry, ref.repo.path, ref.reference)
        spec = parse_tag(ref.reference)

        verified = pick_tag(spec, self._zot.list_tags(ref.repo.physical(VERIFIED)))
        if verified is not None:
            return ServeTarget(ServeSource.VERIFIED, verified)

        target = self._resolve_upstream(ref.repo, spec)
        self._schedule_quarantine(ref.repo, target)
        return ServeTarget(ServeSource.UPSTREAM, target)

    def _resolve_upstream(self, repo: RepoName, spec: TagSpec) -> str:
        """Resuelve el tag concreto que se pedirá al upstream.

        Args:
            repo: Repositorio upstream.
            spec: Referencia pedida, ya clasificada.

        Returns:
            El tag concreto a servir.

        Raises:
            ImageNotFound: Si ningún tag del upstream cumple la petición.
        """
        if spec.kind is TagKind.EXACT:
            target = spec.reference
        else:
            target = pick_tag(spec, skopeo.list_tags(repo.upstream_image))
        if target is None:
            raise ImageNotFound(f"Ningún tag de {repo.upstream_image} cumple '{spec.reference}'")
        return target

    def _locate_digest(self, ref: ImageRef) -> ServeTarget:
        """Sirve un digest de ``verified`` si está; si no, del upstream.

        Un digest no tiene tag, así que no se copia a cuarentena.
        """
        if self._zot.get_digest(ref.repo.physical(VERIFIED), ref.reference) is not None:
            return ServeTarget(ServeSource.VERIFIED, ref.reference)
        return ServeTarget(ServeSource.UPSTREAM, ref.reference)

    def _schedule_quarantine(self, repo: RepoName, target: str) -> None:
        """Copia a ``quarantine`` en segundo plano si no está ya ni está en curso.

        Args:
            repo: Repositorio upstream.
            target: Tag concreto a copiar.
        """
        if target in self._zot.list_tags(repo.physical(QUARANTINE)):
            return
        key = (repo.registry, repo.path, target)
        with self._lock:
            if key in self._in_flight:
                return
            self._in_flight.add(key)
        logger.info("Programada copia a cuarentena de %s:%s", repo.upstream_image, target)
        _spawn(lambda: self._copy_to_quarantine(repo, target, key))

    def _copy_to_quarantine(self, repo: RepoName, target: str, key: tuple[str, str, str]) -> None:
        """Ejecuta la copia a cuarentena y libera la entrada en curso."""
        try:
            quarantine.copy_to_quarantine(self._registry_host, self._state, repo, target)
        except Exception:
            logger.exception("Copia a cuarentena fallida para %s:%s", repo.upstream_image, target)
        finally:
            with self._lock:
                self._in_flight.discard(key)
