"""Tier 4 -- blobs over a real ext4 metadata root (design §7.2, §7.3, §8)."""

from __future__ import annotations

import os
import signal

import pytest

from atoms.core.errors import ProtocolError
from atoms.store.blobs import StagedBlob, digest_to_leaf, leaf_to_digest, require_component
from atoms.store.errors import MetadataStoreInvalid
from tests.store_support import child_dir, open_descriptor_count

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
