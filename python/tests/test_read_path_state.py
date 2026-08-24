"""`read_path_state` — the lease-held, lifecycle-honoring single-path read
(2026-08-24 holdings read/evidence design §3–§4)."""

from __future__ import annotations

import contextlib
import errno
import hashlib
from pathlib import Path

import pytest

from atoms.chain.inspect import STAGING_LEAF
from atoms.coordinator.commands import (
    LifecycleState,
    NotAttemptedReason,
    PathObserved,
    ReadNotAttempted,
    ReadUnestablished,
    UnestablishedReason,
    read_path_state,
)
from atoms.core.errors import CapabilityUnavailable, ProtocolError
from atoms.core.fingerprint import (
    ABSENT,
    DirectoryState,
    FileState,
    SymlinkState,
)
from atoms.core.scratch import CHAIN_LEAF
from tests.test_coordinator_commands import _enable_commands, _register
from tests.test_lifecycle_commands import (
    GENESIS,
    _cold_copy,
    _copy_targets,
    _grant,
    _replicate,
    _seed_source,
)

PAYLOAD = b"holdings payload bytes"


def _write_payload(project_root: str) -> None:
    directory = Path(project_root) / "d"
    directory.mkdir()
    (directory / "f.bin").write_bytes(PAYLOAD)
    (directory / "f.bin").chmod(0o640)


def test_read_path_state_observes_a_file_on_a_writable_root(
    coordinator_on, monkeypatch
) -> None:
    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    backend, project_root, metadata_root, storage = ingredients
    _register(ingredients, b"root", ())
    _write_payload(project_root)

    result = read_path_state(backend, project_root, metadata_root, storage, "d/f.bin")

    assert result == PathObserved(
        FileState(
            content_hash="sha256:" + hashlib.sha256(PAYLOAD).hexdigest(),
            mode=0o640,
            byte_len=len(PAYLOAD),
        )
    )


def test_read_path_state_traversal_answers_are_the_capture_models_own(
    coordinator_on, monkeypatch
) -> None:
    """Absent path, absent-by-file-ancestor, absent-by-symlink-ancestor; a
    final symlink and a final directory observed unfollowed and unhashed —
    the reused `_capture_path` vocabulary, pinned at this command's surface."""
    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    backend, project_root, metadata_root, storage = ingredients
    _register(ingredients, b"root", ())
    root = Path(project_root)
    (root / "d").mkdir(mode=0o750)
    (root / "d" / "f.bin").write_bytes(PAYLOAD)
    (root / "plain").write_bytes(b"not a directory")
    (root / "link").symlink_to("d")

    def read(path: str):
        return read_path_state(backend, project_root, metadata_root, storage, path)

    assert read("missing") == PathObserved(ABSENT)
    assert read("plain/below") == PathObserved(ABSENT)
    assert read("link/f.bin") == PathObserved(ABSENT)
    assert read("link") == PathObserved(SymlinkState(target="d", mode=0o777))
    assert read("d") == PathObserved(DirectoryState(mode=0o750))


def test_read_path_state_refuses_an_inadmissible_path_before_any_read(
    coordinator_on, monkeypatch
) -> None:
    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    backend, project_root, metadata_root, storage = ingredients

    for path in ("/absolute", "../escape", "", "a//b", "a/./b"):
        result = read_path_state(backend, project_root, metadata_root, storage, path)
        assert type(result) is ReadNotAttempted, path
        assert result.reason is NotAttemptedReason.PATH_GRAMMAR, path


def test_read_path_state_refuses_a_metadata_less_root(
    coordinator_on, monkeypatch
) -> None:
    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    backend, project_root, metadata_root, storage = ingredients

    result = read_path_state(backend, project_root, metadata_root, storage, "d/f.bin")

    assert type(result) is ReadNotAttempted
    assert result.reason is NotAttemptedReason.LIFECYCLE_STATE
    assert result.lifecycle_state is LifecycleState.METADATA_LESS


def test_read_path_state_refuses_an_unserviceable_replica(
    coordinator_on, monkeypatch
) -> None:
    ingredients = coordinator_on()
    _seed_source(ingredients, monkeypatch)
    backend, _project_root, _metadata_root, storage = ingredients
    dest_root, dest_metadata = _copy_targets(ingredients, "read")
    _replicate(ingredients, dest_root, dest_metadata)

    result = read_path_state(
        backend, dest_root, dest_metadata, storage, "data/payload.bin"
    )

    assert type(result) is ReadNotAttempted
    assert result.reason is NotAttemptedReason.LIFECYCLE_STATE
    assert result.lifecycle_state is LifecycleState.READ_ONLY_UNSERVICEABLE


