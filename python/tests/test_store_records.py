"""Tier 2 -- the record layer over an in-memory-shaped store (design §11.2)."""

from __future__ import annotations

import json
import sqlite3

import pytest

from atoms.core.canonical import canonical_json
from atoms.core.errors import ProtocolError
from atoms.core.fingerprint import AbsentState, DirectoryState, FileState, SymlinkState
from atoms.core.recovery.model import (
    CommitDecision,
    DiagnosticEntry,
    DiagnosticIdentityRelation,
    EffectJournalState,
    FileBuildRelation,
    HaltDiagnostic,
    HaltReason,
    IdentityRelation,
    JournalState,
    OperatorAction,
    RollbackResult,
    TransactionState,
)
from atoms.store.errors import MetadataStoreInvalid
from atoms.store.records import (
    RULE_BLOB_BYTE_LEN,
    RULE_BLOB_ROW_PRESENT,
    RULE_EFFECT_COVERAGE,
    RULE_EFFECT_VARIANT,
    RULE_HALT_DIAGNOSTIC,
    RULE_ROLLBACK_RESULT,
    RULE_SPEC_COMPILES,
    RULE_SPEC_DECODES,
    coherence_findings,
    decode_diagnostic,
    encode_diagnostic,
)
from tests.store_support import (
    commit_record,
    duplicate_effect_spec,
    every_diagnostic_shape,
    non_compiling_spec,
    one_effect_spec,
    raw_connect,
    replace_spec,
)


@pytest.mark.parametrize("diagnostic", every_diagnostic_shape(), ids=lambda d: d.reason.value)
def test_the_diagnostic_round_trips_exactly(diagnostic):
    assert decode_diagnostic(encode_diagnostic(diagnostic)) == diagnostic


@pytest.mark.parametrize("diagnostic", every_diagnostic_shape(), ids=lambda d: d.reason.value)
def test_the_encoded_form_contains_no_entry_identity(diagnostic):
    """Ledger #12: a snapshot-local identity token must never become durable. A3 keeps
    HaltDiagnostic token-free by construction, which is what makes it storable at all."""
    text = encode_diagnostic(diagnostic)
    assert "identity" not in text or "identity_relations" in text
    assert "entry-identity" not in text
    assert "_token" not in text


def test_every_halt_reason_and_operator_action_encodes():
    """Parametrized over the enums, so a new member fails here rather than at the first
    halt a real recovery produces."""
    for reason in HaltReason:
        for action in OperatorAction:
            diagnostic = HaltDiagnostic(
                pre_halt_state=TransactionState.APPLYING,
                commit_decision=CommitDecision.UNCOMMITTED,
                journals=(),
                projected_transaction_state=TransactionState.HALTED,
                projected_journals=(),
                effect_id=None,
                paths=(),
                expected=(),
                observed=(),
                identity_relations=(),
                reason=reason,
                operator_action=action,
            )
            assert decode_diagnostic(encode_diagnostic(diagnostic)) == diagnostic


@pytest.mark.parametrize(
    "state",
    [
        AbsentState(),
        FileState(content_hash="sha256:" + "a" * 64, mode=0o644, byte_len=3),
        DirectoryState(mode=0o755),
        SymlinkState(target="../elsewhere", mode=0o777),
    ],
)
def test_every_path_state_variant_round_trips_inside_a_diagnostic_entry(state):
    diagnostic = HaltDiagnostic(
        pre_halt_state=TransactionState.APPLYING,
        commit_decision=CommitDecision.UNCOMMITTED,
        journals=(EffectJournalState(effect_id="e1", state=JournalState.STARTED),),
        projected_transaction_state=TransactionState.HALTED,
        projected_journals=(),
        effect_id="e1",
        paths=("a.txt",),
        expected=(
            DiagnosticEntry(
                slot="expected", state=state, has_unmodeled_child=None,
                file_build_relation=None,
            ),
        ),
        observed=(
            DiagnosticEntry(
                slot="observed", state=state, has_unmodeled_child=True,
                file_build_relation=FileBuildRelation.STRICT_PREFIX,
            ),
        ),
        identity_relations=(
            DiagnosticIdentityRelation(
                left_slot="expected", right_slot="observed",
                relation=IdentityRelation.DIFFERENT,
            ),
        ),
        reason=HaltReason.PLAN_PRECONDITION_CHANGED,
        operator_action=OperatorAction.INSPECT_PRESERVED_EVIDENCE,
    )
    assert decode_diagnostic(encode_diagnostic(diagnostic)) == diagnostic


