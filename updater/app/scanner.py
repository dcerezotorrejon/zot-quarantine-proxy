"""Evaluación de CVEs de una imagen en cuarentena usando `zli` contra Zot."""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)

ZLI_TIMEOUT_SECONDS = 120


class ScanVerdict(Enum):
    CLEAN = "clean"  # escaneada y sin CVEs bloqueantes
    VULNERABLE = "vulnerable"  # tiene CVEs con severidad bloqueante
    PENDING = "pending"  # Zot aún no tiene el resultado (escaneo en segundo plano)


@dataclass(frozen=True)
class ScanResult:
    verdict: ScanVerdict
    blocking_ids: tuple[str, ...] = ()


class CveScanner:
    """Consulta los CVEs de una imagen en Zot mediante ``zli`` y da un veredicto."""

    def __init__(self, zli_bin: str, zot_url: str, blocking_severities: frozenset[str]) -> None:
        """Crea el evaluador.

        Args:
            zli_bin: Ruta o nombre del binario ``zli``.
            zot_url: URL de Zot que usará ``zli``.
            blocking_severities: Severidades que bloquean la promoción, p. ej.
                ``{"HIGH", "CRITICAL"}``.
        """
        self._zli_bin = zli_bin
        self._zot_url = zot_url
        self._blocking = blocking_severities

    def evaluate(self, repo_path: str, tag: str) -> ScanResult:
        """Consulta los CVEs de una imagen y decide el veredicto.

        Args:
            repo_path: Ruta física del repositorio en cuarentena.
            tag: Tag a evaluar.

        Returns:
            ``CLEAN`` sin CVEs bloqueantes, ``VULNERABLE`` con los IDs encontrados,
            o ``PENDING`` si Zot aún no tiene el resultado o zli falla.
        """
        command = [
            self._zli_bin,
            "cve",
            "list",
            f"{repo_path}:{tag}",
            "--url",
            self._zot_url,
            "-f",
            "json",
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=ZLI_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.warning("No se pudo ejecutar zli para %s:%s: %s", repo_path, tag, exc)
            return ScanResult(ScanVerdict.PENDING)

        if result.returncode != 0:
            # Zot devuelve error mientras la base de datos o el escaneo no están listos.
            logger.debug("zli sin resultado para %s:%s: %s", repo_path, tag, result.stderr.strip())
            return ScanResult(ScanVerdict.PENDING)

        cves = _parse_cve_list(result.stdout)
        if cves is None:
            return ScanResult(ScanVerdict.PENDING)

        blocking = tuple(
            sorted(
                {
                    str(cve["Id"])
                    for cve in cves
                    if str(cve.get("Severity", "")).upper() in self._blocking and cve.get("Id")
                }
            )
        )
        if blocking:
            return ScanResult(ScanVerdict.VULNERABLE, blocking)
        return ScanResult(ScanVerdict.CLEAN)


def _parse_cve_list(stdout: str) -> list[dict] | None:
    """Lista de CVEs del JSON de `zli cve list -f json`, o None si la salida no es válida.

    `zli` devuelve el texto ``No CVEs found for image`` (en lugar de JSON) cuando
    la imagen no tiene CVEs; en ese caso se interpreta como lista vacía.
    """
    stripped = stdout.strip()
    if not stripped:
        return None
    if stripped == "No CVEs found for image":
        return []
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or "CVEList" not in data:
        return None
    return [cve for cve in (data["CVEList"] or []) if isinstance(cve, dict)]
