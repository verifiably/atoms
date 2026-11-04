from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Mapping
from datetime import date as Date
from pathlib import Path

import pytest

from atoms.fs.volume import CERTIFIED_ALLOWLIST, VolumeConfiguration

ROOT = Path(__file__).parents[2]
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


def test_writer_refuses_to_replace_an_existing_record(tmp_path: Path) -> None:
    output = tmp_path / "record.json"
    assert _run_writer(output).returncode == 0
    original = output.read_bytes()

    result = _run_writer(output)
    assert result.returncode != 0
    assert "FileExistsError" in result.stderr
    assert output.read_bytes() == original


def test_record_cli_refuses_non_matrix_and_non_directory_destinations(tmp_path: Path) -> None:
    file = tmp_path / "not-a-directory"
    file.write_text("sentinel", encoding="utf-8")
    commands = (
        (
            "run",
            "--scenario",
            "minimal-create",
            "--record",
            str(tmp_path),
        ),
        ("run", "--all", "--record", str(file)),
        ("check", "--record", str(tmp_path)),
    )
    for command in commands:
        result = subprocess.run(
            [sys.executable, "-m", "tools.certify", *command],
            check=False,
            cwd=ROOT / "python",
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "--record" in result.stderr


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


def _resolve_certification_ref(
    certification_ref: str,
    *,
    root: Path = ROOT,
    env: Mapping[str, str] | None = None,
) -> Path | None:
    effective_env = os.environ if env is None else env
    explicit_dir = effective_env.get("ATOMS_CERTIFICATION_DIR")
    if explicit_dir:
        return Path(explicit_dir) / Path(certification_ref).name

    if (root / "docs" / "certification").is_dir():
        return root / certification_ref

    tasks_bin = shutil.which("tasks", path=effective_env.get("PATH"))
    if tasks_bin is None:
        return None

    proc = subprocess.run(
        [tasks_bin, "where", "--json"],
        cwd=root,
        env=dict(effective_env),
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    if proc.returncode != 0:
        try:
            err_payload = json.loads(proc.stderr)
        except json.JSONDecodeError:
            err_payload = None
        if isinstance(err_payload, dict):
            err_obj = err_payload.get("error")
            if isinstance(err_obj, dict):
                kind = err_obj.get("kind")
                detail = err_obj.get("detail")
                if kind == "records_unpaired" or (
                    kind == "config"
                    and isinstance(detail, str)
                    and "not on this machine" in detail
                ):
                    return None
        raise RuntimeError(
            f"tasks where failed ({proc.returncode}): "
            f"{proc.stderr.strip() or proc.stdout.strip()}"
        )

    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"tasks where returned invalid JSON: {proc.stdout!r}"
        ) from exc
    if type(payload) is not dict:
        raise RuntimeError(
            f"tasks where returned non-object JSON: {proc.stdout!r}"
        )
    project = payload.get("project")
    if project is None:
        return None
    if type(project) is not dict:
        raise RuntimeError(
            f"tasks where returned invalid project field: {proc.stdout!r}"
        )
    records = project.get("records")
    if records is None or records == "":
        return None
    if type(records) is not str:
        raise RuntimeError(
            f"tasks where returned invalid project.records field: {proc.stdout!r}"
        )
    records_path = Path(records)
    if records_path.is_dir():
        return records_path / certification_ref
    return None


def test_certification_record_matches_the_production_allowlist() -> None:
    assert len(CERTIFIED_ALLOWLIST.entries) == 1
    for entry in CERTIFIED_ALLOWLIST.entries:
        record = _resolve_certification_ref(entry.certification_ref)
        if record is None:
            pytest.skip(
                f"certification record {entry.certification_ref} is not present in this checkout "
                "(private records unavailable)"
            )

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


def _write_tasks_stub(bin_dir: Path, body: str) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    stub = bin_dir / "tasks"
    stub.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    stub.chmod(0o755)


def test_resolve_certification_ref_uses_explicit_env_dir_when_file_exists(
    tmp_path: Path,
) -> None:
    env_dir = tmp_path / "cert"
    env_dir.mkdir()
    ref = "docs/certification/2026-09-03-ext4-linux-7.2.2-arch1-1.json"
    expected = env_dir / Path(ref).name
    expected.write_text("{}\n", encoding="utf-8")

    resolved = _resolve_certification_ref(
        ref,
        root=tmp_path / "root",
        env={"ATOMS_CERTIFICATION_DIR": str(env_dir), "PATH": ""},
    )
    assert resolved == expected


def test_resolve_certification_ref_returns_missing_path_under_explicit_env_dir_so_caller_fails(
    tmp_path: Path,
) -> None:
    env_dir = tmp_path / "cert"
    env_dir.mkdir()
    ref = "docs/certification/2026-09-03-ext4-linux-7.2.2-arch1-1.json"

    resolved = _resolve_certification_ref(
        ref,
        root=tmp_path / "root",
        env={"ATOMS_CERTIFICATION_DIR": str(env_dir), "PATH": ""},
    )
    assert resolved == env_dir / Path(ref).name
    assert resolved is not None
    with pytest.raises(FileNotFoundError):
        resolved.read_text(encoding="utf-8")


def test_resolve_certification_ref_returns_missing_path_under_discovered_records_dir_so_caller_fails(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    records_dir = tmp_path / "records" / "atoms"
    records_dir.mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    payload = json.dumps({"project": {"records": str(records_dir)}})
    _write_tasks_stub(bin_dir, f"printf '%s\\n' '{payload}'")
    ref = "docs/certification/2026-09-03-ext4-linux-7.2.2-arch1-1.json"

    resolved = _resolve_certification_ref(
        ref,
        root=root,
        env={"PATH": str(bin_dir)},
    )
    assert resolved == records_dir / ref
    assert resolved is not None
    with pytest.raises(FileNotFoundError):
        resolved.read_text(encoding="utf-8")


def test_resolve_certification_ref_returns_none_when_records_unavailable(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    ref = "docs/certification/2026-09-03-ext4-linux-7.2.2-arch1-1.json"

    assert _resolve_certification_ref(ref, root=root, env={"PATH": ""}) is None

    for idx, payload in enumerate(
        (
            {"project": None},
            {"project": {"records": None}},
            {"project": {"records": str(tmp_path / "missing-records")}},
        )
    ):
        bin_dir = tmp_path / f"bin-{idx}"
        _write_tasks_stub(bin_dir, f"printf '%s\\n' '{json.dumps(payload)}'")
        assert (
            _resolve_certification_ref(ref, root=root, env={"PATH": str(bin_dir)})
            is None
        )


def test_resolve_certification_ref_ignores_ambient_pretty_format(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    records_dir = tmp_path / "records" / "atoms"
    records_dir.mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    payload = json.dumps({"project": {"records": str(records_dir)}})
    _write_tasks_stub(
        bin_dir,
        'case "$*" in *--json*) printf \'%s\\n\' \''
        + payload
        + "' ;; *) printf 'pretty output\\n' ;; esac",
    )
    ref = "docs/certification/2026-09-03-ext4-linux-7.2.2-arch1-1.json"

    resolved = _resolve_certification_ref(
        ref,
        root=root,
        env={"PATH": str(bin_dir), "TASKS_FORMAT": "pretty"},
    )
    assert resolved == records_dir / ref


def test_resolve_certification_ref_raises_on_corrupt_tasks_output(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    ref = "docs/certification/2026-09-03-ext4-linux-7.2.2-arch1-1.json"

    bin_bad_json = tmp_path / "bin-bad-json"
    _write_tasks_stub(bin_bad_json, "printf 'not-json\\n'")
    with pytest.raises(RuntimeError, match="tasks where"):
        _resolve_certification_ref(ref, root=root, env={"PATH": str(bin_bad_json)})

    bin_err = tmp_path / "bin-err"
    err_payload = json.dumps({"error": {"kind": "config", "detail": "TOML parse error"}})
    _write_tasks_stub(bin_err, f"printf '%s\\n' '{err_payload}' >&2; exit 1")
    with pytest.raises(RuntimeError, match="tasks where"):
        _resolve_certification_ref(ref, root=root, env={"PATH": str(bin_err)})

