"""Configuración del updater, leída de variables de entorno."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from zqp.env import env_duration_seconds, env_int, env_list, env_str, registry_host

DEFAULT_CVE_SEVERITIES = ""


@dataclass(frozen=True)
class Settings:
    zot_url: str
    state_dir: Path
    quarantine_days: int
    cve_severities: frozenset[str]
    check_interval_seconds: int
    zli_bin: str
    umoci_bin: str
    clamscan_bin: str
    freshclam_bin: str
    log_level: str

    @classmethod
    def from_env(cls) -> Settings:
        """Lee la configuración de las variables de entorno.

        Returns:
            Configuración con los valores por defecto cuando falte alguna variable.

        Raises:
            ValueError: Si ``CHECK_INTERVAL`` no tiene formato de duración válido.
        """
        return cls(
            zot_url=env_str("ZOT_URL", "http://zot:5000"),
            state_dir=Path(env_str("STATE_DIR", "/var/lib/proxy")),
            quarantine_days=env_int("QUARANTINE_DAYS", 7),
            cve_severities=frozenset(
                severity.upper()
                for severity in env_list("CVE_SEVERITY_THRESHOLD", DEFAULT_CVE_SEVERITIES)
            ),
            check_interval_seconds=env_duration_seconds("CHECK_INTERVAL", "6h"),
            zli_bin=env_str("ZLI_BIN", "zli"),
            umoci_bin=env_str("UMOCI_BIN", "umoci"),
            clamscan_bin=env_str("CLAMSCAN_BIN", "clamscan"),
            freshclam_bin=env_str("FRESHCLAM_BIN", "freshclam"),
            log_level=env_str("LOG_LEVEL", "INFO").upper(),
        )

    @property
    def zot_registry_host(self) -> str:
        """Host y puerto de Zot, sin esquema (p. ej. ``zot:5000``), para skopeo."""
        return registry_host(self.zot_url)
