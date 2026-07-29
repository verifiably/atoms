import pytest

from atoms.core.recovery import (
    OBSERVED_ABSENT,
    HaltReason,
    IdentityRelation,
    JournalState,
    PreserveExternal,
    RemoveScratch,
    TransformEffectTuple,
)
from atoms.core.recovery.variants import (
    EffectDecisionKind,
    classify_committed_cleanup,
    classify_effect,
)
from tests.recovery_support import make_directory_descendant_case


@pytest.mark.parametrize("symlink", [False, True], ids=["file", "symlink"])
@pytest.mark.parametrize(
    ("live_name", "tombstone_name", "journal", "expected"),
    [
        ("pre", "absent", JournalState.STARTED, "undo_without_mutation"),
        ("absent", "pre", JournalState.STARTED, "restore_tombstone"),
        ("external", "pre", JournalState.STARTED, "halt"),
        ("absent", "pre", JournalState.DONE, "restore_tombstone"),
        ("absent", "absent", JournalState.DONE, "halt"),
        ("absent", "pre", JournalState.UNDO_STARTED, "restore_tombstone"),
        ("pre", "absent", JournalState.UNDO_STARTED, "already_undone"),
        ("absent", "absent", JournalState.UNDO_STARTED, "halt"),
    ],
)
def test_delete_joint_table(
    delete_case,
    live_name,
    tombstone_name,
    journal,
    expected,
    symlink,
):
    snapshot, frontiers = delete_case(
        live_name,
        tombstone_name,
        journal,
        symlink=symlink,
    )
    decision = classify_effect(snapshot, 0, frontiers)
    assert decision.kind.value == expected
    if expected == "halt":
        assert decision.steps == ()


def test_delete_symlink_restore_carries_no_identity_relation(delete_case):
    snapshot, frontiers = delete_case(
        "absent",
        "pre",
        JournalState.STARTED,
        symlink=True,
    )
    decision = classify_effect(snapshot, 0, frontiers)
    (transform,) = decision.steps
    assert type(transform) is TransformEffectTuple
    assert transform.identity_relations == ()


@pytest.mark.parametrize(
    ("source", "destination", "anchor", "relation", "expected"),
    [
        ("pre", "absent", "absent", None, "undo_without_mutation"),
        ("pre", "absent", "pre", "source_anchor_same", "remove_anchor"),
        (
            "absent",
            "pre",
            "pre",
            "destination_anchor_same",
            "restore_source",
        ),
        ("pre", "pre", "pre", "all_same", "remove_destination"),
        ("absent", "absent", "pre", None, "restore_from_anchor"),
        (
            "pre",
            "external",
            "pre",
            "source_anchor_same",
            "refused_preserve_destination",
        ),
    ],
)
def test_move_joint_table(
    move_case,
    source,
    destination,
    anchor,
    relation,
    expected,
):
    snapshot, frontiers = move_case(source, destination, anchor, relation)
    assert classify_effect(snapshot, 0, frontiers).kind.value == expected


@pytest.mark.parametrize(
    ("source", "destination", "anchor", "relation", "journal"),
    [
        ("external", "absent", "pre", None, JournalState.STARTED),
        (
            "absent",
            "pre",
            "pre",
            "different",
            JournalState.STARTED,
        ),
        ("absent", "pre", "absent", None, JournalState.DONE),
        ("absent", "pre", "absent", None, JournalState.STARTED),
    ],
)
def test_move_unattributable_rows_halt_without_mutation(
    move_case,
    source,
    destination,
    anchor,
    relation,
    journal,
):
    snapshot, frontiers = move_case(
        source,
        destination,
        anchor,
        relation,
        journal,
    )
    decision = classify_effect(snapshot, 0, frontiers)
    assert decision.kind is EffectDecisionKind.HALT
    assert decision.steps == ()


