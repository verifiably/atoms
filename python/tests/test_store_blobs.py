"""Tier 4 -- blobs over a real ext4 metadata root (design §7.2, §7.3, §8)."""

from __future__ import annotations

import os
import signal
import socket
import sqlite3

import pytest

from atoms.core.errors import ProtocolError
from atoms.store.blobs import StagedBlob, digest_to_leaf, leaf_to_digest, require_component
from atoms.store.connection import open_store
from atoms.store.errors import MetadataStoreInvalid
from tests.store_support import (
    RELEASES,
    child_dir,
    digest_of,
    metadata_root_snapshot,
    one_effect_spec,
    open_descriptor_count,
    raw_connect,
    release_lock,
    spec_referencing,
    stage,
)

HEX = "a" * 64


def test_the_digest_and_the_leaf_map_in_both_directions():
    assert digest_to_leaf(f"sha256:{HEX}") == HEX
    assert leaf_to_digest(HEX) == f"sha256:{HEX}"


@pytest.mark.parametrize("bad", [3, None, b"x"])
def test_a_digest_with_the_wrong_exact_type_is_refused_before_any_lookup(opened_store, bad):
    with pytest.raises(ProtocolError) as caught:
        opened_store.open_blob(bad)
    assert "must be exactly str" in str(caught.value)


@pytest.mark.parametrize(
    "bad", [HEX, f"sha256:{HEX.upper()}", f"sha256:{HEX[:63]}", f"sha1:{HEX}"]
)
def test_a_digest_failing_the_grammar_is_refused_before_any_lookup(opened_store, bad):
    with pytest.raises(ProtocolError) as caught:
        opened_store.open_blob(bad)
    assert "not sha256:<64 lowercase hex>" in str(caught.value)


@pytest.mark.parametrize("value", [3, None, b"x"])
def test_a_component_with_the_wrong_exact_type_is_refused(value):
    with pytest.raises(ProtocolError) as caught:
        require_component("name", value)
    assert "must be exactly str" in str(caught.value)


@pytest.mark.parametrize("value", ["", ".", "..", "a/b", "a\x00b"])
def test_a_component_that_cannot_be_one_leaf_is_refused(value):
    with pytest.raises(ProtocolError) as caught:
        require_component("name", value)
    assert "not a single pathname component" in str(caught.value)


def test_an_unindexed_digest_raises_protocol_error_on_a_healthy_store(opened_store):
    """Membership, not existence: the caller asked for a blob this store does not have."""
    with pytest.raises(ProtocolError) as caught:
        opened_store.open_blob(f"sha256:{HEX}")
    assert "not indexed" in str(caught.value)


def test_open_blob_returns_a_verified_descriptor_at_offset_zero(promoted_blob):
    store, digest, content = promoted_blob
    fd = store.open_blob(digest)
    try:
        assert os.read(fd, len(content)) == content
    finally:
        os.close(fd)


def test_the_returned_descriptor_is_the_callers_to_close(promoted_blob):
    store, digest, _content = promoted_blob
    fd = store.open_blob(digest)
    store.close()
    os.close(fd)


def test_an_indexed_digest_whose_leaf_is_missing_refuses_without_a_descriptor_leak(
    promoted_blob, store_binding
):
    store, digest, _content = promoted_blob
    with child_dir(store_binding.metadata_root_fd, "blobs/sha256") as blobs_fd:
        os.unlink(digest_to_leaf(digest), dir_fd=blobs_fd)
    before = open_descriptor_count()
    with pytest.raises(MetadataStoreInvalid) as caught:
        store.open_blob(digest)
    assert "has a blob row but no leaf" in str(caught.value)
    assert open_descriptor_count() == before


@pytest.mark.parametrize("kind", ["symlink", "directory"])
def test_an_indexed_digest_whose_leaf_is_not_a_regular_file_refuses(
    promoted_blob, store_binding, kind
):
    store, digest, _content = promoted_blob
    leaf = digest_to_leaf(digest)
    with child_dir(store_binding.metadata_root_fd, "blobs/sha256") as blobs_fd:
        os.unlink(leaf, dir_fd=blobs_fd)
        if kind == "symlink":
            os.symlink("/etc/passwd", leaf, dir_fd=blobs_fd)
        else:
            os.mkdir(leaf, dir_fd=blobs_fd)
    before = open_descriptor_count()
    with pytest.raises(MetadataStoreInvalid) as caught:
        store.open_blob(digest)
    assert ("is a symlink" if kind == "symlink" else "not a regular file") in str(caught.value)
    assert open_descriptor_count() == before


