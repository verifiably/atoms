from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from atoms.fs.volume import CERTIFIED_ALLOWLIST, VolumeConfiguration

ROOT = Path(__file__).parents[2]
CERTIFICATION = ROOT / "docs" / "certification"
_WRITER_PROGRAM = r'''
import json
from pathlib import Path
import sys

from atoms.fs.volume import VolumeConfiguration
from tools.certify.record import write

write(
    Path(sys.argv[1]),
    configuration=VolumeConfiguration(
        backend_id="linux",
        backend_revision="linux-4",
        kernel_identifier="7.1.8-arch1-3",
        filesystem_type="ext4",
        barrier_options=("async", "barrier=1", "commit=5", "data=ordered"),
        durability_features=("compat=0x3c", "incompat=0x246", "ro_compat=0x46b"),
    ),
    storage_id="flush-honoring-disk.v1",
    qemu_command=("qemu-system-x86_64", "-accel", "kvm"),
    cache_mode="writeback",
    mkfs_command=("mkfs.ext4", "-O", "features"),
    mount_command=("mount", "-o", "barrier,data=ordered"),
    versions={"qemu": "9.2.4", "e2fsprogs": "1.47.4"},
    replay_log_commit="acb6d4",
    log_format="1",
    kernel="7.1.8-arch1-3",
    atoms_commit="0123456789abcdef",
    scenarios=(
        {
            "scenario": "minimal-create",
            "marks": json.loads(sys.argv[2]),
            "prefixes": 310,
            "violations": 0,
            "declared_cap": None,
        },
    ),
    date="2026-08-16",
)
'''


def test_writer_emits_the_exact_canonical_record(tmp_path: Path) -> None:
    output = tmp_path / "record.json"
    result = subprocess.run(
        [sys.executable, "-c", _WRITER_PROGRAM, str(output), "90"],
        check=False,
        cwd=ROOT / "python",
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    expected = {
        "record_version": 1,
        "date": "2026-08-16",
        "configuration": {
            "backend_id": "linux",
            "backend_revision": "linux-4",
            "kernel_identifier": "7.1.8-arch1-3",
            "filesystem_type": "ext4",
            "barrier_options": ["async", "barrier=1", "commit=5", "data=ordered"],
            "durability_features": ["compat=0x3c", "incompat=0x246", "ro_compat=0x46b"],
        },
        "storage": {
            "profile_id": "flush-honoring-disk.v1",
            "contract": (
                "completed FLUSH makes all previously completed writes durable; "
                "a completed FUA write is durable at completion"
            ),
        },
        "harness": {
            "qemu_command": ["qemu-system-x86_64", "-accel", "kvm"],
            "cache_mode": "writeback",
            "mkfs_command": ["mkfs.ext4", "-O", "features"],
            "mount_command": ["mount", "-o", "barrier,data=ordered"],
            "versions": {
                "qemu": "9.2.4",
                "e2fsprogs": "1.47.4",
                "kernel": "7.1.8-arch1-3",
            },
            "replay_log_commit": "acb6d4",
            "log_format_version": "1",
        },
        "atoms_commit": "0123456789abcdef",
        "scenarios": [
            {
                "name": "minimal-create",
                "marks": 90,
                "prefixes": 310,
                "violations": 0,
                "declared_cap": None,
            }
        ],
        "zero_violations": True,
    }
    encoded = output.read_text(encoding="utf-8")
    assert json.loads(encoded) == expected
    assert encoded == json.dumps(
        expected, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ) + "\n"


def test_writer_refuses_floats(tmp_path: Path) -> None:
    output = tmp_path / "record.json"
    result = subprocess.run(
        [sys.executable, "-c", _WRITER_PROGRAM, str(output), "1.5"],
        check=False,
        cwd=ROOT / "python",
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "certification records must not contain floats" in result.stderr
    assert not output.exists()


def test_certification_record_matches_the_production_allowlist() -> None:
    records = sorted(CERTIFICATION.glob("*.json"))
    if not records:
        pytest.skip("no certification record has landed")

    assert len(CERTIFIED_ALLOWLIST.entries) == 1
    entry = next(iter(CERTIFIED_ALLOWLIST.entries))
    record = ROOT / entry.certification_ref
    assert record in records

    encoded = record.read_text(encoding="utf-8")
    document = json.loads(encoded)
    assert encoded == json.dumps(
        document, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ) + "\n"
    assert document["zero_violations"] is True
    assert all(row["violations"] == 0 for row in document["scenarios"])

    configuration = document["configuration"]
    assert set(configuration) == {
        "backend_id",
        "backend_revision",
        "kernel_identifier",
        "filesystem_type",
        "barrier_options",
        "durability_features",
    }
    assert entry.configuration == VolumeConfiguration(
        backend_id=configuration["backend_id"],
        backend_revision=configuration["backend_revision"],
        kernel_identifier=configuration["kernel_identifier"],
        filesystem_type=configuration["filesystem_type"],
        barrier_options=tuple(configuration["barrier_options"]),
        durability_features=tuple(configuration["durability_features"]),
    )
    assert entry.storage.profile_id == document["storage"]["profile_id"]
