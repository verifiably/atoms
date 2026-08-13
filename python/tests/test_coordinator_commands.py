"""A7a's public registration and intent commands."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from atoms.core.capabilities import Capability
from atoms.core.errors import (
    CapabilityUnavailable,
    PreconditionRefused,
    ProtocolError,
    SpecValidationError,
)
from atoms.core.scratch import CHAIN_LEAF

ROOT = Path(__file__).resolve().parents[1]


def _enable_commands(ingredients, monkeypatch) -> None:
    from atoms.coordinator import root
    from atoms.fs.lock import acquire_project_lock
    from tests.fs_support import build_test_allowlist

    backend, project_root, metadata_root, storage = ingredients
    with acquire_project_lock(backend, metadata_root) as lock:
        allowlist = build_test_allowlist(lock, project_root, storage)
    monkeypatch.setattr(root, "CERTIFIED_ALLOWLIST", allowlist)


def _register(ingredients, payload, surface):
    from atoms.coordinator.commands import register_root

    backend, project_root, metadata_root, storage = ingredients
    return register_root(
        backend, project_root, metadata_root, storage, payload, surface
    )


def _append(ingredients, payload):
    from atoms.coordinator.commands import append_intent

    backend, project_root, metadata_root, storage = ingredients
    return append_intent(backend, project_root, metadata_root, storage, payload)


def _store_counts(metadata_root: str) -> tuple[int, int]:
    with sqlite3.connect(Path(metadata_root) / "atoms.db") as connection:
        return tuple(
            connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("transaction_record", "blob")
        )


def _durable_entries(project_root: str) -> dict[str, bytes]:
    directory = Path(project_root) / CHAIN_LEAF
    return {path.name: path.read_bytes() for path in directory.iterdir()}


def _fresh_process(project_root: str, metadata_root: str) -> dict:
    finished = subprocess.run(
        [sys.executable, "-m", "tests.coordinator_child", project_root, metadata_root],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=120,
    )
    return json.loads(finished.stdout)


def test_register_root_appends_one_genesis_with_the_typed_baseline(
    coordinator_on, monkeypatch
):
    from atoms.chain.model import GenesisEntry, decode_entry

    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    _, project_root, _, _ = ingredients
    root = Path(project_root)
    (root / "dir").mkdir(mode=0o750)
    (root / "file.bin").write_bytes(b"baseline bytes")
    (root / "file.bin").chmod(0o640)
    (root / "link").symlink_to("dir")

    digest = _register(
        ingredients,
        b"opaque genesis",
        ("absent", "dir", "file.bin", "link", "missing/leaf"),
    )

    entries = _durable_entries(project_root)
    assert set(entries) == {digest}
    previous, entry = decode_entry(entries[digest])
    assert previous is None
    assert entry == GenesisEntry(
        payload=b"opaque genesis",
        baseline=(
            ("absent", (("kind", "absent"),)),
            ("dir", (("kind", "directory"), ("mode", "0o750"))),
            (
                "file.bin",
                (
                    ("kind", "file"),
                    ("content_hash", hashlib.sha256(b"baseline bytes").hexdigest()),
                    ("mode", "0o640"),
                    ("byte_len", str(len(b"baseline bytes"))),
                ),
            ),
            ("link", (("kind", "symlink"), ("target", "dir"), ("mode", "0o777"))),
            ("missing/leaf", (("kind", "absent"),)),
        ),
    )


@pytest.mark.parametrize(
    "surface",
    [
        ("b", "a"),
        ("a", "a"),
        (".#~chain",),
        ["a"],
        (1,),
    ],
)
def test_register_root_validates_the_surface_before_traversing(
    coordinator_on, monkeypatch, surface
):
    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    backend = ingredients[0]

    def forbidden(*args, **kwargs):
        raise AssertionError("root traversal ran before surface validation")

    monkeypatch.setattr(type(backend), "open_root", forbidden)

    with pytest.raises(PreconditionRefused) as caught:
        _register(ingredients, b"opaque genesis", surface)

    if surface == (".#~chain",):
        assert isinstance(caught.value.__cause__, SpecValidationError)


def test_baseline_walk_propagates_a_non_enoent_component_failure(
    coordinator_on, monkeypatch
):
    from atoms.fs.audit import AuditedBackend

    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    opened = AuditedBackend.open_child_directory

    def fail_parent(self, parent_fd, name):
        if name == "parent":
            raise OSError(errno.EIO, "injected component read failure")
        return opened(self, parent_fd, name)

    monkeypatch.setattr(AuditedBackend, "open_child_directory", fail_parent)

    with pytest.raises(OSError) as caught:
        _register(ingredients, b"opaque genesis", ("parent/leaf",))

    assert caught.value.errno == errno.EIO


def test_register_root_exact_retry_returns_the_genesis_without_recapturing(
    coordinator_on, monkeypatch
):
    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    _, project_root, _, _ = ingredients
    tracked = Path(project_root) / "tracked"
    tracked.write_bytes(b"registration-time state")

    first = _register(ingredients, b"opaque genesis", ("tracked",))
    tracked.unlink()
    os.mkfifo(tracked)

    retried = _register(ingredients, b"opaque genesis", ("tracked",))

    assert retried == first
    assert set(_durable_entries(project_root)) == {first}


@pytest.mark.parametrize(
    ("payload", "surface"),
    [
        (b"different genesis", ("tracked",)),
        (b"opaque genesis", ("other",)),
    ],
)
def test_register_root_refuses_a_nonidentical_retry_without_appending(
    coordinator_on, monkeypatch, payload, surface
):
    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    _, project_root, _, _ = ingredients
    (Path(project_root) / "tracked").write_bytes(b"registration-time state")
    first = _register(ingredients, b"opaque genesis", ("tracked",))

    with pytest.raises(PreconditionRefused):
        _register(ingredients, payload, surface)

    assert set(_durable_entries(project_root)) == {first}


def test_append_intent_preserves_bytes_and_extends_one_linear_chain(
    coordinator_on, monkeypatch
):
    from atoms.chain.model import IntentEntry, decode_entry

    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    _, project_root, _, _ = ingredients
    genesis = _register(ingredients, b"root", ())

    first = _append(ingredients, b"\x00first\xff")
    second = _append(ingredients, b"second")

    entries = _durable_entries(project_root)
    assert set(entries) == {genesis, first, second}
    assert decode_entry(entries[first]) == (genesis, IntentEntry(b"\x00first\xff"))
    assert decode_entry(entries[second]) == (first, IntentEntry(b"second"))


def test_an_appended_intent_is_the_tip_in_a_fresh_process(
    coordinator_on, monkeypatch
):
    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    _, project_root, metadata_root, _ = ingredients
    genesis = _register(ingredients, b"root", ())
    intent = _append(ingredients, b"survives restart")

    chain = _fresh_process(project_root, metadata_root)["lease"]["chain"]

    assert chain == {"tip": intent, "digests": [genesis, intent]}


def test_the_next_command_clears_an_intent_cut_before_transfer(
    coordinator_on, monkeypatch
):
    from atoms.chain.model import IntentEntry, decode_entry
    from atoms.chain.read import STAGING_LEAF
    from atoms.fs.audit import AuditedBackend

    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    _, project_root, _, _ = ingredients
    genesis = _register(ingredients, b"root", ())
    transfer = AuditedBackend.transfer_noclobber
    armed = True

    def cut_once(self, src_fd, src, dst_fd, dst):
        nonlocal armed
        if armed and src == STAGING_LEAF:
            armed = False
            raise RuntimeError("cut between staging and transfer")
        return transfer(self, src_fd, src, dst_fd, dst)

    monkeypatch.setattr(AuditedBackend, "transfer_noclobber", cut_once)
    returned = None
    with pytest.raises(RuntimeError, match="cut between staging and transfer"):
        returned = _append(ingredients, b"dead caller")
    assert returned is None
    assert STAGING_LEAF in _durable_entries(project_root)

    survived = _append(ingredients, b"survived")

    entries = _durable_entries(project_root)
    assert STAGING_LEAF not in entries
    assert set(entries) == {genesis, survived}
    assert decode_entry(entries[survived]) == (genesis, IntentEntry(b"survived"))


def test_append_intent_refuses_an_unregistered_root_without_transaction_artifacts(
    coordinator_on, monkeypatch
):
    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    _, project_root, metadata_root, _ = ingredients

    with pytest.raises(PreconditionRefused):
        _append(ingredients, b"opaque intent")

    assert not (Path(project_root) / CHAIN_LEAF).exists()
    assert list((Path(metadata_root) / "work").iterdir()) == []
    assert list((Path(metadata_root) / "blobs" / "sha256").iterdir()) == []
    assert _store_counts(metadata_root) == (0, 0)


def test_missing_chain_with_a_live_record_is_corruption(leased):
    from atoms.chain.errors import ChainStateInvalid
    from atoms.coordinator.commands import _registered_root
    from tests.store_support import APPROVAL_EVIDENCE, one_effect_spec

    with leased() as lease:
        with lease._store.transaction() as transaction:
            transaction.insert_record(
                "tx1", one_effect_spec(), approval_evidence=APPROVAL_EVIDENCE
            )
            transaction.set_active("tx1")

        with pytest.raises(ChainStateInvalid), _registered_root(lease):
            pass


@pytest.mark.parametrize("occupant", ["file", "symlink"])
def test_reserved_chain_leaf_with_the_wrong_kind_is_corruption(leased, occupant):
    from atoms.chain.errors import ChainStateInvalid
    from atoms.coordinator.commands import _registered_root

    with leased() as lease:
        backend = lease._binding.backend
        root_fd = lease._binding.project_root_fd
        if occupant == "file":
            fd = backend.create_exclusive(root_fd, CHAIN_LEAF, 0o600)
            backend.close_fd(fd)
        else:
            backend.symlink_child(root_fd, CHAIN_LEAF, "target")

        with pytest.raises(ChainStateInvalid), _registered_root(lease):
            pass


def test_registered_root_closes_the_chain_descriptor_when_validation_fails(leased):
    from atoms.chain.append import bootstrap_chain
    from atoms.chain.errors import ChainStateInvalid
    from atoms.coordinator.commands import _registered_root

    with leased() as lease:
        backend = lease._binding.backend
        chain_fd = bootstrap_chain(backend, lease._binding.project_root_fd)
        foreign_fd = backend.create_exclusive(chain_fd, "foreign", 0o600)
        backend.close_fd(foreign_fd)
        backend.close_fd(chain_fd)
        before = len(os.listdir("/proc/self/fd"))

        with pytest.raises(ChainStateInvalid), _registered_root(lease):
            pass

        assert len(os.listdir("/proc/self/fd")) == before


def test_baseline_walk_attempts_every_owned_close_and_raises_the_first_failure(
    coordinator_on, leased, monkeypatch
):
    from atoms.coordinator.commands import _capture_path
    from atoms.fs.audit import AuditedBackend
    from atoms.fs.observe import Observation

    ingredients = coordinator_on()
    raw_backend, project_root, _, _ = ingredients
    (Path(project_root) / "parent" / "child").mkdir(parents=True)
    opened = []
    attempted = []
    real_open = AuditedBackend.open_child_directory
    real_close = type(raw_backend).close_fd

    def record_owned_open(self, parent_fd, name):
        fd = real_open(self, parent_fd, name)
        opened.append(fd)
        return fd

    def fail_first_owned_close(self, fd):
        if fd in opened:
            attempted.append(fd)
        real_close(self, fd)
        if len(opened) == 2 and fd == opened[-1]:
            raise OSError(errno.EIO, "injected first close failure")

    with leased(ingredients) as lease:
        backend = lease._binding.backend
        assert isinstance(backend, AuditedBackend)
        with monkeypatch.context() as patched:
            patched.setattr(AuditedBackend, "open_child_directory", record_owned_open)
            patched.setattr(type(raw_backend), "close_fd", fail_first_owned_close)
            with Observation(backend) as observation, pytest.raises(OSError) as caught:
                _capture_path(
                    backend,
                    lease._binding.project_root_fd,
                    "parent/child/absent",
                    observation,
                )
        try:
            assert caught.value.errno == errno.EIO
            assert len(opened) == 2
            assert attempted == list(reversed(opened))
            for fd in opened:
                with pytest.raises(ProtocolError, match="unregistered"):
                    backend.provenance_of(fd)
        finally:
            for fd in opened:
                try:
                    backend.provenance_of(fd)
                except ProtocolError:
                    continue
                backend.close_fd(fd)


def test_a_command_refuses_missing_noclobber_before_creating_the_chain(
    coordinator_on, monkeypatch
):
    from atoms.fs import binding

    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    _, project_root, _, _ = ingredients
    probe_backend = binding.probe_backend

    def without_noclobber(*args, **kwargs):
        return probe_backend(*args, **kwargs) - {Capability.NOCLOBBER_TRANSFER}

    monkeypatch.setattr(binding, "probe_backend", without_noclobber)

    with pytest.raises(CapabilityUnavailable, match="noclobber_transfer"):
        _register(ingredients, b"opaque genesis", ())

    assert not (Path(project_root) / CHAIN_LEAF).exists()


def test_append_refuses_missing_noclobber_without_changing_chain_bytes_or_tip(
    coordinator_on, monkeypatch
):
    from atoms.fs import binding

    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    _, project_root, metadata_root, _ = ingredients
    genesis = _register(ingredients, b"root", ())
    before = _durable_entries(project_root)
    probe_backend = binding.probe_backend

    def without_noclobber(*args, **kwargs):
        return probe_backend(*args, **kwargs) - {Capability.NOCLOBBER_TRANSFER}

    monkeypatch.setattr(binding, "probe_backend", without_noclobber)

    with pytest.raises(CapabilityUnavailable, match="noclobber_transfer"):
        _append(ingredients, b"must not publish")

    assert _durable_entries(project_root) == before
    assert _fresh_process(project_root, metadata_root)["lease"]["chain"]["tip"] == genesis
