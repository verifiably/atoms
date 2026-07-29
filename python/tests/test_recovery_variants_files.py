import pytest

from atoms.core.errors import ProtocolError
from atoms.core.recovery import (
    OBSERVED_ABSENT,
    HaltReason,
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


@pytest.mark.parametrize(
    ("live_name", "staging_name", "journal", "expected"),
    [
        ("pre", "absent", JournalState.STARTED, "undo_without_mutation"),
        ("pre", "prefix", JournalState.STARTED, "remove_scratch"),
        ("pre", "post", JournalState.STARTED, "remove_scratch"),
        ("post", "pre", JournalState.STARTED, "exchange_back"),
        ("post", "external", JournalState.STARTED, "refused_exchange_back"),
        ("post", "pre", JournalState.DONE, "exchange_back"),
        ("post", "absent", JournalState.DONE, "halt"),
        ("post", "pre", JournalState.UNDO_STARTED, "exchange_back"),
        ("pre", "post", JournalState.UNDO_STARTED, "remove_scratch"),
        ("pre", "absent", JournalState.UNDO_STARTED, "already_undone"),
    ],
)
def test_replace_joint_table(
    replace_case,
    live_name,
    staging_name,
    journal,
    expected,
):
    snapshot, frontiers = replace_case(live_name, staging_name, journal)
    decision = classify_effect(snapshot, 0, frontiers)
    assert decision.kind.value == expected
    if expected == "halt":
        assert decision.steps == ()


@pytest.mark.parametrize(
    ("staging_name", "journal", "expected"),
    [
        ("absent", JournalState.STARTED, "undo_without_mutation"),
        ("prefix", JournalState.STARTED, "remove_scratch"),
        ("same", JournalState.STARTED, "remove_scratch"),
        ("same", JournalState.DONE, "remove_scratch"),
        ("absent", JournalState.DONE, "halt"),
        ("same", JournalState.UNDO_STARTED, "remove_scratch"),
        ("absent", JournalState.UNDO_STARTED, "already_undone"),
        ("external", JournalState.STARTED, "halt"),
    ],
)
def test_noop_replace_has_nonoverlapping_precedence(
    noop_replace_case,
    staging_name,
    journal,
    expected,
):
    snapshot, frontiers = noop_replace_case(staging_name, journal)
    decision = classify_effect(snapshot, 0, frontiers)
    assert decision.kind.value == expected
    if expected == "halt":
        assert decision.steps == ()


@pytest.mark.parametrize(
    ("live_name", "staging_name", "journal", "expected"),
    [
        ("absent", "absent", JournalState.STARTED, "undo_without_mutation"),
        ("absent", "prefix", JournalState.STARTED, "remove_scratch"),
        ("absent", "post", JournalState.STARTED, "remove_scratch"),
        ("post", "absent", JournalState.STARTED, "remove_live_creation"),
        ("post", "post", JournalState.STARTED, "refused_preserve_live"),
        ("external", "post", JournalState.STARTED, "refused_preserve_live"),
        ("external", "absent", JournalState.STARTED, "refused_preserve_live"),
        ("post", "absent", JournalState.DONE, "remove_live_creation"),
        ("post", "absent", JournalState.UNDO_STARTED, "remove_live_creation"),
        ("absent", "post", JournalState.UNDO_STARTED, "remove_scratch"),
        ("absent", "absent", JournalState.UNDO_STARTED, "already_undone"),
    ],
)
def test_create_file_joint_table(
    create_file_case,
    live_name,
    staging_name,
    journal,
    expected,
):
    snapshot, frontiers = create_file_case(live_name, staging_name, journal)
    decision = classify_effect(snapshot, 0, frontiers)
    assert decision.kind.value == expected
    if expected == "halt":
        assert decision.steps == ()


@pytest.mark.parametrize(
    ("live_name", "staging_name"),
    [
        ("pre", "diverged"),
        ("pre", "foreign"),
    ],
)
def test_replace_foreign_or_diverged_staging_halts_without_mutation(
    replace_case,
    live_name,
    staging_name,
):
    snapshot, frontiers = replace_case(
        live_name,
        staging_name,
        JournalState.STARTED,
    )
    decision = classify_effect(snapshot, 0, frontiers)
    assert decision.kind is EffectDecisionKind.HALT
    assert decision.steps == ()


@pytest.mark.parametrize("staging_name", ["diverged", "foreign"])
def test_create_file_foreign_or_diverged_staging_halts_without_mutation(
    create_file_case,
    staging_name,
):
    snapshot, frontiers = create_file_case(
        "absent",
        staging_name,
        JournalState.STARTED,
    )
    decision = classify_effect(snapshot, 0, frontiers)
    assert decision.kind is EffectDecisionKind.HALT
    assert decision.steps == ()


@pytest.mark.parametrize(
    ("live_name", "staging_name", "expected"),
    [
        ("post", "pre", "remove_scratch"),
        ("post", "absent", "no_action"),
        ("post", "external", "halt"),
    ],
)
def test_replace_committed_cleanup_rows(
    replace_case,
    live_name,
    staging_name,
    expected,
):
    snapshot, frontiers = replace_case(
        live_name,
        staging_name,
        JournalState.DONE,
        committed=True,
    )
    assert classify_effect(snapshot, 0, frontiers).kind.value == expected
    assert classify_committed_cleanup(snapshot, 0).kind.value == expected


@pytest.mark.parametrize(
    ("staging_name", "expected"),
    [("same", "remove_scratch"), ("absent", "no_action")],
)
def test_noop_replace_committed_cleanup_rows(
    noop_replace_case,
    staging_name,
    expected,
):
    snapshot, frontiers = noop_replace_case(
        staging_name,
        JournalState.DONE,
        committed=True,
    )
    assert classify_effect(snapshot, 0, frontiers).kind.value == expected
    assert classify_committed_cleanup(snapshot, 0).kind.value == expected


@pytest.mark.parametrize(
    ("staging_name", "expected"),
    [("absent", "no_action"), ("post", "halt")],
)
def test_create_file_committed_cleanup_rows(
    create_file_case,
    staging_name,
    expected,
):
    snapshot, frontiers = create_file_case(
        "post",
        staging_name,
        JournalState.DONE,
        committed=True,
    )
    decision = classify_effect(snapshot, 0, frontiers)
    assert decision.kind.value == expected
    assert classify_committed_cleanup(snapshot, 0).kind.value == expected
    if expected == "halt":
        assert decision.steps == ()


def test_committed_cleanup_replace_uses_superseded_live_surface(
    committed_repeated_replace_snapshot,
):
    source = committed_repeated_replace_snapshot
    decisions = tuple(
        classify_committed_cleanup(source, index)
        for index in range(len(source.compiled.spec.effects))
    )

    assert tuple(decision.kind for decision in decisions) == (
        EffectDecisionKind.REMOVE_SCRATCH,
        EffectDecisionKind.REMOVE_SCRATCH,
    )
    for decision in decisions:
        assert len(decision.steps) == 1
        step = decision.steps[0]
        assert type(step) is RemoveScratch
        assert step.expected_before.persistent == ()
        assert step.result_after.persistent == ()
        assert step.expected_before.parent_occupancy == ()
        assert step.result_after.parent_occupancy == ()


def test_pending_live_drift_is_preserved_and_refused(pending_drift_case):
    snapshot, frontiers = pending_drift_case()
    decision = classify_effect(snapshot, 0, frontiers)
    assert decision.kind is EffectDecisionKind.PRESERVE_EXTERNAL
    assert decision.refused
    assert decision.halt_reason is None
    assert any(type(step) is PreserveExternal for step in decision.steps)


def test_pending_initial_tuple_is_explicit_no_action(pending_clean_case):
    snapshot, frontiers = pending_clean_case()
    decision = classify_effect(snapshot, 0, frontiers)
    assert decision.kind is EffectDecisionKind.NO_ACTION
    assert decision.steps == ()


def test_pending_scratch_survivor_halts(pending_scratch_case):
    snapshot, frontiers = pending_scratch_case()
    decision = classify_effect(snapshot, 0, frontiers)
    assert decision.halt_reason is HaltReason.EFFECT_TUPLE_UNATTRIBUTABLE


def test_undone_requires_initial_surface_and_absent_scratch(undone_drift_case):
    snapshot, frontiers = undone_drift_case()
    assert classify_effect(snapshot, 0, frontiers).kind is EffectDecisionKind.HALT


def test_replace_exchange_steps_keep_complete_joint_identity_evidence(replace_case):
    snapshot, frontiers = replace_case(
        "post",
        "pre",
        JournalState.STARTED,
    )
    decision = classify_effect(snapshot, 0, frontiers)
    transform, remove = decision.steps
    assert type(transform) is TransformEffectTuple
    assert type(remove) is RemoveScratch
    assert transform.expected_before == decision.observed
    assert len(transform.result_after.persistent) == 1
    assert len(transform.result_after.scratch) == 1
    assert transform.result_after.persistent[0].entry is (
        snapshot.scratch_observations[0].entry
    )
    assert transform.result_after.scratch[0].entry is (
        snapshot.persistent_observations[0].entry
    )
    assert remove.expected_before == transform.result_after
    assert remove.result_after.persistent == transform.result_after.persistent
    assert remove.result_after.scratch[0].entry is OBSERVED_ABSENT


@pytest.mark.parametrize(
    ("effect_index", "frontiers_override"),
    [
        (False, None),
        (1, None),
        (0, []),
        (0, ()),
        (0, (object(),)),
    ],
)
def test_classifier_refuses_malformed_public_arguments(
    replace_case,
    effect_index,
    frontiers_override,
):
    snapshot, frontiers = replace_case(
        "pre",
        "absent",
        JournalState.STARTED,
    )
    supplied = frontiers if frontiers_override is None else frontiers_override
    with pytest.raises(ProtocolError):
        classify_effect(snapshot, effect_index, supplied)
