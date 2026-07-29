from dataclasses import FrozenInstanceError, replace

import pytest

from atoms.core.compiler import compile_spec
from atoms.core.effects import CreateFileNoClobber, ReplaceFile
from atoms.core.errors import ProtocolError
from atoms.core.fingerprint import ABSENT
from atoms.core.recovery import (
    OBSERVED_ABSENT,
    CommitDecision,
    DiagnosticEntry,
    EffectJournalState,
    EntryIdentity,
    FileBuildRelation,
    HaltDiagnostic,
    HaltReason,
    JournalState,
    ObservedFile,
    OperatorAction,
    PersistentNode,
    PersistentObservation,
    ProjectRoot,
    RecoverySnapshot,
    RecoveryTopology,
    ScratchNode,
    ScratchObservation,
    ScratchRole,
    TopologyDirectory,
    TopologyParent,
    TransactionState,
    build_recovery_snapshot,
)
from atoms.core.spec import build_spec
from tests.recovery_support import compiled_create, create_snapshot, create_topology
from tests.support import DIGEST, F, G


def test_valid_snapshot_is_frozen_and_factory_controlled():
    snapshot = create_snapshot()
    with pytest.raises(FrozenInstanceError):
        snapshot.active = False  # type: ignore[misc]
    with pytest.raises(TypeError, match="build_recovery_snapshot"):
        RecoverySnapshot(
            compiled=snapshot.compiled,
            topology=snapshot.topology,
            transaction_state=snapshot.transaction_state,
            commit_decision=snapshot.commit_decision,
            rollback_result=snapshot.rollback_result,
            halt_diagnostic=snapshot.halt_diagnostic,
            active=snapshot.active,
            journals=snapshot.journals,
            persistent_observations=snapshot.persistent_observations,
            scratch_observations=snapshot.scratch_observations,
        )
    with pytest.raises(TypeError, match="build_recovery_snapshot"):
        replace(snapshot)


@pytest.mark.parametrize("missing", ["journal", "persistent", "scratch"])
def test_snapshot_requires_exact_coverage(missing):
    compiled = compiled_create()
    journals = (EffectJournalState("e1", JournalState.STARTED),)
    persistent = (PersistentObservation("a.txt", OBSERVED_ABSENT),)
    scratch = (
        ScratchObservation(
            "e1",
            ScratchRole.STAGING,
            OBSERVED_ABSENT,
            None,
        ),
    )
    values = {"journal": journals, "persistent": persistent, "scratch": scratch}
    values[missing] = ()
    with pytest.raises(ProtocolError, match=missing):
        build_recovery_snapshot(
            compiled=compiled,
            topology=create_topology(),
            transaction_state=TransactionState.APPLYING,
            commit_decision=CommitDecision.UNCOMMITTED,
            rollback_result=None,
            halt_diagnostic=None,
            active=True,
            journals=values["journal"],
            persistent_observations=values["persistent"],
            scratch_observations=values["scratch"],
        )


def test_started_create_requires_relation_for_present_staging():
    with pytest.raises(ProtocolError, match="file_build_relation"):
        create_snapshot(relation=None)


def test_absent_staging_forbids_relation():
    with pytest.raises(ProtocolError, match="file_build_relation"):
        create_snapshot(staging=OBSERVED_ABSENT, relation=FileBuildRelation.EXACT)


@pytest.mark.parametrize(
    "field",
    [
        "compiled",
        "topology",
        "transaction_state",
        "commit_decision",
        "active",
        "journals",
        "persistent_observations",
        "scratch_observations",
    ],
)
def test_snapshot_refuses_subclass_or_wrong_exact_type(field):
    snapshot = create_snapshot()
    values = {
        "compiled": snapshot.compiled,
        "topology": snapshot.topology,
        "transaction_state": snapshot.transaction_state,
        "commit_decision": snapshot.commit_decision,
        "rollback_result": snapshot.rollback_result,
        "halt_diagnostic": snapshot.halt_diagnostic,
        "active": snapshot.active,
        "journals": snapshot.journals,
        "persistent_observations": snapshot.persistent_observations,
        "scratch_observations": snapshot.scratch_observations,
    }
    values[field] = [] if field.endswith("s") else object()
    with pytest.raises(ProtocolError, match=field):
        build_recovery_snapshot(**values)


