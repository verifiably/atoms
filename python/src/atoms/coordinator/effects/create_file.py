"""CreateFileNoClobber forward execution."""

from __future__ import annotations

import errno
import os

from atoms.coordinator.effects.common import (
    EffectMismatch,
    build_staged_file,
    run_determinate,
    verify_live_file,
)
from atoms.coordinator.effects.sites import CreateFileSite
from atoms.core.effects import CreateFileNoClobber
from atoms.core.errors import PreconditionRefused
from atoms.fs.audit import AuditedBackend
from atoms.store.connection import Store


def apply(
    backend: AuditedBackend,
    store: Store,
    site: CreateFileSite,
    effect: CreateFileNoClobber,
) -> None:
    staged_fd = build_staged_file(
        backend, store, site.parent_fd, site.staging_leaf, effect.post
    )
    try:
        try:
            run_determinate(
                "transfer_noclobber",
                str(effect.path),
                lambda: backend.transfer_noclobber(
                    site.parent_fd,
                    site.staging_leaf,
                    site.parent_fd,
                    site.live_leaf,
                ),
                passthrough=(errno.EEXIST,),
            )
        except OSError as caught:
            if caught.errno != errno.EEXIST:
                raise
            _remove_attributable_staging(backend, staged_fd, site)
            backend.flush_directory(site.parent_fd)
            raise PreconditionRefused(
                f"{effect.path!r} already exists; CreateFileNoClobber refuses"
            ) from caught
        verify_live_file(
            backend, staged_fd, site.parent_fd, site.live_leaf, effect.post
        )
        backend.flush_directory(site.parent_fd)
    finally:
        backend.close_fd(staged_fd)


def _remove_attributable_staging(
    backend: AuditedBackend, staged_fd: int, site: CreateFileSite
) -> None:
    staged = run_determinate(
        "lstat",
        site.staging_leaf,
        lambda: os.lstat(site.staging_leaf, dir_fd=site.parent_fd),
    )
    retained = os.fstat(staged_fd)
    if (staged.st_dev, staged.st_ino) != (retained.st_dev, retained.st_ino):
        raise EffectMismatch("staging_identity", site.staging_leaf)
    run_determinate(
        "unlink_child",
        site.staging_leaf,
        lambda: backend.unlink_child(site.parent_fd, site.staging_leaf),
    )
