"""DeletePath forward execution."""

from __future__ import annotations

import stat

from atoms.coordinator.effects.common import (
    EffectMismatch,
    run_determinate,
    verify_live_file,
)
from atoms.coordinator.effects.sites import DeleteSite
from atoms.core.effects import DeletePath
from atoms.core.fingerprint import FileState, SymlinkState
from atoms.fs.audit import AuditedBackend


def apply(backend: AuditedBackend, site: DeleteSite, effect: DeletePath) -> None:
    run_determinate(
        "transfer_noclobber",
        str(effect.path),
        lambda: backend.transfer_noclobber(
            site.parent_fd, site.live_leaf, site.parent_fd, site.tombstone_leaf
        ),
    )
    try:
        _verify_tombstone(backend, site, effect.pre)
        backend.flush_directory(site.parent_fd)
    except EffectMismatch:
        run_determinate(
            "transfer_noclobber",
            str(effect.path),
            lambda: backend.transfer_noclobber(
                site.parent_fd, site.tombstone_leaf, site.parent_fd, site.live_leaf
            ),
        )
        backend.flush_directory(site.parent_fd)
        raise


def _verify_tombstone(backend: AuditedBackend, site: DeleteSite, pre) -> None:
    if type(pre) is FileState:
        retained_fd = run_determinate(
            "open_regular_nofollow",
            site.tombstone_leaf,
            lambda: backend.open_regular_nofollow(
                site.parent_fd, site.tombstone_leaf
            ),
        )
        try:
            verify_live_file(
                backend,
                retained_fd,
                site.parent_fd,
                site.tombstone_leaf,
                pre,
            )
        finally:
            backend.close_fd(retained_fd)
        return
    if type(pre) is SymlinkState:
        info, target = run_determinate(
            "symlink_fingerprint",
            site.tombstone_leaf,
            lambda: backend.symlink_fingerprint(
                site.parent_fd, site.tombstone_leaf
            ),
        )
        if target != pre.target or stat.S_IMODE(info.st_mode) != pre.mode:
            raise EffectMismatch("verify_symlink", site.tombstone_leaf)
        return
    raise EffectMismatch("verify_preimage_kind", site.tombstone_leaf)
