from __future__ import annotations

import json
import subprocess
import sys
from datetime import date as Date
from pathlib import Path

import pytest

from atoms.fs.volume import CERTIFIED_ALLOWLIST, VolumeConfiguration

ROOT = Path(__file__).parents[2]
CERTIFICATION = ROOT / "docs" / "certification"
CERTIFICATION_SCENARIOS = (
    "minimal-create",
    "minimal-replace",
    "minimal-delete",
    "minimal-move",
    "minimal-mkdir",
    "corpus-write",
    "archive-move",
    "caught-rollback",
    "caught-rollback-move",
)
STORAGE_CONTRACT = (
    "completed FLUSH makes all previously completed writes durable; "
    "a completed FUA write is durable at completion"
)
_WRITER_PROGRAM = r'''
import json
from pathlib import Path
import sys

from atoms.fs.volume import VolumeConfiguration
from tools.certify.record import write

names = (
    "minimal-create",
    "minimal-replace",
    "minimal-delete",
    "minimal-move",
    "minimal-mkdir",
    "corpus-write",
    "archive-move",
    "caught-rollback",
    "caught-rollback-move",
)
if sys.argv[4] == "empty":
    names = ()
elif sys.argv[4] == "subset":
    names = names[:1]
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
    scenarios=tuple(
        {
            "scenario": name,
            "marks": json.loads(sys.argv[2]),
            "prefixes": 310,
            "violations": json.loads(sys.argv[3]) if index == 0 else 0,
            "violation_details": [],
            "declared_cap": None,
        }
        for index, name in enumerate(names)
    ),
    date="2026-08-16",
)
'''


def _expected_record() -> dict[str, object]:
    return {
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
            "contract": STORAGE_CONTRACT,
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
                "name": name,
                "marks": 90,
                "prefixes": 310,
                "violations": 0,
                "declared_cap": None,
            }
            for name in CERTIFICATION_SCENARIOS
        ],
        "zero_violations": True,
    }


def _object(value: object, fields: set[str]) -> dict[str, object]:
    assert isinstance(value, dict)
    assert set(value) == fields
    return value


def _string(value: object) -> None:
    assert isinstance(value, str) and value


def _string_list(value: object, *, nonempty: bool = True) -> None:
    assert isinstance(value, list)
    assert not nonempty or value
    assert all(isinstance(item, str) and item for item in value)


def _assert_no_floats(value: object) -> None:
    assert not isinstance(value, float)
    if isinstance(value, dict):
        for key, item in value.items():
            _assert_no_floats(key)
            _assert_no_floats(item)
    elif isinstance(value, list):
        for item in value:
            _assert_no_floats(item)


def _assert_record_schema(document: object) -> None:
    _assert_no_floats(document)
    record = _object(
        document,
        {
            "record_version",
            "date",
            "configuration",
            "storage",
            "harness",
            "atoms_commit",
            "scenarios",
            "zero_violations",
        },
    )
    assert type(record["record_version"]) is int and record["record_version"] == 1
    date = record["date"]
    _string(date)
    assert isinstance(date, str) and Date.fromisoformat(date).isoformat() == date
    _string(record["atoms_commit"])
    assert record["zero_violations"] is True

    configuration = _object(
        record["configuration"],
        {
            "backend_id",
            "backend_revision",
            "kernel_identifier",
            "filesystem_type",
            "barrier_options",
            "durability_features",
        },
    )
    for field in ("backend_id", "backend_revision", "kernel_identifier", "filesystem_type"):
        _string(configuration[field])
    _string_list(configuration["barrier_options"])
    _string_list(configuration["durability_features"])

    storage = _object(record["storage"], {"profile_id", "contract"})
    _string(storage["profile_id"])
    assert storage["contract"] == STORAGE_CONTRACT

    harness = _object(
        record["harness"],
        {
            "qemu_command",
            "cache_mode",
            "mkfs_command",
            "mount_command",
            "versions",
            "replay_log_commit",
            "log_format_version",
        },
    )
    for field in ("qemu_command", "mkfs_command", "mount_command"):
        _string_list(harness[field])
    assert harness["cache_mode"] == "writeback"
    _string(harness["replay_log_commit"])
    _string(harness["log_format_version"])
    versions = _object(harness["versions"], {"qemu", "e2fsprogs", "kernel"})
    for version in versions.values():
        _string(version)

    scenarios = record["scenarios"]
    assert isinstance(scenarios, list) and scenarios
    names: list[str] = []
    for value in scenarios:
        row = _object(
            value,
            {"name", "marks", "prefixes", "violations", "declared_cap"},
        )
        name = row["name"]
        _string(name)
        assert isinstance(name, str)
        names.append(name)
        for field in ("marks", "prefixes"):
            count = row[field]
            assert isinstance(count, int) and not isinstance(count, bool) and count >= 0
        assert type(row["violations"]) is int and row["violations"] == 0
        cap = row["declared_cap"]
        assert cap is None or (type(cap) is int and cap > 0)
    assert len(names) == len(set(names))
    assert tuple(names) == CERTIFICATION_SCENARIOS


