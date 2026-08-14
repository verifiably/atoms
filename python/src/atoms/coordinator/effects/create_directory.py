"""CreateDirectory forward execution."""

from __future__ import annotations

import errno
import os
import stat
from collections.abc import Callable

from atoms.coordinator.effects.common import EffectMismatch, run_determinate
from atoms.coordinator.effects.sites import MkdirSite
from atoms.core.effects import CreateDirectory
from atoms.core.errors import PreconditionRefused
from atoms.fs.audit import AuditedBackend, Provenance, RootKind


def apply(
    backend: AuditedBackend,
    site: MkdirSite,
    effect: CreateDirectory,
    *,
    gate: Callable[[], None],
) -> int:
    run_determinate(
        "mkdir_child",
        site.work_leaf,
        lambda: backend.mkdir_child(site.work_fd, site.work_leaf, 0o700),
    )
    run_determinate(
        "repair_entry_mode",
        site.work_leaf,
        lambda: backend.repair_entry_mode(
            site.work_fd, site.work_leaf, 0o700, before_change=gate
        ),
    )
    retained_fd = run_determinate(
        "open_child_directory",
        site.work_leaf,
        lambda: backend.open_child_directory(site.work_fd, site.work_leaf),
    )
    try:
        backend.set_mode(retained_fd, effect.post.mode)
        backend.flush_file(retained_fd)
        try:
            run_determinate(
                "transfer_noclobber",
                str(effect.path),
                lambda: backend.transfer_noclobber(
                    site.work_fd,
                    site.work_leaf,
                    site.parent_fd,
                    site.live_leaf,
                ),
                passthrough=(errno.EEXIST,),
            )
        except OSError as caught:
            if caught.errno != errno.EEXIST:
                raise
            _remove_work_if_ours(backend, retained_fd, site)
            backend.flush_directory(site.work_fd)
            raise PreconditionRefused(
                f"{effect.path!r} already exists; CreateDirectory refuses"
            ) from caught
        backend.flush_directory(site.parent_fd)
        _verify_directory(backend, retained_fd, site, effect)
        backend.flush_directory(site.work_fd)
        backend.rebind(
            retained_fd, Provenance(RootKind.PROJECT, str(effect.path))
        )
        return retained_fd
    except BaseException:
        backend.close_fd(retained_fd)
        raise


def _verify_directory(
    backend: AuditedBackend,
    retained_fd: int,
    site: MkdirSite,
    effect: CreateDirectory,
) -> None:
    live = run_determinate(
        "lstat",
        site.live_leaf,
        lambda: os.lstat(site.live_leaf, dir_fd=site.parent_fd),
    )
    retained = os.fstat(retained_fd)
    if (live.st_dev, live.st_ino) != (retained.st_dev, retained.st_ino):
        raise EffectMismatch("verify_identity", str(effect.path))
    if stat.S_IMODE(retained.st_mode) != effect.post.mode:
        raise EffectMismatch("verify_mode", str(effect.path))
    if os.listdir(retained_fd):
        raise EffectMismatch("verify_empty", str(effect.path))


def _remove_work_if_ours(
    backend: AuditedBackend, retained_fd: int, site: MkdirSite
) -> None:
    work = run_determinate(
        "lstat",
        site.work_leaf,
        lambda: os.lstat(site.work_leaf, dir_fd=site.work_fd),
    )
    retained = os.fstat(retained_fd)
    if (work.st_dev, work.st_ino) != (retained.st_dev, retained.st_ino):
        raise EffectMismatch("work_identity", site.work_leaf)
    run_determinate(
        "rmdir_child",
        site.work_leaf,
        lambda: backend.rmdir_child(site.work_fd, site.work_leaf),
    )
