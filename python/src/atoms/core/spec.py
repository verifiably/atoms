"""The internal TransactionSpec and its canonicalizing builder (design §5.1)."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from atoms.core.capabilities import Capability, required_capabilities
from atoms.core.effects import Effect
from atoms.core.errors import SpecValidationError
from atoms.core.fingerprint import PathState

SCHEMA_VERSION = 2
_FULFILLS_DIGEST = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class SurfaceEntry:
    path: str
    state: PathState


@dataclass(frozen=True, slots=True, order=True)
class Dependency:
    before: str
    after: str


@dataclass(frozen=True, slots=True)
class TransactionSpec:
    schema_version: int
    consumer_tag: str
    intent_digest: str
    initial_surface: tuple[SurfaceEntry, ...]
    final_surface: tuple[SurfaceEntry, ...]
    effects: tuple[Effect, ...]
    dependencies: tuple[Dependency, ...]
    fulfills: str | None = None
    registered_paths: tuple[str, ...] = ()

    def required_capabilities(self) -> frozenset[Capability]:
        return required_capabilities(self.effects)


def validate_v2_members(spec: TransactionSpec) -> None:
    fulfills = spec.fulfills
    if fulfills is not None and type(fulfills) is not str:
        raise SpecValidationError("fulfills must be a string or None")
    if fulfills is not None and _FULFILLS_DIGEST.fullmatch(fulfills) is None:
        raise SpecValidationError("fulfills must match <64 lowercase hex>")

    registered_paths = spec.registered_paths
    if type(registered_paths) is not tuple:
        raise SpecValidationError("registered_paths must be a tuple")
    if any(type(path) is not str for path in registered_paths):
        raise SpecValidationError("registered_paths entries must be strings")
    if registered_paths != tuple(sorted(registered_paths)):
        raise SpecValidationError("registered_paths must be sorted")
    if len(set(registered_paths)) != len(registered_paths):
        raise SpecValidationError("registered_paths must be duplicate-free")

    surface_paths = {
        *(entry.path for entry in spec.initial_surface),
        *(entry.path for entry in spec.final_surface),
    }
    if any(path not in surface_paths for path in registered_paths):
        raise SpecValidationError("registered_paths must be a subset of the surfaces")


def _canonical_surface(surface: Mapping[str, PathState]) -> tuple[SurfaceEntry, ...]:
    return tuple(
        SurfaceEntry(path=path, state=surface[path]) for path in sorted(surface)
    )


def build_spec(
    *,
    consumer_tag: str,
    intent_digest: str,
    initial_surface: Mapping[str, PathState],
    final_surface: Mapping[str, PathState],
    effects: Sequence[Effect],
    dependencies: Iterable[tuple[str, str]] = (),
    fulfills: str | None = None,
    registered_paths: Iterable[str] = (),
) -> TransactionSpec:
    """Construct a spec in canonical form.

    Imposes deterministic ordering only; it does not validate (that is compilation
    validation, design §5.4).
    """
    deps = tuple(sorted(Dependency(before=before, after=after) for before, after in dependencies))
    return TransactionSpec(
        schema_version=SCHEMA_VERSION,
        consumer_tag=consumer_tag,
        intent_digest=intent_digest,
        initial_surface=_canonical_surface(initial_surface),
        final_surface=_canonical_surface(final_surface),
        effects=tuple(effects),
        dependencies=deps,
        fulfills=fulfills,
        registered_paths=tuple(sorted(registered_paths)),
    )