def _run_writer(
    output: Path, *, marks: str = "90", violations: str = "0", shape: str = "row"
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", _WRITER_PROGRAM, str(output), marks, violations, shape],
        check=False,
        cwd=ROOT / "python",
        capture_output=True,
        text=True,
    )


def test_writer_emits_the_exact_canonical_record(tmp_path: Path) -> None:
    output = tmp_path / "record.json"
    result = _run_writer(output)
    assert result.returncode == 0, result.stderr

    expected = _expected_record()
    encoded = output.read_text(encoding="utf-8")
    assert json.loads(encoded) == expected
    assert encoded == json.dumps(
        expected, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ) + "\n"


def test_writer_refuses_floats(tmp_path: Path) -> None:
    output = tmp_path / "record.json"
    result = _run_writer(output, marks="1.5")
    assert result.returncode != 0
    assert "certification records must not contain floats" in result.stderr
    assert not output.exists()


@pytest.mark.parametrize(
    ("violations", "shape", "message"),
    [
        pytest.param("0", "empty", "at least one scenario", id="empty"),
        pytest.param("0", "subset", "exact certification scenarios", id="subset"),
        pytest.param("false", "row", "violations must be an integer", id="boolean-false"),
    ],
)
def test_writer_refuses_evidence_that_cannot_prove_zero_violations(
    tmp_path: Path, violations: str, shape: str, message: str
) -> None:
    output = tmp_path / "record.json"
    result = _run_writer(output, violations=violations, shape=shape)
    assert result.returncode != 0
    assert message in result.stderr
    assert not output.exists()


def test_writer_records_positive_violations_as_failed_certification(tmp_path: Path) -> None:
    output = tmp_path / "record.json"
    result = _run_writer(output, violations="1")
    assert result.returncode == 0, result.stderr
    document = json.loads(output.read_text(encoding="utf-8"))
    assert document["zero_violations"] is False
    assert document["scenarios"][0]["violations"] == 1


@pytest.mark.parametrize("mutation", ["empty", "boolean-false"])
def test_collected_check_rejects_evidence_that_cannot_prove_zero_violations(
    mutation: str,
) -> None:
    document = _expected_record()
    _assert_record_schema(document)

    if mutation == "empty":
        document["scenarios"] = []
    else:
        scenarios = document["scenarios"]
        assert isinstance(scenarios, list) and isinstance(scenarios[0], dict)
        scenarios[0]["violations"] = False
    with pytest.raises(AssertionError):
        _assert_record_schema(document)


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
    _assert_record_schema(document)

    configuration = document["configuration"]
    assert entry.configuration == VolumeConfiguration(
        backend_id=configuration["backend_id"],
        backend_revision=configuration["backend_revision"],
        kernel_identifier=configuration["kernel_identifier"],
        filesystem_type=configuration["filesystem_type"],
        barrier_options=tuple(configuration["barrier_options"]),
        durability_features=tuple(configuration["durability_features"]),
    )
    assert entry.storage.profile_id == document["storage"]["profile_id"]
