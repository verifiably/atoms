"""Host prerequisites for a reproducible certification run."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path

_PYTHON_ROOT = Path(__file__).resolve().parents[2]
_REPOSITORY_ROOT = _PYTHON_ROOT.parent
_REPLAY_LOG = _PYTHON_ROOT / ".certify" / "replay-log"


def _has_dm_log_writes_module() -> bool:
    modules = Path("/lib/modules") / platform.release()
    return any(modules.rglob("dm-log-writes.ko*")) if modules.is_dir() else False


def _is_clean_checkout() -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=_REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git status failed: {result.stderr.strip()}")
    return not result.stdout


def check() -> list[str]:
    """Return the required host capabilities that are unavailable."""
    checks = (
        ("qemu-system-x86_64", shutil.which("qemu-system-x86_64") is not None),
        ("/boot/vmlinuz-linux", os.access("/boot/vmlinuz-linux", os.R_OK)),
        ("dm-log-writes", _has_dm_log_writes_module()),
        ("mkinitcpio", shutil.which("mkinitcpio") is not None),
        ("dmsetup", shutil.which("dmsetup") is not None),
        ("mkfs.ext4", shutil.which("mkfs.ext4") is not None),
        ("debugfs", shutil.which("debugfs") is not None),
        ("replay-log", _REPLAY_LOG.is_file() and os.access(_REPLAY_LOG, os.X_OK)),
        ("clean atoms checkout", _is_clean_checkout()),
    )
    return [name for name, available in checks if not available]


def names() -> tuple[str, ...]:
    """Return prerequisite names in the order displayed by the CLI."""
    return (
        "qemu-system-x86_64",
        "/boot/vmlinuz-linux",
        "dm-log-writes",
        "mkinitcpio",
        "dmsetup",
        "mkfs.ext4",
        "debugfs",
        "replay-log",
        "clean atoms checkout",
    )
