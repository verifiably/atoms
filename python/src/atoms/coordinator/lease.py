"""The lease value and the protocol that runs over it (design §5)."""

from __future__ import annotations

from dataclasses import dataclass

from atoms.fs.binding import ProjectBinding
from atoms.store.connection import Store


@dataclass(frozen=True, slots=True)
class Lease:
    """Borrowed resources, not owned ones.

    `_recovery_lease` closes both in reverse acquisition order; a `Lease` that outlives
    its `with` block therefore references spent objects, and every A5a call through it
    refuses. The escape is caught by the layer below rather than by a flag here.
    """

    _binding: ProjectBinding
    _store: Store
