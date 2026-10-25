"""Paso 3 del ciclo: promover de cuarentena a verified las imágenes que cumplen las reglas."""

from __future__ import annotations

import logging
import time

import httpx

from zqp import skopeo
from zqp.image_ref import QUARANTINE, VERIFIED, RepoName, zot_reference
from zqp.quarantine import remove_from_quarantine
from zqp.skopeo import SkopeoError
from zqp.state import QuarantineRecord, StateStore
from zqp.zot_api import ZotApi

from .scanner import CveScanner, ScanResult, ScanVerdict

logger = logging.getLogger(__name__)

SECONDS_PER_DAY = 86400
MAX_IDS_IN_LOG = 20


class Promoter:
    """Paso 3: al cumplir el plazo, promueve a ``verified`` o elimina según el escaneo."""

    def __init__(
        self,
        state: StateStore,
        zot: ZotApi,
        scanner: CveScanner,
        registry_host: str,
        quarantine_days: int,
    ) -> None:
        """Crea el paso.

        Args:
            state: Base de datos con las entradas de cuarentena.
            zot: Cliente de la API de Zot.
            scanner: Evaluador de CVEs.
            registry_host: Host y puerto de Zot para skopeo.
            quarantine_days: Días mínimos en cuarentena antes de promover.
        """
        self._state = state
        self._zot = zot
        self._scanner = scanner
        self._registry_host = registry_host
        self._min_age_seconds = quarantine_days * SECONDS_PER_DAY

    def run(self) -> None:
        """Evalúa cada imagen en cuarentena que ya cumple la edad mínima."""
        now = int(time.time())
        for record in self._state.list_quarantine():
            if now - record.quarantined_at < self._min_age_seconds:
                continue
            try:
                self._evaluate(record)
            except (SkopeoError, httpx.HTTPError):
                logger.exception("Promoción fallida para %s:%s", record.path, record.tag)

    def _evaluate(self, record: QuarantineRecord) -> None:
        """Decide si una imagen se promueve, se bloquea o se espera al siguiente ciclo.

        Args:
            record: Imagen en cuarentena a evaluar.
        """
        repo = RepoName(record.registry, record.path)
        result = self._scanner.evaluate(repo.physical(QUARANTINE), record.tag)
        if result.verdict is ScanVerdict.PENDING:
            logger.info("Escaneo pendiente para %s:%s", repo.upstream_image, record.tag)
            return
        if result.verdict is ScanVerdict.VULNERABLE:
            logger.warning(
                "Eliminada %s:%s al terminar la cuarentena por CVEs: %s",
                repo.upstream_image,
                record.tag,
                _format_ids(result),
            )
            remove_from_quarantine(self._zot, self._state, repo, record.tag)
            return
        self._promote(repo, record.tag)

    def _promote(self, repo: RepoName, tag: str) -> None:
        """Copia una imagen a ``verified`` y la quita de la cuarentena.

        Args:
            repo: Repositorio upstream.
            tag: Tag a promover.
        """
        skopeo.copy(
            zot_reference(self._registry_host, repo.physical(QUARANTINE), tag),
            zot_reference(self._registry_host, repo.physical(VERIFIED), tag),
        )
        remove_from_quarantine(self._zot, self._state, repo, tag)
        logger.info("Promovida %s:%s a verified", repo.upstream_image, tag)


def _format_ids(result: ScanResult) -> str:
    shown = ", ".join(result.blocking_ids[:MAX_IDS_IN_LOG])
    extra = len(result.blocking_ids) - MAX_IDS_IN_LOG
    return f"{shown} (+{extra} más)" if extra > 0 else shown
