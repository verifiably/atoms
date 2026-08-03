"""The transaction admission gate (design §6)."""

from __future__ import annotations

import secrets

from atoms.coordinator.lease import Lease
from atoms.core.compiler import CompiledSpec
from atoms.core.errors import PreconditionRefused, ProtocolError
from atoms.fs.approval import ProjectApprovedSpec, ProjectContext, approve_for_project

#: Design §6.3. A constant so a test can drive the loop to exhaustion; an unbounded
#: loop would have no reachable refusal to test.
SCRATCH_ATTEMPTS = 3


def new_txid() -> str:
    """Thirty-two hex characters satisfy A1's `^[A-Za-z0-9_-]{1,64}$`.

    The coordinator owns generation because it is the only layer that can know a
    regeneration is needed; the consumer never holds one.
    """
    return secrets.token_hex(16)


def _require_admitted(lease: Lease, approved: ProjectApprovedSpec) -> None:
    """Ledger #9's enforcement half, in one place.

    Every post-approval transaction-stage entry point opens with this call, and Task 9's
    architecture guard asserts that it is each one's first statement. Stated once rather
    than repeated so a later entry point cannot implement a subtly weaker version.
    """
    if type(approved) is not ProjectApprovedSpec:
        raise ProtocolError(
            f"expected exactly ProjectApprovedSpec, got {type(approved).__name__}; a "
            "raw TransactionSpec, CompiledSpec, or synthetic A3 snapshot carries no "
            "rooted project proof"
        )
    if approved.binding is not lease._binding:
        raise ProtocolError(
            "the proof's binding is not this lease's binding; a proof resolved against "
            "one project volume authorizes nothing on another"
        )


def admit(lease: Lease, compiled: CompiledSpec) -> ProjectApprovedSpec:
    """Design §6.3.

    Approval lives inside the loop because ledger #21 says regeneration voids the
    proof: each attempt produces a wholly fresh one, and names are never substituted
    into an existing proof.
    """
    occupied: tuple[str, ...] = ()
    for _ in range(SCRATCH_ATTEMPTS):
        txid = new_txid()
        if lease._store.read_record(txid) is not None:
            # A detached terminal record owns its txid permanently while leaving no
            # scratch behind, so occupancy would find nothing and the collision would
            # surface much later as a primary-key failure inside the publication
            # COMMIT. Reset `occupied` so the refusal cannot name a previous attempt's
            # leaves as the reason it gave up.
            occupied = ()
            continue
        approved = approve_for_project(compiled, ProjectContext(lease._binding, txid))
        occupied = _occupied_scratch(lease, approved)
        if not occupied:
            return approved
    raise PreconditionRefused(
        f"no usable txid after {SCRATCH_ATTEMPTS} attempts"
        + (f"; scratch occupied at {', '.join(occupied)}" if occupied else "")
    )


def _occupied_scratch(lease: Lease, approved: ProjectApprovedSpec) -> tuple[str, ...]:
    """Task 6 fills this in.

    Returning `()` here means Task 5's tests exercise the durable-record path only,
    which is exactly what they assert.
    """
    _ = (lease, approved)
    return ()
