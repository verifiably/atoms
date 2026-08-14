"""DeletePath forward execution."""

from __future__ import annotations

import os
from typing import cast

from atoms.coordinator.admission import admit
from atoms.coordinator.capture import capture_initial_surface
from atoms.coordinator.effects.delete_path import apply
from atoms.coordinator.effects.sites import DeleteSite, _site_for
from atoms.coordinator.prepare import open_workspace, prepare_transaction
from atoms.core.compiler import compile_spec
from atoms.core.effects import DeletePath
from tests.capture_support import BEFORE, DictPayloads, write_project_file
from tests.coordinator_support import delete_spec


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


def test_delete_moves_the_verified_preimage_to_the_tombstone(leased) -> None:
    with leased() as lease:
        write_project_file(lease, "d/f.txt", BEFORE)
        approved = admit(lease, compile_spec(delete_spec()))
        with open_workspace(lease, approved) as workspace, capture_initial_surface(
            lease, approved, workspace, DictPayloads({})
        ) as captured:
            prepare_transaction(lease, approved, workspace, captured.manifest)
            backend = lease._binding.backend
            backend.set_declared_paths(frozenset(entry.path for entry in approved.paths))
            effect = cast(DeletePath, approved.compiled.spec.effects[0])
            site = cast(DeleteSite, _site_for(approved, captured.descriptors, effect))

            apply(backend, site, effect)

            assert absent_at(site.parent_fd, site.live_leaf)
            assert read_at(site.parent_fd, site.tombstone_leaf) == BEFORE
