"""Public preimages: transaction authority, owned bytes, and writable admission."""

import contextlib
import errno
import os
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from atoms.chain.errors import ChainStateInvalid
from atoms.chain.model import ChainOutcome, SettledEntry, state_to_json
from atoms.coordinator import commands
from atoms.coordinator.commands import read_chain, read_preimage, run_transaction
from atoms.core.effects import CreateFileNoClobber, DeletePath, ReplaceFile
from atoms.core.errors import PreconditionRefused, ProtocolError, TransactionHalted
from atoms.core.fingerprint import ABSENT, DirectoryState, SymlinkState
from atoms.core.recovery import TransactionState
from atoms.core.scratch import CHAIN_LEAF
from atoms.core.spec import build_spec
from atoms.fs.audit import Provenance, RootKind
from atoms.fs.backend import Backend
from atoms.fs.volume import StorageProfile
from atoms.store.connection import Store
from atoms.store.errors import MetadataStoreInvalid
from tests.capture_support import DictPayloads
from tests.store_support import digest_of, file_state
from tests.test_coordinator_commands import _enable_commands, _register
from tests.test_coordinator_lease import _contend

_Ingredients = tuple[Backend, str, str, StorageProfile]


def _root(coordinator_on, monkeypatch) -> _Ingredients:
    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    (Path(ingredients[1]) / "d").mkdir()
    _register(ingredients, b"root", ())
    return ingredients


def _remove(ingredients: _Ingredients, payload: bytes, *, registered=True):
    target = Path(ingredients[1]) / "d/f.txt"
    target.write_bytes(payload)
    target.chmod(0o644)
    state = file_state(payload)
    spec = build_spec(
        consumer_tag="test", intent_digest=digest_of(b"remove"),
        initial_surface={"d/f.txt": state}, final_surface={"d/f.txt": ABSENT},
        effects=[DeletePath(effect_id="e1", path="d/f.txt", pre=state)],
        registered_paths=("d/f.txt",) if registered else (),
    )
    return run_transaction(*ingredients, spec, DictPayloads({}))


@pytest.mark.parametrize("payload", [b"", b"before", b"x" * 65537])
def test_returns_owned_preimage_after_removal(coordinator_on, monkeypatch, payload):
    ingredients = _root(coordinator_on, monkeypatch)
    outcome = _remove(ingredients, payload)
    assert not (Path(ingredients[1]) / "d/f.txt").exists()
    result = read_preimage(*ingredients, outcome.txid, "d/f.txt", max_bytes=len(payload))
    assert type(result) is bytes
    assert result == payload


def test_two_removals_select_their_own_version(coordinator_on, monkeypatch):
    ingredients = _root(coordinator_on, monkeypatch)
    first = _remove(ingredients, b"first")
    second = _remove(ingredients, b"second")
    assert read_preimage(*ingredients, first.txid, "d/f.txt", max_bytes=6) == b"first"
    assert read_preimage(*ingredients, second.txid, "d/f.txt", max_bytes=6) == b"second"
    for txid, path in (("unknown", "d/f.txt"), (first.txid, "d/other.txt")):
        with pytest.raises(PreconditionRefused):
            read_preimage(*ingredients, txid, path, max_bytes=6)


def test_replacement_returns_initial_bytes_not_the_current_file(coordinator_on, monkeypatch):
    ingredients = _root(coordinator_on, monkeypatch)
    target = Path(ingredients[1]) / "d/f.txt"
    target.write_bytes(b"before")
    target.chmod(0o644)
    pre, post = file_state(b"before"), file_state(b"after")
    spec = build_spec(
        consumer_tag="test", intent_digest=digest_of(b"replace"),
        initial_surface={"d/f.txt": pre}, final_surface={"d/f.txt": post},
        effects=[ReplaceFile(effect_id="e1", path="d/f.txt", pre=pre, post=post)],
        registered_paths=("d/f.txt",),
    )
    outcome = run_transaction(*ingredients, spec, DictPayloads({post.content_hash: b"after"}))
    assert target.read_bytes() == b"after"
    assert read_preimage(*ingredients, outcome.txid, "d/f.txt", max_bytes=6) == b"before"


class _String(str):
    pass


class _Integer(int):
    pass


