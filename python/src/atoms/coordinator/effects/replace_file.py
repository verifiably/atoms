"""ReplaceFile forward execution."""

from __future__ import annotations

import os

from atoms.coordinator.effects.common import (
    EffectMismatch,
    build_staged_file,
    run_determinate,
    verify_live_file,
)
from atoms.coordinator.effects.sites import ReplaceSite
from atoms.core.effects import ReplaceFile
from atoms.fs.audit import AuditedBackend
from atoms.store.connection import Store


def apply(
    backend: AuditedBackend, store: Store, site: ReplaceSite, effect: ReplaceFile
) -> None:
    staged_fd = build_staged_file(
        backend, store, site.parent_fd, site.staging_leaf, effect.post
    )
    try:
        run_determinate(
            "exchange",
            str(effect.path),
            lambda: backend.exchange(
                site.parent_fd, site.live_leaf, site.staging_leaf
            ),
        )
        verify_live_file(
            backend, staged_fd, site.parent_fd, site.live_leaf, effect.post
        )
        _verify_displaced_pre(backend, site, effect)
        backend.flush_directory(site.parent_fd)
    except EffectMismatch:
        _exchange_back_if_ours(backend, staged_fd, site, effect.post)
        raise
    finally:
        backend.close_fd(staged_fd)


def _verify_displaced_pre(
    backend: AuditedBackend, site: ReplaceSite, effect: ReplaceFile
) -> None:
    displaced_fd = run_determinate(
        "open_regular_nofollow",
        str(effect.path),
        lambda: backend.open_regular_nofollow(site.parent_fd, site.staging_leaf),
    )
    try:
        verify_live_file(
            backend,
            displaced_fd,
            site.parent_fd,
            site.staging_leaf,
            effect.pre,
        )
    finally:
        backend.close_fd(displaced_fd)


def _exchange_back_if_ours(
    backend: AuditedBackend, staged_fd: int, site: ReplaceSite, state
) -> None:
    live = run_determinate(
        "lstat",
        site.live_leaf,
        lambda: os.lstat(site.live_leaf, dir_fd=site.parent_fd),
    )
    retained = os.fstat(staged_fd)
    if (live.st_dev, live.st_ino) != (retained.st_dev, retained.st_ino):
        return
    run_determinate(
        "exchange",
        site.live_leaf,
        lambda: backend.exchange(site.parent_fd, site.live_leaf, site.staging_leaf),
    )
    backend.flush_directory(site.parent_fd)