@pytest.mark.parametrize(
    ("mutate", "names"),
    [
        (
            lambda obj: obj.pop("reason"),
            # `_require_keys` is the only check that can catch a missing field, so there is
            # no field-specific path underneath it to mask -- but the assertion still has to
            # prove *reason specifically* went missing, not merely that some key did. The
            # `got` list is the complete, alphabetically sorted set of the other 11 fields
            # with `reason` absent; pinned whole, it cannot be produced by any other case's
            # mutation (verified: absent from all 12 other real messages).
            (
                "got ['commit_decision', 'effect_id', 'expected', 'identity_relations', "
                "'journals', 'observed', 'operator_action', 'paths', 'pre_halt_state', "
                "'projected_journals', 'projected_transaction_state']"
            ),
        ),
        (lambda obj: obj.update({"reason": "no_such_reason"}), "HaltReason"),
        (lambda obj: obj.update({"unexpected": 1}), "unexpected"),
        (lambda obj: obj.update({"pre_halt_state": "not_a_state"}), "TransactionState"),
        (
            lambda obj: obj.update({"journals": [{"effect_id": "e1"}]}),
            "expected keys ['effect_id', 'state'], got ['effect_id']",
        ),
        # Shapes that reached a *raw* exception before the field checks existed.
        # `journals: 5` left as `TypeError: 'int' object is not iterable`; `paths` as a
        # string decoded character by character and refused nothing; `effect_id: 7` was
        # copied straight through into a HaltDiagnostic A3 would later choke on.
        (lambda obj: obj.update({"journals": 5}), "journals must be an array"),
        (lambda obj: obj.update({"paths": "a.txt"}), "paths must be an array"),
        (lambda obj: obj.update({"paths": ["a.txt", 7]}), "paths[1]"),
        (lambda obj: obj.update({"effect_id": 7}), "effect_id must be a string"),
        (lambda obj: obj.update({"expected": {"slot": "a"}}), "expected must be an array"),
        (lambda obj: obj["journals"][0].update({"effect_id": None}), "NoneType"),
        # The two nullable fields: null is a value, but only null. A helper that admits
        # `None` must not thereby admit everything else.
        (lambda obj: obj["expected"][0].update({"has_unmodeled_child": "yes"}), "has_unmodeled_child"),
        (lambda obj: obj.update({"effect_id": False}), "effect_id must be a string, got bool"),
    ],
)
def test_a_malformed_diagnostic_payload_refuses(mutate, names):
    """A missing field, an extra one, an unknown enum member, and a field of the wrong
    primitive type all refuse -- design §9 lists a malformed payload as a
    MetadataStoreInvalid shape, and `halt_diagnostic` is the one column with no CHECK
    behind it, so this decoder is its entire boundary.

    Asserting only `MetadataStoreInvalid` proves *something* refused, not that it refused
    for the stated reason: a regression that let an unrelated, over-eager check fire first
    would still raise the right exception type while masking a broken field-specific path.
    So each case also asserts the field or member name its own refusal must name.
    """
    diagnostic = every_diagnostic_shape()[0]
    obj = json.loads(encode_diagnostic(diagnostic))
    mutate(obj)
    with pytest.raises(MetadataStoreInvalid) as caught:
        decode_diagnostic(json.dumps(obj))
    assert names in str(caught.value)


def test_a_duplicate_key_in_the_payload_refuses():
    diagnostic = every_diagnostic_shape()[0]
    text = encode_diagnostic(diagnostic)
    doubled = text[:-1] + ', "reason": "directory_not_empty"}'
    with pytest.raises(MetadataStoreInvalid):
        decode_diagnostic(doubled)