def test_snapshot_refuses_duplicate_journal_coverage():
    snapshot = create_snapshot()
    with pytest.raises(ProtocolError, match="journals"):
        build_recovery_snapshot(
            compiled=snapshot.compiled,
            topology=snapshot.topology,
            transaction_state=snapshot.transaction_state,
            commit_decision=snapshot.commit_decision,
            rollback_result=snapshot.rollback_result,
            halt_diagnostic=snapshot.halt_diagnostic,
            active=snapshot.active,
            journals=(*snapshot.journals, snapshot.journals[0]),
            persistent_observations=snapshot.persistent_observations,
            scratch_observations=snapshot.scratch_observations,
        )


def test_snapshot_refuses_observation_subclasses():
    class PersistentObservationSubclass(PersistentObservation):
        pass

    snapshot = create_snapshot()
    with pytest.raises(ProtocolError, match="persistent_observations"):
        build_recovery_snapshot(
            compiled=snapshot.compiled,
            topology=snapshot.topology,
            transaction_state=snapshot.transaction_state,
            commit_decision=snapshot.commit_decision,
            rollback_result=snapshot.rollback_result,
            halt_diagnostic=snapshot.halt_diagnostic,
            active=snapshot.active,
            journals=snapshot.journals,
            persistent_observations=(
                PersistentObservationSubclass("a.txt", OBSERVED_ABSENT),
            ),
            scratch_observations=snapshot.scratch_observations,
        )


def test_snapshot_refuses_cyclic_topology():
    topology = create_topology()
    directory_one = TopologyDirectory(1)
    directory_two = TopologyDirectory(2)
    cyclic = RecoveryTopology(
        parents=(
            *topology.parents,
            TopologyParent(directory_one, directory_two),
            TopologyParent(directory_two, directory_one),
        )
    )
    with pytest.raises(ProtocolError, match="acyclic"):
        build_recovery_snapshot(
            compiled=compiled_create(),
            topology=cyclic,
            transaction_state=TransactionState.APPLYING,
            commit_decision=CommitDecision.UNCOMMITTED,
            rollback_result=None,
            halt_diagnostic=None,
            active=True,
            journals=(EffectJournalState("e1", JournalState.PENDING),),
            persistent_observations=(PersistentObservation("a.txt", OBSERVED_ABSENT),),
            scratch_observations=(
                ScratchObservation("e1", ScratchRole.STAGING, OBSERVED_ABSENT, None),
            ),
        )


def test_snapshot_refuses_unparented_endpoint_with_protocol_error():
    topology = RecoveryTopology(
        parents=(
            TopologyParent(TopologyDirectory(1), PersistentNode("a.txt")),
            TopologyParent(ScratchNode("e1", ScratchRole.STAGING), ProjectRoot()),
        )
    )
    with pytest.raises(ProtocolError, match="topology node is missing a parent"):
        build_recovery_snapshot(
            compiled=compiled_create(),
            topology=topology,
            transaction_state=TransactionState.APPLYING,
            commit_decision=CommitDecision.UNCOMMITTED,
            rollback_result=None,
            halt_diagnostic=None,
            active=True,
            journals=(EffectJournalState("e1", JournalState.PENDING),),
            persistent_observations=(PersistentObservation("a.txt", OBSERVED_ABSENT),),
            scratch_observations=(
                ScratchObservation("e1", ScratchRole.STAGING, OBSERVED_ABSENT, None),
            ),
        )


