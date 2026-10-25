"""Configuración del proxy, leída de variables de entorno."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from zqp.env import env_list, env_str, registry_host

DEFAULT_UPSTREAM_REGISTRIES = "docker.io,ghcr.io,quay.io,registry.k8s.io"


@dataclass(frozen=True)
class Settings:
    zot_url: str
    upstream_registries: frozenset[str]
    state_dir: Path
    log_level: str

    @classmethod
    def from_env(cls) -> Settings:
        """Lee la configuración de las variables de entorno.

        Returns:
            Configuración con los valores por defecto cuando falte alguna variable.
        """
        return cls(
            zot_url=env_str("ZOT_URL", "http://zot:5000"),
            upstream_registries=env_list("UPSTREAM_REGISTRIES", DEFAULT_UPSTREAM_REGISTRIES),
            state_dir=Path(env_str("STATE_DIR", "/var/lib/proxy")),
            log_level=env_str("LOG_LEVEL", "INFO").upper(),
        )

    @property
    def zot_registry_host(self) -> str:
        """Host y puerto de Zot, sin esquema (p. ej. ``zot:5000``), para skopeo."""
        return registry_host(self.zot_url)