def test_insert_record_stores_the_canonical_encoding(opened_store, store_binding):
    spec = one_effect_spec()
    with opened_store.transaction() as txn:
        txn.insert_record("tx1", spec)
    raw = raw_connect(store_binding)
    try:
        stored = raw.execute(
            "SELECT spec_json, state, committed FROM transaction_record WHERE txid = ?",
            ("tx1",),
        ).fetchone()
    finally:
        raw.close()
    assert stored == (canonical_json(spec), "prepared", "uncommitted")


def test_insert_record_derives_every_effect_row_from_the_spec(opened_store, store_binding):
    with opened_store.transaction() as txn:
        txn.insert_record("tx1", one_effect_spec(effect_id="only"))
    raw = raw_connect(store_binding)
    try:
        rows = raw.execute(
            "SELECT effect_id, variant, journal_state FROM effect WHERE txid = ?", ("tx1",)
        ).fetchall()
    finally:
        raw.close()
    assert rows == [("only", "create_file_no_clobber", "pending")]


def test_spec_json_is_write_once_at_the_database(opened_store, store_binding):
    with opened_store.transaction() as txn:
        txn.insert_record("tx1", one_effect_spec())
    raw = raw_connect(store_binding)
    try:
        with pytest.raises(Exception) as caught:
            raw.execute("UPDATE transaction_record SET spec_json = '{}' WHERE txid = 'tx1'")
        assert "write-once" in str(caught.value)
    finally:
        raw.close()


def test_a_duplicate_txid_is_refused(opened_store):
    """Narrowed to `sqlite3.IntegrityError` on `transaction_record.txid` specifically
    (review round 1, finding 2): both inserts used to default to `effect_id="e1"`, so
    `effect`'s own composite PK collided too -- measured, the test stayed green even with
    `transaction_record`'s PRIMARY KEY removed entirely. A distinct `effect_id` on the
    second insert leaves the record PK as the only constraint that can fire.
    """
    with opened_store.transaction() as txn:
        txn.insert_record("tx1", one_effect_spec(effect_id="e1"))
    with pytest.raises(sqlite3.IntegrityError) as caught, opened_store.transaction() as txn:
        txn.insert_record("tx1", one_effect_spec(effect_id="e2"))
    assert "transaction_record.txid" in str(caught.value)


def test_a_caught_write_failure_cannot_be_committed(opened_store, store_binding):
    """Design §7.7's poison rule, end to end.

    The duplicate effect_id fails the *second* INSERT INTO effect. By then the record
    row and the first effect row -- written by the same method call -- are already in
    the transaction, and SQLite rolls back only the failing statement: measured,
    `in_transaction` stays true, the earlier rows stay visible, and the COMMIT makes
    them durable. A caller that catches the failure inside the block and carries on must
    not be able to commit that half-written record.
    """
    with pytest.raises(ProtocolError) as caught, opened_store.transaction() as txn:
        # `try`/`except` rather than a nested `pytest.raises`: this is literally the
        # shape under test -- a caller that catches A5a's failure and carries on -- and
        # ruff's SIM117 refuses the nested `with` anyway.
        try:
            txn.insert_record("tx1", duplicate_effect_spec())
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("the duplicate effect_id did not raise")
    assert "poison" in str(caught.value).lower()
    raw = raw_connect(store_binding)
    try:
        assert raw.execute("SELECT count(*) FROM transaction_record").fetchone()[0] == 0
        assert raw.execute("SELECT count(*) FROM effect").fetchone()[0] == 0
    finally:
        raw.close()


def test_a_caught_write_failure_does_not_break_the_next_transaction(opened_store):
    """The poisoned transaction rolls back cleanly, so the store is still usable."""
    with pytest.raises(ProtocolError), opened_store.transaction() as txn:
        try:
            txn.insert_record("tx1", duplicate_effect_spec())
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("the duplicate effect_id did not raise")
    with opened_store.transaction() as txn:
        txn.insert_record("tx1", one_effect_spec())


