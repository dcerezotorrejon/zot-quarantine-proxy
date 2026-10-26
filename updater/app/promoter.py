"""Paso de promoción del ciclo: promover de cuarentena a verified los digests que cumplen las reglas."""

from __future__ import annotations

import logging
import time

import httpx

from zqp.image_ref import QUARANTINE, RepoName, digest_to_tag
from zqp.malware import MalwareScanner, MalwareVerdict, format_infected
from zqp.quarantine import promote_to_verified, remove_from_quarantine
from zqp.skopeo import SkopeoError
from zqp.state import QuarantineRecord, StateStore
from zqp.zot_api import ZotApi

from .scanner import CveScanner, ScanResult, ScanVerdict

logger = logging.getLogger(__name__)

SECONDS_PER_DAY = 86400
MAX_IDS_IN_LOG = 20


class Promoter:
    """Paso de promoción: al cumplir el plazo, promueve a ``verified`` o elimina según el escaneo."""

    def __init__(
        self,
        state: StateStore,
        zot: ZotApi,
        scanner: CveScanner,
        malware: MalwareScanner,
        registry_host: str,
        quarantine_days: int,
    ) -> None:
        """Crea el paso.

        Args:
            state: Base de datos con las entradas de cuarentena.
            zot: Cliente de la API de Zot.
            scanner: Evaluador de CVEs.
            malware: Evaluador de malware.
            registry_host: Host y puerto de Zot para skopeo.
            quarantine_days: Días mínimos en cuarentena antes de promover.
        """
        self._state = state
        self._zot = zot
        self._scanner = scanner
        self._malware = malware
        self._registry_host = registry_host
        self._min_age_seconds = quarantine_days * SECONDS_PER_DAY

    def run(self) -> None:
        """Evalúa cada digest en cuarentena que ya cumple la edad mínima."""
        now = int(time.time())
        for record in self._state.list_quarantine():
            if now - record.quarantined_at < self._min_age_seconds:
                continue
            try:
                self._evaluate(record)
            except (SkopeoError, httpx.HTTPError):
                logger.exception("Promoción fallida para %s@%s", record.path, record.digest)

    def _evaluate(self, record: QuarantineRecord) -> None:
        """Decide si un digest se promueve, se bloquea o se espera al siguiente ciclo.

        Args:
            record: Digest en cuarentena a evaluar.
        """
        repo = RepoName(record.registry, record.path)
        tag = digest_to_tag(record.digest)
        result = self._scanner.evaluate(repo.physical(QUARANTINE), tag)
        if result.verdict is ScanVerdict.PENDING:
            logger.info("Escaneo pendiente para %s@%s", repo.upstream_image, record.digest)
            return
        if result.verdict is ScanVerdict.VULNERABLE:
            logger.warning(
                "Eliminado %s@%s al terminar la cuarentena por CVEs: %s",
                repo.upstream_image,
                record.digest,
                _format_ids(result),
            )
            remove_from_quarantine(self._zot, self._state, repo, record.digest)
            return
        malware = self._malware.evaluate(repo.physical(QUARANTINE), tag)
        if malware.verdict is MalwareVerdict.PENDING:
            logger.info("Escaneo de malware pendiente para %s@%s", repo.upstream_image, record.digest)
            return
        if malware.verdict is MalwareVerdict.INFECTED:
            logger.warning(
                "Eliminado %s@%s al terminar la cuarentena por malware: %s",
                repo.upstream_image,
                record.digest,
                format_infected(malware.infected_files),
            )
            remove_from_quarantine(self._zot, self._state, repo, record.digest)
            return
        self._promote(repo, record.digest)

    def _promote(self, repo: RepoName, digest: str) -> None:
        """Copia un digest a ``verified`` bajo cada referencia que lo pide y lo quita de cuarentena.

        Args:
            repo: Repositorio upstream.
            digest: Digest del manifest a promover.
        """
        promote_to_verified(self._zot, self._registry_host, self._state, repo, digest)
        logger.info("Promovido %s@%s a verified", repo.upstream_image, digest)


def _format_ids(result: ScanResult) -> str:
    shown = ", ".join(result.blocking_ids[:MAX_IDS_IN_LOG])
    extra = len(result.blocking_ids) - MAX_IDS_IN_LOG
    return f"{shown} (+{extra} más)" if extra > 0 else shown