def test_snapshot_accepts_permuted_persistent_and_scratch_coverage():
    compiled = _compiled_nested_create()
    project = ProjectRoot()
    topology = RecoveryTopology(
        parents=(
            *(TopologyParent(PersistentNode(path), project) for path in ("one/a.txt", "one/b.txt", "two/c.txt")),
            *(TopologyParent(ScratchNode(f"e{index}", ScratchRole.STAGING), project) for index in range(1, 4)),
        )
    )
    persistent = tuple(
        PersistentObservation(path, OBSERVED_ABSENT)
        for path in ("two/c.txt", "one/b.txt", "one/a.txt")
    )
    scratch = tuple(
        ScratchObservation(f"e{index}", ScratchRole.STAGING, OBSERVED_ABSENT, None)
        for index in (3, 2, 1)
    )
    snapshot = build_recovery_snapshot(
        compiled=compiled,
        topology=topology,
        transaction_state=TransactionState.APPLYING,
        commit_decision=CommitDecision.UNCOMMITTED,
        rollback_result=None,
        halt_diagnostic=None,
        active=True,
        journals=tuple(EffectJournalState(f"e{index}", JournalState.PENDING) for index in range(1, 4)),
        persistent_observations=persistent,
        scratch_observations=scratch,
    )
    assert snapshot.persistent_observations == persistent
    assert snapshot.scratch_observations == scratch


@pytest.mark.parametrize(
    ("field", "members"),
    [
        (
            "persistent_observations",
            (
                PersistentObservation("a.txt", OBSERVED_ABSENT),
                PersistentObservation("a.txt", OBSERVED_ABSENT),
            ),
        ),
        (
            "persistent_observations",
            (
                PersistentObservation("a.txt", OBSERVED_ABSENT),
                PersistentObservation("extra.txt", OBSERVED_ABSENT),
            ),
        ),
        (
            "scratch_observations",
            (
                ScratchObservation("e1", ScratchRole.STAGING, OBSERVED_ABSENT, None),
                ScratchObservation("e1", ScratchRole.STAGING, OBSERVED_ABSENT, None),
            ),
        ),
        (
            "scratch_observations",
            (
                ScratchObservation("e1", ScratchRole.STAGING, OBSERVED_ABSENT, None),
                ScratchObservation("extra", ScratchRole.STAGING, OBSERVED_ABSENT, None),
            ),
        ),
    ],
)
def test_snapshot_refuses_duplicate_or_extra_unordered_coverage(field, members):
    snapshot = create_snapshot()
    values = {
        "compiled": snapshot.compiled,
        "topology": snapshot.topology,
        "transaction_state": snapshot.transaction_state,
        "commit_decision": snapshot.commit_decision,
        "rollback_result": snapshot.rollback_result,
        "halt_diagnostic": snapshot.halt_diagnostic,
        "active": snapshot.active,
        "journals": snapshot.journals,
        "persistent_observations": snapshot.persistent_observations,
        "scratch_observations": snapshot.scratch_observations,
    }
    values[field] = members
    with pytest.raises(ProtocolError, match=field):
        build_recovery_snapshot(**values)


def test_snapshot_refuses_wrong_scratch_role():
    snapshot = create_snapshot()
    with pytest.raises(ProtocolError, match="scratch_observations"):
        build_recovery_snapshot(
            compiled=snapshot.compiled,
            topology=snapshot.topology,
            transaction_state=snapshot.transaction_state,
            commit_decision=snapshot.commit_decision,
            rollback_result=snapshot.rollback_result,
            halt_diagnostic=snapshot.halt_diagnostic,
            active=snapshot.active,
            journals=snapshot.journals,
            persistent_observations=snapshot.persistent_observations,
            scratch_observations=(
                ScratchObservation("e1", ScratchRole.TOMBSTONE, OBSERVED_ABSENT, None),
            ),
        )