@pytest.mark.parametrize("txid,path,budget,error", [
    (None, "a", 1, ProtocolError), (b"id", "a", 1, ProtocolError),
    (_String("id"), "a", 1, ProtocolError), ("id", None, 1, ProtocolError),
    ("id", _String("a"), 1, ProtocolError), ("id", "a", True, ProtocolError),
    ("id", "a", _Integer(1), ProtocolError), ("id", "a", 1.0, ProtocolError),
    ("", "a", 1, PreconditionRefused), ("a/b", "a", 1, PreconditionRefused),
    ("x" * 65, "a", 1, PreconditionRefused), ("id", "", 1, PreconditionRefused),
    ("id", "../x", 1, PreconditionRefused), ("id", "/x", 1, PreconditionRefused),
    ("id", "a//b", 1, PreconditionRefused), ("id", "a\0b", 1, PreconditionRefused),
    ("id", "a", -1, PreconditionRefused),
])
def test_request_errors_precede_lease_entry(monkeypatch, txid, path, budget, error):
    def no_lease(*args, **kwargs):
        raise AssertionError("lease entered")
    monkeypatch.setattr(commands, "_writable_recovery_lease", no_lease)
    with pytest.raises(error):
        read_preimage(None, "unused", "unused", None, txid, path, max_bytes=budget)  # type: ignore[arg-type]


def _no_blob(*args, **kwargs):
    raise AssertionError("blob opened before authorization")


def test_budget_refusal_precedes_blob_open(coordinator_on, monkeypatch):
    ingredients = _root(coordinator_on, monkeypatch)
    outcome = _remove(ingredients, b"before")
    monkeypatch.setattr(Store, "open_blob", _no_blob)
    with pytest.raises(PreconditionRefused, match="max_bytes"):
        read_preimage(*ingredients, outcome.txid, "d/f.txt", max_bytes=5)


def test_private_spec_path_cannot_authorize_a_read(coordinator_on, monkeypatch):
    ingredients = _root(coordinator_on, monkeypatch)
    outcome = _remove(ingredients, b"private", registered=False)
    monkeypatch.setattr(Store, "open_blob", _no_blob)
    with pytest.raises(PreconditionRefused):
        read_preimage(*ingredients, outcome.txid, "d/f.txt", max_bytes=7)


@pytest.mark.parametrize("kind", ["absent", "symlink"])
def test_nonfile_initial_state_cannot_select_indexed_bytes(coordinator_on, monkeypatch, kind):
    ingredients = _root(coordinator_on, monkeypatch)
    _remove(ingredients, b"retained unrelated blob")
    target = Path(ingredients[1]) / "d/f.txt"
    payloads = {}
    if kind == "absent":
        pre, post = ABSENT, file_state(b"postimage")
        effect = CreateFileNoClobber(effect_id="e1", path="d/f.txt", post=post)
        payloads[post.content_hash] = b"postimage"
    else:
        target.symlink_to("missing")
        pre = SymlinkState(target="missing", mode=0o777)
        post = ABSENT
        effect = DeletePath(effect_id="e1", path="d/f.txt", pre=pre)
    spec = build_spec(
        consumer_tag="test", intent_digest=digest_of(b"nonfile"),
        initial_surface={"d/f.txt": pre}, final_surface={"d/f.txt": post},
        effects=[effect], registered_paths=("d/f.txt",),
    )
    outcome = run_transaction(*ingredients, spec, DictPayloads(payloads))
    monkeypatch.setattr(Store, "open_blob", _no_blob)
    with pytest.raises(PreconditionRefused, match="regular file"):
        read_preimage(*ingredients, outcome.txid, "d/f.txt", max_bytes=100)


