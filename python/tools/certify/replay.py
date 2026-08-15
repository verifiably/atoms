"""Pinned xfstests replay-log build and prefix replay."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

XFS_TESTS_COMMIT = "acb6d4cb84205a8e3f19ca470cfcf7bf6d93a509"
_XFS_TESTS_URL = "https://git.kernel.org/pub/scm/fs/xfs/xfstests-dev.git"


def _run(command: list[str], *, cwd: Path | None = None) -> str:
    result = subprocess.run(command, cwd=cwd, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"{command[0]} failed with exit {result.returncode}: {detail}")
    return result.stdout.strip()


def ensure_replay_log(work: Path) -> Path:
    """Fetch the exact xfstests source revision and build replay-log."""
    workspace = Path(work)
    if workspace.exists() and not workspace.is_dir():
        raise ValueError(f"work path is not a directory: {workspace}")
    workspace.mkdir(parents=True, exist_ok=True)
    source = workspace / "xfstests"
    if not source.exists():
        source.mkdir()
        _run(["git", "init"], cwd=source)
        _run(["git", "remote", "add", "origin", _XFS_TESTS_URL], cwd=source)
    elif not (source / ".git").is_dir():
        raise ValueError(f"xfstests source is not a git checkout: {source}")
    remote = _run(["git", "remote", "get-url", "origin"], cwd=source)
    if remote != _XFS_TESTS_URL:
        raise ValueError(f"unexpected xfstests origin: {remote}")
    _run(["git", "fetch", "--depth", "1", "origin", XFS_TESTS_COMMIT], cwd=source)
    _run(["git", "checkout", "--detach", "FETCH_HEAD"], cwd=source)
    actual = _run(["git", "rev-parse", "HEAD"], cwd=source)
    if actual != XFS_TESTS_COMMIT:
        raise RuntimeError(f"xfstests checkout is {actual}, expected {XFS_TESTS_COMMIT}")

    output = workspace / "replay-log"
    temporary = workspace / "replay-log.tmp"
    log_writes = source / "src" / "log-writes"
    _run(
        [
            "gcc",
            "-O2",
            "-o",
            os.fspath(temporary),
            "replay-log.c",
            "log-writes.c",
        ],
        cwd=log_writes,
    )
    temporary.replace(output)
    return output


def replay_prefix(
    log_device: Path,
    clone_image: Path,
    *,
    end_mark: str | None,
    end_entry: int | None,
) -> None:
    """Replay exactly one mark- or entry-bounded log prefix onto a fresh clone."""
    if (end_mark is None) == (end_entry is None):
        raise ValueError("exactly one of end_mark and end_entry is required")
    if end_mark is not None and (not end_mark or "\0" in end_mark):
        raise ValueError("end_mark must be non-empty and contain no NUL bytes")
    if end_entry is not None and (
        not isinstance(end_entry, int) or isinstance(end_entry, bool) or end_entry < 0
    ):
        raise ValueError("end_entry must be a non-negative integer")
    log = Path(log_device)
    target = Path(clone_image)
    if not log.is_file() and not log.is_block_device():
        raise ValueError(f"log is not a file or block device: {log}")
    if not target.is_file() and not target.is_block_device():
        raise ValueError(f"replay target is not a file or block device: {target}")
    binary = Path(__file__).resolve().parents[2] / ".certify" / "replay-log"
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise RuntimeError(f"replay-log is not executable: {binary}")
    bound = ["--end-mark", end_mark] if end_mark is not None else ["--limit", str(end_entry)]
    _run(
        [
            os.fspath(binary),
            "--log",
            os.fspath(log),
            "--replay",
            os.fspath(target),
            *bound,
        ]
    )
