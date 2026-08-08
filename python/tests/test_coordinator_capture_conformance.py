"""A6 tier 4 -- the observer's output against A3's two entry points (design §11.4)."""

from __future__ import annotations

import os

from atoms.core.recovery import (
    CommitDecision,
    EffectJournalState,
    JournalState,
    PersistentObservation,
    ScratchObservation,
    TransactionState,
    authorize_recovery_step,
    build_recovery_snapshot,
    classify_recovery,
)
from atoms.core.recovery.model import FileBuildRelation
from atoms.core.recovery.plan import ActionPlan, AuthorizedStep, JointObservation
from atoms.fs.linux import LinuxBackend
from atoms.fs.observe import Observation
from tests.capture_support import approved_replace, digest_of


def _observed(lease, approved, workspace):
    from atoms.coordinator.descriptors import _build_descriptor_table

    with Observation(LinuxBackend()) as observation:
        table = _build_descriptor_table(lease, approved, workspace, observation)
        try:
            (path_entry,) = approved.paths
            scratch = approved.scratch[0]
            live = observation.observe(
                table.fd_for(path_entry.parent_node), path_entry.leaf
            )
            slot = observation.observe(
                table.fd_for(scratch.parent_node), scratch.leaf
            )
        finally:
            table.close()
    return path_entry, scratch, live, slot


def test_a_complete_observation_is_accepted_by_build_recovery_snapshot(leased):
    """Proves the observer satisfies A3's coverage and shape validators without A7."""
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        approved = approved_replace(lease)
        with open_workspace(lease, approved) as workspace:
            path_entry, scratch, live, slot = _observed(lease, approved, workspace)

            snapshot = build_recovery_snapshot(
                compiled=approved.compiled,
                topology=approved.topology,
                transaction_state=TransactionState.PREPARED,
                commit_decision=CommitDecision.UNCOMMITTED,
                rollback_result=None,
                halt_diagnostic=None,
                active=True,
                journals=(EffectJournalState("e1", JournalState.PENDING),),
                persistent_observations=(PersistentObservation(path_entry.path, live),),
                scratch_observations=(
                    ScratchObservation("e1", scratch.role, slot, None),
                ),
            )

    assert snapshot.persistent_observations[0].entry is live


def test_a_scratch_only_observation_authorizes_a_committed_cleanup_step(leased):
    """The committed-cleanup route, driven to an exact verdict.

    Ledger #13's contract for this route is specific: after one coherent complete
    final-surface observation, each authorization observation is a *fresh* one covering
    exactly the named retained scratch slot, with empty persistent and occupancy
    coverage. A PREPARED/UNCOMMITTED snapshot carrying persistent evidence exercises a
    different route entirely, and `assert outcome is not None` accepts a `HaltPlan` --
    which is the failure this arm exists to catch.

    The freshness is load-bearing and is asserted, not assumed: the authorization runs on
    a second `Observation` -- a new token universe -- taken after classification, while
    the tombstone is still on disk. Reusing the snapshot's `retained` would authorize
    against evidence gathered before the plan existed and against an entry that the
    cleanup has no proof is still there.
    """
    from atoms.coordinator.descriptors import _build_descriptor_table
    from atoms.coordinator.prepare import open_workspace
    from atoms.core.recovery.model import ObservedFile
    from atoms.core.recovery.plan import PlanDisposition, RemoveScratch
    from tests.capture_support import AFTER, BEFORE, approved_superseded

    with leased() as lease:
        # DeletePath("a.txt") then CreateFileNoClobber("a.txt"): committed, so e1's
        # tombstone is retained scratch awaiting cleanup while the live path already
        # holds the final surface.
        approved = approved_superseded(lease)
        tombstone, staging = approved.scratch
        with open_workspace(lease, approved) as workspace, Observation(
            LinuxBackend()
        ) as complete:
            table = _build_descriptor_table(lease, approved, workspace, complete)
            root_fd = table.fd_for(tombstone.parent_node)
            try:
                fd = os.open(
                    tombstone.leaf,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o644,
                    dir_fd=root_fd,
                )
                os.write(fd, BEFORE)
                os.close(fd)
                live = complete.observe(root_fd, "a.txt")
                retained = complete.observe(root_fd, tombstone.leaf)
                consumed = complete.observe(root_fd, staging.leaf)
                assert type(live) is ObservedFile
                assert type(retained) is ObservedFile

                snapshot = build_recovery_snapshot(
                    compiled=approved.compiled,
                    topology=approved.topology,
                    transaction_state=TransactionState.COMMITTED,
                    commit_decision=CommitDecision.COMMITTED,
                    rollback_result=None,
                    halt_diagnostic=None,
                    active=True,
                    journals=(
                        EffectJournalState("e1", JournalState.DONE),
                        EffectJournalState("e2", JournalState.DONE),
                    ),
                    persistent_observations=(
                        PersistentObservation("a.txt", live),
                    ),
                    scratch_observations=(
                        ScratchObservation("e1", tombstone.role, retained, None),
                        ScratchObservation("e2", staging.role, consumed, None),
                    ),
                )

                assert live.state.content_hash == digest_of(AFTER)
                plan = classify_recovery(snapshot)
                assert type(plan) is ActionPlan
                assert plan.disposition is PlanDisposition.COMMITTED_CLEANUP

                index, step = next(
                    (i, s)
                    for i, s in enumerate(plan.steps)
                    if type(s) is RemoveScratch
                )
                assert step.effect_id == "e1"

                # The fresh pass. Same held descriptor, same name, new tokens.
                with Observation(LinuxBackend()) as authorization:
                    reobserved = authorization.observe(root_fd, tombstone.leaf)
                assert type(reobserved) is ObservedFile

                # Genuinely a new universe, and genuinely the same entry.
                assert reobserved.identity is not retained.identity
                assert reobserved.state == retained.state

                # Exactly the named slot. Empty persistent and occupancy coverage is
                # the contract, not an omission. This authorizes because
                # `_project_identity_relations` compares pairwise SAME/DIFFERENT
                # among the observation's own tokens, never a raw token against the
                # snapshot's -- which is what makes a fresh pass admissible at all.
                joint = JointObservation(
                    persistent=(),
                    scratch=(
                        ScratchObservation("e1", tombstone.role, reobserved, None),
                    ),
                    parent_occupancy=(),
                )
                outcome = authorize_recovery_step(plan, index, joint)

                assert type(outcome) is AuthorizedStep
            finally:
                os.unlink(tombstone.leaf, dir_fd=root_fd)
                table.close()


