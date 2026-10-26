"""Punto de entrada del updater: ejecuta el ciclo de mantenimiento cada CHECK_INTERVAL."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from zqp.malware import MalwareScanner
from zqp.state import StateStore
from zqp.zot_api import ZotApi

from .clamav import ClamavUpdater
from .cleanup import StateCleaner
from .config import Settings
from .promoter import Promoter
from .retention import Retention
from .scanner import CveScanner
from .upstream_sync import UpstreamSync

logger = logging.getLogger(__name__)

REGISTRY_WAIT_SECONDS = 300
REGISTRY_POLL_SECONDS = 5

Step = tuple[str, Callable[[], None]]


def build_cycle(settings: Settings, zot: ZotApi, state: StateStore) -> list[Step]:
    """Compone los pasos del ciclo de mantenimiento, en orden.

    Args:
        settings: Configuración del updater.
        zot: Cliente de la API de Zot.
        state: Base de datos de estado.

    Returns:
        Lista de pares ``(nombre, función)`` a ejecutar en cada ciclo.
    """
    host = settings.zot_registry_host
    scanner = CveScanner(settings.zli_bin, settings.zot_url, settings.cve_severities)
    malware = MalwareScanner(host, settings.umoci_bin, settings.clamscan_bin)
    clamav = ClamavUpdater(settings.freshclam_bin)
    promoter = Promoter(state, zot, scanner, malware, host, settings.quarantine_days)
    return [
        ("limpieza de estado", StateCleaner(state, zot).run),
        ("sincronizar upstream", UpstreamSync(state, zot, host).run),
        ("actualizar firmas ClamAV", clamav.run),
        ("promover desde cuarentena", promoter.run),
        ("retención", Retention(state, zot, host).run),
    ]


def run_cycle(steps: list[Step]) -> None:
    """Ejecuta cada paso del ciclo. Un fallo se registra y no detiene los siguientes.

    Args:
        steps: Pasos devueltos por ``build_cycle``.
    """
    for name, step in steps:
        try:
            step()
        except Exception:
            logger.exception("El paso '%s' falló; se continúa con el siguiente", name)


def wait_for_registry(zot: ZotApi) -> None:
    """Espera a que Zot responda antes de empezar el primer ciclo.

    Args:
        zot: Cliente de la API de Zot.

    Raises:
        RuntimeError: Si Zot no responde en ``REGISTRY_WAIT_SECONDS``.
    """
    deadline = time.monotonic() + REGISTRY_WAIT_SECONDS
    while not zot.ping():
        if time.monotonic() > deadline:
            raise RuntimeError("Zot no responde tras esperar al arranque")
        logger.info("Esperando a que Zot esté disponible...")
        time.sleep(REGISTRY_POLL_SECONDS)


def main() -> None:
    """Arranca el updater: espera a Zot y ejecuta el ciclo cada ``CHECK_INTERVAL``."""
    settings = Settings.from_env()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    zot = ZotApi(settings.zot_url)
    state = StateStore(settings.state_dir / "state.sqlite3")
    wait_for_registry(zot)

    steps = build_cycle(settings, zot, state)
    while True:
        logger.info("Iniciando ciclo de mantenimiento")
        run_cycle(steps)
        logger.info("Ciclo terminado; siguiente en %ss", settings.check_interval_seconds)
        time.sleep(settings.check_interval_seconds)


if __name__ == "__main__":
    main()