def test_move_done_rollback_is_distinct_from_undo_started(move_case):
    done, done_frontiers = move_case(
        "absent",
        "pre",
        "pre",
        "destination_anchor_same",
        JournalState.DONE,
    )
    undo, undo_frontiers = move_case(
        "pre",
        "absent",
        "absent",
        None,
        JournalState.UNDO_STARTED,
    )
    assert classify_effect(done, 0, done_frontiers).kind.value == "restore_source"
    assert classify_effect(undo, 0, undo_frontiers).kind.value == "already_undone"


def test_move_repairs_retain_complete_joint_evidence(move_case):
    snapshot, frontiers = move_case(
        "absent",
        "pre",
        "pre",
        "destination_anchor_same",
    )
    decision = classify_effect(snapshot, 0, frontiers)
    transform = next(
        step for step in decision.steps if type(step) is TransformEffectTuple
    )
    assert transform.expected_before == decision.observed
    assert len(transform.result_after.persistent) == 2
    assert len(transform.result_after.scratch) == 1
    assert transform.identity_relations[0].left_slot == "destination"
    assert transform.identity_relations[0].right_slot == "anchor"


def test_move_anchor_only_repair_converges_before_anchor_removal(move_case):
    snapshot, frontiers = move_case(
        "absent",
        "absent",
        "pre",
        None,
    )
    decision = classify_effect(snapshot, 0, frontiers)
    transform, remove = decision.steps
    assert type(transform) is TransformEffectTuple
    assert type(remove) is RemoveScratch
    source, destination = transform.result_after.persistent
    anchor = transform.result_after.scratch[0]
    assert source.entry is snapshot.scratch_observations[0].entry
    assert destination.entry is OBSERVED_ABSENT
    assert anchor.entry is snapshot.scratch_observations[0].entry
    assert transform.identity_relations[0].relation is IdentityRelation.SAME
    assert remove.expected_before == transform.result_after
    assert remove.result_after.scratch[0].entry is OBSERVED_ABSENT


@pytest.mark.parametrize(
    ("live", "work", "relation", "unmodeled", "expected"),
    [
        ("absent", "absent", None, False, "undo_without_mutation"),
        ("absent", "post", None, False, "remove_work"),
        ("post", "absent", None, False, "remove_live_directory"),
        ("post", "post", "same", False, "remove_dual_name_directory"),
        ("external", "post", "different", False, "refused_preserve_live"),
        ("post", "absent", None, True, "halt"),
    ],
)
def test_create_directory_joint_table(
    directory_case,
    live,
    work,
    relation,
    unmodeled,
    expected,
):
    snapshot, frontiers = directory_case(live, work, relation, unmodeled)
    decision = classify_effect(snapshot, 0, frontiers)
    assert decision.kind.value == expected
    if expected == "halt":
        assert decision.steps == ()
        assert decision.halt_reason is HaltReason.DIRECTORY_NOT_EMPTY


def test_directory_parent_waits_for_resolved_descendant_reversal():
    before, before_frontiers = make_directory_descendant_case(
        descendant_present=True,
    )
    after, after_frontiers = make_directory_descendant_case(
        descendant_present=False,
    )
    before_decision = classify_effect(before, 0, before_frontiers)
    after_decision = classify_effect(after, 0, after_frontiers)
    assert before_decision.kind is EffectDecisionKind.HALT
    assert before_decision.halt_reason is HaltReason.DIRECTORY_NOT_EMPTY
    assert after_decision.kind is EffectDecisionKind.REMOVE_LIVE_DIRECTORY


def test_directory_done_and_undo_started_have_distinct_endpoints(directory_case):
    done, done_frontiers = directory_case(
        "post",
        "absent",
        None,
        False,
        JournalState.DONE,
    )
    undo, undo_frontiers = directory_case(
        "absent",
        "absent",
        None,
        False,
        JournalState.UNDO_STARTED,
    )
    assert (
        classify_effect(done, 0, done_frontiers).kind
        is EffectDecisionKind.REMOVE_LIVE_DIRECTORY
    )
    assert (
        classify_effect(undo, 0, undo_frontiers).kind
        is EffectDecisionKind.ALREADY_UNDONE
    )


