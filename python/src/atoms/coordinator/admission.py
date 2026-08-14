"""The transaction admission gate (design §6)."""

from __future__ import annotations

import contextlib
import secrets
from collections.abc import Iterator

from atoms.coordinator.lease import Lease
from atoms.core.compiler import CompiledSpec
from atoms.core.errors import (
    CapabilityUnavailable,
    PreconditionRefused,
    ProjectApprovalRefused,
    ProtocolError,
)
from atoms.core.fingerprint import AbsentState
from atoms.core.recovery import PersistentNode, ProjectRoot, TopologyNode, WorkRoot
from atoms.fs.approval import ProjectApprovedSpec, ProjectContext, approve_for_project
from atoms.fs.resolve import ChildObservation, observe_child, observe_work_child
from atoms.fs.topology import (
    ApprovedExistingDirectory,
    ApprovedPath,
    ApprovedPlannedDirectory,
)

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
    backend = approved.binding.backend
    del backend


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


def _parent_paths(approved: ProjectApprovedSpec) -> dict[TopologyNode, str]:
    """Design §6.4's node-to-path table, for the project-space branches only.

    Every scratch leaf shares its target's parent, and that target is itself an
    `ApprovedPath`, so the second assignment is what makes this table total: a parent
    node's path is its child's path minus the child's leaf. `WorkRoot` is deliberately
    absent -- it is metadata space and has its own branch.

    Measured: the parent of `d/f.txt` under an existing `d` is
    `TopologyDirectory(node_id=0)`, which no other rule would name.
    """
    mapping: dict[TopologyNode, str] = {ProjectRoot(): ""}
    for entry in approved.paths:
        mapping[PersistentNode(path=entry.path)] = entry.path
        mapping[entry.parent_node] = entry.path.removesuffix(entry.leaf).rstrip("/")
    return mapping


def _parent_path(mapping: dict[TopologyNode, str], node: object) -> str:
    for candidate, path in mapping.items():
        if candidate == node:
            return path
    raise ProtocolError(f"no parent path for topology node {node!r}")


def _approved_path_for(approved: ProjectApprovedSpec, path: str) -> ApprovedPath:
    for entry in approved.paths:
        if entry.path == path:
            return entry
    raise ProtocolError(
        f"the proof declares no path {path!r}; a planned parent must be declared to "
        "have been approved as planned"
    )


def _require_matches_approval(
    observed: ChildObservation, entry: object, label: str
) -> None:
    """Ledger #19: the proof is the expected baseline, never current authority."""
    if type(entry) is not ApprovedExistingDirectory:
        raise ProtocolError(
            f"parent {label!r} is not approved as an existing directory; the proof "
            f"carries {type(entry).__name__}"
        )
    if observed.parent_identity != entry.identity:
        raise PreconditionRefused(
            f"parent {label!r} changed identity since approval: approved "
            f"{entry.identity}, observed {observed.parent_identity}"
        )
    if observed.parent_constraints != entry.constraints:
        raise PreconditionRefused(
            f"parent {label!r} changed lookup constraints since approval: approved "
            f"{entry.constraints}, observed {observed.parent_constraints}"
        )


def _declared_first_state(approved: ProjectApprovedSpec, path: str) -> object | None:
    """The timeline's first declared state for `path`, or None if it has no timeline.

    `PathTimeline` is `(path, occurrences)` and `TimelineOccurrence` is
    `(effect_id, effect_index, role, pre, post)`, so the first occurrence's `pre` is the
    state the spec says the path is in before anything runs.
    """
    for timeline in approved.compiled.timelines:
        if timeline.path == path:
            return timeline.occurrences[0].pre
    return None


def _require_planned_absent(
    lease: Lease,
    approved: ProjectApprovedSpec,
    mapping: dict[TopologyNode, str],
    directories: dict[TopologyNode, object],
    node: TopologyNode,
) -> None:
    """Design §6.4 branch two.

    An `ApprovedPlannedDirectory` carries constraints but no identity, because the
    directory did not exist when the proof was issued. If it exists now there is
    nothing to compare it against, so this refuses rather than observing it as a
    parent -- UNLESS the timeline itself declares an occupant there (design §8.2): a
    present entry that the proof's own first declared state already accounts for is the
    proof being right, not drift, and capture verifies it coherently against that same
    declared state through a descriptor. It observes the planned directory only as a
    *child* of its own parent, and recurses when that parent is planned too: only the
    outermost planned ancestor has an existing parent whose identity can be checked, and
    an absent ancestor makes everything beneath it absent as well.
    """
    path = _parent_path(mapping, node)
    declared = _approved_path_for(approved, path)
    grandparent = directories.get(declared.parent_node)
    if type(grandparent) is ApprovedPlannedDirectory:
        _require_planned_absent(
            lease, approved, mapping, directories, declared.parent_node
        )
        return

    grandparent_path = _parent_path(mapping, declared.parent_node)
    observed = observe_child(lease._binding, grandparent_path, declared.leaf)
    _require_matches_approval(observed, grandparent, grandparent_path or ".")
    if observed.present:
        first_state = _declared_first_state(approved, path)
        if first_state is not None and type(first_state) is not AbsentState:
            # Design §8.2. The timeline declares an occupant here and A4b already
            # approved that state against disk, so presence is the proof being right.
            # Capture verifies the blocker against this same first declared state,
            # through a descriptor; admission holds only a presence bit and must not
            # second-guess the kind.
            return
        raise PreconditionRefused(
            f"the planned parent directory {path!r} exists now but was absent when the "
            "proof was issued; a planned directory has no approved identity, so its "
            "lookup relation cannot be compared against anything"
        )