def test_an_indexed_fifo_leaf_is_refused_without_waiting_or_leaking_a_descriptor(
    promoted_blob, store_binding
):
    """O_RDONLY alone blocks before fstat can reject a FIFO as nonregular."""
    store, digest, _content = promoted_blob
    leaf = digest_to_leaf(digest)
    with child_dir(store_binding.metadata_root_fd, "blobs/sha256") as blobs_fd:
        os.unlink(leaf, dir_fd=blobs_fd)
        os.mkfifo(leaf, 0o600, dir_fd=blobs_fd)
    before = open_descriptor_count()

    def timeout(_signum, _frame) -> None:
        raise TimeoutError("opening a FIFO waited for a writer")

    previous = signal.signal(signal.SIGALRM, timeout)
    signal.setitimer(signal.ITIMER_REAL, 0.2)
    try:
        with pytest.raises(MetadataStoreInvalid) as caught:
            store.open_blob(digest)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
    assert "not a regular file" in str(caught.value)
    assert open_descriptor_count() == before


def test_a_truncated_blob_whose_prefix_matches_still_refuses(promoted_blob, store_binding):
    store, digest, content = promoted_blob
    with child_dir(store_binding.metadata_root_fd, "blobs/sha256") as blobs_fd:
        fd = os.open(digest_to_leaf(digest), os.O_WRONLY, dir_fd=blobs_fd)
        try:
            os.ftruncate(fd, len(content) - 1)
        finally:
            os.close(fd)
    before = open_descriptor_count()
    with pytest.raises(MetadataStoreInvalid) as caught:
        store.open_blob(digest)
    assert "the index says" in str(caught.value)
    assert open_descriptor_count() == before


def test_a_substituted_blob_of_the_same_length_refuses_without_a_descriptor_leak(
    promoted_blob, store_binding
):
    store, digest, content = promoted_blob
    with child_dir(store_binding.metadata_root_fd, "blobs/sha256") as blobs_fd:
        fd = os.open(digest_to_leaf(digest), os.O_WRONLY, dir_fd=blobs_fd)
        try:
            os.write(fd, bytes(len(content)))
        finally:
            os.close(fd)
    before = open_descriptor_count()
    with pytest.raises(MetadataStoreInvalid) as caught:
        store.open_blob(digest)
    assert "hashes to sha256:" in str(caught.value)
    assert open_descriptor_count() == before


def test_public_blob_read_is_refused_during_a_store_write_transaction(promoted_blob):
    store, digest, _content = promoted_blob
    with store.transaction(), pytest.raises(ProtocolError) as caught:
        store.open_blob(digest)
    assert "write transaction" in str(caught.value)


def test_a_successful_blob_read_ends_its_sqlite_read_transaction(promoted_blob):
    store, digest, _content = promoted_blob
    statements: list[str] = []
    store._connection.set_trace_callback(statements.append)
    try:
        fd = store.open_blob(digest)
    finally:
        store._connection.set_trace_callback(None)
    os.close(fd)
    assert any(statement == "ROLLBACK" for statement in statements)
    assert not any(statement == "COMMIT" for statement in statements)


def test_a_liveness_loss_during_verification_refuses_before_descriptor_handoff(
    promoted_blob, monkeypatch
):
    """Removing open_blob's final gate would hand out an fd after Store.close()."""
    from atoms.store import blobs

    store, digest, _content = promoted_blob
    real = blobs.verify_leaf
    counts: list[int] = []

    def verify_then_close(fd, verified_digest, byte_len):
        observed = real(fd, verified_digest, byte_len)
        store.close()
        counts.append(open_descriptor_count())
        return observed

    monkeypatch.setattr(blobs, "verify_leaf", verify_then_close)
    with pytest.raises(ProtocolError) as caught:
        store.open_blob(digest)
    assert "Store is closed" in str(caught.value)
    assert open_descriptor_count() == counts[0] - 1


def test_staged_blob_is_an_ordinary_dataclass():
    """A6 builds manifests, so a construction token would block the intended caller."""
    assert StagedBlob(name="a", digest=f"sha256:{HEX}", byte_len=1).byte_len == 1


def _manifest(*entries: tuple[str, bytes]) -> tuple[StagedBlob, ...]:
    return tuple(
        StagedBlob(name=name, digest=digest_of(content), byte_len=len(content))
        for name, content in entries
    )


def test_promotion_publishes_indexes_and_removes_the_staging_directory(
    opened_store, store_binding
):
    with opened_store.create_workspace("tx1") as workspace:
        stage(workspace, "one", b"first")
        stage(workspace, "two", b"second")
        manifest = _manifest(("one", b"first"), ("two", b"second"))
        with opened_store.transaction() as txn:
            txn.promote_staging(workspace, manifest)
            txn.insert_record("tx1", spec_referencing(b"first", b"second"))
    with child_dir(store_binding.metadata_root_fd, "blobs/sha256") as blobs_fd:
        assert set(os.listdir(blobs_fd)) == {digest_to_leaf(entry.digest) for entry in manifest}
    with child_dir(store_binding.metadata_root_fd, "staging") as staging_fd:
        assert os.listdir(staging_fd) == []
    assert opened_store.list_workspaces() == ("tx1",)
    for entry in manifest:
        fd = opened_store.open_blob(entry.digest)
        os.close(fd)