def test_a_wrong_mode_staging_directory_is_reported_with_its_actual_mode(leased):
    """§10's wrong-mode staging directory, observed rather than judged.

    A6's obligation is to report the mode and occupancy it actually found; deciding that
    the mode is *wrong* is A3's, and an observer that normalised or corrected it would
    hide the case A7 has to repair.
    """
    from atoms.coordinator.descriptors import _build_descriptor_table
    from atoms.coordinator.prepare import open_workspace
    from atoms.core.recovery.model import ObservedDirectory
    from tests.capture_support import DIRECTORY_POST

    with leased() as lease:
        approved = approved_replace(lease)
        scratch = approved.scratch[0]
        with open_workspace(lease, approved) as workspace, Observation(
            LinuxBackend()
        ) as observation:
            table = _build_descriptor_table(lease, approved, workspace, observation)
            try:
                parent_fd = table.fd_for(scratch.parent_node)
                os.mkdir(scratch.leaf, 0o700, dir_fd=parent_fd)
                entry = observation.observe(
                    parent_fd, scratch.leaf, modeled=frozenset()
                )
            finally:
                os.rmdir(scratch.leaf, dir_fd=parent_fd)
                table.close()

    assert type(entry) is ObservedDirectory
    assert entry.state.mode == 0o700
    assert entry.state.mode != DIRECTORY_POST.mode
    assert entry.has_unmodeled_child is False


def test_the_prefix_relation_comes_from_real_files(leased):
    """§11.3: over actual files and descriptors, not hand-built model values."""
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        approved = approved_replace(lease)
        with open_workspace(lease, approved) as workspace:
            staging_fd = workspace.staging_fd
            for name, payload in (("staged", b"pay"), ("planned", b"payload")):
                fd = os.open(
                    name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=staging_fd
                )
                os.write(fd, payload)
                os.close(fd)
            staged_fd = os.open("staged", os.O_RDONLY, dir_fd=staging_fd)
            planned_fd = os.open("planned", os.O_RDONLY, dir_fd=staging_fd)
            try:
                with Observation(LinuxBackend()) as observation:
                    relation = observation.build_relation(staged_fd, planned_fd)
            finally:
                os.close(staged_fd)
                os.close(planned_fd)
                os.unlink("staged", dir_fd=staging_fd)
                os.unlink("planned", dir_fd=staging_fd)

    assert relation is FileBuildRelation.STRICT_PREFIX