def _observe_work_slot(lease: Lease, approved: ProjectApprovedSpec) -> bool:
    """Ledger #19 for metadata space: re-resolve `work/` against `approved.work_base`.

    Returns whether `work/<txid>` is present. Identity or constraint drift raises
    instead: a moved work base is not something a fresh txid would fix, so it must not
    feed the regeneration loop.
    """
    base = approved.work_base
    if base is None:
        raise ProtocolError(
            "the proof carries no approved work base, so the work-root branch has no "
            "baseline to re-resolve against"
        )
    observed = observe_work_child(lease._binding, approved.txid)
    if observed.parent_identity != base.identity:
        raise PreconditionRefused(
            f"metadata_root/work changed identity since approval: approved "
            f"{base.identity}, observed {observed.parent_identity}"
        )
    if observed.parent_constraints != base.constraints:
        raise PreconditionRefused(
            f"metadata_root/work changed lookup constraints since approval: approved "
            f"{base.constraints}, observed {observed.parent_constraints}"
        )
    return observed.present


def _require_work_slot_free(lease: Lease, approved: ProjectApprovedSpec) -> None:
    """The same re-resolution, at a point where the txid is already fixed.

    Preparation cannot regenerate, so an occupied slot is a refusal there rather than a
    reason to try again.
    """
    if _observe_work_slot(lease, approved):
        raise PreconditionRefused(
            f"work/{approved.txid} already exists; this transaction's engine-derived "
            "work directory is occupied by pre-existing state"
        )


def _occupied_scratch(lease: Lease, approved: ProjectApprovedSpec) -> tuple[str, ...]:
    """Design §6.4. The proof is the expected baseline and never current authority."""
    mapping = _parent_paths(approved)
    directories: dict[TopologyNode, object] = {
        entry.node: entry for entry in approved.directories
    }
    occupied: list[str] = []

    with _translated_resolution():
        # Every WORK scratch leaf lives inside work/<txid>, which create_workspace has
        # not made yet, so the slot's own presence is the whole question -- and asking
        # once avoids naming it twice for a spec with two created directories.
        if approved.work_base is not None and _observe_work_slot(lease, approved):
            occupied.append(f"work/{approved.txid}")

        for scratch in approved.scratch:
            if scratch.parent_node == WorkRoot():
                continue

            entry = directories.get(scratch.parent_node)
            if type(entry) is ApprovedPlannedDirectory:
                _require_planned_absent(
                    lease, approved, mapping, directories, scratch.parent_node
                )
                continue

            parent_path = _parent_path(mapping, scratch.parent_node)
            observed = observe_child(lease._binding, parent_path, scratch.leaf)
            _require_matches_approval(observed, entry, parent_path or ".")
            if observed.present:
                occupied.append(f"{parent_path}/{scratch.leaf}".lstrip("/"))

    return tuple(occupied)


@contextlib.contextmanager
def _translated_resolution() -> Iterator[None]:
    """Design §6.4.

    `resolve.py` raises `ProjectApprovalRefused` and `CapabilityUnavailable` -- correct
    at approval time, wrong afterwards. A post-approval divergence is drift, and §9
    requires `PreconditionRefused`. Stated categorically over the refusal types the
    resolver declares, so it cannot drift as `resolve.py` grows, and it never wraps
    `approve_for_project`, whose refusals are genuine. `PreconditionRefused` and
    `ProtocolError` pass through untouched: the first is already the right type, and
    the second names an engine bug that must not be recoloured as external state.
    """
    try:
        yield
    except (ProjectApprovalRefused, CapabilityUnavailable) as exc:
        raise PreconditionRefused(
            f"post-approval drift during re-resolution: {exc}"
        ) from exc