def test_snapshot_requires_same_parent_for_staging_and_live_nodes():
    project = ProjectRoot()
    other_parent = TopologyDirectory(1)
    topology = RecoveryTopology(
        parents=(
            TopologyParent(other_parent, project),
            TopologyParent(PersistentNode("a.txt"), project),
            TopologyParent(ScratchNode("e1", ScratchRole.STAGING), other_parent),
        )
    )
    with pytest.raises(ProtocolError, match="same resolved parent"):
        build_recovery_snapshot(
            compiled=compiled_create(),
            topology=topology,
            transaction_state=TransactionState.APPLYING,
            commit_decision=CommitDecision.UNCOMMITTED,
            rollback_result=None,
            halt_diagnostic=None,
            active=True,
            journals=(EffectJournalState("e1", JournalState.PENDING),),
            persistent_observations=(PersistentObservation("a.txt", OBSERVED_ABSENT),),
            scratch_observations=(
                ScratchObservation("e1", ScratchRole.STAGING, OBSERVED_ABSENT, None),
            ),
        )


def test_completed_replace_forbids_irrelevant_file_relation():
    spec = build_spec(
        consumer_tag="cnsmr",
        intent_digest=DIGEST,
        initial_surface={"a.txt": F},
        final_surface={"a.txt": G},
        effects=(ReplaceFile("e1", "a.txt", F, G),),
    )
    compiled = compile_spec(spec)
    with pytest.raises(ProtocolError, match="file_build_relation"):
        build_recovery_snapshot(
            compiled=compiled,
            topology=create_topology(),
            transaction_state=TransactionState.APPLYING,
            commit_decision=CommitDecision.UNCOMMITTED,
            rollback_result=None,
            halt_diagnostic=None,
            active=True,
            journals=(EffectJournalState("e1", JournalState.DONE),),
            persistent_observations=(PersistentObservation("a.txt", ObservedFile(G, EntryIdentity())),),
            scratch_observations=(
                ScratchObservation(
                    "e1",
                    ScratchRole.STAGING,
                    ObservedFile(F, EntryIdentity()),
                    FileBuildRelation.EXACT,
                ),
            ),
        )


def test_snapshot_refuses_malformed_nested_halt_diagnostic_member():
    diagnostic = _halt_diagnostic(expected=(object(),))
    with pytest.raises(ProtocolError, match="halt_diagnostic.expected"):
        create_snapshot(state=TransactionState.HALTED, halt_diagnostic=diagnostic)


def test_halt_diagnostic_accepts_an_absent_projected_entry():
    diagnostic = _halt_diagnostic(
        expected=(DiagnosticEntry("expected:a.txt", ABSENT, None, None),),
    )
    snapshot = create_snapshot(state=TransactionState.HALTED, halt_diagnostic=diagnostic)
    assert snapshot.halt_diagnostic is diagnostic


def test_snapshot_refuses_halted_diagnostic_mismatch():
    diagnostic = _halt_diagnostic(commit_decision=CommitDecision.COMMITTED)
    with pytest.raises(ProtocolError, match="halt_diagnostic does not match"):
        create_snapshot(state=TransactionState.HALTED, halt_diagnostic=diagnostic)


def test_snapshot_refuses_absolute_halt_diagnostic_path():
    diagnostic = replace(_halt_diagnostic(), paths=("/outside",))
    with pytest.raises(ProtocolError, match="halt_diagnostic.paths"):
        create_snapshot(state=TransactionState.HALTED, halt_diagnostic=diagnostic)