@pytest.mark.parametrize("bad", [3, None, b"tx", "../escape", "", "x" * 65])
def test_set_transaction_state_validates_the_txid(opened_store, bad):
    """Renamed from `test_every_setter_validates_the_txid` (review round 1, finding 2):
    it only ever called `set_transaction_state`, so the old name overclaimed.

    Bound and message-checked rather than a bare `pytest.raises(ProtocolError)` (review
    round 1, finding 2): `_set_column`'s `rowcount != 1` branch raises `ProtocolError`
    too, for a txid that binds fine as a SQLite parameter and simply matches no row --
    measured, monkeypatching `require_identifier` to the identity function leaves every
    parametrization here green under the un-narrowed assertion. Splitting the expected
    text by whether `bad` is even a `str` -- `require_identifier`'s own two-step order --
    and refusing the rowcount message by name is what ties the pass to the identifier
    gate specifically.
    """
    with pytest.raises(ProtocolError) as caught, opened_store.transaction() as txn:
        txn.set_transaction_state(bad, TransactionState.APPLYING)
    message = str(caught.value)
    if type(bad) is str:
        assert "is not 1-64 characters" in message
    else:
        assert "must be exactly str" in message
    assert "no transaction_record row" not in message


@pytest.mark.parametrize(
    ("method", "bad", "refusal"),
    [
        ("set_transaction_state", "applied", "state must be exactly TransactionState"),
        ("set_transaction_state", CommitDecision.COMMITTED, "state must be exactly TransactionState"),
        ("set_commit_decision", "committed", "decision must be exactly CommitDecision"),
        ("set_commit_decision", TransactionState.COMMITTED, "decision must be exactly CommitDecision"),
        ("set_rollback_result", 0, "result must be exactly RollbackResult"),
        ("set_halt_diagnostic", "{}", "diagnostic must be exactly HaltDiagnostic"),
    ],
)
def test_a_wrong_exact_type_refuses_with_protocol_error(opened_store, method, bad, refusal):
    """§9's table: a wrong exact type is caller misuse, `ProtocolError`, never a raw
    `AttributeError` from inside the store.

    The four one-line setters read `.value` in their argument list, so before
    `require_member` existed `set_transaction_state(txid, "applied")` left as
    `AttributeError: 'str' object has no attribute 'value'` -- a message about the store's
    internals for a mistake the caller made.

    The enum-of-the-wrong-kind pairs are the ones the CHECK constraints cannot catch.
    `CommitDecision.COMMITTED` and `TransactionState.COMMITTED` both carry the value
    `"committed"`, so each passes the *other* column's generated CHECK list: without an
    exact-type gate the write succeeds and the record ends up in a state its author never
    named. A wrong enum whose value happens not to collide would raise `IntegrityError`
    from the CHECK instead -- correct by accident, and only until someone adds a member.
    """
    with pytest.raises(ProtocolError) as caught, opened_store.transaction() as txn:
        getattr(txn, method)("tx1", bad)
    assert refusal in str(caught.value)


@pytest.mark.parametrize(
    ("method", "bad"),
    [
        ("set_transaction_state", "applied"),
        ("set_commit_decision", "committed"),
        ("set_rollback_result", 0),
        ("set_halt_diagnostic", "{}"),
    ],
)
def test_a_wrong_typed_argument_checks_ownership_first_and_cannot_commit(
    opened_store, store_binding, method, bad
):
    """Finding 1 (review round 1): for these four setters, `require_member`/the exact-type
    check has to run *inside* `_mutating()`, not in the argument list evaluated before
    `_set_column` opens it -- otherwise a caught argument failure never poisons because
    `_mutating()`'s `try` was never entered.

    Two properties, on the same four methods: on a spent transaction the ownership
    refusal fires (never the argument refusal); on a live one, the argument refusal
    poisons -- a prior successful write makes the blocked commit observable.
    """
    with opened_store.transaction() as txn:
        pass
    with pytest.raises(ProtocolError) as caught:
        getattr(txn, method)("tx1", bad)
    assert "spent" in str(caught.value).lower()
    assert "exactly" not in str(caught.value)

    with pytest.raises(ProtocolError) as caught, opened_store.transaction() as txn:
        txn.insert_record("tx1", one_effect_spec())
        try:
            getattr(txn, method)("tx1", bad)
        except ProtocolError:
            pass
    assert "poison" in str(caught.value).lower()
    raw = raw_connect(store_binding)
    try:
        assert raw.execute("SELECT count(*) FROM transaction_record").fetchone() == (0,)
    finally:
        raw.close()


