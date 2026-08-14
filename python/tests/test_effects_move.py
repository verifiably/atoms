"""MoveNoClobber forward execution."""

from __future__ import annotations

import os
from typing import cast

from atoms.coordinator.admission import admit
from atoms.coordinator.capture import capture_initial_surface
from atoms.coordinator.effects.move import apply
from atoms.coordinator.effects.sites import MoveSite, _site_for
from atoms.coordinator.prepare import open_workspace, prepare_transaction
from atoms.core.compiler import compile_spec
from atoms.core.effects import MoveNoClobber
from tests.capture_support import BEFORE, DictPayloads, write_project_file
from tests.coordinator_support import move_spec


def test_move_publishes_destination_and_retains_the_anchor(leased) -> None:
    with leased() as lease:
        write_project_file(lease, "d/source.txt", BEFORE)
        approved = admit(lease, compile_spec(move_spec()))
        with open_workspace(lease, approved) as workspace, capture_initial_surface(
            lease, approved, workspace, DictPayloads({})
        ) as captured:
            prepare_transaction(lease, approved, workspace, captured.manifest)
            backend = lease._binding.backend
            backend.set_declared_paths(frozenset(entry.path for entry in approved.paths))
            effect = cast(MoveNoClobber, approved.compiled.spec.effects[0])
            site = cast(MoveSite, _site_for(approved, captured.descriptors, effect))

            apply(backend, lease._store, site, effect)

            destination = os.lstat(site.destination_leaf, dir_fd=site.destination_fd)
            anchor = os.lstat(site.anchor_leaf, dir_fd=site.source_fd)
            assert (destination.st_dev, destination.st_ino) == (
                anchor.st_dev,
                anchor.st_ino,
            )
            try:
                os.lstat(site.source_leaf, dir_fd=site.source_fd)
            except FileNotFoundError:
                pass
            else:
                raise AssertionError("move left the source name present")