def test_read_path_state_refuses_a_binding_mismatched_root(
    coordinator_on, monkeypatch
) -> None:
    from atoms.coordinator import lifecycle

    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    backend, project_root, metadata_root, storage = ingredients
    _register(ingredients, GENESIS, ())
    monkeypatch.setattr(
        lifecycle, "_read_machine_identity", lambda: "other-machine-identity"
    )

    result = read_path_state(backend, project_root, metadata_root, storage, "d/f.bin")

    assert type(result) is ReadNotAttempted
    assert result.reason is NotAttemptedReason.LIFECYCLE_STATE
    assert result.lifecycle_state is LifecycleState.BINDING_MISMATCHED


def test_read_path_state_refuses_a_v2_store_by_its_lifecycle_state(
    coordinator_on, monkeypatch
) -> None:
    from tests.lifecycle_support import fabricate_v2_store

    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    backend, project_root, metadata_root, storage = ingredients
    fabricate_v2_store(metadata_root)

    result = read_path_state(backend, project_root, metadata_root, storage, "d/f.bin")

    assert type(result) is ReadNotAttempted
    assert result.reason is NotAttemptedReason.LIFECYCLE_STATE
    assert result.lifecycle_state is LifecycleState.READ_ONLY_UNSERVICEABLE


def test_read_path_state_refuses_a_serviceable_root_missing_its_chain(
    coordinator_on, monkeypatch
) -> None:
    import shutil

    ingredients = coordinator_on()
    _seed_source(ingredients, monkeypatch)
    backend, _project_root, _metadata_root, storage = ingredients
    dest_root, dest_metadata = _cold_copy(ingredients, monkeypatch, "nochain")
    _grant(backend, dest_root, dest_metadata, storage)
    shutil.rmtree(Path(dest_root) / CHAIN_LEAF)

    result = read_path_state(
        backend, dest_root, dest_metadata, storage, "data/payload.bin"
    )

    assert type(result) is ReadNotAttempted
    assert result.reason is NotAttemptedReason.QUIESCENCE


def test_read_path_state_refuses_a_serviceable_root_with_a_staging_survivor(
    coordinator_on, monkeypatch
) -> None:
    ingredients = coordinator_on()
    _seed_source(ingredients, monkeypatch)
    backend, _project_root, _metadata_root, storage = ingredients
    dest_root, dest_metadata = _cold_copy(ingredients, monkeypatch, "survivor")
    _grant(backend, dest_root, dest_metadata, storage)
    (Path(dest_root) / CHAIN_LEAF / STAGING_LEAF).write_bytes(b"interrupted append")

    result = read_path_state(
        backend, dest_root, dest_metadata, storage, "data/payload.bin"
    )

    assert type(result) is ReadNotAttempted
    assert result.reason is NotAttemptedReason.QUIESCENCE


def _doctored_view(monkeypatch, view) -> None:
    from atoms.coordinator import commands

    monkeypatch.setattr(commands, "_lifecycle_view", lambda *_a, **_k: view)


def test_read_path_state_refuses_a_serviceable_root_with_an_incomplete_operation(
    coordinator_on, monkeypatch
) -> None:
    from atoms.coordinator.lifecycle import CarrierView
    from atoms.store.records import RootOperationRow

    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    backend, project_root, metadata_root, storage = ingredients
    incomplete = RootOperationRow(
        operation_id="op-1",
        kind="replicate",
        phase="copying",
        request_json="{}",
        request_hash="0" * 64,
        source_snapshot_json=None,
        destination_snapshot_json=None,
        genesis_digest=None,
    )
    _doctored_view(
        monkeypatch,
        CarrierView(LifecycleState.READ_ONLY_SERVICEABLE, None, incomplete, 3),
    )

    result = read_path_state(backend, project_root, metadata_root, storage, "d/f.bin")

    assert type(result) is ReadNotAttempted
    assert result.reason is NotAttemptedReason.QUIESCENCE


def test_read_path_state_refuses_a_serviceable_root_with_an_active_transaction(
    coordinator_on, monkeypatch
) -> None:
    from atoms.coordinator.lifecycle import CarrierView

    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    backend, project_root, metadata_root, storage = ingredients
    _doctored_view(
        monkeypatch,
        CarrierView(
            LifecycleState.READ_ONLY_SERVICEABLE, None, None, 3, active_txid="tx-1"
        ),
    )

    result = read_path_state(backend, project_root, metadata_root, storage, "d/f.bin")

    assert type(result) is ReadNotAttempted
    assert result.reason is NotAttemptedReason.QUIESCENCE


