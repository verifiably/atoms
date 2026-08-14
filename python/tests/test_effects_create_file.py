"""CreateFileNoClobber forward execution."""

from __future__ import annotations

import os
from typing import cast

import pytest

from atoms.coordinator.admission import admit
from atoms.coordinator.capture import capture_initial_surface
from atoms.coordinator.effects.create_file import apply
from atoms.coordinator.effects.sites import CreateFileSite, _site_for
from atoms.coordinator.prepare import open_workspace, prepare_transaction
from atoms.core.compiler import compile_spec
from atoms.core.effects import CreateFileNoClobber
from atoms.core.errors import PreconditionRefused
from tests.capture_support import AFTER, DictPayloads, digest_of
from tests.coordinator_support import create_file_spec, make_child_directory


def read_at(parent_fd: int, leaf: str) -> bytes:
    fd = os.open(leaf, os.O_RDONLY, dir_fd=parent_fd)
    try:
        return os.read(fd, 1024)
    finally:
        os.close(fd)


def absent_at(parent_fd: int, leaf: str) -> bool:
    try:
        os.lstat(leaf, dir_fd=parent_fd)
    except FileNotFoundError:
        return True
    return False


def test_create_file_publishes_the_postimage(leased) -> None:
    with leased() as lease:
        make_child_directory(lease)
        approved = admit(lease, compile_spec(create_file_spec()))
        with open_workspace(lease, approved) as workspace, capture_initial_surface(
            lease,
            approved,
            workspace,
            DictPayloads({digest_of(AFTER): AFTER}),
        ) as captured:
            prepare_transaction(lease, approved, workspace, captured.manifest)
            backend = lease._binding.backend
            backend.set_declared_paths(frozenset(entry.path for entry in approved.paths))
            effect = cast(CreateFileNoClobber, approved.compiled.spec.effects[0])
            site = cast(CreateFileSite, _site_for(approved, captured.descriptors, effect))

            apply(backend, lease._store, site, effect)

            assert read_at(site.parent_fd, site.live_leaf) == AFTER
            assert absent_at(site.parent_fd, site.staging_leaf)


@pytest.mark.parametrize("occupant", [b"foreign", AFTER])
def test_create_file_never_adopts_an_existing_occupant(leased, occupant: bytes) -> None:
    with leased() as lease:
        make_child_directory(lease)
        approved = admit(lease, compile_spec(create_file_spec()))
        with open_workspace(lease, approved) as workspace, capture_initial_surface(
            lease,
            approved,
            workspace,
            DictPayloads({digest_of(AFTER): AFTER}),
        ) as captured:
            prepare_transaction(lease, approved, workspace, captured.manifest)
            backend = lease._binding.backend
            backend.set_declared_paths(frozenset(entry.path for entry in approved.paths))
            effect = cast(CreateFileNoClobber, approved.compiled.spec.effects[0])
            site = cast(CreateFileSite, _site_for(approved, captured.descriptors, effect))
            fd = os.open(
                site.live_leaf,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o644,
                dir_fd=site.parent_fd,
            )
            os.write(fd, occupant)
            os.close(fd)

            with pytest.raises(PreconditionRefused):
                apply(backend, lease._store, site, effect)

            assert read_at(site.parent_fd, site.live_leaf) == occupant
            assert absent_at(site.parent_fd, site.staging_leaf)