def test_directory_shared_work_name_is_settled_before_live_removal(
    directory_case,
):
    snapshot, frontiers = directory_case(
        "post",
        "post",
        "same",
        False,
    )
    decision = classify_effect(snapshot, 0, frontiers)
    remove_stale, remove_live = decision.steps
    assert type(remove_stale) is RemoveScratch
    assert type(remove_live) is TransformEffectTuple
    assert remove_stale.result_after.persistent[0].entry is (
        snapshot.persistent_observations[0].entry
    )
    assert remove_stale.result_after.scratch[0].entry is OBSERVED_ABSENT
    assert remove_live.expected_before == remove_stale.result_after
    assert remove_live.result_after.persistent[0].entry is OBSERVED_ABSENT
    assert remove_live.result_after.scratch[0].entry is OBSERVED_ABSENT
    assert remove_live.identity_relations[0].relation is IdentityRelation.SAME


def test_directory_refusal_preserves_blocker_before_removing_work(directory_case):
    snapshot, frontiers = directory_case(
        "external",
        "post",
        "different",
        False,
    )
    decision = classify_effect(snapshot, 0, frontiers)
    assert decision.refused
    assert type(decision.steps[0]) is PreserveExternal
    assert type(decision.steps[1]) is RemoveScratch


@pytest.mark.parametrize(
    ("tombstone_name", "expected"),
    [
        ("pre", "remove_scratch"),
        ("absent", "no_action"),
        ("external", "halt"),
    ],
)
def test_delete_committed_cleanup_rows(delete_case, tombstone_name, expected):
    snapshot, frontiers = delete_case(
        "absent",
        tombstone_name,
        JournalState.DONE,
        committed=True,
    )
    assert classify_effect(snapshot, 0, frontiers).kind.value == expected
    assert classify_committed_cleanup(snapshot, 0).kind.value == expected


@pytest.mark.parametrize(
    ("anchor", "relation", "expected"),
    [
        ("pre", "destination_anchor_same", "remove_anchor"),
        ("absent", None, "no_action"),
        ("external", None, "halt"),
    ],
)
def test_move_committed_cleanup_rows(move_case, anchor, relation, expected):
    snapshot, frontiers = move_case(
        "absent",
        "pre",
        anchor,
        relation,
        committed=True,
    )
    assert classify_effect(snapshot, 0, frontiers).kind.value == expected
    assert classify_committed_cleanup(snapshot, 0).kind.value == expected


@pytest.mark.parametrize(
    ("work", "relation", "expected"),
    [("absent", None, "no_action"), ("post", "same", "halt")],
)
def test_directory_committed_cleanup_rows(
    directory_case,
    work,
    relation,
    expected,
):
    snapshot, frontiers = directory_case(
        "post",
        work,
        relation,
        False,
        committed=True,
    )
    decision = classify_effect(snapshot, 0, frontiers)
    assert decision.kind.value == expected
    assert classify_committed_cleanup(snapshot, 0).kind.value == expected
    if expected == "halt":
        assert decision.steps == ()


@pytest.mark.parametrize(
    ("case", "effect_index", "expected_kind"),
    [
        ("delete_then_create", 0, EffectDecisionKind.REMOVE_SCRATCH),
        ("move_then_replace", 0, EffectDecisionKind.REMOVE_ANCHOR),
    ],
)
def test_committed_cleanup_path_effects_use_superseded_live_surface(
    committed_superseded_cleanup_case,
    case,
    effect_index,
    expected_kind,
):
    source, _ = committed_superseded_cleanup_case(case)

    decision = classify_committed_cleanup(source, effect_index)

    assert decision.kind is expected_kind
    assert len(decision.steps) == 1
    step = decision.steps[0]
    assert type(step) is RemoveScratch
    assert step.expected_before.persistent == ()
    assert step.result_after.persistent == ()
    assert step.expected_before.parent_occupancy == ()
    assert step.result_after.parent_occupancy == ()
