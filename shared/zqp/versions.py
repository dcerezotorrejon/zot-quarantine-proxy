"""Interpretación de tags: exactos, parciales (1, 1.27) y flotantes (latest, stable).

Módulo sin I/O: toda la lógica de selección de versiones es pura y testeable.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import Enum

FLOATING_TAGS = frozenset({"latest", "stable"})
_VERSION_PATTERN = re.compile(r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:-(.+))?$")


class TagKind(Enum):
    """Clase de referencia pedida por el cliente."""

    FLOATING = "floating"  # latest, stable: la versión verificada más reciente
    PARTIAL = "partial"  # 1, 1.27: la mayor versión que cumple el prefijo
    EXACT = "exact"  # cualquier otro tag: se trata como literal


@dataclass(frozen=True)
class Version:
    """Versión semver-like. `minor` y `patch` pueden faltar; `suffix` es el texto tras el guion."""

    major: int
    minor: int | None
    patch: int | None
    suffix: str | None

    @property
    def sort_key(self) -> tuple[int, int, int]:
        return (self.major, self.minor or 0, self.patch or 0)


@dataclass(frozen=True)
class TagSpec:
    """Referencia pedida ya clasificada, con su versión si la tiene."""

    reference: str
    kind: TagKind
    version: Version | None = None


def parse_version(tag: str) -> Version | None:
    """Interpreta un tag como versión semver-like.

    Args:
        tag: Tag tal y como aparece en el registro, p. ej. ``1.27.4-alpine``.

    Returns:
        La versión interpretada, o None si el tag no sigue el patrón.
    """
    match = _VERSION_PATTERN.match(tag)
    if match is None:
        return None
    major, minor, patch, suffix = match.groups()
    return Version(int(major), _optional_int(minor), _optional_int(patch), suffix)


def parse_tag(reference: str) -> TagSpec:
    """Clasifica la referencia pedida por el cliente.

    Args:
        reference: Tag pedido, p. ej. ``latest``, ``1.27`` o ``1.27.4``.

    Returns:
        La referencia con su clase (flotante, parcial o exacta).
    """
    if reference in FLOATING_TAGS:
        return TagSpec(reference, TagKind.FLOATING)
    version = parse_version(reference)
    is_partial = (
        version is not None
        and version.suffix is None
        and (version.minor is None or version.patch is None)
    )
    if is_partial:
        return TagSpec(reference, TagKind.PARTIAL, version)
    return TagSpec(reference, TagKind.EXACT, version)


def pick_tag(spec: TagSpec, candidates: Iterable[str]) -> str | None:
    """Elige el tag concreto que satisface una referencia.

    Args:
        spec: Referencia pedida por el cliente, ya clasificada.
        candidates: Tags disponibles en el registro.

    Returns:
        El tag elegido, o None si ninguno cumple la referencia.
    """
    tags = set(candidates)
    if spec.kind is TagKind.EXACT:
        return spec.reference if spec.reference in tags else None
    if spec.kind is TagKind.FLOATING:
        best = _highest(tags, lambda version: version.suffix is None)
        if best is None and spec.reference in tags:
            # Repos sin tags semver: el tag literal (p. ej. "latest") es la única opción.
            return spec.reference
        return best
    return _highest(tags, lambda version: _matches_prefix(version, spec.version))


def tags_to_keep(tags: Iterable[str], specs: Iterable[TagSpec]) -> set[str]:
    """Calcula las versiones que la retención debe conservar.

    Args:
        tags: Tags existentes en el repositorio verificado.
        specs: Referencias pedidas por los clientes.

    Returns:
        Conjunto con un tag por cada referencia que tenga candidato.
    """
    tag_list = list(tags)
    keep: set[str] = set()
    for spec in specs:
        target = pick_tag(spec, tag_list)
        if target is not None:
            keep.add(target)
    return keep


def alias_targets(tags: Iterable[str], specs: Iterable[TagSpec]) -> dict[str, str]:
    """Calcula qué alias deben apuntar a qué tag concreto.

    Solo afecta a referencias flotantes y parciales; las exactas no tienen alias.

    Args:
        tags: Tags existentes en el repositorio verificado.
        specs: Referencias pedidas por los clientes.

    Returns:
        Diccionario ``alias -> tag concreto``, p. ej. ``{"latest": "2.1.0"}``.
    """
    tag_list = list(tags)
    targets: dict[str, str] = {}
    for spec in specs:
        if spec.kind is TagKind.EXACT:
            continue
        target = pick_tag(spec, tag_list)
        if target is not None and target != spec.reference:
            targets[spec.reference] = target
    return targets


def _highest(tags: Iterable[str], accept: Callable[[Version], bool]) -> str | None:
    scored: list[tuple[tuple[int, int, int], str]] = []
    for tag in tags:
        version = parse_version(tag)
        if version is not None and accept(version):
            scored.append((version.sort_key, tag))
    return max(scored)[1] if scored else None


def _matches_prefix(candidate: Version, prefix: Version | None) -> bool:
    if prefix is None or candidate.suffix is not None:
        return False
    if candidate.major != prefix.major:
        return False
    return prefix.minor is None or candidate.minor == prefix.minor


def _optional_int(value: str | None) -> int | None:
    return None if value is None else int(value)
