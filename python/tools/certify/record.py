"""Canonical crash-certification record writer."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from datetime import date as Date
from pathlib import Path

from atoms.fs.volume import VolumeConfiguration

_STORAGE_CONTRACT = (
    "completed FLUSH makes all previously completed writes durable; "
    "a completed FUA write is durable at completion"
)
_ROW_KEYS = frozenset(
    {"scenario", "marks", "prefixes", "violations", "violation_details", "declared_cap"}
)


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise TypeError(f"{label} must be a nonempty string")
    return value


def _strings(value: object, label: str, *, nonempty: bool = True) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise TypeError(f"{label} must be a string vector")
    result = [_string(item, f"{label} item") for item in value]
    if nonempty and not result:
        raise ValueError(f"{label} must not be empty")
    return result


def _count(row: Mapping[str, object], field: str) -> int:
    value = row[field]
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise TypeError(f"scenario {field} must be an integer at least zero")
    return value


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
    _reject_floats(scenarios)
    if not isinstance(configuration, VolumeConfiguration):
        raise TypeError("configuration must be a VolumeConfiguration")
    for field in (
        "backend_id",
        "backend_revision",
        "kernel_identifier",
        "filesystem_type",
    ):
        _string(getattr(configuration, field), f"configuration.{field}")
    _strings(configuration.barrier_options, "configuration.barrier_options", nonempty=False)
    _strings(
        configuration.durability_features,
        "configuration.durability_features",
        nonempty=False,
    )
    _string(storage_id, "storage_id")
    _strings(qemu_command, "qemu_command")
    _string(cache_mode, "cache_mode")
    _strings(mkfs_command, "mkfs_command")
    _strings(mount_command, "mount_command")
    if set(versions) != {"qemu", "e2fsprogs"}:
        raise ValueError("versions must contain exactly qemu and e2fsprogs")
    for name, version in versions.items():
        _string(version, f"versions.{name}")
    for value, label in (
        (replay_log_commit, "replay_log_commit"),
        (log_format, "log_format"),
        (kernel, "kernel"),
        (atoms_commit, "atoms_commit"),
        (date, "date"),
    ):
        _string(value, label)
    if Date.fromisoformat(date).isoformat() != date:
        raise ValueError("date must be canonical YYYY-MM-DD")

    if not scenarios:
        raise ValueError("certification requires at least one scenario")
    rows: list[dict[str, object]] = []
    names: set[str] = set()
    for row in scenarios:
        if set(row) != _ROW_KEYS:
            raise ValueError("scenario row has the wrong fields")
        name = _string(row["scenario"], "scenario name")
        if name in names:
            raise ValueError(f"duplicate scenario name: {name}")
        names.add(name)
        cap = row["declared_cap"]
        if cap is not None and (
            not isinstance(cap, int) or isinstance(cap, bool) or cap <= 0
        ):
            raise TypeError("scenario declared_cap must be None or a positive integer")
        details = row["violation_details"]
        if not isinstance(details, list):
            raise TypeError("scenario violation_details must be a list")
        _strings(details, "scenario violation_details", nonempty=False)
        rows.append(
            {
                "name": name,
                "marks": _count(row, "marks"),
                "prefixes": _count(row, "prefixes"),
                "violations": _count(row, "violations"),
                "declared_cap": cap,
            }
        )
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