@pytest.mark.parametrize("fault,error", [
    ("missing", PreconditionRefused), ("nonterminal", PreconditionRefused),
    ("directory", PreconditionRefused),
    ("projection", ChainStateInvalid), ("registration", ChainStateInvalid),
    ("settlement", ChainStateInvalid), ("outcome", ChainStateInvalid),
    ("backfill", ChainStateInvalid), ("append", ChainStateInvalid),
    ("duplicate", ChainStateInvalid),
])
def test_record_and_chain_authority_precedes_blob_open(coordinator_on, monkeypatch, fault, error):
    ingredients = _root(coordinator_on, monkeypatch)
    outcome = _remove(ingredients, b"before")
    other = _remove(ingredients, b"another")
    real_root, real_read = commands._registered_root, Store.read_record

    def changed_record(store, txid):
        record = real_read(store, txid)
        if txid != outcome.txid:
            return record
        assert record is not None
        if fault == "directory":
            # No current effect accepts a directory preimage. Inject agreement
            # at the read boundary to verify its closed non-file refusal.
            surface = (replace(record.spec.initial_surface[0], state=DirectoryState(0o755)),)
            return replace(record, spec=replace(record.spec, initial_surface=surface))
        if fault == "missing":
            return None
        if fault == "nonterminal":
            return replace(record, state=TransactionState.PREPARED)
        if fault == "projection":
            return replace(record, spec=replace(record.spec, consumer_tag="contradiction"))
        if fault == "registration":
            return replace(record, registration_digest=other.registration)
        if fault == "settlement":
            return replace(record, settlement_digest=other.settlement)
        if fault == "outcome":
            return replace(record, state=TransactionState.ROLLED_BACK)
        if fault in ("append", "backfill"):
            return replace(record, settlement_digest=None)
        return record

    @contextlib.contextmanager
    def changed_view(lease):
        # Inject after the real lease/recovery/chain checks, isolating the reader's
        # authorization from the store's SQL guards and recovery's own judgments.
        with real_root(lease) as (fd, chain), monkeypatch.context() as patch:
            patch.setattr(Store, "read_record", changed_record)
            if fault == "directory":
                chain = replace(chain, entries=tuple(
                    (digest, replace(entry, initial=(("d/f.txt", state_to_json(DirectoryState(0o755))),)))
                    if digest == outcome.registration else (digest, entry)
                    for digest, entry in chain.entries
                ))
            elif fault == "append":
                chain = replace(chain, entries=tuple(
                    row for row in chain.entries if row[0] != outcome.settlement
                ))
            elif fault == "duplicate":
                row = next(row for row in chain.entries if row[0] == outcome.registration)
                chain = replace(chain, entries=chain.entries + (row,))
            yield fd, chain

    monkeypatch.setattr(commands, "_registered_root", changed_view)
    monkeypatch.setattr(Store, "open_blob", _no_blob)
    with pytest.raises(error):
        read_preimage(*ingredients, outcome.txid, "d/f.txt", max_bytes=6)
    assert read_chain(*ingredients).tip == other.settlement


@pytest.mark.parametrize("fault,rule", [
    ("missing", "blob_row_present"), ("length", "blob_byte_len"),
])
def test_record_blob_index_coherence_fails_before_blob_open(coordinator_on, monkeypatch, fault, rule):
    ingredients = _root(coordinator_on, monkeypatch)
    outcome = _remove(ingredients, b"before")
    with sqlite3.connect(Path(ingredients[2]) / "atoms.db") as connection:
        if fault == "missing":
            connection.execute("DELETE FROM blob WHERE digest = ?", (digest_of(b"before"),))
        else:
            connection.execute("UPDATE blob SET byte_len = 1 WHERE digest = ?", (digest_of(b"before"),))
    monkeypatch.setattr(Store, "open_blob", _no_blob)
    with pytest.raises(MetadataStoreInvalid, match=rule):
        read_preimage(*ingredients, outcome.txid, "d/f.txt", max_bytes=6)


def _blob_leaf(ingredients):
    return Path(ingredients[2]) / "blobs/sha256" / digest_of(b"before").removeprefix("sha256:")


@pytest.mark.parametrize("fault", ["missing", "short", "long", "hash", "symlink", "directory", "fifo"])
def test_corrupt_indexed_leaf_never_returns_bytes(coordinator_on, monkeypatch, fault):
    ingredients = _root(coordinator_on, monkeypatch)
    outcome = _remove(ingredients, b"before")
    leaf = _blob_leaf(ingredients)
    if fault in ("short", "long", "hash"):
        leaf.write_bytes({"short": b"b", "long": b"before!", "hash": b"xxxxxx"}[fault])
    else:
        leaf.unlink()
        if fault == "symlink":
            leaf.symlink_to("missing")
        elif fault == "directory":
            leaf.mkdir()
        elif fault == "fifo":
            os.mkfifo(leaf)
    with pytest.raises(MetadataStoreInvalid):
        read_preimage(*ingredients, outcome.txid, "d/f.txt", max_bytes=6)
    assert _contend(ingredients[2]) == 0


