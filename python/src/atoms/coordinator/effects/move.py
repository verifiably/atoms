"""MoveNoClobber forward execution."""

from __future__ import annotations

import os

from atoms.coordinator.effects.common import (
    EffectMismatch,
    run_determinate,
    verify_live_file,
)
from atoms.coordinator.effects.sites import MoveSite
from atoms.core.effects import MoveNoClobber
from atoms.fs.audit import AuditedBackend
from atoms.store.connection import Store


def apply(
    backend: AuditedBackend,
    store: Store,
    site: MoveSite,
    effect: MoveNoClobber,
) -> None:
    del store
    run_determinate(
        "link_anchor",
        str(effect.source),
        lambda: backend.link_anchor(
            site.source_fd, site.source_leaf, site.source_fd, site.anchor_leaf
        ),
    )
    backend.flush_directory(site.source_fd)
    anchor_fd = run_determinate(
        "open_regular_nofollow",
        site.anchor_leaf,
        lambda: backend.open_regular_nofollow(site.source_fd, site.anchor_leaf),
    )
    try:
        try:
            verify_live_file(
                backend,
                anchor_fd,
                site.source_fd,
                site.anchor_leaf,
                effect.source_pre,
            )
        except EffectMismatch:
            _remove_anchor_if_ours(backend, anchor_fd, site)
            backend.flush_directory(site.source_fd)
            raise

        run_determinate(
            "transfer_noclobber",
            str(effect.destination),
            lambda: backend.transfer_noclobber(
                site.source_fd,
                site.source_leaf,
                site.destination_fd,
                site.destination_leaf,
            ),
        )
        try:
            verify_live_file(
                backend,
                anchor_fd,
                site.destination_fd,
                site.destination_leaf,
                effect.source_pre,
            )
        except EffectMismatch:
            run_determinate(
                "transfer_noclobber",
                str(effect.source),
                lambda: backend.transfer_noclobber(
                    site.destination_fd,
                    site.destination_leaf,
                    site.source_fd,
                    site.source_leaf,
                ),
            )
            backend.flush_directory(site.source_fd)
            backend.flush_directory(site.destination_fd)
            raise
        backend.flush_directory(site.destination_fd)
        backend.flush_directory(site.source_fd)
    finally:
        backend.close_fd(anchor_fd)


def _remove_anchor_if_ours(
    backend: AuditedBackend, anchor_fd: int, site: MoveSite
) -> None:
    anchor = run_determinate(
        "lstat",
        site.anchor_leaf,
        lambda: os.lstat(site.anchor_leaf, dir_fd=site.source_fd),
    )
    retained = os.fstat(anchor_fd)
    if (anchor.st_dev, anchor.st_ino) != (retained.st_dev, retained.st_ino):
        return
    run_determinate(
        "unlink_child",
        site.anchor_leaf,
        lambda: backend.unlink_child(site.source_fd, site.anchor_leaf),
    )
