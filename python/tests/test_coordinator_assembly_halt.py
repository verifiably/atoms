"""Approval-world drift becomes durable assembly evidence."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from atoms.chain.errors import ChainStateInvalid
from atoms.core.assembly import AssemblyFindingKind
from atoms.core.errors import TransactionHalted
from atoms.core.recovery import TransactionState
from atoms.core.scratch import CHAIN_LEAF, scratch_leaf
from atoms.store.errors import MetadataStoreInvalid
from tests.test_coordinator_resolve import (
    _prepare_registered,
    _prepare_registered_directory,
)


def _active_record(ingredients):
    from atoms.fs.binding import bind_project_volume
    from atoms.fs.lock import acquire_project_lock
    from atoms.store.connection import open_store
    from tests.fs_support import build_test_allowlist

    backend, project_root, metadata_root, storage = ingredients
    with acquire_project_lock(backend, metadata_root) as lock:
        allowlist = build_test_allowlist(lock, project_root, storage)
        with bind_project_volume(
            project_root, lock, allowlist=allowlist, storage=storage
        ) as binding, open_store(binding) as store:
            return store.read_active()


def test_missing_approved_directory_persists_and_replays_an_assembly_halt(
    coordinator_on, leased
) -> None:
    ingredients = coordinator_on()
    _, project_root, _, _ = ingredients
    with leased(ingredients) as lease:
        txid = _prepare_registered(lease)
    (Path(project_root) / "d").rename(Path(project_root) / "moved-d")

    with pytest.raises(TransactionHalted) as first, leased(ingredients):
        pass
    halt = first.value.args[0]
    assert tuple((item.path, item.kind) for item in halt.findings) == (
        ("d", AssemblyFindingKind.NODE_MISSING),
    )

    with pytest.raises(TransactionHalted) as second, leased(ingredients):
        pass
    assert second.value.args[0] == halt

    record = _active_record(ingredients)
    assert record is not None and record.txid == txid
    assert record.state is TransactionState.PREPARED
    assert record.assembly_halt == halt


def test_missing_transaction_work_slot_has_its_reserved_assembly_path(
    coordinator_on, leased
) -> None:
    ingredients = coordinator_on()
    with leased(ingredients) as lease:
        approved = _prepare_registered_directory(lease)
        work_parent = os.open(
            "work",
            os.O_RDONLY | os.O_DIRECTORY,
            dir_fd=lease._binding.metadata_root_fd,
        )
        try:
            os.rmdir(approved.txid, dir_fd=work_parent)
        finally:
            os.close(work_parent)

    with pytest.raises(TransactionHalted) as caught, leased(ingredients):
        pass
    halt = caught.value.args[0]
    assert tuple((item.path, item.kind) for item in halt.findings) == (
        (".#~work_root", AssemblyFindingKind.NODE_MISSING),
    )


def test_present_staging_slot_is_invalid_metadata_not_an_assembly_halt(
    coordinator_on, leased
) -> None:
    ingredients = coordinator_on()
    with leased(ingredients) as lease:
        txid = _prepare_registered(lease)
        staging_parent = os.open(
            "staging",
            os.O_RDONLY | os.O_DIRECTORY,
            dir_fd=lease._binding.metadata_root_fd,
        )
        try:
            os.mkdir(txid, dir_fd=staging_parent)
        finally:
            os.close(staging_parent)

    with pytest.raises(MetadataStoreInvalid, match="staging"), leased(ingredients):
        pass
    record = _active_record(ingredients)
    assert record is not None and record.assembly_halt is None


def test_a_frozen_factory_halt_never_masks_a_malformed_chain(
    coordinator_on, leased
) -> None:
    ingredients = coordinator_on()
    _, project_root, _, _ = ingredients
    with leased(ingredients) as lease:
        txid = _prepare_registered(lease)
        leaf = scratch_leaf(txid, "e1", "staging")
        parent_fd = os.open(
            "d",
            os.O_RDONLY | os.O_DIRECTORY,
            dir_fd=lease._binding.project_root_fd,
        )
        try:
            fd = os.open(
                leaf,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
                dir_fd=parent_fd,
            )
            try:
                os.write(fd, b"foreign")
            finally:
                os.close(fd)
        finally:
            os.close(parent_fd)

    with pytest.raises(TransactionHalted), leased(ingredients):
        pass
    chain = Path(project_root) / CHAIN_LEAF
    next(path for path in sorted(chain.iterdir()) if path.name != ".#~stage").write_bytes(
        b"malformed"
    )

    with pytest.raises(ChainStateInvalid), leased(ingredients):
        pass
