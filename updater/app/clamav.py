"""Actualización de la base de firmas de ClamAV con freshclam."""

from __future__ import annotations

import logging
import subprocess

logger = logging.getLogger(__name__)

FRESHCLAM_TIMEOUT_SECONDS = 600


class ClamavUpdater:
    """Ejecuta ``freshclam`` para mantener al día la base de firmas de ClamAV."""

    def __init__(self, freshclam_bin: str) -> None:
        """Crea el actualizador.

        Args:
            freshclam_bin: Ruta o nombre del binario ``freshclam``.
        """
        self._freshclam_bin = freshclam_bin

    def run(self) -> None:
        """Actualiza la base de firmas una vez. Un fallo se registra y no detiene el ciclo."""
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
            return
        if result.returncode != 0:
            logger.warning(
                "freshclam falló (exit %s): %s", result.returncode, result.stderr.strip()
            )
            return
        logger.info("Base de firmas de ClamAV actualizada")
