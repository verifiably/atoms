"""Canonical crash-certification record writer."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from pathlib import Path

from atoms.fs.volume import VolumeConfiguration

_STORAGE_CONTRACT = (
    "completed FLUSH makes all previously completed writes durable; "
    "a completed FUA write is durable at completion"
)


def _reject_floats(value: object) -> None:
    if isinstance(value, float):
        raise TypeError("certification records must not contain floats")
    if isinstance(value, Mapping):
        for key, item in value.items():
            _reject_floats(key)
            _reject_floats(item)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for item in value:
            _reject_floats(item)


def write(
    path: Path,
    *,
    configuration: VolumeConfiguration,
    storage_id: str,
    qemu_command: Sequence[str],
    cache_mode: str,
    mkfs_command: Sequence[str],
    mount_command: Sequence[str],
    versions: Mapping[str, str],
    replay_log_commit: str,
    log_format: str,
    kernel: str,
    atoms_commit: str,
    scenarios: Sequence[Mapping[str, object]],
    date: str,
) -> None:
    rows = [
        {
            "name": row["scenario"],
            "marks": row["marks"],
            "prefixes": row["prefixes"],
            "violations": row["violations"],
            "declared_cap": row["declared_cap"],
        }
        for row in scenarios
    ]
    document = {
        "record_version": 1,
        "date": date,
        "configuration": asdict(configuration),
        "storage": {"profile_id": storage_id, "contract": _STORAGE_CONTRACT},
        "harness": {
            "qemu_command": list(qemu_command),
            "cache_mode": cache_mode,
            "mkfs_command": list(mkfs_command),
            "mount_command": list(mount_command),
            "versions": dict(versions) | {"kernel": kernel},
            "replay_log_commit": replay_log_commit,
            "log_format_version": log_format,
        },
        "atoms_commit": atoms_commit,
        "scenarios": rows,
        "zero_violations": all(row["violations"] == 0 for row in rows),
    }
    _reject_floats(document)
    Path(path).write_text(
        json.dumps(document, ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n",
        encoding="utf-8",
    )
