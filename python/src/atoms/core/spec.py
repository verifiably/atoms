"""The internal TransactionSpec and its canonicalizing builder (design §5.1)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from atoms.core.capabilities import Capability, required_capabilities
from atoms.core.effects import Effect
from atoms.core.fingerprint import PathState

SCHEMA_VERSION = 1


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

    def required_capabilities(self) -> frozenset[Capability]:
        return required_capabilities(self.effects)


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
    )
