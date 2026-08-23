"""Root lifecycle: fail-closed writer state, binding, and the state query.

Contract: docs/2026-08-23-root-lifecycle-commands-design.md (approved at
`b1469f4`). Tier 1 here is the writer-state half — the grant carrier, the
host/root binding, the closed five-value union, and the writability gate in
front of every cooperative mutator. The copy commands, serviceability grant,
and migration are the later tiers of this same file.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from pathlib import Path

import pytest

from atoms.chain.errors import PendingUnresolved
from atoms.coordinator.commands import (
    LifecycleState,
    append_intent,
    read_lifecycle_state,
)
from atoms.core.errors import PreconditionRefused
from tests.test_coordinator_commands import _enable_commands, _register

GENESIS = b"lifecycle genesis payload"

_OTHER_MACHINE = "f" * 32


def _read_state(ingredients) -> LifecycleState:
    backend, project_root, metadata_root, storage = ingredients
    return read_lifecycle_state(backend, project_root, metadata_root, storage)


def _interrupt_before_grant(ingredients, monkeypatch) -> None:
    """Fabricate the recorded-operation/durable-genesis/no-grant window.

    The cut is the completion seam itself: everything before the final
    lifecycle/complete transaction has run for real — claim, stamp, baseline,
    genesis, tree proof — so the residue is exactly what a crash there leaves.
    """
    from atoms.coordinator import lifecycle

    real = lifecycle._complete_root_operation

    class Interrupted(BaseException):
        pass

    def cut(*args, **kwargs):
        raise Interrupted("cut before the grant")

    monkeypatch.setattr(lifecycle, "_complete_root_operation", cut)
    with pytest.raises(Interrupted):
        _register(ingredients, GENESIS, ())
    monkeypatch.setattr(lifecycle, "_complete_root_operation", real)


def _metadata_entries(metadata_root: str) -> dict[str, tuple[int, int, str]]:
    """Every entry under the metadata root with size, mtime, and content hash."""
    observed: dict[str, tuple[int, int, str]] = {}
    for directory, directories, files in os.walk(metadata_root):
        directories.sort()
        for name in sorted(files):
            full = Path(directory) / name
            info = full.lstat()
            observed[os.path.relpath(full, metadata_root)] = (
                info.st_size,
                info.st_mtime_ns,
                hashlib.sha256(full.read_bytes()).hexdigest(),
            )
    return observed


def test_fresh_register_root_reads_writable(coordinator_on, monkeypatch) -> None:
    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    digest = _register(ingredients, GENESIS, ())
    assert len(digest) == 64 and set(digest) <= set("0123456789abcdef")
    assert _read_state(ingredients) is LifecycleState.WRITABLE


def test_interrupted_registration_matching_retry_grants(
    coordinator_on, monkeypatch
) -> None:
    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    _interrupt_before_grant(ingredients, monkeypatch)
    assert _read_state(ingredients) is LifecycleState.READ_ONLY_UNSERVICEABLE

    digest = _register(ingredients, GENESIS, ())
    assert _read_state(ingredients) is LifecycleState.WRITABLE
    # The retry completed the recorded operation rather than re-minting: the
    # digest is the interrupted attempt's own durable genesis.
    assert digest == _register(ingredients, GENESIS, ())


def test_bare_reregistration_over_an_existing_genesis_never_grants(
    coordinator_on, monkeypatch
) -> None:
    import shutil

    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    _register(ingredients, GENESIS, ())
    _backend, _project_root, metadata_root, _storage = ingredients
    shutil.rmtree(metadata_root)

    assert _read_state(ingredients) is LifecycleState.METADATA_LESS
    with pytest.raises(
        PreconditionRefused, match="no local initialization operation"
    ):
        _register(ingredients, GENESIS, ())
    assert _read_state(ingredients) is LifecycleState.METADATA_LESS


def test_metadata_less_tree_reads_metadata_less(coordinator_on, monkeypatch) -> None:
    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    assert _read_state(ingredients) is LifecycleState.METADATA_LESS


def test_host_delta_reads_binding_mismatched(coordinator_on, monkeypatch) -> None:
    from atoms.coordinator import lifecycle

    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    _register(ingredients, GENESIS, ())
    monkeypatch.setattr(
        lifecycle, "_read_machine_identity", lambda: _OTHER_MACHINE
    )
    assert _read_state(ingredients) is LifecycleState.BINDING_MISMATCHED


def test_path_delta_reads_binding_mismatched(coordinator_on, monkeypatch) -> None:
    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    _register(ingredients, GENESIS, ())
    backend, project_root, metadata_root, storage = ingredients
    moved = project_root + "-moved"
    os.rename(project_root, moved)
    assert (
        read_lifecycle_state(backend, moved, metadata_root, storage)
        is LifecycleState.BINDING_MISMATCHED
    )


def test_lifecycle_union_has_exactly_five_members() -> None:
    assert {member.value for member in LifecycleState} == {
        "writable",
        "read-only-serviceable",
        "read-only-unserviceable",
        "metadata-less",
        "binding-mismatched",
    }
    assert len(LifecycleState) == 5


def test_nonwritable_mutation_refuses_before_recovery(
    coordinator_on, monkeypatch
) -> None:
    from atoms.coordinator import root as root_module

    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    _interrupt_before_grant(ingredients, monkeypatch)

    recovery_calls: list[str] = []
    monkeypatch.setattr(
        root_module,
        "resolve",
        lambda *args, **kwargs: recovery_calls.append("resolve"),
    )
    monkeypatch.setattr(
        root_module,
        "reclaim_probe_survivors",
        lambda *args, **kwargs: recovery_calls.append("reclaim"),
    )
    backend, project_root, metadata_root, storage = ingredients
    with pytest.raises(
        PreconditionRefused,
        match="read-only-unserviceable.*does not grant writability",
    ):
        append_intent(backend, project_root, metadata_root, storage, b"intent")
    assert recovery_calls == []


def test_writable_pending_root_reaches_pending_unresolved(
    coordinator_on, monkeypatch
) -> None:
    from atoms.coordinator.commands import run_transaction
    from tests.capture_support import DictPayloads, digest_of
    from tests.coordinator_support import AFTER, create_file_spec
    from tests.test_pending_gate import _unsettle

    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    backend, project_root, metadata_root, storage = ingredients
    (Path(project_root) / "d").mkdir()
    _register(ingredients, GENESIS, ())
    run_transaction(
        backend,
        project_root,
        metadata_root,
        storage,
        create_file_spec(),
        DictPayloads({digest_of(AFTER): AFTER}),
    )
    _unsettle(project_root)

    assert _read_state(ingredients) is LifecycleState.WRITABLE
    # The lifecycle gate passes — the grant stands — and the refusal is the
    # chain's own, which is the ordering the gate-precedence rows rely on.
    with pytest.raises(PendingUnresolved):
        append_intent(backend, project_root, metadata_root, storage, b"intent")


def test_read_only_lifecycle_open_cannot_write_sqlite_or_sidecars(
    coordinator_on, monkeypatch
) -> None:
    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    _register(ingredients, GENESIS, ())
    _backend, _project_root, metadata_root, _storage = ingredients

    before = _metadata_entries(metadata_root)
    assert "atoms.db-wal" not in before and "atoms.db-shm" not in before
    assert _read_state(ingredients) is LifecycleState.WRITABLE
    after = _metadata_entries(metadata_root)
    assert after == before


def test_v2_store_reads_read_only_unserviceable(coordinator_on, monkeypatch) -> None:
    """An exact pre-lifecycle store: bookkeeping exists, but no grant does."""
    from tests.lifecycle_support import fabricate_v2_store

    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    _backend, _project_root, metadata_root, _storage = ingredients
    fabricate_v2_store(metadata_root)
    assert _read_state(ingredients) is LifecycleState.READ_ONLY_UNSERVICEABLE


def test_normal_store_open_refuses_v2_and_names_the_migration(
    coordinator_on, monkeypatch
) -> None:
    from tests.lifecycle_support import fabricate_v2_store

    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    backend, project_root, metadata_root, storage = ingredients
    fabricate_v2_store(metadata_root)
    with pytest.raises(
        PreconditionRefused, match="migrate_root_to_lifecycle_v3"
    ):
        _register(ingredients, GENESIS, ())
    # The mutator gate refuses first, in the gate's own words: a v2 store
    # holds bookkeeping but no grant.
    with pytest.raises(
        PreconditionRefused, match="read-only-unserviceable.*does not grant"
    ):
        append_intent(backend, project_root, metadata_root, storage, b"intent")


def test_operation_row_without_lifecycle_row_is_invalid(
    coordinator_on, monkeypatch
) -> None:
    """The atomic pair, broken by hand: a raw write, never a cooperative state."""
    from atoms.store.errors import MetadataStoreInvalid
    from tests.lifecycle_support import fabricate_v3_store

    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    _backend, _project_root, metadata_root, _storage = ingredients
    database = fabricate_v3_store(metadata_root)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO root_operation (singleton, operation_id, kind, phase,"
            " request_json, request_hash) VALUES (0, ?, 'register', 'recorded',"
            " '{}', ?)",
            ("a" * 32, "b" * 64),
        )
    with pytest.raises(MetadataStoreInvalid):
        _read_state(ingredients)


# --- Tier 2: the copy commands, pending seam, grant, and migration ---


def _seed_source(ingredients, monkeypatch) -> str:
    """A registered source with payload spanning the closed vocabulary.

    Returns the source's validated chain tip.
    """
    from atoms.coordinator.commands import append_intent as _intent
    from atoms.coordinator.commands import read_chain

    _enable_commands(ingredients, monkeypatch)
    backend, project_root, metadata_root, storage = ingredients
    root = Path(project_root)
    (root / "data").mkdir(mode=0o750)
    (root / "data" / "payload.bin").write_bytes(b"payload bytes")
    (root / "data" / "payload.bin").chmod(0o640)
    (root / "empty").mkdir(mode=0o700)
    (root / "link").symlink_to("data/payload.bin")
    _register(ingredients, GENESIS, ())
    _intent(backend, project_root, metadata_root, storage, b"seeded intent")
    return read_chain(backend, project_root, metadata_root, storage).tip


def _copy_targets(ingredients, tag: str) -> tuple[str, str]:
    """Fresh sibling destination root/metadata spellings on the same volume."""
    _backend, project_root, _metadata_root, _storage = ingredients
    parent = Path(project_root).parent
    return (
        str(parent / f"{Path(project_root).name}-{tag}-copy"),
        str(parent / f"{Path(project_root).name}-{tag}-copy-metadata"),
    )


def _replicate(ingredients, dest_root: str, dest_metadata: str):
    from atoms.coordinator.commands import replicate_root

    backend, project_root, metadata_root, storage = ingredients
    return replicate_root(
        backend, project_root, metadata_root, dest_root, dest_metadata, storage
    )


def _fork(ingredients, dest_root: str, dest_metadata: str, head: str, **overrides):
    from atoms.coordinator.commands import DestinationOverride, fork_root

    backend, project_root, metadata_root, storage = ingredients
    arguments = {
        "expected_source_head": head,
        "genesis_payload": b"opaque child genesis",
        "surface_paths": ("data/payload.bin", "manifest.json"),
        "dest_overrides": (
            DestinationOverride(
                path="manifest.json", payload=b"child manifest", mode=0o644
            ),
        ),
    }
    arguments.update(overrides)
    return fork_root(
        backend,
        project_root,
        metadata_root,
        dest_root,
        dest_metadata,
        storage,
        **arguments,
    )


def _tree_facts(root: str) -> dict[str, tuple]:
    """Portable identity of one tree: kind, mode, size or target, content hash."""
    facts: dict[str, tuple] = {}
    base = Path(root)
    for directory, directories, files in os.walk(root, followlinks=False):
        directories.sort()
        for name in sorted(directories) + sorted(files):
            full = Path(directory) / name
            relative = str(full.relative_to(base))
            if relative == ".#~root-claim":
                continue
            info = full.lstat()
            import stat as _stat

            if _stat.S_ISLNK(info.st_mode):
                facts[relative] = ("symlink", os.readlink(full))
            elif _stat.S_ISDIR(info.st_mode):
                facts[relative] = ("directory", _stat.S_IMODE(info.st_mode))
            else:
                facts[relative] = (
                    "file",
                    _stat.S_IMODE(info.st_mode),
                    info.st_size,
                    hashlib.sha256(full.read_bytes()).hexdigest(),
                )
    return facts


def _cut(monkeypatch, seam: str):
    """Cut one lifecycle seam; returns the exception type the cut raises."""
    from atoms.coordinator import lifecycle

    real = getattr(lifecycle, seam)

    class Cut(BaseException):
        pass

    def failing(*args, **kwargs):
        raise Cut(f"cut at {seam}")

    failing._cut_of = real
    monkeypatch.setattr(lifecycle, seam, failing)
    return Cut


def _uncut(monkeypatch, seam: str):
    from atoms.coordinator import lifecycle

    current = getattr(lifecycle, seam)
    monkeypatch.setattr(lifecycle, seam, current._cut_of)


def test_replicate_copies_chain_and_payload_byte_identical(
    coordinator_on, monkeypatch
) -> None:
    ingredients = coordinator_on()
    _seed_source(ingredients, monkeypatch)
    _backend, project_root, _metadata_root, _storage = ingredients
    dest_root, dest_metadata = _copy_targets(ingredients, "byte")

    operation_id = _replicate(ingredients, dest_root, dest_metadata)
    assert len(operation_id) == 32
    assert _tree_facts(dest_root) == _tree_facts(project_root)


def test_replica_reads_read_only_unserviceable(coordinator_on, monkeypatch) -> None:
    ingredients = coordinator_on()
    _seed_source(ingredients, monkeypatch)
    backend, _project_root, _metadata_root, storage = ingredients
    dest_root, dest_metadata = _copy_targets(ingredients, "state")

    _replicate(ingredients, dest_root, dest_metadata)
    assert (
        read_lifecycle_state(backend, dest_root, dest_metadata, storage)
        is LifecycleState.READ_ONLY_UNSERVICEABLE
    )


def test_replicate_refuses_an_existing_destination(
    coordinator_on, monkeypatch
) -> None:
    ingredients = coordinator_on()
    _seed_source(ingredients, monkeypatch)
    dest_root, dest_metadata = _copy_targets(ingredients, "occupied")
    Path(dest_root).mkdir()
    (Path(dest_root) / "squatter").write_bytes(b"occupied")

    with pytest.raises(PreconditionRefused, match="no-clobber|occupied|claim"):
        _replicate(ingredients, dest_root, dest_metadata)
    # Nothing was stamped: the squatter tree is untouched and no metadata
    # carrier appeared for it.
    assert sorted(entry.name for entry in Path(dest_root).iterdir()) == ["squatter"]
    assert not Path(dest_metadata).exists()


def test_replicate_different_metadata_root_cannot_claim_destination(
    coordinator_on, monkeypatch
) -> None:
    from atoms.coordinator.commands import RootOperationMismatch

    ingredients = coordinator_on()
    _seed_source(ingredients, monkeypatch)
    dest_root, dest_metadata = _copy_targets(ingredients, "twometa")
    cut = _cut(monkeypatch, "_stamp_copy_destination")
    with pytest.raises(cut):
        _replicate(ingredients, dest_root, dest_metadata)
    _uncut(monkeypatch, "_stamp_copy_destination")

    other_metadata = dest_metadata + "-other"
    with pytest.raises(RootOperationMismatch):
        _replicate(ingredients, dest_root, other_metadata)
    # The original claim still stands and its own retry still converges.
    operation_id = _replicate(ingredients, dest_root, dest_metadata)
    assert len(operation_id) == 32


def test_replicate_interrupted_before_claim_leaves_destination_absent(
    coordinator_on, monkeypatch
) -> None:
    ingredients = coordinator_on()
    _seed_source(ingredients, monkeypatch)
    dest_root, dest_metadata = _copy_targets(ingredients, "preclaim")
    cut = _cut(monkeypatch, "_publish_claimed_destination")
    with pytest.raises(cut):
        _replicate(ingredients, dest_root, dest_metadata)
    assert not Path(dest_root).exists()


def test_replicate_interrupted_before_stamp_is_metadata_less(
    coordinator_on, monkeypatch
) -> None:
    ingredients = coordinator_on()
    _seed_source(ingredients, monkeypatch)
    backend, _project_root, _metadata_root, storage = ingredients
    dest_root, dest_metadata = _copy_targets(ingredients, "prestamp")
    cut = _cut(monkeypatch, "_stamp_copy_destination")
    with pytest.raises(cut):
        _replicate(ingredients, dest_root, dest_metadata)

    # The durable claim-only directory exists and classifies metadata-less.
    assert sorted(entry.name for entry in Path(dest_root).iterdir()) == [
        ".#~root-claim"
    ]
    assert (
        read_lifecycle_state(backend, dest_root, dest_metadata, storage)
        is LifecycleState.METADATA_LESS
    )


def test_replicate_interrupted_after_stamp_is_read_only_unserviceable(
    coordinator_on, monkeypatch
) -> None:
    ingredients = coordinator_on()
    _seed_source(ingredients, monkeypatch)
    backend, _project_root, _metadata_root, storage = ingredients
    dest_root, dest_metadata = _copy_targets(ingredients, "poststamp")
    cut = _cut(monkeypatch, "_copy_tree")
    with pytest.raises(cut):
        _replicate(ingredients, dest_root, dest_metadata)

    assert (
        read_lifecycle_state(backend, dest_root, dest_metadata, storage)
        is LifecycleState.READ_ONLY_UNSERVICEABLE
    )


def test_replicate_interrupted_after_parent_flush_retries(
    coordinator_on, monkeypatch
) -> None:
    ingredients = coordinator_on()
    _seed_source(ingredients, monkeypatch)
    _backend, project_root, _metadata_root, _storage = ingredients
    dest_root, dest_metadata = _copy_targets(ingredients, "postflush")
    cut = _cut(monkeypatch, "_complete_root_operation")
    with pytest.raises(cut):
        _replicate(ingredients, dest_root, dest_metadata)
    _uncut(monkeypatch, "_complete_root_operation")

    operation_id = _replicate(ingredients, dest_root, dest_metadata)
    assert len(operation_id) == 32
    assert _tree_facts(dest_root) == _tree_facts(project_root)


def test_replicate_retry_converges(coordinator_on, monkeypatch) -> None:
    ingredients = coordinator_on()
    _seed_source(ingredients, monkeypatch)
    backend, project_root, _metadata_root, storage = ingredients
    dest_root, dest_metadata = _copy_targets(ingredients, "converge")
    cut = _cut(monkeypatch, "_copy_tree")
    with pytest.raises(cut):
        _replicate(ingredients, dest_root, dest_metadata)
    _uncut(monkeypatch, "_copy_tree")

    first = _replicate(ingredients, dest_root, dest_metadata)
    second = _replicate(ingredients, dest_root, dest_metadata)
    assert first == second
    assert _tree_facts(dest_root) == _tree_facts(project_root)
    assert (
        read_lifecycle_state(backend, dest_root, dest_metadata, storage)
        is LifecycleState.READ_ONLY_UNSERVICEABLE
    )


def test_replicate_retry_refuses_different_request(
    coordinator_on, monkeypatch
) -> None:
    from atoms.coordinator.commands import RootOperationMismatch, replicate_root

    ingredients = coordinator_on()
    _seed_source(ingredients, monkeypatch)
    _backend, _project_root, _metadata_root, _storage = ingredients
    dest_root, dest_metadata = _copy_targets(ingredients, "different")
    cut = _cut(monkeypatch, "_copy_tree")
    with pytest.raises(cut):
        _replicate(ingredients, dest_root, dest_metadata)
    _uncut(monkeypatch, "_copy_tree")

    # A second *source* is a different request over the same claimed
    # destination, whatever its bytes.
    other = coordinator_on()
    _seed_source(other, monkeypatch)
    other_backend, other_root, other_metadata, other_storage = other
    with pytest.raises(RootOperationMismatch):
        replicate_root(
            other_backend,
            other_root,
            other_metadata,
            dest_root,
            dest_metadata,
            other_storage,
        )


def test_replicate_retry_after_tree_durable_does_not_open_source(
    coordinator_on, monkeypatch
) -> None:
    import shutil

    ingredients = coordinator_on()
    _seed_source(ingredients, monkeypatch)
    backend, project_root, metadata_root, storage = ingredients
    dest_root, dest_metadata = _copy_targets(ingredients, "nosource")
    cut = _cut(monkeypatch, "_complete_root_operation")
    with pytest.raises(cut):
        _replicate(ingredients, dest_root, dest_metadata)
    _uncut(monkeypatch, "_complete_root_operation")

    # The source vanishes entirely; the tree-durable retry must not miss it.
    shutil.rmtree(project_root)
    shutil.rmtree(metadata_root)
    operation_id = _replicate(ingredients, dest_root, dest_metadata)
    assert len(operation_id) == 32
    assert (
        read_lifecycle_state(backend, dest_root, dest_metadata, storage)
        is LifecycleState.READ_ONLY_UNSERVICEABLE
    )


def test_copy_retry_rereads_phase_after_reacquiring_destination(
    coordinator_on, monkeypatch
) -> None:
    from atoms.coordinator import lifecycle

    ingredients = coordinator_on()
    _seed_source(ingredients, monkeypatch)
    dest_root, dest_metadata = _copy_targets(ingredients, "reread")
    cut = _cut(monkeypatch, "_copy_tree")
    with pytest.raises(cut):
        _replicate(ingredients, dest_root, dest_metadata)
    _uncut(monkeypatch, "_copy_tree")

    rereads: list[str] = []
    real = lifecycle._reread_phase_after_reacquisition

    def recording(store):
        row = real(store)
        rereads.append(row.phase if row is not None else "absent")
        return row

    monkeypatch.setattr(lifecycle, "_reread_phase_after_reacquisition", recording)
    _replicate(ingredients, dest_root, dest_metadata)
    assert rereads, "the retry never re-read the phase after reacquisition"


def test_copy_retry_moved_source_raises_source_snapshot_moved(
    coordinator_on, monkeypatch
) -> None:
    from atoms.coordinator.commands import SourceSnapshotMoved
    from atoms.coordinator.commands import append_intent as _intent

    ingredients = coordinator_on()
    _seed_source(ingredients, monkeypatch)
    backend, project_root, metadata_root, storage = ingredients
    dest_root, dest_metadata = _copy_targets(ingredients, "moved")
    cut = _cut(monkeypatch, "_copy_tree")
    with pytest.raises(cut):
        _replicate(ingredients, dest_root, dest_metadata)
    _uncut(monkeypatch, "_copy_tree")

    # The source head moves before the retry that still needs its bytes.
    _intent(backend, project_root, metadata_root, storage, b"moved the head")
    with pytest.raises(SourceSnapshotMoved):
        _replicate(ingredients, dest_root, dest_metadata)


def test_copy_retry_missing_source_preserves_evidence(
    coordinator_on, monkeypatch
) -> None:
    import shutil

    ingredients = coordinator_on()
    _seed_source(ingredients, monkeypatch)
    dest_root, dest_metadata = _copy_targets(ingredients, "missing")
    cut = _cut(monkeypatch, "_copy_tree")
    with pytest.raises(cut):
        _replicate(ingredients, dest_root, dest_metadata)
    _uncut(monkeypatch, "_copy_tree")

    _backend, project_root, _metadata_root, _storage = ingredients
    before_claim = (Path(dest_root) / ".#~root-claim").read_bytes()
    shutil.rmtree(project_root)
    with pytest.raises(OSError):
        _replicate(ingredients, dest_root, dest_metadata)
    # Claim, row, and tree survive for a later retry.
    assert (Path(dest_root) / ".#~root-claim").read_bytes() == before_claim
    assert (Path(dest_metadata) / "atoms.db").exists()


def test_fork_applies_overrides_before_baseline(coordinator_on, monkeypatch) -> None:
    from atoms.chain.model import GenesisEntry, state_from_json
    from atoms.coordinator.commands import read_chain

    ingredients = coordinator_on()
    head = _seed_source(ingredients, monkeypatch)
    backend, _project_root, _metadata_root, storage = ingredients
    dest_root, dest_metadata = _copy_targets(ingredients, "forkbase")

    _fork(ingredients, dest_root, dest_metadata, head)
    view = read_chain(backend, dest_root, dest_metadata, storage)
    _digest, genesis = view.entries[0]
    assert type(genesis) is GenesisEntry
    baseline = dict(genesis.baseline)
    assert set(baseline) == {"data/payload.bin", "manifest.json"}
    from atoms.core.fingerprint import FileState

    manifest_state = state_from_json(baseline["manifest.json"])
    expected_hash = hashlib.sha256(b"child manifest").hexdigest()
    assert type(manifest_state) is FileState
    assert manifest_state.content_hash == f"sha256:{expected_hash}"
    assert (Path(dest_root) / "manifest.json").read_bytes() == b"child manifest"


def test_fork_appends_a_new_chain_with_the_supplied_genesis_bytes(
    coordinator_on, monkeypatch
) -> None:
    from atoms.chain.model import GenesisEntry
    from atoms.coordinator.commands import read_chain

    ingredients = coordinator_on()
    head = _seed_source(ingredients, monkeypatch)
    backend, project_root, metadata_root, storage = ingredients
    dest_root, dest_metadata = _copy_targets(ingredients, "forkchain")

    _fork(ingredients, dest_root, dest_metadata, head)
    child = read_chain(backend, dest_root, dest_metadata, storage)
    parent = read_chain(backend, project_root, metadata_root, storage)
    genesis = child.entries[0][1]
    assert type(genesis) is GenesisEntry
    assert genesis.payload == b"opaque child genesis"
    assert len(child.entries) == 1
    assert child.tip != parent.tip
    assert (
        read_lifecycle_state(backend, dest_root, dest_metadata, storage)
        is LifecycleState.WRITABLE
    )


def test_fork_refuses_a_moved_source(coordinator_on, monkeypatch) -> None:
    from atoms.coordinator.commands import SourceSnapshotMoved
    from atoms.coordinator.commands import append_intent as _intent

    ingredients = coordinator_on()
    head = _seed_source(ingredients, monkeypatch)
    backend, project_root, metadata_root, storage = ingredients
    dest_root, dest_metadata = _copy_targets(ingredients, "forkmoved")
    _intent(backend, project_root, metadata_root, storage, b"the head moves")

    with pytest.raises(SourceSnapshotMoved):
        _fork(ingredients, dest_root, dest_metadata, head)
    assert not Path(dest_root).exists()


def test_fork_interrupted_before_grant_is_read_only(
    coordinator_on, monkeypatch
) -> None:
    ingredients = coordinator_on()
    head = _seed_source(ingredients, monkeypatch)
    backend, _project_root, _metadata_root, storage = ingredients
    dest_root, dest_metadata = _copy_targets(ingredients, "forkcut")
    cut = _cut(monkeypatch, "_complete_root_operation")
    with pytest.raises(cut):
        _fork(ingredients, dest_root, dest_metadata, head)

    assert (
        read_lifecycle_state(backend, dest_root, dest_metadata, storage)
        is LifecycleState.READ_ONLY_UNSERVICEABLE
    )


def test_fork_pregrant_retry_completes_with_identical_inputs(
    coordinator_on, monkeypatch
) -> None:
    ingredients = coordinator_on()
    head = _seed_source(ingredients, monkeypatch)
    backend, _project_root, _metadata_root, storage = ingredients
    dest_root, dest_metadata = _copy_targets(ingredients, "forkretry")
    cut = _cut(monkeypatch, "_complete_root_operation")
    with pytest.raises(cut):
        _fork(ingredients, dest_root, dest_metadata, head)
    _uncut(monkeypatch, "_complete_root_operation")

    operation_id = _fork(ingredients, dest_root, dest_metadata, head)
    assert len(operation_id) == 32
    assert (
        read_lifecycle_state(backend, dest_root, dest_metadata, storage)
        is LifecycleState.WRITABLE
    )


def test_fork_pregrant_retry_refuses_different_bytes(
    coordinator_on, monkeypatch
) -> None:
    from atoms.coordinator.commands import RootOperationMismatch

    ingredients = coordinator_on()
    head = _seed_source(ingredients, monkeypatch)
    dest_root, dest_metadata = _copy_targets(ingredients, "forkdiff")
    cut = _cut(monkeypatch, "_complete_root_operation")
    with pytest.raises(cut):
        _fork(ingredients, dest_root, dest_metadata, head)
    _uncut(monkeypatch, "_complete_root_operation")

    with pytest.raises(RootOperationMismatch):
        _fork(
            ingredients,
            dest_root,
            dest_metadata,
            head,
            genesis_payload=b"different opaque bytes",
        )


def test_fork_postgrant_retry_returns_success_after_legitimate_writes(
    coordinator_on, monkeypatch
) -> None:
    from atoms.coordinator.commands import append_intent as _intent

    ingredients = coordinator_on()
    head = _seed_source(ingredients, monkeypatch)
    backend, _project_root, _metadata_root, storage = ingredients
    dest_root, dest_metadata = _copy_targets(ingredients, "forkpost")

    first = _fork(ingredients, dest_root, dest_metadata, head)
    # The child is writable; legitimate logged writes change its tree.
    _intent(backend, dest_root, dest_metadata, storage, b"legitimate write")
    (Path(dest_root) / "grown.txt").write_bytes(b"legitimately different")

    second = _fork(ingredients, dest_root, dest_metadata, head)
    assert first == second


def test_pending_fork_claim_returns_original_operation_id(
    coordinator_on, monkeypatch
) -> None:
    from atoms.coordinator.commands import read_pending_fork_operation

    ingredients = coordinator_on()
    head = _seed_source(ingredients, monkeypatch)
    backend, _project_root, _metadata_root, storage = ingredients
    dest_root, dest_metadata = _copy_targets(ingredients, "pendingclaim")
    cut = _cut(monkeypatch, "_stamp_copy_destination")
    with pytest.raises(cut):
        _fork(ingredients, dest_root, dest_metadata, head)
    _uncut(monkeypatch, "_stamp_copy_destination")

    from tests.lifecycle_support import claim_operation_id

    claimed = claim_operation_id(dest_root)
    assert (
        read_pending_fork_operation(backend, dest_root, dest_metadata, storage)
        == claimed
    )


def test_pending_fork_row_returns_original_operation_id(
    coordinator_on, monkeypatch
) -> None:
    from atoms.coordinator.commands import read_pending_fork_operation

    ingredients = coordinator_on()
    head = _seed_source(ingredients, monkeypatch)
    backend, _project_root, _metadata_root, storage = ingredients
    dest_root, dest_metadata = _copy_targets(ingredients, "pendingrow")
    cut = _cut(monkeypatch, "_complete_root_operation")
    with pytest.raises(cut):
        _fork(ingredients, dest_root, dest_metadata, head)
    _uncut(monkeypatch, "_complete_root_operation")

    # The marker is already legitimately removed at this cut; the retained
    # row alone answers the query.
    with sqlite3.connect(Path(dest_metadata) / "atoms.db") as connection:
        recorded = connection.execute(
            "SELECT operation_id FROM root_operation"
        ).fetchone()[0]
    assert not (Path(dest_root) / ".#~root-claim").exists()
    assert (
        read_pending_fork_operation(backend, dest_root, dest_metadata, storage)
        == recorded
    )


def test_pending_fork_query_refuses_mismatch_malformed_and_nonfork(
    coordinator_on, monkeypatch
) -> None:
    from atoms.coordinator.commands import (
        RootOperationInvalid,
        read_pending_fork_operation,
    )

    ingredients = coordinator_on()
    head = _seed_source(ingredients, monkeypatch)
    backend, _project_root, _metadata_root, storage = ingredients

    # Completed fork: None.
    done_root, done_metadata = _copy_targets(ingredients, "pendingdone")
    _fork(ingredients, done_root, done_metadata, head)
    assert (
        read_pending_fork_operation(backend, done_root, done_metadata, storage)
        is None
    )

    # Absent destination: None.
    absent_root, absent_metadata = _copy_targets(ingredients, "pendingabsent")
    assert (
        read_pending_fork_operation(backend, absent_root, absent_metadata, storage)
        is None
    )

    # A retained non-fork operation refuses.
    replica_root, replica_metadata = _copy_targets(ingredients, "pendingrep")
    cut = _cut(monkeypatch, "_copy_tree")
    with pytest.raises(cut):
        _replicate(ingredients, replica_root, replica_metadata)
    _uncut(monkeypatch, "_copy_tree")
    with pytest.raises(PreconditionRefused):
        read_pending_fork_operation(
            backend, replica_root, replica_metadata, storage
        )

    # A malformed claim is invalid evidence.
    broken_root, broken_metadata = _copy_targets(ingredients, "pendingbad")
    cut = _cut(monkeypatch, "_stamp_copy_destination")
    with pytest.raises(cut):
        _fork(ingredients, broken_root, broken_metadata, head)
    _uncut(monkeypatch, "_stamp_copy_destination")
    (Path(broken_root) / ".#~root-claim").write_bytes(b"{not canonical")
    with pytest.raises(RootOperationInvalid):
        read_pending_fork_operation(backend, broken_root, broken_metadata, storage)


def test_resume_fork_uses_retained_bytes(coordinator_on, monkeypatch) -> None:
    from atoms.chain.model import GenesisEntry
    from atoms.coordinator.commands import (
        read_chain,
        read_pending_fork_operation,
        resume_fork_root,
    )

    ingredients = coordinator_on()
    head = _seed_source(ingredients, monkeypatch)
    backend, _project_root, _metadata_root, storage = ingredients
    dest_root, dest_metadata = _copy_targets(ingredients, "resume")
    cut = _cut(monkeypatch, "_copy_tree")
    with pytest.raises(cut):
        _fork(ingredients, dest_root, dest_metadata, head)
    _uncut(monkeypatch, "_copy_tree")

    pending = read_pending_fork_operation(backend, dest_root, dest_metadata, storage)
    assert pending is not None
    finished = resume_fork_root(backend, dest_root, dest_metadata, storage, pending)
    assert finished == pending
    child = read_chain(backend, dest_root, dest_metadata, storage)
    genesis = child.entries[0][1]
    assert type(genesis) is GenesisEntry
    assert genesis.payload == b"opaque child genesis"


def test_fork_destination_only_retry_survives_moved_or_missing_source(
    coordinator_on, monkeypatch
) -> None:
    import shutil

    ingredients = coordinator_on()
    head = _seed_source(ingredients, monkeypatch)
    backend, project_root, metadata_root, storage = ingredients
    dest_root, dest_metadata = _copy_targets(ingredients, "forkkill")
    # Killed after genesis but before the grant: everything durable but the
    # completion transaction.
    cut = _cut(monkeypatch, "_complete_root_operation")
    with pytest.raises(cut):
        _fork(ingredients, dest_root, dest_metadata, head)
    _uncut(monkeypatch, "_complete_root_operation")

    shutil.rmtree(project_root)
    shutil.rmtree(metadata_root)
    operation_id = _fork(ingredients, dest_root, dest_metadata, head)
    assert len(operation_id) == 32
    assert (
        read_lifecycle_state(backend, dest_root, dest_metadata, storage)
        is LifecycleState.WRITABLE
    )


def test_fork_tree_contradiction_is_root_operation_invalid(
    coordinator_on, monkeypatch
) -> None:
    from atoms.coordinator.commands import RootOperationInvalid

    ingredients = coordinator_on()
    head = _seed_source(ingredients, monkeypatch)
    dest_root, dest_metadata = _copy_targets(ingredients, "forkbadtree")
    cut = _cut(monkeypatch, "_complete_root_operation")
    with pytest.raises(cut):
        _fork(ingredients, dest_root, dest_metadata, head)
    _uncut(monkeypatch, "_complete_root_operation")

    # A pre-grant tree is nobody's to mutate: contradicting bytes at a proved
    # path refuse and preserve the evidence.
    target = Path(dest_root) / "data" / "payload.bin"
    target.chmod(0o600)
    target.write_bytes(b"contradicted")
    with pytest.raises(RootOperationInvalid):
        _fork(ingredients, dest_root, dest_metadata, head)
    # Evidence preserved: the row and tree survive, and the refusal repeats.
    assert (Path(dest_metadata) / "atoms.db").exists()
    assert target.read_bytes() == b"contradicted"
    with pytest.raises(RootOperationInvalid):
        _fork(ingredients, dest_root, dest_metadata, head)


def test_malformed_root_claim_is_root_operation_invalid(
    coordinator_on, monkeypatch
) -> None:
    from atoms.coordinator.commands import RootOperationInvalid

    ingredients = coordinator_on()
    head = _seed_source(ingredients, monkeypatch)
    dest_root, dest_metadata = _copy_targets(ingredients, "badclaim")
    cut = _cut(monkeypatch, "_stamp_copy_destination")
    with pytest.raises(cut):
        _fork(ingredients, dest_root, dest_metadata, head)
    _uncut(monkeypatch, "_stamp_copy_destination")

    claim = Path(dest_root) / ".#~root-claim"
    claim.write_bytes(b'{"domain":"atoms.root-claim.v1"}')
    with pytest.raises(RootOperationInvalid):
        _fork(ingredients, dest_root, dest_metadata, head)
    # Evidence preserved.
    assert claim.exists()


def test_copy_paths_are_pairwise_nonoverlapping(coordinator_on, monkeypatch) -> None:
    ingredients = coordinator_on()
    _seed_source(ingredients, monkeypatch)
    backend, project_root, metadata_root, storage = ingredients
    from atoms.coordinator.commands import replicate_root

    dest_root, dest_metadata = _copy_targets(ingredients, "overlap")
    cases = (
        # destination root beneath the source root
        (project_root, metadata_root, str(Path(project_root) / "sub"), dest_metadata),
        # destination metadata beneath the destination root
        (project_root, metadata_root, dest_root, str(Path(dest_root) / "metadata")),
        # source metadata equal to the destination metadata
        (project_root, metadata_root, dest_root, metadata_root),
        # destination equal to the source
        (project_root, metadata_root, project_root, dest_metadata),
    )
    for source_root, source_metadata, target_root, target_metadata in cases:
        with pytest.raises(PreconditionRefused, match="overlap|equal|non-over"):
            replicate_root(
                backend,
                source_root,
                source_metadata,
                target_root,
                target_metadata,
                storage,
            )


def test_opposite_direction_copy_contention_refuses_without_deadlock(
    coordinator_on, monkeypatch
) -> None:
    from atoms.fs.linux import LinuxBackend
    from atoms.fs.lock import acquire_project_lock

    ingredients = coordinator_on()
    _seed_source(ingredients, monkeypatch)
    _backend, _project_root, _metadata_root, _storage = ingredients
    dest_root, dest_metadata = _copy_targets(ingredients, "contend")

    # Model the opposite-direction holder: someone holds the destination's
    # metadata lock (their source lock) while we come the other way.
    with acquire_project_lock(LinuxBackend(), dest_metadata), pytest.raises(
        PreconditionRefused, match="copy destination lock is busy"
    ):
        _replicate(ingredients, dest_root, dest_metadata)

    # Our source lock was released with the refusal — the claim remains
    # resumable and the same copy now converges.
    operation_id = _replicate(ingredients, dest_root, dest_metadata)
    assert len(operation_id) == 32


def test_root_claim_request_and_snapshot_encoding_are_canonical(
    coordinator_on, monkeypatch
) -> None:
    ingredients = coordinator_on()
    _seed_source(ingredients, monkeypatch)
    _backend, _project_root, _metadata_root, _storage = ingredients
    dest_root, dest_metadata = _copy_targets(ingredients, "canon")
    # Unicode payload content exercises ensure_ascii=False; the profile and
    # base64 fields exercise the closed grammar.
    (Path(_project_root) / "unicodé.txt").write_bytes("géo ▲".encode())

    _replicate(ingredients, dest_root, dest_metadata)

    with sqlite3.connect(Path(dest_metadata) / "atoms.db") as connection:
        row = connection.execute(
            "SELECT request_json, request_hash, source_snapshot_json,"
            " destination_snapshot_json FROM root_operation"
        ).fetchone()
    request_json, request_hash, source_json, destination_json = row
    import json as _json

    for text in (request_json, source_json, destination_json):
        decoded = _json.loads(text)
        canonical = _json.dumps(
            decoded,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        assert canonical == text
    assert (
        hashlib.sha256(request_json.encode("utf-8")).hexdigest() == request_hash
    )
    request = _json.loads(request_json)
    assert request["domain"] == "atoms.root-operation.v1"
    assert request["kind"] == "replicate"
    assert request["storage_profile"] == {"profile_id": _storage.profile_id}
    assert len(request["source_head"]) == 64
    snapshot = _json.loads(source_json)
    assert snapshot["domain"] == "atoms.root-snapshot.v1"
    assert snapshot["chain_head"] == request["source_head"]
    paths = [path for path, _state in snapshot["entries"]]
    assert paths == sorted(paths)
    assert "unicodé.txt" in paths


def test_copy_flushes_destination_root_and_containing_parent(
    coordinator_on, monkeypatch
) -> None:

    from atoms.fs.linux import LinuxBackend

    flushed: set[int] = set()
    real = LinuxBackend.flush_directory

    def recording(self, fd: int) -> None:
        flushed.add(os.fstat(fd).st_ino)
        real(self, fd)

    monkeypatch.setattr(LinuxBackend, "flush_directory", recording)

    ingredients = coordinator_on()
    _seed_source(ingredients, monkeypatch)
    dest_root, dest_metadata = _copy_targets(ingredients, "flush")
    _replicate(ingredients, dest_root, dest_metadata)

    assert os.stat(dest_root).st_ino in flushed
    assert os.stat(Path(dest_root).parent).st_ino in flushed


# --- Tier 3: serviceability grant and the v2 migration ---


def _grant(backend, root: str, metadata_root: str, storage) -> None:
    from atoms.coordinator.commands import grant_read_serviceability

    grant_read_serviceability(backend, root, metadata_root, storage)


def _cold_copy(ingredients, monkeypatch, tag: str) -> tuple[str, str]:
    """A completed replica whose carrier is then discarded: a validated,
    metadata-less cold root, the restore arrival shape."""
    import shutil

    dest_root, dest_metadata = _copy_targets(ingredients, tag)
    _replicate(ingredients, dest_root, dest_metadata)
    shutil.rmtree(dest_metadata)
    return dest_root, dest_metadata


def test_grant_refuses_a_writable_root(coordinator_on, monkeypatch) -> None:
    ingredients = coordinator_on()
    _seed_source(ingredients, monkeypatch)
    backend, project_root, metadata_root, storage = ingredients
    with pytest.raises(PreconditionRefused, match="writable"):
        _grant(backend, project_root, metadata_root, storage)


def test_grant_refuses_a_binding_mismatched_root(coordinator_on, monkeypatch) -> None:
    from atoms.coordinator import lifecycle

    ingredients = coordinator_on()
    _seed_source(ingredients, monkeypatch)
    backend, _project_root, _metadata_root, storage = ingredients
    dest_root, dest_metadata = _copy_targets(ingredients, "grantbind")
    _replicate(ingredients, dest_root, dest_metadata)
    monkeypatch.setattr(lifecycle, "_read_machine_identity", lambda: _OTHER_MACHINE)
    with pytest.raises(PreconditionRefused, match="mismatch"):
        _grant(backend, dest_root, dest_metadata, storage)


def test_grant_refuses_a_root_claim_residue(coordinator_on, monkeypatch) -> None:
    import shutil

    ingredients = coordinator_on()
    _seed_source(ingredients, monkeypatch)
    backend, _project_root, _metadata_root, storage = ingredients
    dest_root, dest_metadata = _cold_copy(ingredients, monkeypatch, "grantclaim")
    # A raw copy taken before claim removal carries the reserved leaf.
    (Path(dest_root) / ".#~root-claim").write_bytes(b"{}")
    with pytest.raises(PreconditionRefused, match="claim|residue"):
        _grant(backend, dest_root, dest_metadata, storage)
    shutil.rmtree(dest_metadata, ignore_errors=True)


def test_grant_refuses_a_chain_staging_survivor(coordinator_on, monkeypatch) -> None:
    ingredients = coordinator_on()
    _seed_source(ingredients, monkeypatch)
    backend, _project_root, _metadata_root, storage = ingredients
    dest_root, dest_metadata = _cold_copy(ingredients, monkeypatch, "grantstage")
    (Path(dest_root) / ".#~chain" / ".#~stage").write_bytes(b"staged bytes")
    with pytest.raises(PreconditionRefused, match="staging|survivor"):
        _grant(backend, dest_root, dest_metadata, storage)


def test_grant_refuses_an_incomplete_operation(coordinator_on, monkeypatch) -> None:
    ingredients = coordinator_on()
    _seed_source(ingredients, monkeypatch)
    backend, _project_root, _metadata_root, storage = ingredients
    dest_root, dest_metadata = _copy_targets(ingredients, "grantincomplete")
    cut = _cut(monkeypatch, "_copy_tree")
    with pytest.raises(cut):
        _replicate(ingredients, dest_root, dest_metadata)
    _uncut(monkeypatch, "_copy_tree")
    with pytest.raises(PreconditionRefused, match="incomplete|operation"):
        _grant(backend, dest_root, dest_metadata, storage)


def test_grant_refuses_exact_v2(coordinator_on, monkeypatch) -> None:
    from tests.lifecycle_support import fabricate_v2_store

    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    backend, project_root, metadata_root, storage = ingredients
    fabricate_v2_store(metadata_root)
    with pytest.raises(PreconditionRefused, match="version 2|v2|migrate"):
        _grant(backend, project_root, metadata_root, storage)


def test_grant_creates_bookkeeping_for_a_metadata_less_root(
    coordinator_on, monkeypatch
) -> None:
    ingredients = coordinator_on()
    _seed_source(ingredients, monkeypatch)
    backend, _project_root, _metadata_root, storage = ingredients
    dest_root, dest_metadata = _cold_copy(ingredients, monkeypatch, "grantcold")
    assert (
        read_lifecycle_state(backend, dest_root, dest_metadata, storage)
        is LifecycleState.METADATA_LESS
    )
    _grant(backend, dest_root, dest_metadata, storage)
    assert (
        read_lifecycle_state(backend, dest_root, dest_metadata, storage)
        is LifecycleState.READ_ONLY_SERVICEABLE
    )


def test_grant_is_idempotent_on_read_only_serviceable(
    coordinator_on, monkeypatch
) -> None:
    from atoms.fs import binding as binding_module
    from atoms.store import connection as connection_module

    ingredients = coordinator_on()
    _seed_source(ingredients, monkeypatch)
    backend, _project_root, _metadata_root, storage = ingredients
    dest_root, dest_metadata = _cold_copy(ingredients, monkeypatch, "grantidem")
    _grant(backend, dest_root, dest_metadata, storage)

    def trapped(*args, **kwargs):
        raise AssertionError("the already-serviceable grant took a writable path")

    monkeypatch.setattr(binding_module, "bind_project_volume", trapped)
    monkeypatch.setattr(connection_module, "open_store", trapped)
    from atoms.coordinator import root as root_module

    monkeypatch.setattr(root_module, "bind_project_volume", trapped)
    monkeypatch.setattr(root_module, "open_store", trapped)
    monkeypatch.setattr(root_module, "reclaim_probe_survivors", trapped)
    monkeypatch.setattr(root_module, "resolve", trapped)

    before = _metadata_entries(dest_metadata)
    _grant(backend, dest_root, dest_metadata, storage)
    assert _metadata_entries(dest_metadata) == before
    assert (
        read_lifecycle_state(backend, dest_root, dest_metadata, storage)
        is LifecycleState.READ_ONLY_SERVICEABLE
    )


def _migrate(backend, root: str, metadata_root: str, storage) -> None:
    from atoms.coordinator.commands import migrate_root_to_lifecycle_v3

    migrate_root_to_lifecycle_v3(backend, root, metadata_root, storage)


def _fabricated_v2_vintage(ingredients, monkeypatch) -> None:
    """A pre-lifecycle vintage: a registered chain beside an exact v2 store.

    Fabricated by registering under the current engine, then rewriting the
    carrier back to the frozen v2 shape with raw sqlite — the only writer a
    pre-lifecycle store could have had is not this build.
    """
    import shutil

    from tests.lifecycle_support import fabricate_v2_store

    _seed_source(ingredients, monkeypatch)
    _backend, _project_root, metadata_root, _storage = ingredients
    shutil.rmtree(metadata_root)
    fabricate_v2_store(metadata_root)
    Path(metadata_root, "lock").touch(mode=0o600)


def test_migration_authorized_success_grants_with_a_fresh_binding(
    coordinator_on, monkeypatch
) -> None:
    ingredients = coordinator_on()
    _fabricated_v2_vintage(ingredients, monkeypatch)
    backend, project_root, metadata_root, storage = ingredients
    assert (
        read_lifecycle_state(backend, project_root, metadata_root, storage)
        is LifecycleState.READ_ONLY_UNSERVICEABLE
    )
    _migrate(backend, project_root, metadata_root, storage)
    assert (
        read_lifecycle_state(backend, project_root, metadata_root, storage)
        is LifecycleState.WRITABLE
    )
    with sqlite3.connect(Path(metadata_root) / "atoms.db") as connection:
        origin, machine_id = connection.execute(
            "SELECT origin, machine_id FROM root_lifecycle"
        ).fetchone()
    assert origin == "migration-v2"
    assert len(machine_id) == 32
    # A no-write exact retry of the matching migration origin succeeds.
    _migrate(backend, project_root, metadata_root, storage)


def test_migration_refuses_a_metadata_less_root(coordinator_on, monkeypatch) -> None:
    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    backend, project_root, metadata_root, storage = ingredients
    with pytest.raises(PreconditionRefused, match="metadata"):
        _migrate(backend, project_root, metadata_root, storage)


def test_migration_refuses_a_binding_mismatch(coordinator_on, monkeypatch) -> None:
    from atoms.coordinator import lifecycle

    ingredients = coordinator_on()
    _seed_source(ingredients, monkeypatch)
    backend, project_root, metadata_root, storage = ingredients
    monkeypatch.setattr(lifecycle, "_read_machine_identity", lambda: _OTHER_MACHINE)
    with pytest.raises(PreconditionRefused, match="mismatch"):
        _migrate(backend, project_root, metadata_root, storage)


def test_migration_is_atomic_at_every_catalog_cut(
    coordinator_on, monkeypatch
) -> None:
    from atoms.coordinator import lifecycle
    from atoms.store.schema import ROOT_LIFECYCLE_V3_STATEMENTS

    real = lifecycle._execute_migration_statement

    for cut_at in range(len(ROOT_LIFECYCLE_V3_STATEMENTS)):
        ingredients = coordinator_on()
        _fabricated_v2_vintage(ingredients, monkeypatch)
        backend, project_root, metadata_root, storage = ingredients

        executed = {"count": 0}

        class CutHere(BaseException):
            pass

        def cutting(connection, statement, _executed=executed, _cut_at=cut_at):
            if _executed["count"] == _cut_at:
                raise CutHere(f"cut before statement {_cut_at}")
            _executed["count"] += 1
            real(connection, statement)

        monkeypatch.setattr(lifecycle, "_execute_migration_statement", cutting)
        with pytest.raises(CutHere):
            _migrate(backend, project_root, metadata_root, storage)
        monkeypatch.setattr(lifecycle, "_execute_migration_statement", real)

        # The rollback leaves exact v2 with no grant.
        assert (
            read_lifecycle_state(backend, project_root, metadata_root, storage)
            is LifecycleState.READ_ONLY_UNSERVICEABLE
        )
        # And the untouched retry migrates.
        _migrate(backend, project_root, metadata_root, storage)
        assert (
            read_lifecycle_state(backend, project_root, metadata_root, storage)
            is LifecycleState.WRITABLE
        )
