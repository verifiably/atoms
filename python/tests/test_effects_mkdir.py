"""CreateDirectory forward execution."""

from __future__ import annotations

import os
import stat
from typing import cast

from atoms.coordinator.admission import _require_admitted, admit
from atoms.coordinator.capture import capture_initial_surface
from atoms.coordinator.effects.create_directory import apply
from atoms.coordinator.effects.sites import MkdirSite, _site_for
from atoms.coordinator.prepare import open_workspace, prepare_transaction
from atoms.core.compiler import compile_spec
from atoms.core.effects import CreateDirectory
from atoms.fs.audit import Provenance, RootKind
from tests.capture_support import AFTER, DictPayloads, digest_of
from tests.coordinator_support import directory_spec


def test_mkdir_publishes_an_empty_directory_and_rebinds_its_descriptor(leased) -> None:
    with leased() as lease:
        approved = admit(lease, compile_spec(directory_spec()))
        with open_workspace(lease, approved) as workspace, capture_initial_surface(
            lease,
            approved,
            workspace,
            DictPayloads({digest_of(AFTER): AFTER}),
        ) as captured:
            prepare_transaction(lease, approved, workspace, captured.manifest)
            backend = lease._binding.backend
            backend.set_declared_paths(frozenset(entry.path for entry in approved.paths))
            effect = cast(CreateDirectory, approved.compiled.spec.effects[0])
            site = cast(MkdirSite, _site_for(approved, captured.descriptors, effect))

            retained_fd = apply(
                backend,
                site,
                effect,
                gate=lambda: _require_admitted(lease, approved),
            )
            try:
                assert stat.S_IMODE(os.fstat(retained_fd).st_mode) == effect.post.mode
                assert os.listdir(retained_fd) == []
                assert backend.provenance_of(retained_fd) == Provenance(
                    RootKind.PROJECT, str(effect.path)
                )
            finally:
                backend.close_fd(retained_fd)