@pytest.mark.parametrize("replacement", [b"xxxxxx", b"short", b"before!", b""])
def test_returned_buffer_is_verified_again_and_fd_closed(coordinator_on, monkeypatch, replacement):
    ingredients = _root(coordinator_on, monkeypatch)
    outcome = _remove(ingredients, b"before")
    real_open = Store.open_blob
    opened = []

    def tamper(store, digest):
        fd = real_open(store, digest)
        opened.append(fd)
        _blob_leaf(ingredients).write_bytes(replacement)
        return fd

    monkeypatch.setattr(Store, "open_blob", tamper)
    with pytest.raises(MetadataStoreInvalid):
        read_preimage(*ingredients, outcome.txid, "d/f.txt", max_bytes=6)
    with pytest.raises(OSError) as closed:
        os.fstat(opened[0])
    assert closed.value.errno == errno.EBADF
    assert _contend(ingredients[2]) == 0


@pytest.mark.parametrize("failure", [None, OSError(errno.EIO, "read failed"), MemoryError()])
def test_read_holds_lock_and_audited_fd_then_releases_both(coordinator_on, monkeypatch, failure):
    ingredients = _root(coordinator_on, monkeypatch)
    outcome = _remove(ingredients, b"before")
    real_open, real_read = Store.open_blob, commands._read_preimage_bytes
    borrowed = []

    def remember(store, digest):
        fd = real_open(store, digest)
        borrowed.append((fd, store._binding.backend))
        return fd

    def while_locked(fd, state):
        assert _contend(ingredients[2]) == 3
        assert borrowed[0][1].provenance_of(fd) == Provenance(
            RootKind.METADATA, f"blobs/sha256/{digest_of(b'before').removeprefix('sha256:')}"
        )
        if failure is not None:
            raise failure
        result = real_read(fd, state)
        assert _contend(ingredients[2]) == 3
        return result

    monkeypatch.setattr(Store, "open_blob", remember)
    monkeypatch.setattr(commands, "_read_preimage_bytes", while_locked)
    if failure is None:
        result = read_preimage(*ingredients, outcome.txid, "d/f.txt", max_bytes=6)
        assert result == b"before"
    else:
        with pytest.raises(type(failure)) as caught:
            read_preimage(*ingredients, outcome.txid, "d/f.txt", max_bytes=6)
        assert caught.value is failure
    fd, audited = borrowed[0]
    with pytest.raises(OSError) as closed:
        os.fstat(fd)
    assert closed.value.errno == errno.EBADF
    with pytest.raises(ProtocolError, match="unregistered"):
        audited.provenance_of(fd)
    assert _contend(ingredients[2]) == 0


def _snapshot(*roots):
    rows = []
    for root in map(Path, roots):
        if not root.exists():
            rows.append((str(root), None))
            continue
        for path in [root, *sorted(root.rglob("*"))]:
            mode = path.lstat().st_mode
            content = path.readlink() if path.is_symlink() else path.read_bytes() if path.is_file() else None
            rows.append((str(path), mode, content))
    return rows


@pytest.mark.parametrize("state", ["metadata-less", "missing-lock", "unserviceable", "serviceable", "cold", "mismatched", "v2"])
def test_nonwritable_reads_refuse_without_writes(coordinator_on, monkeypatch, state):
    from atoms.coordinator import lifecycle, root
    from tests.lifecycle_support import fabricate_v2_store
    from tests.test_lifecycle_commands import _cold_copy, _copy_targets, _grant, _replicate

    if state in ("metadata-less", "v2"):
        ingredients = coordinator_on()
        _enable_commands(ingredients, monkeypatch)
        backend, project, metadata, storage = ingredients
        if state == "v2":
            fabricate_v2_store(metadata)
        else:
            (Path(metadata) / "lock").unlink()
            Path(metadata).rmdir()
    else:
        ingredients = _root(coordinator_on, monkeypatch)
        _remove(ingredients, b"source history")
        backend, project, metadata, storage = ingredients
        if state == "cold":
            project, metadata = _cold_copy(ingredients, monkeypatch, "cold-read")
            _grant(backend, project, metadata, storage)
        elif state in ("serviceable", "unserviceable"):
            project, metadata = _copy_targets(ingredients, "replica-read")
            _replicate(ingredients, project, metadata)
            if state == "serviceable":
                _grant(backend, project, metadata, storage)
        elif state == "mismatched":
            monkeypatch.setattr(lifecycle, "_read_machine_identity", lambda: "another-host")
        elif state == "missing-lock":
            (Path(metadata) / "lock").unlink()

    def no_writable_suffix(*args, **kwargs):
        raise AssertionError("non-writable root entered the writable suffix")

    before = _snapshot(project, metadata)
    monkeypatch.setattr(root, "_leased_stack", no_writable_suffix)
    with pytest.raises(PreconditionRefused):
        read_preimage(backend, project, metadata, storage, "history", "d/f.txt", max_bytes=100)
    assert _snapshot(project, metadata) == before