def test_promotion_spends_the_staging_half(opened_store):
    with opened_store.create_workspace("tx1") as workspace:
        stage(workspace, "one", b"first")
        with opened_store.transaction() as txn:
            txn.promote_staging(workspace, _manifest(("one", b"first")))
            txn.insert_record("tx1", spec_referencing(b"first"))
        with pytest.raises(ProtocolError):
            _ = workspace.staging_fd
        with pytest.raises(ProtocolError), opened_store.transaction() as txn:
            txn.promote_staging(workspace, _manifest(("one", b"first")))


def test_an_empty_manifest_still_spends_the_staging_half(opened_store, store_binding):
    with opened_store.create_workspace("tx1") as workspace:
        with opened_store.transaction() as txn:
            txn.promote_staging(workspace, ())
            txn.insert_record("tx1", one_effect_spec())
        with pytest.raises(ProtocolError):
            _ = workspace.staging_fd
    with child_dir(store_binding.metadata_root_fd, "staging") as staging_fd:
        assert os.listdir(staging_fd) == []


def test_a_promoted_digest_must_be_referenced_by_its_workspaces_record(opened_store):
    with opened_store.create_workspace("tx1") as workspace:
        stage(workspace, "one", b"shared")
        with pytest.raises(ProtocolError) as caught, opened_store.transaction() as txn:
            txn.promote_staging(workspace, _manifest(("one", b"shared")))
            txn.insert_record("tx1", one_effect_spec())
            txn.insert_record("tx2", spec_referencing(b"shared"))
    assert "tx1" in str(caught.value)


def test_a_final_surface_reference_does_not_justify_promotion(opened_store):
    with opened_store.create_workspace("tx1") as workspace:
        stage(workspace, "one", b"after")
        with pytest.raises(ProtocolError), opened_store.transaction() as txn:
            txn.promote_staging(workspace, _manifest(("one", b"after")))
            txn.insert_record("tx1", one_effect_spec(content=b"after"))


@pytest.mark.parametrize(
    "manifest",
    [
        [StagedBlob(name="one", digest="not-a-digest", byte_len=1)],
        [StagedBlob(name="../escape", digest=f"sha256:{HEX}", byte_len=1)],
        [StagedBlob(name="one", digest=f"sha256:{HEX}", byte_len=True)],
        [StagedBlob(name="one", digest=f"sha256:{HEX}", byte_len=-1)],
    ],
)
def test_a_malformed_manifest_moves_nothing(opened_store, manifest):
    with opened_store.create_workspace("tx1") as workspace:
        stage(workspace, "one", b"first")
        with pytest.raises(ProtocolError), opened_store.transaction() as txn:
            txn.promote_staging(workspace, tuple(manifest))
        assert set(os.listdir(workspace.staging_fd)) == {"one"}


def test_the_entire_manifest_is_validated_before_the_first_transfer(
    opened_store, store_binding, monkeypatch
):
    with opened_store.create_workspace("tx1") as workspace:
        stage(workspace, "one", b"first")
        stage(workspace, "two", b"second")
        called = False

        def armed(*_args):
            nonlocal called
            called = True
            raise AssertionError("preflight mutated before validating the final entry")

        monkeypatch.setattr(store_binding.backend, "transfer_noclobber", armed)
        manifest = (
            *_manifest(("one", b"first")),
            StagedBlob(name="two", digest="bad", byte_len=6),
        )
        with pytest.raises(ProtocolError), opened_store.transaction() as txn:
            txn.promote_staging(workspace, manifest)
        assert not called
        assert set(os.listdir(workspace.staging_fd)) == {"one", "two"}


def test_manifest_and_entries_require_exact_types(opened_store):
    class DerivedBlob(StagedBlob):
        pass

    with opened_store.create_workspace("tx1") as workspace:
        stage(workspace, "one", b"first")
        entry = StagedBlob("one", digest_of(b"first"), 5)
        with pytest.raises(ProtocolError) as manifest_error, opened_store.transaction() as txn:
            txn.promote_staging(workspace, [entry])
        assert "exactly tuple" in str(manifest_error.value)
        with pytest.raises(ProtocolError) as entry_error, opened_store.transaction() as txn:
            txn.promote_staging(workspace, (DerivedBlob("one", entry.digest, 5),))
        assert "exactly StagedBlob" in str(entry_error.value)


def test_duplicate_source_names_are_refused_before_anything_moves(opened_store):
    with opened_store.create_workspace("tx1") as workspace:
        stage(workspace, "one", b"first")
        manifest = _manifest(("one", b"first"), ("one", b"first"))
        with pytest.raises(ProtocolError), opened_store.transaction() as txn:
            txn.promote_staging(workspace, manifest)
        assert set(os.listdir(workspace.staging_fd)) == {"one"}


def test_duplicate_digests_must_agree_on_length(opened_store):
    with opened_store.create_workspace("tx1") as workspace:
        stage(workspace, "one", b"first")
        stage(workspace, "two", b"first")
        manifest = (
            StagedBlob("one", digest_of(b"first"), 5),
            StagedBlob("two", digest_of(b"first"), 4),
        )
        with pytest.raises(ProtocolError), opened_store.transaction() as txn:
            txn.promote_staging(workspace, manifest)
        assert set(os.listdir(workspace.staging_fd)) == {"one", "two"}