def test_a_live_transaction_on_an_ungranted_root_raises_the_alarm(
    coordinator_on, monkeypatch
) -> None:
    """read_chain's own alarm, kept: a live transaction record on a root whose
    lifecycle carries no grant is never a routine read outcome."""
    from atoms.chain.errors import ChainStateInvalid
    from atoms.coordinator.lifecycle import CarrierView

    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    backend, project_root, metadata_root, storage = ingredients
    _doctored_view(
        monkeypatch,
        CarrierView(LifecycleState.METADATA_LESS, None, None, 0, active_txid="tx-1"),
    )

    with pytest.raises(ChainStateInvalid):
        read_path_state(backend, project_root, metadata_root, storage, "d/f.bin")


def test_read_path_state_reports_root_unresolvable_when_the_boundary_vanishes(
    coordinator_on, monkeypatch
) -> None:
    """The race the table scopes `root-unresolvable` to: classification said
    writable, and the boundary is gone at acquisition."""
    from atoms.coordinator import commands

    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    backend, project_root, metadata_root, storage = ingredients
    _register(ingredients, b"root", ())

    @contextlib.contextmanager
    def vanished(*_args, **_kwargs):
        raise FileNotFoundError(errno.ENOENT, "root vanished at acquisition")
        yield

    monkeypatch.setattr(commands, "_recovery_lease", vanished)

    result = read_path_state(backend, project_root, metadata_root, storage, "d/f.bin")

    assert type(result) is ReadNotAttempted
    assert result.reason is NotAttemptedReason.ROOT_UNRESOLVABLE


def test_a_routine_failure_after_the_observation_begins_is_unestablished(
    coordinator_on, monkeypatch
) -> None:
    from atoms.coordinator import commands

    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    backend, project_root, metadata_root, storage = ingredients
    _register(ingredients, b"root", ())

    def torn(*_args, **_kwargs):
        raise OSError(errno.EIO, "device error mid-hash")

    monkeypatch.setattr(commands, "_capture_path", torn)

    result = read_path_state(backend, project_root, metadata_root, storage, "d/f.bin")

    assert type(result) is ReadUnestablished
    assert result.reason is UnestablishedReason.IO_FAILURE


@pytest.mark.parametrize(
    ("code", "expected"),
    [(errno.EOPNOTSUPP, CapabilityUnavailable), (errno.EBADF, ProtocolError)],
)
def test_a_non_routine_failure_propagates_whatever_its_position(
    coordinator_on, monkeypatch, code, expected
) -> None:
    """The classification applied where `translated_lookup` does not already
    wrap: a raw unsupported-capability or closed-descriptor errno escaping the
    ancestor walk propagates as its named class, never `ReadUnestablished`."""
    from atoms.coordinator import commands

    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    backend, project_root, metadata_root, storage = ingredients
    _register(ingredients, b"root", ())

    def raw(*_args, **_kwargs):
        raise OSError(code, "raw errno from the ancestor walk")

    monkeypatch.setattr(commands, "_capture_path", raw)

    with pytest.raises(expected):
        read_path_state(backend, project_root, metadata_root, storage, "d/f.bin")


def test_read_path_state_serializes_behind_the_writable_recovery_lease(
    coordinator_on, monkeypatch
) -> None:
    """The coherence claim's mechanism: a reader on a writable root waits for
    the held boundary and answers only after it releases."""
    import threading

    from atoms.coordinator.root import _recovery_lease

    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    backend, project_root, metadata_root, storage = ingredients
    _register(ingredients, b"root", ())
    _write_payload(project_root)
    results: list[object] = []
    done = threading.Event()

    def read() -> None:
        results.append(
            read_path_state(backend, project_root, metadata_root, storage, "d/f.bin")
        )
        done.set()

    with _recovery_lease(backend, project_root, metadata_root, storage):
        reader = threading.Thread(target=read)
        reader.start()
        assert not done.wait(timeout=0.3), "the read completed under a held lease"
    assert done.wait(timeout=30), "the read never completed after release"
    reader.join(timeout=30)
    assert results == [
        PathObserved(
            FileState(
                content_hash="sha256:" + hashlib.sha256(PAYLOAD).hexdigest(),
                mode=0o640,
                byte_len=len(PAYLOAD),
            )
        )
    ]


def test_read_path_state_reads_quiescently_on_a_serviceable_root(
    coordinator_on, monkeypatch
) -> None:
    """A granted cold copy answers with the observed file state, no recovery,
    no writable open (the read_chain non-writable arm's discipline)."""
    ingredients = coordinator_on()
    _seed_source(ingredients, monkeypatch)
    backend, _project_root, _metadata_root, storage = ingredients
    dest_root, dest_metadata = _cold_copy(ingredients, monkeypatch, "serviceable")
    _grant(backend, dest_root, dest_metadata, storage)

    result = read_path_state(
        backend, dest_root, dest_metadata, storage, "data/payload.bin"
    )

    assert result == PathObserved(
        FileState(
            content_hash="sha256:" + hashlib.sha256(b"payload bytes").hexdigest(),
            mode=0o640,
            byte_len=len(b"payload bytes"),
        )
    )
