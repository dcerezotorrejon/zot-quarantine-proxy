"""Actualización de la base de firmas de ClamAV con freshclam y recarga en clamd."""

from __future__ import annotations

import logging
import subprocess

logger = logging.getLogger(__name__)

FRESHCLAM_TIMEOUT_SECONDS = 600
CLAMDSCAN_TIMEOUT_SECONDS = 60


class ClamavUpdater:
    """Ejecuta ``freshclam`` y recarga la base en el daemon ``clamd``."""

    def __init__(self, freshclam_bin: str, clamdscan_bin: str, clamd_config: str) -> None:
        """Crea el actualizador.

        Args:
            freshclam_bin: Ruta o nombre del binario ``freshclam``.
            clamdscan_bin: Ruta o nombre del binario ``clamdscan``.
            clamd_config: Ruta del fichero de configuración de clamd.
        """
        self._freshclam_bin = freshclam_bin
        self._clamdscan_bin = clamdscan_bin
        self._clamd_config = clamd_config

    def run(self) -> None:
        """Actualiza la base de firmas y la recarga en clamd. Un fallo se registra y no detiene el ciclo."""
        if not self._freshclam():
            return
        logger.info("Base de firmas de ClamAV actualizada")
        self._reload_clamd()

    def _freshclam(self) -> bool:
        """Ejecuta freshclam una vez.

        Returns:
            True si freshclam terminó sin error (haya o no novedades).
        """
        try:
            result = subprocess.run(
                [self._freshclam_bin],
                capture_output=True,
                text=True,
                timeout=FRESHCLAM_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("No se pudo actualizar la base de firmas de ClamAV: %s", exc)
            return False
        if result.returncode != 0:
            logger.warning(
                "freshclam falló (exit %s): %s", result.returncode, result.stderr.strip()
            )
            return False
        return True

    def _reload_clamd(self) -> None:
        """Pide a clamd que recargue la base de firmas actualizada."""
        try:
            result = subprocess.run(
                [self._clamdscan_bin, "--config-file", self._clamd_config, "--reload"],
                capture_output=True,
                text=True,
                timeout=CLAMDSCAN_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("No se pudo recargar la base en clamd: %s", exc)
            return
        if result.returncode != 0:
            logger.warning(
                "clamd no recargó la base (exit %s): %s",
                result.returncode,
                result.stderr.strip(),
            )
            return
        logger.info("Base de firmas recargada en clamd")