def test_rolled_back_history_is_readable(coordinator_on, monkeypatch):
    from atoms.coordinator import execute

    ingredients = _root(coordinator_on, monkeypatch)

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(execute.delete_path, "apply", interrupt)
    with pytest.raises(KeyboardInterrupt):
        _remove(ingredients, b"before")
    settlement = next(entry for _, entry in read_chain(*ingredients).entries if type(entry) is SettledEntry)
    assert settlement.outcome is ChainOutcome.ROLLED_BACK
    assert read_preimage(*ingredients, settlement.txid, "d/f.txt", max_bytes=6) == b"before"


def _prepare_after_history(lease):
    from atoms.chain.append import append_entry
    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace, prepare_transaction
    from atoms.coordinator.recover import _registered_root, _registration_entry
    from tests.coordinator_support import AFTER, admission_for

    # _prepare_registered also appends genesis; this root already has history.
    approved = admission_for(lease)
    with open_workspace(lease, approved) as workspace, capture_initial_surface(
        lease, approved, workspace, DictPayloads({digest_of(AFTER): AFTER})
    ) as captured:
        prepare_transaction(lease, approved, workspace, captured.manifest)
    with _registered_root(lease) as (fd, chain):
        digest = append_entry(
            lease._binding.backend, fd, chain,
            _registration_entry(approved.compiled.spec, approved.txid),
        )
    with lease._store.transaction() as txn:
        txn.set_registration_digest(approved.txid, digest)
    return approved.txid


def test_active_transaction_resolves_before_historical_read(coordinator_on, monkeypatch, leased):
    from tests.test_coordinator_assembly_halt import _active_record

    ingredients = _root(coordinator_on, monkeypatch)
    history = _remove(ingredients, b"before")
    with leased(ingredients) as lease:
        active_txid = _prepare_after_history(lease)
    active = _active_record(ingredients)
    assert active is not None and active.txid == active_txid
    assert read_preimage(*ingredients, history.txid, "d/f.txt", max_bytes=6) == b"before"
    assert _active_record(ingredients) is None
    assert any(
        type(entry) is SettledEntry and entry.txid == active_txid
        for _, entry in read_chain(*ingredients).entries
    )


def test_persisted_assembly_halt_blocks_unrelated_history(coordinator_on, monkeypatch, leased):
    from tests.test_coordinator_assembly_halt import _active_record

    ingredients = _root(coordinator_on, monkeypatch)
    history = _remove(ingredients, b"before")
    with leased(ingredients) as lease:
        active_txid = _prepare_after_history(lease)
    (Path(ingredients[1]) / "d").rename(Path(ingredients[1]) / "moved-d")
    for txid in (history.txid, active_txid, history.txid):
        with pytest.raises(TransactionHalted):
            read_preimage(*ingredients, txid, "d/f.txt", max_bytes=6)
        assert _contend(ingredients[2]) == 0
    record = _active_record(ingredients)
    assert record is not None and record.txid == active_txid and record.assembly_halt is not None


def test_unrelated_pending_registration_does_not_gate_history(coordinator_on, monkeypatch):
    ingredients = _root(coordinator_on, monkeypatch)
    history = _remove(ingredients, b"before")
    pending = _remove(ingredients, b"pending")
    (Path(ingredients[1]) / CHAIN_LEAF / pending.settlement).unlink()
    assert read_chain(*ingredients).tip == pending.registration
    assert read_preimage(*ingredients, history.txid, "d/f.txt", max_bytes=6) == b"before"
