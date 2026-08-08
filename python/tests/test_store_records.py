"""Tier 2 -- the record layer over an in-memory-shaped store (design §11.2)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace

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
    COHERENCE_RULES,
    RULE_ACTIVE_RECORD,
    RULE_BLOB_BYTE_LEN,
    RULE_BLOB_ROW_PRESENT,
    RULE_DIAGNOSTIC_DECISION,
    RULE_DIAGNOSTIC_JOURNALS,
    RULE_EFFECT_COVERAGE,
    RULE_EFFECT_VARIANT,
    RULE_HALT_DIAGNOSTIC,
    RULE_ROLLBACK_RESULT,
    RULE_SPEC_CANONICAL,
    RULE_SPEC_COMPILES,
    RULE_SPEC_DECODES,
    SELECT_ACTIVE,
    SELECT_EFFECTS,
    coherence_findings,
    decode_diagnostic,
    encode_diagnostic,
    referenced_digests,
)
from tests.store_support import (
    CorruptsStatement,
    commit_record,
    digest_of,
    duplicate_effect_spec,
    every_diagnostic_shape,
    matching_diagnostic,
    non_compiling_spec,
    one_effect_spec,
    raw_connect,
    replace_spec,
    two_length_spec,
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
    assert rows == [("only", "create_directory", "pending")]


def test_referenced_digests_include_every_declared_file_state():
    spec = replace_spec(before=b"before", after=b"after")
    assert referenced_digests(spec) == tuple(
        sorted(((digest_of(b"before"), 6), (digest_of(b"after"), 5)))
    )


def _state(payload: bytes, byte_len: int | None = None):
    import hashlib

    from atoms.core.fingerprint import FileState

    return FileState(
        content_hash="sha256:" + hashlib.sha256(payload).hexdigest(),
        mode=0o644,
        byte_len=len(payload) if byte_len is None else byte_len,
    )


def test_referenced_digests_include_an_intermediate_postimage():
    """Measured: `ReplaceFile(p, A->B)` + `ReplaceFile(p, B->C)` puts B in neither surface.

    The initial surface has A and the final has C. Before this repair the helper missed B
    entirely, so `connection.py`'s barrier refused a promoted B as unreferenced -- and not
    promoting it would leave A7 without the bytes it must publish.
    """
    from atoms.core.effects import ReplaceFile
    from atoms.core.spec import build_spec

    a, b, c = _state(b"aaa"), _state(b"bbb"), _state(b"ccc")
    spec = build_spec(
        consumer_tag="test",
        intent_digest="sha256:" + "3" * 64,
        initial_surface={"p": a},
        final_surface={"p": c},
        effects=[
            ReplaceFile(effect_id="e1", path="p", pre=a, post=b),
            ReplaceFile(effect_id="e2", path="p", pre=b, post=c),
        ],
    )

    assert {digest for digest, _ in referenced_digests(spec)} == {
        a.content_hash,
        b.content_hash,
        c.content_hash,
    }


def test_referenced_digests_keep_conflicting_lengths_as_distinct_pairs():
    """Measured: `compile_spec` accepts one content_hash at two byte_len values.

    Collapsing to digests would erase the contradiction capture must detect (A6 design
    §7.2), in the one helper positioned to see every declared FileState at once.
    """
    from atoms.core.effects import ReplaceFile
    from atoms.core.spec import build_spec

    honest = _state(b"aaa")
    liar = _state(b"aaa", byte_len=99)
    other = _state(b"zzz")
    spec = build_spec(
        consumer_tag="test",
        intent_digest="sha256:" + "5" * 64,
        initial_surface={"p": other, "q": other},
        final_surface={"p": honest, "q": liar},
        effects=[
            ReplaceFile(effect_id="e1", path="p", pre=other, post=honest),
            ReplaceFile(effect_id="e2", path="q", pre=other, post=liar),
        ],
    )

    assert sorted(
        n for d, n in referenced_digests(spec) if d == honest.content_hash
    ) == [3, 99]


def test_one_digest_with_conflicting_lengths_across_surfaces_keeps_both_pairs():
    assert referenced_digests(two_length_spec()) == (
        ("sha256:" + "a" * 64, 5),
        ("sha256:" + "a" * 64, 6),
    )


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
    commit_record(opened_store, "tx1", one_effect_spec())
    commit_record(opened_store, "tx2", replace_spec(), b"before", b"after")
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
    commit_record(opened_store, "tx1", spec, b"before", b"after")
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


def _plant_halted(raw, diagnostic):
    raw.execute(
        "UPDATE transaction_record SET state = 'halted', halt_diagnostic = ? "
        "WHERE txid = 'tx1'",
        (encode_diagnostic(diagnostic),),
    )


def _plant_blob_for_non_compiling_spec(raw) -> None:
    """Stage the blob row for the digest referenced by non_compiling_spec's effect.
    This is for the write-side test where insert_record is called inside a transaction."""
    raw.execute(
        "INSERT INTO blob VALUES (?, ?)",
        (digest_of(b"after"), len(b"after")),
    )


def _plant_non_compiling_spec(raw) -> None:
    """The planted spec states a postimage in neither surface, so it references a
    digest the committed record never wrote. Insert the row it needs: this case
    corrupts `spec_json_compiles` and nothing else."""
    _plant_spec_json(raw, canonical_json(non_compiling_spec()), CREATE_FILE_ROW)
    _plant_blob_for_non_compiling_spec(raw)


ONE_EFFECT_ROW = (("only", "create_directory"),)
CREATE_FILE_ROW = (("only", "create_file_no_clobber"),)


def _only_spec():
    return one_effect_spec(effect_id="only")


CROSS_ROW_CASES = (
        (RULE_SPEC_DECODES, _only_spec, lambda raw: _plant_spec_json(raw, '{"nope": 1}', ())),
        (RULE_SPEC_CANONICAL, _only_spec, lambda raw: _plant_spec_json(raw, " " + canonical_json(_only_spec()), ONE_EFFECT_ROW)),
        (RULE_SPEC_COMPILES, _only_spec, _plant_non_compiling_spec),
        (RULE_EFFECT_COVERAGE, _only_spec, lambda raw: _plant(raw, "DELETE FROM effect")),
        (RULE_EFFECT_VARIANT, _only_spec, lambda raw: _plant(raw, "UPDATE effect SET variant = 'delete_path'")),
        (RULE_BLOB_ROW_PRESENT, replace_spec, lambda raw: _plant(raw, "DELETE FROM blob WHERE digest = ?", (digest_of(b"before"),))),
        (RULE_BLOB_BYTE_LEN, replace_spec, lambda raw: _plant(raw, "UPDATE blob SET byte_len = 999 WHERE digest = ?", (digest_of(b"before"),))),
        (RULE_ROLLBACK_RESULT, _only_spec, lambda raw: _plant(raw, "UPDATE transaction_record SET state = 'rolled_back'")),
        (RULE_HALT_DIAGNOSTIC, _only_spec, lambda raw: _plant(raw, "UPDATE transaction_record SET state = 'halted'")),
        (RULE_DIAGNOSTIC_DECISION, _only_spec, lambda raw: _plant_halted(raw, replace(matching_diagnostic("only"), commit_decision=CommitDecision.COMMITTED))),
        (RULE_DIAGNOSTIC_JOURNALS, _only_spec, lambda raw: _plant_halted(raw, replace(matching_diagnostic("only"), journals=(EffectJournalState(effect_id="only", state=JournalState.DONE),)))),
        (RULE_ACTIVE_RECORD, _only_spec, lambda raw: _plant(raw, "INSERT INTO active VALUES (0, 'ghost')")),
)


@pytest.mark.parametrize(
    ("rule", "prepare", "corrupt"),
    CROSS_ROW_CASES,
)
def test_cross_row_corruption_refuses_on_load(opened_store, store_binding, rule, prepare, corrupt):
    contents = (b"before", b"after") if prepare is replace_spec else ()
    commit_record(opened_store, "tx1", prepare(), *contents)
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


def test_the_cross_row_matrix_covers_every_rule_on_read_side():
    assert {case[0] for case in CROSS_ROW_CASES} == set(COHERENCE_RULES)


def test_reading_a_ghost_active_reference_refuses(opened_store, store_binding):
    commit_record(opened_store, "tx1", _only_spec())
    raw = raw_connect(store_binding)
    try:
        raw.execute("DELETE FROM effect WHERE txid = 'tx1'")
        raw.execute("DELETE FROM transaction_record WHERE txid = 'tx1'")
        raw.execute("INSERT INTO active VALUES (0, 'ghost')")
    finally:
        raw.close()
    with pytest.raises(MetadataStoreInvalid) as caught:
        opened_store.read_active()
    assert RULE_ACTIVE_RECORD in str(caught.value)


def test_a_cross_row_violation_is_protocol_error_before_commit(opened_store):
    with pytest.raises(ProtocolError) as caught, opened_store.transaction() as txn:
        txn.insert_record("tx1", one_effect_spec())
        txn.set_transaction_state("tx1", TransactionState.ROLLED_BACK)
    assert RULE_ROLLBACK_RESULT in str(caught.value)
    assert opened_store.read_record("tx1") is None


def _halt_with(txn, diagnostic):
    txn.set_transaction_state("tx2", TransactionState.HALTED)
    txn.set_halt_diagnostic("tx2", diagnostic)


def _plant_replace_blob_rows(raw, *, before_len: int | None) -> None:
    if before_len is not None:
        raw.execute(
            "INSERT INTO blob VALUES (?, ?)",
            (digest_of(b"before"), before_len),
        )
    raw.execute(
        "INSERT INTO blob VALUES (?, ?)",
        (digest_of(b"after"), len(b"after")),
    )


WRITE_SIDE_INCOHERENCE = (
    (RULE_SPEC_COMPILES, _plant_blob_for_non_compiling_spec, lambda txn: txn.insert_record("tx3", non_compiling_spec())),
    (RULE_BLOB_ROW_PRESENT, lambda raw: _plant_replace_blob_rows(raw, before_len=None), lambda txn: txn.insert_record("tx3", replace_spec())),
    (RULE_BLOB_BYTE_LEN, lambda raw: _plant_replace_blob_rows(raw, before_len=999), lambda txn: txn.insert_record("tx3", replace_spec())),
    (RULE_ROLLBACK_RESULT, None, lambda txn: txn.set_transaction_state("tx2", TransactionState.ROLLED_BACK)),
    (RULE_ROLLBACK_RESULT, None, lambda txn: txn.set_rollback_result("tx2", RollbackResult.RESTORED)),
    (RULE_HALT_DIAGNOSTIC, None, lambda txn: txn.set_transaction_state("tx2", TransactionState.HALTED)),
    (RULE_HALT_DIAGNOSTIC, None, lambda txn: txn.set_halt_diagnostic("tx2", matching_diagnostic("only"))),
    (RULE_DIAGNOSTIC_DECISION, None, lambda txn: _halt_with(txn, replace(matching_diagnostic("only"), commit_decision=CommitDecision.COMMITTED))),
    (RULE_DIAGNOSTIC_JOURNALS, None, lambda txn: _halt_with(txn, replace(matching_diagnostic("only"), journals=(EffectJournalState("only", JournalState.DONE),)))),
)
WRITE_UNREACHABLE_RULES = (
    # `insert_record` writes `canonical_json(spec)` of an exact `TransactionSpec`, so no
    # caller can make the stored text fail to decode or fail to re-encode --
    # `test_insert_record_stores_the_canonical_encoding` is the property.
    RULE_SPEC_DECODES,
    RULE_SPEC_CANONICAL,
    # The same method derives every `effect` row and its variant from that spec, and no
    # setter adds, drops, or retypes one --
    # `test_insert_record_derives_every_effect_row_from_the_spec` is the property.
    RULE_EFFECT_COVERAGE,
    RULE_EFFECT_VARIANT,
    # `active.txid` is a real foreign key under `foreign_keys = ON`, so `set_active`
    # raises `IntegrityError` long before the barrier --
    # `test_set_active_refuses_a_transaction_that_does_not_exist` is the property.
    RULE_ACTIVE_RECORD,
)


@pytest.mark.parametrize(("rule", "plant", "body"), WRITE_SIDE_INCOHERENCE)
def test_every_reachable_cross_row_rule_refuses_on_a_write(opened_store, store_binding, rule, plant, body):
    commit_record(opened_store, "tx2", _only_spec())
    if plant:
        raw = raw_connect(store_binding)
        try:
            plant(raw)
        finally:
            raw.close()
    findings = []
    with pytest.raises(ProtocolError) as caught, opened_store.transaction() as txn:
        body(txn)
        for txid in ("tx2", "tx3"):
            findings.extend(coherence_findings(opened_store._connection, txid))
    assert len(findings) == 1, findings
    assert findings[0].startswith(f"{rule}: ")
    assert rule in str(caught.value)


def test_the_cross_row_matrix_covers_every_rule_on_both_sides():
    """The claim design §11.2 makes, as an assertion rather than a list.

    The read side is total. The write side covers every rule the public write API can
    actually produce; the rest are listed once, each with the API property that makes it
    unreachable and the test that proves that property. A rule added to the predicate
    with no case on either side fails here.
    """
    assert {case[0] for case in CROSS_ROW_CASES} == set(COHERENCE_RULES)
    written = {case[0] for case in WRITE_SIDE_INCOHERENCE}
    assert not written & set(WRITE_UNREACHABLE_RULES)
    assert written | set(WRITE_UNREACHABLE_RULES) == set(COHERENCE_RULES)


RULE_INVENTORY = (
    RULE_SPEC_DECODES, RULE_SPEC_CANONICAL, RULE_SPEC_COMPILES,
    RULE_EFFECT_COVERAGE, RULE_EFFECT_VARIANT, RULE_BLOB_ROW_PRESENT,
    RULE_BLOB_BYTE_LEN, RULE_ROLLBACK_RESULT, RULE_HALT_DIAGNOSTIC,
    RULE_DIAGNOSTIC_DECISION, RULE_DIAGNOSTIC_JOURNALS, RULE_ACTIVE_RECORD,
)


def test_every_rule_constant_is_listed_in_coherence_rules():
    assert set(RULE_INVENTORY) == set(COHERENCE_RULES)
    assert len(RULE_INVENTORY) == len(COHERENCE_RULES) == 12


def test_the_journal_vector_follows_spec_order_not_row_order(opened_store):
    from atoms.core.effects import CreateFileNoClobber
    from atoms.core.fingerprint import ABSENT
    from atoms.core.spec import build_spec
    from tests.store_support import file_state

    posts = {name: file_state(name.encode()) for name in ("z", "a", "m")}
    spec = build_spec(
        consumer_tag="test", intent_digest="sha256:" + "2" * 64,
        initial_surface={f"{name}.txt": ABSENT for name in posts},
        final_surface={f"{name}.txt": posts[name] for name in posts},
        effects=[CreateFileNoClobber(effect_id=name, path=f"{name}.txt", post=posts[name]) for name in ("z", "a", "m")],
    )
    commit_record(opened_store, "tx1", spec, *(name.encode() for name in posts))
    assert tuple(j.effect_id for j in opened_store.read_record("tx1").journals) == ("z", "a", "m")


def test_a_read_takes_one_snapshot_across_a_concurrent_commit(store_on):
    from atoms.store.connection import open_store

    with store_on() as binding, open_store(binding) as writer, open_store(binding) as reader:
        with writer.transaction() as txn:
            txn.insert_record("tx1", one_effect_spec(effect_id="e1"))
        fired = []
        def commit_between(statement):
            if "FROM effect" not in statement or fired:
                return
            fired.append(statement)
            with writer.transaction() as txn:
                txn.set_journal_state("tx1", "e1", JournalState.STARTED)
                txn.set_transaction_state("tx1", TransactionState.APPLYING)
        reader._connection.set_trace_callback(commit_between)
        try:
            during = reader.read_record("tx1")
        finally:
            reader._connection.set_trace_callback(None)
        after = reader.read_record("tx1")
    assert fired
    assert during is not None and after is not None
    assert (during.state, during.journals[0].state) == (TransactionState.PREPARED, JournalState.PENDING)
    assert (after.state, after.journals[0].state) == (TransactionState.APPLYING, JournalState.STARTED)


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


def test_corruption_from_the_record_materialization_query_is_translated(
    opened_store, monkeypatch
):
    with opened_store.transaction() as txn:
        txn.insert_record("tx1", one_effect_spec())
    monkeypatch.setattr(
        opened_store,
        "_connection",
        CorruptsStatement(opened_store._connection, SELECT_EFFECTS, occurrence=2),
    )
    with pytest.raises(MetadataStoreInvalid) as caught:
        opened_store.read_record("tx1")
    assert isinstance(caught.value.__cause__, sqlite3.DatabaseError)


def test_corruption_from_a_precommit_coherence_query_is_translated(
    opened_store, monkeypatch
):
    monkeypatch.setattr(
        opened_store,
        "_connection",
        CorruptsStatement(opened_store._connection, SELECT_ACTIVE),
    )
    with pytest.raises(MetadataStoreInvalid) as caught, opened_store.transaction() as txn:
        txn.insert_record("tx1", one_effect_spec())
    assert isinstance(caught.value.__cause__, sqlite3.DatabaseError)
