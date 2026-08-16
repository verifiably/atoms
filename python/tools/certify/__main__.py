"""Command line entry point for host certification tooling."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from dataclasses import asdict
from datetime import datetime as DateTime
from pathlib import Path

from atoms.fs.volume import (
    FeatureMasks,
    VolumeConfiguration,
    build_configuration,
    kernel_identifier,
    read_mountinfo,
    resolve_mount_entry,
)

from . import guest, prerequisites, record
from .images import build_log_image
from .record import CERTIFICATION_SCENARIOS
from .replay import XFS_TESTS_COMMIT, ensure_replay_log

_PYTHON_ROOT = Path(__file__).resolve().parents[2]
_REPOSITORY_ROOT = _PYTHON_ROOT.parent
_STORAGE_ID = "flush-honoring-disk.v1"


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _target_configuration(path: Path) -> VolumeConfiguration:
    target = path.resolve()
    descriptor = os.open(target, os.O_RDONLY | os.O_DIRECTORY)
    try:
        entry = resolve_mount_entry(descriptor, read_mountinfo())
        return build_configuration(entry, kernel_identifier(), directory_fd=descriptor)
    finally:
        os.close(descriptor)


def _record_directory(destination: Path, all_scenarios: bool, trials: int) -> Path:
    if not all_scenarios or trials != 1:
        raise ValueError("--record requires run --all with exactly one trial")
    path = destination if destination.is_absolute() else _REPOSITORY_ROOT / destination
    path = path.resolve()
    if path.exists():
        if not path.is_dir():
            raise ValueError(f"--record destination is not a directory: {path}")
    elif not path.parent.is_dir():
        raise ValueError(f"--record destination parent is not a directory: {path.parent}")
    return path


def _record_path(
    directory: Path, configuration: VolumeConfiguration, certification_date: str
) -> Path:
    if configuration.filesystem_type != "ext4" or configuration.backend_id != "linux":
        raise ValueError("certification records require the ext4 Linux backend")
    kernel = configuration.kernel_identifier
    if not kernel or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._+-" for character in kernel):
        raise ValueError(f"kernel identifier is unsafe for a record filename: {kernel!r}")
    output = directory / f"{certification_date}-ext4-linux-{kernel}.json"
    if output.exists():
        raise FileExistsError(f"certification record already exists: {output}")
    return output


def _string_vector(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or not all(isinstance(item, str) and item for item in value):
        raise guest.GuestRunError(f"guest reported invalid {label}")
    return tuple(value)


def _guest_harness_evidence(
    final: dict[str, object], configuration: VolumeConfiguration
) -> tuple[tuple[str, ...], tuple[str, ...], str]:
    expected = json.loads(json.dumps(asdict(configuration)))
    if final.get("configuration") != expected:
        raise guest.GuestRunError("guest configuration does not equal the selected target")
    if final.get("storage") != _STORAGE_ID:
        raise guest.GuestRunError("guest storage profile does not equal the certification profile")
    log_format = final.get("log_format_version")
    if log_format != "1":
        raise guest.GuestRunError(f"guest reported invalid log format: {log_format!r}")
    assert isinstance(log_format, str)
    return (
        _string_vector(final.get("mkfs_command"), "mkfs command"),
        _string_vector(final.get("mount_command"), "mount command"),
        log_format,
    )


def _qemu_cache_mode(command: tuple[str, ...]) -> str:
    drives = [command[index + 1] for index, value in enumerate(command[:-1]) if value == "-drive"]
    if len(drives) != 2:
        raise guest.GuestRunError("retained QEMU command does not contain exactly two drives")
    modes = {
        field.removeprefix("cache=")
        for drive in drives
        for field in drive.split(",")
        if field.startswith("cache=")
    }
    if len(modes) != 1 or any("cache=" not in drive for drive in drives):
        raise guest.GuestRunError("retained QEMU drives do not share one explicit cache mode")
    return modes.pop()


def _version(command: list[str]) -> str:
    lines = guest._run(command).stdout.splitlines()
    if not lines or not lines[0]:
        raise RuntimeError(f"{command[0]} returned no version")
    return lines[0]


def _feature_masks(configuration: VolumeConfiguration) -> FeatureMasks:
    values = dict(option.split("=", 1) for option in configuration.durability_features)
    if set(values) != {"compat", "incompat", "ro_compat"}:
        raise RuntimeError("target configuration has no complete ext4 feature-mask vector")
    return FeatureMasks(*(int(values[name], 16) for name in ("compat", "incompat", "ro_compat")))


def _mount_options(configuration: VolumeConfiguration) -> str:
    options: list[str] = []
    for option in configuration.barrier_options:
        if option == "async":
            continue
        if option == "barrier=1":
            options.append("barrier")
        elif option.startswith(("data=", "commit=")) or option in {
            "nobarrier",
            "journal_async_commit",
            "sync",
            "dirsync",
        }:
            options.append(option)
        else:
            raise RuntimeError(f"cannot reproduce target mount option {option!r}")
    return ",".join(options)


def _build_guest_initramfs(work: Path) -> Path:
    ensure_replay_log(_PYTHON_ROOT / ".certify")
    return guest.build_initramfs(work / "init")


def _self_test(acceleration: str) -> int:
    missing = prerequisites.check()
    if missing:
        raise RuntimeError(f"missing certification prerequisites: {', '.join(missing)}")
    with tempfile.TemporaryDirectory(prefix="atoms-certify-host-") as temporary:
        work = Path(temporary)
        initramfs = _build_guest_initramfs(work)
        data = build_log_image(work / "data.img", 8)
        log = build_log_image(work / "log.img", 8)
        before = (_digest(data), _digest(log))
        result = guest.run(
            Path("/boot/vmlinuz-linux"),
            initramfs,
            data,
            log,
            shared_root=Path("/"),
            guest_arguments=("--self-test",),
            acceleration=acceleration,
        )
        if not result.records or result.records[-1] != {"self_test": "ok"}:
            raise guest.GuestRunError("guest did not report a successful self-test")
        if (_digest(data), _digest(log)) != before:
            raise guest.GuestRunError("scratch self-test mutated an attached certification device")
    return 0


def _selected_scenarios(scenario: str | None, all_scenarios: bool) -> tuple[str, ...]:
    if (scenario is None) == (not all_scenarios):
        raise ValueError("run requires exactly one of --scenario or --all")
    if all_scenarios:
        return CERTIFICATION_SCENARIOS
    assert scenario is not None
    if scenario not in CERTIFICATION_SCENARIOS:
        raise ValueError(f"scenario is not certifiable: {scenario}")
    return (scenario,)


def _row_count(row: dict[str, object], field: str) -> int:
    value = row.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise guest.GuestRunError(f"scenario reported invalid {field}: {value!r}")
    return value


def _validate_scenario_row(
    row: dict[str, object], scenario: str, declared_cap: int | None
) -> dict[str, object]:
    if row.get("scenario") != scenario:
        raise guest.GuestRunError("guest reported the wrong scenario")
    for field in ("marks", "prefixes"):
        _row_count(row, field)
    if _row_count(row, "violations") != 0:
        raise guest.GuestRunError(f"scenario did not report zero violations: {row}")
    if "declared_cap" not in row:
        raise guest.GuestRunError("guest omitted the declared prefix cap")
    if declared_cap is None:
        if row["declared_cap"] is not None:
            raise guest.GuestRunError("guest changed the undeclared prefix cap")
    elif _row_count(row, "declared_cap") != declared_cap:
        raise guest.GuestRunError("guest did not preserve the declared prefix cap")
    return row


def _run_scenarios(
    scenarios: tuple[str, ...],
    trials: int,
    target: Path,
    declared_cap: int | None,
    acceleration: str,
    record_directory: Path | None = None,
) -> int:
    if trials != 1:
        raise ValueError("certification supports exactly one trial")
    if declared_cap is not None and (
        not isinstance(declared_cap, int) or isinstance(declared_cap, bool) or declared_cap <= 0
    ):
        raise ValueError("--declare-cap must be a positive integer")
    configuration = _target_configuration(target)
    if configuration.filesystem_type != "ext4":
        raise RuntimeError(f"certification target is not ext4: {configuration.filesystem_type}")
    certification_date = DateTime.now().astimezone().date().isoformat()
    output = (
        _record_path(record_directory, configuration, certification_date)
        if record_directory is not None
        else None
    )
    atoms_commit = guest.checkout_commit() if output is not None else None
    missing = prerequisites.check()
    if missing:
        raise RuntimeError(f"missing certification prerequisites: {', '.join(missing)}")
    masks = _feature_masks(configuration)
    with tempfile.TemporaryDirectory(prefix="atoms-certify-host-") as temporary:
        work = Path(temporary)
        initramfs = _build_guest_initramfs(work)
        rows: list[dict[str, object]] = []
        final_result: guest.GuestResult | None = None
        harness: tuple[tuple[str, ...], tuple[str, ...], str] | None = None
        for scenario in scenarios:
            with tempfile.TemporaryDirectory(prefix=f"{scenario}-", dir=work) as temporary:
                scenario_work = Path(temporary)
                data = build_log_image(scenario_work / "data.img", 128)
                log = build_log_image(scenario_work / "log.img", 512)
                arguments = [
                    "--scenario",
                    scenario,
                    "--trials",
                    str(trials),
                    "--compat",
                    str(masks.compat),
                    "--incompat",
                    str(masks.incompat),
                    "--ro-compat",
                    str(masks.ro_compat),
                    "--mount-options",
                    _mount_options(configuration),
                ]
                if declared_cap is not None:
                    arguments += ["--declare-cap", str(declared_cap)]
                result = guest.run(
                    Path("/boot/vmlinuz-linux"),
                    initramfs,
                    data,
                    log,
                    shared_root=Path("/"),
                    guest_arguments=tuple(arguments),
                    acceleration=acceleration,
                )
                if fatal := next((row for row in result.records if "fatal" in row), None):
                    raise guest.GuestRunError(
                        f"guest reported a fatal certification error: {fatal}"
                    )
                row = next(
                    (
                        record
                        for record in result.records
                        if record.get("scenario") == scenario and "violations" in record
                    ),
                    None,
                )
                if row is None:
                    raise guest.GuestRunError("guest omitted the scenario result")
                _validate_scenario_row(row, scenario, declared_cap)
                if not result.records:
                    raise guest.GuestRunError("guest omitted its final summary")
                harness = _guest_harness_evidence(result.records[-1], configuration)
                rows.append(row)
                final_result = result
        total_marks = sum(_row_count(row, "marks") for row in rows)
        total_prefixes = sum(_row_count(row, "prefixes") for row in rows)
        if output is not None:
            if scenarios != CERTIFICATION_SCENARIOS or len(rows) != len(CERTIFICATION_SCENARIOS):
                raise guest.GuestRunError("recording requires the exact certification matrix")
            assert (
                record_directory is not None
                and final_result is not None
                and harness is not None
                and atoms_commit is not None
            )
            if guest.checkout_commit() != atoms_commit:
                raise guest.GuestRunError("atoms checkout changed during certification")
            if not record_directory.exists():
                record_directory.mkdir()
            if not record_directory.is_dir():
                raise ValueError(f"--record destination is not a directory: {record_directory}")
            _record_path(record_directory, configuration, certification_date)
            mkfs_command, mount_command, log_format = harness
            record.write(
                output,
                configuration=configuration,
                storage_id=_STORAGE_ID,
                qemu_command=final_result.command,
                cache_mode=_qemu_cache_mode(final_result.command),
                mkfs_command=mkfs_command,
                mount_command=mount_command,
                versions={
                    "qemu": _version(["qemu-system-x86_64", "--version"]),
                    "e2fsprogs": _version(["mkfs.ext4", "-V"]),
                },
                replay_log_commit=XFS_TESTS_COMMIT,
                log_format=log_format,
                kernel=configuration.kernel_identifier,
                atoms_commit=atoms_commit,
                scenarios=rows,
                date=certification_date,
            )
        print(
            "CERTIFY-JSON:"
            + json.dumps(
                {
                    "configuration": json.loads(json.dumps(asdict(configuration))),
                    "storage": _STORAGE_ID,
                    "scenarios": rows,
                    "total_marks": total_marks,
                    "total_prefixes": total_prefixes,
                    **({"record": os.fspath(output)} if output is not None else {}),
                },
                ensure_ascii=True,
                sort_keys=True,
            ),
            flush=True,
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command", choices=("check", "build-replay", "self-check", "self-test", "target", "run")
    )
    parser.add_argument("path", nargs="?", type=Path)
    parser.add_argument("--scenario")
    parser.add_argument("--all", action="store_true", dest="all_scenarios")
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--target", type=Path, default=_REPOSITORY_ROOT)
    parser.add_argument("--record", type=Path)
    parser.add_argument("--declare-cap", type=int)
    parser.add_argument("--accel", choices=("tcg", "kvm"), default="tcg")
    args = parser.parse_args()
    if args.record is not None and args.command != "run":
        parser.error("--record is accepted only by run")
    if args.command == "target":
        if args.path is None:
            parser.error("target requires a path")
        print(json.dumps(asdict(_target_configuration(args.path)), ensure_ascii=True, sort_keys=True))
        return 0
    if args.path is not None:
        parser.error(f"{args.command} does not accept a positional path")
    if args.command == "build-replay":
        print(ensure_replay_log(Path(__file__).resolve().parents[2] / ".certify"))
        return 0
    if args.command == "self-check":
        from .self_check import run

        return run()
    if args.command == "self-test":
        return _self_test(args.accel)
    if args.command == "run":
        record_directory = (
            _record_directory(args.record, args.all_scenarios, args.trials)
            if args.record is not None
            else None
        )
        return _run_scenarios(
            _selected_scenarios(args.scenario, args.all_scenarios),
            args.trials,
            args.target,
            args.declare_cap,
            args.accel,
            record_directory,
        )

    missing = prerequisites.check()
    missing_set = set(missing)
    for name in prerequisites.names():
        print(f"{'MISSING' if name in missing_set else 'ok'}: {name}")
    return int(bool(missing))


if __name__ == "__main__":
    raise SystemExit(main())