def test_setting_a_journal_state_updates_exactly_one_row(opened_store, store_binding):
    with opened_store.transaction() as txn:
        txn.insert_record("tx1", one_effect_spec(effect_id="only"))
        txn.set_journal_state("tx1", "only", JournalState.STARTED)
    raw = raw_connect(store_binding)
    try:
        assert raw.execute(
            "SELECT journal_state FROM effect WHERE txid = 'tx1'"
        ).fetchone() == ("started",)
    finally:
        raw.close()


def test_setting_a_journal_state_for_an_unknown_effect_refuses(opened_store):
    with pytest.raises(ProtocolError) as caught, opened_store.transaction() as txn:
        txn.insert_record("tx1", one_effect_spec(effect_id="only"))
        txn.set_journal_state("tx1", "ghost", JournalState.STARTED)
    assert "ghost" in str(caught.value)


def test_set_active_enforces_the_single_active_row(opened_store, store_binding):
    commit_record(opened_store, store_binding, "tx1", one_effect_spec())
    commit_record(opened_store, store_binding, "tx2", replace_spec())
    with opened_store.transaction() as txn:
        txn.set_active("tx1")
        txn.set_active("tx2")
    raw = raw_connect(store_binding)
    try:
        assert raw.execute("SELECT singleton, txid FROM active").fetchall() == [(0, "tx2")]
    finally:
        raw.close()


def test_set_active_none_clears_the_row(opened_store, store_binding):
    with opened_store.transaction() as txn:
        txn.insert_record("tx1", one_effect_spec())
        txn.set_active("tx1")
        txn.set_active(None)
    raw = raw_connect(store_binding)
    try:
        assert raw.execute("SELECT count(*) FROM active").fetchone() == (0,)
    finally:
        raw.close()


def test_set_active_refuses_a_transaction_that_does_not_exist(opened_store):
    """foreign_keys=ON makes active.txid a real reference, so `active` can never name a
    transaction that does not exist -- the shape authority §7.3 promises recovery will
    never see (design §6.2).

    Narrowed to `sqlite3.IntegrityError` naming `FOREIGN KEY` (review round 1, finding 2):
    a bare `pytest.raises(Exception)` pins that *some* refusal fired, not which one.
    """
    with pytest.raises(sqlite3.IntegrityError) as caught, opened_store.transaction() as txn:
        txn.set_active("never-inserted")
    assert "FOREIGN KEY" in str(caught.value)


@pytest.mark.parametrize("value", [42.5, "abc", b"x"])
def test_strict_typing_refuses_a_value_that_cannot_convert(opened_store, store_binding, value):
    """STRICT coerces losslessly -- '42' and 42.0 both store as integer 42 -- so the test
    uses the values that actually raise (design §6.2)."""
    raw = raw_connect(store_binding)
    try:
        with pytest.raises(sqlite3.IntegrityError) as caught:
            raw.execute(
                "INSERT INTO blob (digest, byte_len) VALUES (?, ?)",
                ("sha256:" + "a" * 64, value),
            )
        assert "blob.byte_len" in str(caught.value)
    finally:
        raw.close()


def test_a_record_round_trips_through_the_store(opened_store, store_binding):
    spec = replace_spec(effect_id="e1")
    commit_record(opened_store, store_binding, "tx1", spec)
    with opened_store.transaction() as txn:
        txn.set_active("tx1")
    record = opened_store.read_record("tx1")
    assert record is not None
    assert (record.txid, record.spec, record.state, record.committed) == (
        "tx1", spec, TransactionState.PREPARED, CommitDecision.UNCOMMITTED
    )
    assert record.journals == (EffectJournalState("e1", JournalState.PENDING),)
    assert opened_store.read_active() == record


def test_reads_of_absent_records_and_active_rows_return_none(opened_store):
    assert opened_store.read_record("nope") is None
    assert opened_store.read_active() is None


def _plant(raw, sql, parameters=()):
    raw.execute(sql, parameters)