def test_duplicate_digests_with_the_same_length_are_promoted_once(opened_store):
    with opened_store.create_workspace("tx1") as workspace:
        stage(workspace, "one", b"first")
        stage(workspace, "two", b"first")
        with opened_store.transaction() as txn:
            txn.promote_staging(
                workspace, _manifest(("one", b"first"), ("two", b"first"))
            )
            txn.insert_record("tx1", spec_referencing(b"first"))


def test_the_staging_set_must_equal_the_manifest(opened_store):
    with opened_store.create_workspace("tx1") as workspace:
        stage(workspace, "one", b"first")
        stage(workspace, "two", b"second")
        with pytest.raises(ProtocolError) as caught, opened_store.transaction() as txn:
            txn.promote_staging(workspace, _manifest(("one", b"first")))
        assert "two" in str(caught.value)
        assert set(os.listdir(workspace.staging_fd)) == {"one", "two"}


@pytest.mark.parametrize("corrupt", ["length", "content", "kind", "symlink"])
def test_every_staged_source_is_verified_before_anything_moves(opened_store, corrupt):
    with opened_store.create_workspace("tx1") as workspace:
        stage(workspace, "first", b"valid")
        if corrupt == "kind":
            os.mkdir("last", dir_fd=workspace.staging_fd)
        elif corrupt == "symlink":
            os.symlink("elsewhere", "last", dir_fd=workspace.staging_fd)
        else:
            stage(workspace, "last", b"wrong")
        declared = b"wrong" if corrupt == "length" else b"right"
        length = 99 if corrupt == "length" else len(declared)
        manifest = (
            *_manifest(("first", b"valid")),
            StagedBlob("last", digest_of(declared), length),
        )
        with pytest.raises(MetadataStoreInvalid), opened_store.transaction() as txn:
            txn.promote_staging(workspace, manifest)
        assert set(os.listdir(workspace.staging_fd)) == {"first", "last"}


def test_a_matching_indexed_destination_unlinks_the_source(opened_store, promoted_blob):
    store, digest, content = promoted_blob
    with store.create_workspace("tx2") as workspace:
        stage(workspace, "again", content)
        with store.transaction() as txn:
            txn.promote_staging(workspace, (StagedBlob("again", digest, len(content)),))
            txn.insert_record("tx2", spec_referencing(content))


def test_a_matching_orphan_destination_unlinks_the_source(opened_store, store_binding):
    content = b"good"
    digest = digest_of(content)
    with child_dir(store_binding.metadata_root_fd, "blobs/sha256") as blobs_fd:
        fd = os.open(digest_to_leaf(digest), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600,
                     dir_fd=blobs_fd)
        os.write(fd, content)
        os.close(fd)
    with opened_store.create_workspace("tx1") as workspace:
        stage(workspace, "one", content)
        with opened_store.transaction() as txn:
            txn.promote_staging(workspace, (StagedBlob("one", digest, len(content)),))
            txn.insert_record("tx1", spec_referencing(content))


@pytest.mark.parametrize("kind", ["content", "symlink"])
def test_a_mismatching_orphan_destination_preserves_the_source(
    opened_store, store_binding, kind
):
    content = b"good"
    digest = digest_of(content)
    with child_dir(store_binding.metadata_root_fd, "blobs/sha256") as blobs_fd:
        leaf = digest_to_leaf(digest)
        if kind == "symlink":
            os.symlink("elsewhere", leaf, dir_fd=blobs_fd)
        else:
            fd = os.open(leaf, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600, dir_fd=blobs_fd)
            os.write(fd, b"bad!")
            os.close(fd)
    with opened_store.create_workspace("tx1") as workspace:
        stage(workspace, "one", content)
        with pytest.raises(MetadataStoreInvalid), opened_store.transaction() as txn:
            txn.promote_staging(workspace, (StagedBlob("one", digest, len(content)),))
        assert os.listdir(workspace.staging_fd) == ["one"]


@pytest.mark.parametrize("corrupt", ["missing", "length", "content", "symlink"])
def test_an_indexed_leaf_is_fully_verified_during_preflight(
    opened_store, store_binding, promoted_blob, corrupt
):
    store, digest, content = promoted_blob
    with child_dir(store_binding.metadata_root_fd, "blobs/sha256") as blobs_fd:
        leaf = digest_to_leaf(digest)
        os.unlink(leaf, dir_fd=blobs_fd)
        if corrupt == "symlink":
            os.symlink("elsewhere", leaf, dir_fd=blobs_fd)
        elif corrupt != "missing":
            fd = os.open(leaf, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600, dir_fd=blobs_fd)
            replacement = b"x" if corrupt == "length" else bytes(len(content))
            os.write(fd, replacement)
            os.close(fd)
    with store.create_workspace("tx9") as workspace:
        stage(workspace, "again", content)
        with pytest.raises(MetadataStoreInvalid), store.transaction() as txn:
            txn.promote_staging(workspace, (StagedBlob("again", digest, len(content)),))
        assert os.listdir(workspace.staging_fd) == ["again"]