def test_nested_topology_retains_resolved_parent_nodes():
    compiled = _compiled_nested_create()
    project = ProjectRoot()
    parent_one = TopologyDirectory(1)
    parent_two = TopologyDirectory(2)
    first = PersistentNode("one/a.txt")
    second = PersistentNode("one/b.txt")
    third = PersistentNode("two/c.txt")
    topology = RecoveryTopology(
        parents=(
            TopologyParent(parent_one, project),
            TopologyParent(parent_two, project),
            TopologyParent(first, parent_one),
            TopologyParent(second, parent_one),
            TopologyParent(third, parent_two),
            TopologyParent(ScratchNode("e1", ScratchRole.STAGING), parent_one),
            TopologyParent(ScratchNode("e2", ScratchRole.STAGING), parent_one),
            TopologyParent(ScratchNode("e3", ScratchRole.STAGING), parent_two),
        )
    )
    snapshot = build_recovery_snapshot(
        compiled=compiled,
        topology=topology,
        transaction_state=TransactionState.APPLYING,
        commit_decision=CommitDecision.UNCOMMITTED,
        rollback_result=None,
        halt_diagnostic=None,
        active=True,
        journals=tuple(EffectJournalState(f"e{index}", JournalState.PENDING) for index in range(1, 4)),
        persistent_observations=tuple(
            PersistentObservation(path, OBSERVED_ABSENT)
            for path in ("one/a.txt", "one/b.txt", "two/c.txt")
        ),
        scratch_observations=tuple(
            ScratchObservation(f"e{index}", ScratchRole.STAGING, OBSERVED_ABSENT, None)
            for index in range(1, 4)
        ),
    )
    parents = {edge.node: edge.parent for edge in snapshot.topology.parents}
    assert parents[first] is parent_one
    assert parents[second] is parent_one
    assert parents[third] is parent_two
    assert parents[first] is not project
    assert parents[third] is not project


def test_topology_validation_accepts_a_1100_component_chain():
    compiled = compiled_create()
    project = ProjectRoot()
    directories = tuple(TopologyDirectory(index) for index in range(1100))
    persistent = PersistentNode("a.txt")
    scratch = ScratchNode("e1", ScratchRole.STAGING)
    topology = RecoveryTopology(
        parents=(
            TopologyParent(directories[0], project),
            *(TopologyParent(node, parent) for node, parent in zip(directories[1:], directories)),
            TopologyParent(persistent, directories[-1]),
            TopologyParent(scratch, directories[-1]),
        )
    )
    snapshot = build_recovery_snapshot(
        compiled=compiled,
        topology=topology,
        transaction_state=TransactionState.APPLYING,
        commit_decision=CommitDecision.UNCOMMITTED,
        rollback_result=None,
        halt_diagnostic=None,
        active=True,
        journals=(EffectJournalState("e1", JournalState.PENDING),),
        persistent_observations=(PersistentObservation("a.txt", OBSERVED_ABSENT),),
        scratch_observations=(ScratchObservation("e1", ScratchRole.STAGING, OBSERVED_ABSENT, None),),
    )
    assert snapshot.topology is topology


def _compiled_nested_create():
    spec = build_spec(
        consumer_tag="cnsmr",
        intent_digest=DIGEST,
        initial_surface={"one/a.txt": ABSENT, "one/b.txt": ABSENT, "two/c.txt": ABSENT},
        final_surface={"one/a.txt": F, "one/b.txt": F, "two/c.txt": F},
        effects=(
            CreateFileNoClobber("e1", "one/a.txt", F),
            CreateFileNoClobber("e2", "one/b.txt", F),
            CreateFileNoClobber("e3", "two/c.txt", F),
        ),
    )
    return compile_spec(spec)


def _halt_diagnostic(*, commit_decision=CommitDecision.UNCOMMITTED, expected=()):
    return HaltDiagnostic(
        pre_halt_state=TransactionState.APPLYING,
        commit_decision=commit_decision,
        journals=(EffectJournalState("e1", JournalState.STARTED),),
        projected_transaction_state=TransactionState.APPLYING,
        projected_journals=(EffectJournalState("e1", JournalState.STARTED),),
        effect_id=None,
        paths=(),
        expected=expected,
        observed=(),
        identity_relations=(),
        reason=HaltReason.PLAN_PRECONDITION_CHANGED,
        operator_action=OperatorAction.INSPECT_PRESERVED_EVIDENCE,
    )