def _plant_spec_json(raw, spec_json, effect_rows):
    raw.execute("DELETE FROM effect WHERE txid = 'tx1'")
    raw.execute("DELETE FROM transaction_record WHERE txid = 'tx1'")
    raw.execute(
        "INSERT INTO transaction_record VALUES "
        "('tx1', ?, 'prepared', 'uncommitted', NULL, NULL)",
        (spec_json,),
    )
    for effect_id, variant in effect_rows:
        raw.execute(
            "INSERT INTO effect VALUES ('tx1', ?, ?, 'pending')", (effect_id, variant)
        )


@pytest.mark.parametrize(
    ("rule", "prepare", "corrupt"),
    (
        (RULE_SPEC_DECODES, one_effect_spec, lambda raw: _plant_spec_json(raw, '{"nope": 1}', ())),
        (RULE_SPEC_COMPILES, one_effect_spec, lambda raw: _plant_spec_json(raw, canonical_json(non_compiling_spec()), (("only", "create_file_no_clobber"),))),
        (RULE_EFFECT_COVERAGE, one_effect_spec, lambda raw: _plant(raw, "DELETE FROM effect")),
        (RULE_EFFECT_VARIANT, one_effect_spec, lambda raw: _plant(raw, "UPDATE effect SET variant = 'delete_path'")),
        (RULE_BLOB_ROW_PRESENT, replace_spec, lambda raw: _plant(raw, "DELETE FROM blob")),
        (RULE_BLOB_BYTE_LEN, replace_spec, lambda raw: _plant(raw, "UPDATE blob SET byte_len = 999")),
        (RULE_ROLLBACK_RESULT, one_effect_spec, lambda raw: _plant(raw, "UPDATE transaction_record SET state = 'rolled_back'")),
        (RULE_HALT_DIAGNOSTIC, one_effect_spec, lambda raw: _plant(raw, "UPDATE transaction_record SET state = 'halted'")),
    ),
)
def test_cross_row_corruption_refuses_on_load(opened_store, store_binding, rule, prepare, corrupt):
    commit_record(opened_store, store_binding, "tx1", prepare())
    raw = raw_connect(store_binding)
    try:
        corrupt(raw)
        findings = coherence_findings(raw, "tx1")
    finally:
        raw.close()
    assert len(findings) == 1, findings
    assert findings[0].startswith(f"{rule}: ")
    with pytest.raises(MetadataStoreInvalid) as caught:
        opened_store.read_record("tx1")
    assert rule in str(caught.value)


def test_a_cross_row_violation_is_protocol_error_before_commit(opened_store):
    with pytest.raises(ProtocolError) as caught, opened_store.transaction() as txn:
        txn.insert_record("tx1", one_effect_spec())
        txn.set_transaction_state("tx1", TransactionState.ROLLED_BACK)
    assert RULE_ROLLBACK_RESULT in str(caught.value)
    assert opened_store.read_record("tx1") is None


def test_a_coherent_rollback_sequence_commits(opened_store):
    with opened_store.transaction() as txn:
        txn.insert_record("tx1", one_effect_spec())
    with opened_store.transaction() as txn:
        txn.set_transaction_state("tx1", TransactionState.ROLLED_BACK)
        txn.set_rollback_result("tx1", RollbackResult.RESTORED)
    assert opened_store.read_record("tx1").rollback_result is RollbackResult.RESTORED


def test_two_touched_records_roll_back_together(opened_store):
    with pytest.raises(ProtocolError), opened_store.transaction() as txn:
        txn.insert_record("tx1", one_effect_spec())
        txn.insert_record("tx2", one_effect_spec())
        txn.set_transaction_state("tx2", TransactionState.ROLLED_BACK)
    assert opened_store.read_record("tx1") is None
    assert opened_store.read_record("tx2") is None


def test_a_read_inside_a_write_transaction_is_refused(opened_store):
    with opened_store.transaction() as txn:
        txn.insert_record("tx1", one_effect_spec())
        with pytest.raises(ProtocolError):
            opened_store.read_record("tx1")


def test_a_failed_read_closes_its_transaction(opened_store, store_binding):
    with opened_store.transaction() as txn:
        txn.insert_record("tx1", one_effect_spec())
    raw = raw_connect(store_binding)
    try:
        raw.execute("DELETE FROM effect")
    finally:
        raw.close()
    with pytest.raises(MetadataStoreInvalid):
        opened_store.read_record("tx1")
    with opened_store.transaction():
        pass
