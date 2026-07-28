"""Repeated-path timelines and the continuity rule (design §5.3)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise

from atoms.core.effects import Effect, RelPath, occurrences
from atoms.core.errors import SpecValidationError
from atoms.core.fingerprint import PathState


@dataclass(frozen=True, slots=True)
class TimelineOccurrence:
    """One effect's transition of one path, with its position in the effect order."""

    effect_id: str
    effect_index: int
    role: str
    pre: PathState
    post: PathState


@dataclass(frozen=True, slots=True)
class PathTimeline:
    """Every occurrence touching one path, in authoritative order."""

    path: RelPath
    occurrences: tuple[TimelineOccurrence, ...]


def build_timelines(effects: Sequence[Effect]) -> tuple[PathTimeline, ...]:
    """Group occurrences by path and require a continuous timeline for each.

    The caller guarantees every element of ``effects`` is one of the five variants;
    ``occurrences`` raises ``TypeError`` otherwise, which the compiler's error contract
    forbids escaping. Compiler phase 1 is that gate.
    """
    grouped: dict[RelPath, list[TimelineOccurrence]] = {}
    for index, effect in enumerate(effects):
        for occurrence in occurrences(effect):
            grouped.setdefault(occurrence.path, []).append(
                TimelineOccurrence(
                    effect_id=effect.effect_id,
                    effect_index=index,
                    role=occurrence.role,
                    pre=occurrence.pre,
                    post=occurrence.post,
                )
            )

    timelines: list[PathTimeline] = []
    for path in sorted(grouped):
        items = tuple(grouped[path])
        for earlier, later in pairwise(items):
            if earlier.post != later.pre:
                raise SpecValidationError(
                    f"path {path!r} has a discontinuous timeline: effect {earlier.effect_id!r} "
                    f"leaves it {earlier.post!r} but effect {later.effect_id!r} declares "
                    f"a precondition of {later.pre!r}"
                )
        timelines.append(PathTimeline(path=path, occurrences=items))
    return tuple(timelines)
