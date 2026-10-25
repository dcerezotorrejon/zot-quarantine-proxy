"""Selección de la copia local que se sirve para una referencia pedida."""

from __future__ import annotations

from dataclasses import dataclass

from zqp.image_ref import QUARANTINE, VERIFIED
from zqp.versions import TagSpec, pick_tag


@dataclass(frozen=True)
class LocalCopy:
    """Dónde está la copia que se sirve: namespace de Zot + tag o digest."""

    namespace: str
    reference: str


def find_local_copy(
    spec: TagSpec, verified_tags: list[str], quarantine_tags: list[str]
) -> LocalCopy | None:
    """Elige la copia local a servir: ``verified`` primero y ``quarantine`` después.

    Args:
        spec: Referencia pedida, ya clasificada.
        verified_tags: Tags existentes en ``verified/``.
        quarantine_tags: Tags existentes en ``quarantine/``.

    Returns:
        La copia elegida, o None si ninguno de los dos namespaces la tiene.
    """
    verified = pick_tag(spec, verified_tags)
    if verified is not None:
        return LocalCopy(VERIFIED, verified)
    quarantined = pick_tag(spec, quarantine_tags)
    if quarantined is not None:
        return LocalCopy(QUARANTINE, quarantined)
    return None