def test_an_indexed_row_disagreeing_with_verified_content_refuses(
    opened_store, store_binding, promoted_blob
):
    store, digest, content = promoted_blob
    raw = raw_connect(store_binding)
    try:
        raw.execute("UPDATE blob SET byte_len = 999 WHERE digest = ?", (digest,))
    finally:
        raw.close()
    with store.create_workspace("tx9") as workspace:
        stage(workspace, "again", content)
        with pytest.raises(MetadataStoreInvalid) as caught, store.transaction() as txn:
            txn.promote_staging(workspace, (StagedBlob("again", digest, len(content)),))
        assert "stored row" in str(caught.value)
        assert os.listdir(workspace.staging_fd) == ["again"]


def test_a_failed_second_transfer_spends_staging_after_the_first_rename(
    opened_store, store_binding, monkeypatch
):
    backend = store_binding.backend
    real = backend.transfer_noclobber
    calls = 0

    def fail_second(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected")
        real(*args)

    monkeypatch.setattr(backend, "transfer_noclobber", fail_second)
    with opened_store.create_workspace("tx1") as workspace:
        stage(workspace, "one", b"first")
        stage(workspace, "two", b"second")
        with pytest.raises(OSError), opened_store.transaction() as txn:
            txn.promote_staging(workspace, _manifest(("one", b"first"), ("two", b"second")))
        with pytest.raises(ProtocolError) as caught:
            _ = workspace.staging_fd
        assert "spent" in str(caught.value)


def test_a_failed_second_transfer_spends_staging_after_an_eexist_unlink(
    opened_store, store_binding, monkeypatch
):
    first = b"first"
    digest = digest_of(first)
    with child_dir(store_binding.metadata_root_fd, "blobs/sha256") as blobs_fd:
        fd = os.open(digest_to_leaf(digest), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600,
                     dir_fd=blobs_fd)
        os.write(fd, first)
        os.close(fd)
    backend = store_binding.backend
    real = backend.transfer_noclobber
    calls = 0

    def fail_second(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected")
        real(*args)

    monkeypatch.setattr(backend, "transfer_noclobber", fail_second)
    with opened_store.create_workspace("tx1") as workspace:
        stage(workspace, "one", first)
        stage(workspace, "two", b"second")
        with pytest.raises(OSError), opened_store.transaction() as txn:
            txn.promote_staging(workspace, _manifest(("one", first), ("two", b"second")))
        with pytest.raises(ProtocolError):
            _ = workspace.staging_fd


@pytest.mark.parametrize("release", RELEASES, ids=("closed_binding", "released_lock"))
def test_each_transfer_has_a_late_gate_after_an_earlier_mutation(
    store_on, monkeypatch, release
):
    with store_on() as binding, metadata_root_snapshot(binding) as root:
        store = open_store(binding)
        workspace = store.create_workspace("tx1")
        stage(workspace, "one", b"first")
        stage(workspace, "two", b"second")
        backend = binding.backend
        real = backend.transfer_noclobber
        calls = 0

        def release_after_first(*args):
            nonlocal calls
            real(*args)
            calls += 1
            if calls == 1:
                release(binding)

        monkeypatch.setattr(backend, "transfer_noclobber", release_after_first)
        with pytest.raises(ProtocolError) as caught, store.transaction() as txn:
            txn.promote_staging(workspace, _manifest(("one", b"first"), ("two", b"second")))
        assert ("closed" if release.__name__ == "close_binding" else "lock") in str(caught.value)
        assert calls == 1
        with child_dir(root, "staging") as staging, child_dir(staging, "tx1") as tx_staging:
            assert os.listdir(tx_staging) == ["two"]
        store.close()


def test_eexist_source_unlink_has_its_own_late_gate(
    store_on, monkeypatch
):
    with store_on() as binding, metadata_root_snapshot(binding) as root:
        store = open_store(binding)
        workspace = store.create_workspace("tx1")
        stage(workspace, "one", b"first")
        digest = digest_of(b"first")
        with child_dir(root, "blobs/sha256") as blobs_fd:
            fd = os.open(digest_to_leaf(digest), os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                         0o600, dir_fd=blobs_fd)
            os.write(fd, b"first")
            os.close(fd)
        backend = binding.backend
        real = backend.transfer_noclobber

        def release_on_eexist(*args):
            try:
                real(*args)
            except FileExistsError:
                release_lock(binding)
                raise

        monkeypatch.setattr(backend, "transfer_noclobber", release_on_eexist)
        with pytest.raises(ProtocolError) as caught, store.transaction() as txn:
            txn.promote_staging(workspace, _manifest(("one", b"first")))
        assert "lock" in str(caught.value)
        with child_dir(root, "staging") as staging, child_dir(staging, "tx1") as tx_staging:
            assert os.listdir(tx_staging) == ["one"]
        store.close()


def test_flushes_continue_on_held_descriptors_then_rmdir_has_a_late_gate(
    store_on, monkeypatch
):
    with store_on() as binding, metadata_root_snapshot(binding) as root:
        store = open_store(binding)
        workspace = store.create_workspace("tx1")
        stage(workspace, "one", b"first")
        backend = binding.backend
        real_transfer = backend.transfer_noclobber
        real_flush = backend.flush_directory
        flushes: list[int] = []

        def release_after_transfer(*args):
            real_transfer(*args)
            release_lock(binding)

        def record_flush(fd):
            flushes.append(os.fstat(fd).st_ino)
            real_flush(fd)

        monkeypatch.setattr(backend, "transfer_noclobber", release_after_transfer)
        monkeypatch.setattr(backend, "flush_directory", record_flush)
        with pytest.raises(ProtocolError), store.transaction() as txn:
            txn.promote_staging(workspace, _manifest(("one", b"first")))
        assert len(flushes) == 2
        with child_dir(root, "staging") as staging:
            assert "tx1" in os.listdir(staging)
        store.close()


def test_successful_promotion_flushes_blob_staging_and_staging_parent_in_order(
    opened_store, store_binding, monkeypatch
):
    workspace = opened_store.create_workspace("tx1")
    stage(workspace, "one", b"first")
    with child_dir(store_binding.metadata_root_fd, "blobs/sha256") as blobs_fd:
        blobs_inode = os.fstat(blobs_fd).st_ino
    staging_inode = os.fstat(workspace.staging_fd).st_ino
    with child_dir(store_binding.metadata_root_fd, "staging") as staging_fd:
        parent_inode = os.fstat(staging_fd).st_ino
    backend = store_binding.backend
    real = backend.flush_directory
    flushes: list[int] = []

    def record(fd):
        flushes.append(os.fstat(fd).st_ino)
        real(fd)

    monkeypatch.setattr(backend, "flush_directory", record)
    with opened_store.transaction() as txn:
        txn.promote_staging(workspace, _manifest(("one", b"first")))
        txn.insert_record("tx1", spec_referencing(b"first"))
    assert flushes == [blobs_inode, staging_inode, parent_inode]


def test_promoting_without_a_record_rolls_back_index_but_leaves_the_orphan(
    opened_store, store_binding
):
    with opened_store.create_workspace("tx1") as workspace:
        stage(workspace, "one", b"first")
        with pytest.raises(ProtocolError), opened_store.transaction() as txn:
            txn.promote_staging(workspace, _manifest(("one", b"first")))
    raw = raw_connect(store_binding)
    try:
        assert raw.execute("SELECT count(*) FROM blob").fetchone() == (0,)
    finally:
        raw.close()
    with child_dir(store_binding.metadata_root_fd, "blobs/sha256") as blobs_fd:
        assert os.listdir(blobs_fd) == [digest_to_leaf(digest_of(b"first"))]


def test_promotion_refuses_a_workspace_from_another_store(store_binding, opened_store):
    other = open_store(store_binding)
    try:
        workspace = other.create_workspace("tx1")
        stage(workspace, "one", b"first")
        with pytest.raises(ProtocolError) as caught, opened_store.transaction() as txn:
            txn.promote_staging(workspace, _manifest(("one", b"first")))
        assert "different Store" in str(caught.value)
    finally:
        other.close()


def test_promotion_refuses_a_value_that_is_not_exactly_workspace(opened_store):
    with pytest.raises(ProtocolError) as caught, opened_store.transaction() as txn:
        txn.promote_staging(object(), ())
    assert "expected exactly Workspace" in str(caught.value)


def test_promotion_refuses_a_closed_workspace_at_its_staging_anchor(opened_store):
    workspace = opened_store.create_workspace("tx1")
    workspace.close()
    with pytest.raises(ProtocolError) as caught, opened_store.transaction() as txn:
        txn.promote_staging(workspace, ())
    assert "workspace is closed" in str(caught.value)


def test_a_retained_transaction_cannot_promote_into_a_later_transaction(opened_store):
    with opened_store.transaction() as stale:
        pass
    with (
        opened_store.create_workspace("tx1") as workspace,
        opened_store.transaction(),
        pytest.raises(ProtocolError) as caught,
    ):
        stale.promote_staging(workspace, ())
    assert "spent" in str(caught.value)


def test_promotion_is_only_a_transaction_operation(opened_store):
    assert not hasattr(opened_store, "promote_staging")
    assert not hasattr(opened_store, "insert_blobs")


def _promote_orphan(store, content: bytes = b"orphaned") -> str:
    digest = digest_of(content)
    with store.create_workspace("tx1") as workspace:
        stage(workspace, "one", content)
        with pytest.raises(ProtocolError) as caught, store.transaction() as txn:
            txn.promote_staging(workspace, _manifest(("one", content)))
    assert digest in str(caught.value)
    assert "does not reference" in str(caught.value)
    return digest


def test_a_promoted_orphan_is_listed_and_removed(opened_store, store_binding):
    digest = _promote_orphan(opened_store)
    assert opened_store.list_unindexed_blobs() == (digest,)
    opened_store.remove_unindexed_blob(digest)
    assert opened_store.list_unindexed_blobs() == ()
    with child_dir(store_binding.metadata_root_fd, "blobs/sha256") as blobs_fd:
        assert os.listdir(blobs_fd) == []


def test_enumeration_is_one_deferred_read_transaction(opened_store):
    digest = _promote_orphan(opened_store)
    statements: list[str] = []
    opened_store._connection.set_trace_callback(statements.append)
    try:
        assert opened_store.list_unindexed_blobs() == (digest,)
    finally:
        opened_store._connection.set_trace_callback(None)
    assert statements[0] == "BEGIN"
    assert statements[-1] == "ROLLBACK"
    assert statements.count("BEGIN") == 1
    assert "BEGIN IMMEDIATE" not in statements


def test_removal_rechecks_in_one_immediate_transaction(opened_store):
    digest = _promote_orphan(opened_store)
    statements: list[str] = []
    opened_store._connection.set_trace_callback(statements.append)
    try:
        opened_store.remove_unindexed_blob(digest)
    finally:
        opened_store._connection.set_trace_callback(None)
    assert statements[0] == "BEGIN IMMEDIATE"
    assert statements[-1] == "COMMIT"
    assert statements.count("BEGIN IMMEDIATE") == 1


def test_an_indexed_digest_is_refused_and_still_exists(promoted_blob):
    store, digest, _content = promoted_blob
    assert digest not in store.list_unindexed_blobs()
    with pytest.raises(ProtocolError) as caught:
        store.remove_unindexed_blob(digest)
    assert digest in str(caught.value)
    assert "indexed" in str(caught.value)
    os.close(store.open_blob(digest))


def test_removing_an_already_absent_leaf_raises_protocol_error(opened_store):
    digest = f"sha256:{HEX}"
    with pytest.raises(ProtocolError) as caught:
        opened_store.remove_unindexed_blob(digest)
    assert digest in str(caught.value)
    assert "stale" in str(caught.value)


@pytest.mark.parametrize(
    ("kind", "message"),
    [
        ("symlink", "is a symlink"),
        ("directory", "not a regular file"),
        ("fifo", "not a regular file"),
        ("socket", "not a regular file"),
        ("wrong_content", "hashes to sha256:"),
    ],
)
def test_an_unverifiable_orphan_is_preserved(opened_store, store_binding, kind, message):
    digest = digest_of(b"pretend")
    leaf = digest_to_leaf(digest)
    with child_dir(store_binding.metadata_root_fd, "blobs/sha256") as blobs_fd:
        before = open_descriptor_count()
        unix_socket = None
        try:
            if kind == "symlink":
                os.symlink("/etc/passwd", leaf, dir_fd=blobs_fd)
            elif kind == "directory":
                os.mkdir(leaf, dir_fd=blobs_fd)
            elif kind == "fifo":
                os.mkfifo(leaf, 0o600, dir_fd=blobs_fd)
            elif kind == "socket":
                unix_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                unix_socket.bind(f"/proc/self/fd/{blobs_fd}/{leaf}")
            else:
                fd = os.open(
                    leaf,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                    dir_fd=blobs_fd,
                )
                try:
                    os.write(fd, b"different")
                finally:
                    os.close(fd)
            with pytest.raises(MetadataStoreInvalid) as caught:
                opened_store.remove_unindexed_blob(digest)
            assert digest in str(caught.value)
            assert message in str(caught.value)
            assert leaf in os.listdir(blobs_fd)
        finally:
            if unix_socket is not None:
                unix_socket.close()
        assert open_descriptor_count() == before
        assert leaf in os.listdir(blobs_fd)


@pytest.mark.parametrize(
    ("kind", "message"),
    [
        ("symlink", "is a symlink"),
        ("directory", "not a regular file"),
        ("fifo", "not a regular file"),
        ("socket", "not a regular file"),
        ("wrong_content", "hashes to sha256:"),
    ],
)
def test_enumeration_preserves_and_refuses_an_unverifiable_leaf(
    opened_store, store_binding, kind, message
):
    digest = digest_of(b"pretend")
    leaf = digest_to_leaf(digest)
    with child_dir(store_binding.metadata_root_fd, "blobs/sha256") as blobs_fd:
        before = open_descriptor_count()
        unix_socket = None
        try:
            if kind == "symlink":
                os.symlink("elsewhere", leaf, dir_fd=blobs_fd)
            elif kind == "directory":
                os.mkdir(leaf, dir_fd=blobs_fd)
            elif kind == "fifo":
                os.mkfifo(leaf, 0o600, dir_fd=blobs_fd)
            elif kind == "socket":
                unix_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                unix_socket.bind(f"/proc/self/fd/{blobs_fd}/{leaf}")
            else:
                fd = os.open(
                    leaf,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                    dir_fd=blobs_fd,
                )
                try:
                    os.write(fd, b"different")
                finally:
                    os.close(fd)
            with pytest.raises(MetadataStoreInvalid) as caught:
                opened_store.list_unindexed_blobs()
            assert digest in str(caught.value)
            assert message in str(caught.value)
            assert leaf in os.listdir(blobs_fd)
        finally:
            if unix_socket is not None:
                unix_socket.close()
        assert open_descriptor_count() == before
        assert leaf in os.listdir(blobs_fd)


def test_a_leaf_that_is_not_well_formed_hex_refuses_the_enumeration(
    opened_store, store_binding
):
    with child_dir(store_binding.metadata_root_fd, "blobs/sha256") as blobs_fd:
        fd = os.open("tmp.part", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600, dir_fd=blobs_fd)
        os.close(fd)
        with pytest.raises(MetadataStoreInvalid) as caught:
            opened_store.list_unindexed_blobs()
        assert "tmp.part" in str(caught.value)
        assert "fixed-width hex" in str(caught.value)
        assert "tmp.part" in os.listdir(blobs_fd)


@pytest.mark.parametrize("bad", [3, None, b"x"])
def test_reclamation_requires_an_exact_string_digest(opened_store, bad):
    with pytest.raises(ProtocolError) as caught:
        opened_store.remove_unindexed_blob(bad)
    assert "must be exactly str" in str(caught.value)


def test_removal_is_refused_inside_a_write_transaction(opened_store):
    with opened_store.transaction() as txn:
        txn.insert_record("tx1", one_effect_spec())
        with pytest.raises(ProtocolError) as caught:
            opened_store.remove_unindexed_blob(f"sha256:{HEX}")
        assert "write transaction" in str(caught.value)


def test_a_reclaimer_does_not_race_another_stores_promotion(store_on):
    import threading

    content = b"contested"
    digest = digest_of(content)
    with store_on() as binding, open_store(binding) as writer:
        with writer.create_workspace("tx1") as workspace:
            stage(workspace, "one", content)
            ready = threading.Event()
            started = threading.Event()
            enumerated = threading.Event()
            attempted_remove = threading.Event()
            outcome: dict[str, object] = {}

            def reclaim():
                try:
                    with open_store(binding) as reclaimer:
                        reclaimer._connection.set_trace_callback(
                            lambda statement: attempted_remove.set()
                            if statement == "BEGIN IMMEDIATE"
                            else None
                        )
                        ready.set()
                        if not started.wait(5):
                            raise AssertionError("writer never armed the interleaving")
                        candidates = reclaimer.list_unindexed_blobs()
                        outcome["candidates"] = candidates
                        enumerated.set()
                        for candidate in candidates:
                            reclaimer.remove_unindexed_blob(candidate)
                        outcome["error"] = None
                except Exception as caught:  # noqa: BLE001 -- recorded, then asserted
                    outcome["error"] = caught

            thread = threading.Thread(target=reclaim)
            thread.start()
            assert ready.wait(5)
            with writer.transaction() as txn:
                txn.promote_staging(
                    workspace,
                    (StagedBlob(name="one", digest=digest, byte_len=len(content)),),
                )
                txn.insert_record("tx1", spec_referencing(content))
                started.set()
                assert enumerated.wait(5)
                assert attempted_remove.wait(5)
            thread.join(10)
            assert not thread.is_alive()
        assert outcome.get("candidates") == (digest,)
        assert isinstance(outcome.get("error"), ProtocolError)
        assert digest in str(outcome["error"])
        assert "indexed" in str(outcome["error"])
        with open_store(binding) as reader:
            record = reader.read_record("tx1")
            assert record is not None
            os.close(reader.open_blob(digest))


def test_a_failed_orphan_verification_rolls_back_the_writer_transaction(
    opened_store, store_binding
):
    digest = digest_of(b"pretend")
    with child_dir(store_binding.metadata_root_fd, "blobs/sha256") as blobs_fd:
        fd = os.open(
            digest_to_leaf(digest),
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
            dir_fd=blobs_fd,
        )
        os.write(fd, b"different")
        os.close(fd)
    statements: list[str] = []
    opened_store._connection.set_trace_callback(statements.append)
    try:
        with pytest.raises(MetadataStoreInvalid) as caught:
            opened_store.remove_unindexed_blob(digest)
    finally:
        opened_store._connection.set_trace_callback(None)
    assert digest in str(caught.value)
    assert statements[-1] == "ROLLBACK"
    assert not opened_store._connection.in_transaction


def test_a_failed_reclamation_commit_rolls_back_the_sqlite_transaction(
    opened_store, monkeypatch
):
    from tests.store_support import CommitFails

    digest = _promote_orphan(opened_store)
    proxy = CommitFails(opened_store._connection)
    monkeypatch.setattr(opened_store, "_connection", proxy)
    with pytest.raises(sqlite3.OperationalError) as caught:
        opened_store.remove_unindexed_blob(digest)
    assert "disk I/O error" in str(caught.value)
    assert not proxy.in_transaction
