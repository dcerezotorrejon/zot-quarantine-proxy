"""Caso de uso del proxy: decide de dónde servir cada imagen y completa la cuarentena en segundo plano."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from zqp import quarantine, skopeo
from zqp.image_ref import QUARANTINE, VERIFIED, ImageRef, RepoName, digest_to_tag
from zqp.malware import MalwareScanner, MalwareVerdict, format_infected
from zqp.state import StateStore
from zqp.zot_api import ZotApi

from .config import Settings
from .resolver import ServeSource, ServeTarget

logger = logging.getLogger(__name__)


class UpstreamNotAllowed(Exception):
    """El registro pedido no está en la allowlist."""


def _spawn(callback: Callable[[], None]) -> None:
    """Lanza una tarea en un hilo daemon para no bloquear la petición."""
    threading.Thread(target=callback, daemon=True).start()


class ImageService:
    """Decide de dónde se sirve cada imagen y programa la copia a cuarentena.

    Los tags se sirven de forma literal: si el tag está en ``verified`` se sirve
    de ahí; si no, se sirve directamente del registro upstream y, en segundo
    plano, se copia a ``quarantine`` para el ciclo de escaneo y promoción.
    """

    def __init__(
        self, settings: Settings, zot: ZotApi, state: StateStore, malware: MalwareScanner
    ) -> None:
        """Crea el servicio.

        Args:
            settings: Configuración del proxy (allowlist, URL de Zot).
            zot: Cliente de la API de Zot.
            state: Base de datos de estado.
            malware: Escáner de malware para la vía rápida.
        """
        self._settings = settings
        self._zot = zot
        self._state = state
        self._malware = malware
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

        Un digest se sirve de ``verified`` si está; si no, del upstream. Un tag
        se sirve de forma literal: si el tag está en ``verified`` se devuelve de
        ahí; si no, se sirve del upstream y se programa su copia a ``quarantine``
        en segundo plano.

        Args:
            ref: Petición del cliente (repositorio más tag o digest).

        Returns:
            Origen (``verified`` o ``upstream``) y referencia concreta a servir.

        Raises:
            UpstreamNotAllowed: Si el registro no está permitido.
        """
        self.ensure_allowed(ref.repo)
        if ref.is_digest:
            return self._locate_digest(ref)

        self._state.upsert_ref(ref.repo.registry, ref.repo.path, ref.reference)
        if ref.reference in self._zot.list_tags(ref.repo.physical(VERIFIED)):
            return ServeTarget(ServeSource.VERIFIED, ref.reference)

        self._schedule_quarantine(ref.repo, ref.reference)
        return ServeTarget(ServeSource.UPSTREAM, ref.reference)

    def _locate_digest(self, ref: ImageRef) -> ServeTarget:
        """Sirve un digest de ``verified`` si está; si no, del upstream.

        Un digest no tiene tag, así que no se copia a cuarentena.
        """
        if self._zot.get_digest(ref.repo.physical(VERIFIED), ref.reference) is not None:
            return ServeTarget(ServeSource.VERIFIED, ref.reference)
        return ServeTarget(ServeSource.UPSTREAM, ref.reference)

    def _schedule_quarantine(self, repo: RepoName, reference: str) -> None:
        """Copia a ``quarantine`` en segundo plano si no está ya en curso.

        Args:
            repo: Repositorio upstream.
            reference: Tag concreto a copiar.
        """
        key = (repo.registry, repo.path, reference)
        with self._lock:
            if key in self._in_flight:
                return
            self._in_flight.add(key)
        logger.info("Programada copia a cuarentena de %s:%s", repo.upstream_image, reference)
        _spawn(lambda: self._copy_to_quarantine(repo, reference, key))

    def _copy_to_quarantine(self, repo: RepoName, reference: str, key: tuple[str, str, str]) -> None:
        """Inspecciona el tag en el upstream, lo cuarentena y libera la entrada en curso."""
        try:
            digest = skopeo.inspect_digest(f"{repo.upstream_image}:{reference}")
            if self._zot.get_digest(repo.physical(VERIFIED), reference) == digest:
                return
            quarantine.ensure_quarantined(
                self._zot, self._registry_host, self._state, repo, reference, digest
            )
            self._fast_track(repo, digest)
        except Exception:
            logger.exception("Copia a cuarentena fallida para %s:%s", repo.upstream_image, reference)
        finally:
            with self._lock:
                self._in_flight.discard(key)

    def _fast_track(self, repo: RepoName, digest: str) -> None:
        """Escanea con ClamAV y, si está limpio, promueve a ``verified`` al momento.

        Vía rápida para imágenes nuevas: sin CVEs y sin esperar los días de
        cuarentena. Si el escaneo no está listo o detecta malware, se deja al
        updater (que lo reintentará o lo borrará en su ciclo).

        Args:
            repo: Repositorio upstream.
            digest: Digest del manifest a evaluar.
        """
        tag = digest_to_tag(digest)
        result = self._malware.evaluate(repo.physical(QUARANTINE), tag)
        if result.verdict is MalwareVerdict.PENDING:
            logger.info(
                "Escaneo de malware pendiente para %s@%s; lo reintentará el updater",
                repo.upstream_image,
                digest,
            )
            return
        if result.verdict is MalwareVerdict.INFECTED:
            logger.warning(
                "Eliminado %s@%s por malware (vía rápida): %s",
                repo.upstream_image,
                digest,
                format_infected(result.infected_files),
            )
            quarantine.remove_from_quarantine(self._zot, self._state, repo, digest)
            return
        quarantine.promote_to_verified(
            self._zot, self._registry_host, self._state, repo, digest
        )
        logger.info("Promovido %s@%s a verified (vía rápida)", repo.upstream_image, digest)
