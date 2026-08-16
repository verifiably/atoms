"""Command line entry point for host certification tooling."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path

from atoms.fs.volume import (
    FeatureMasks,
    VolumeConfiguration,
    build_configuration,
    kernel_identifier,
    read_mountinfo,
    resolve_mount_entry,
)

from . import guest, prerequisites
from .images import build_log_image
from .replay import ensure_replay_log

_PYTHON_ROOT = Path(__file__).resolve().parents[2]
_REPOSITORY_ROOT = _PYTHON_ROOT.parent
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
) -> int:
    if trials != 1:
        raise ValueError("certification supports exactly one trial")
    if declared_cap is not None and (
        not isinstance(declared_cap, int) or isinstance(declared_cap, bool) or declared_cap <= 0
    ):
        raise ValueError("--declare-cap must be a positive integer")
    missing = prerequisites.check()
    if missing:
        raise RuntimeError(f"missing certification prerequisites: {', '.join(missing)}")
    configuration = _target_configuration(target)
    if configuration.filesystem_type != "ext4":
        raise RuntimeError(f"certification target is not ext4: {configuration.filesystem_type}")
    masks = _feature_masks(configuration)
    with tempfile.TemporaryDirectory(prefix="atoms-certify-host-") as temporary:
        work = Path(temporary)
        initramfs = _build_guest_initramfs(work)
        rows: list[dict[str, object]] = []
        for scenario in scenarios:
            scenario_work = work / scenario
            scenario_work.mkdir()
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
                raise guest.GuestRunError(f"guest reported a fatal certification error: {fatal}")
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
            expected = json.loads(json.dumps(asdict(configuration)))
            if not result.records or result.records[-1].get("configuration") != expected:
                raise guest.GuestRunError("guest configuration does not equal the selected target")
            rows.append(row)
        total_marks = sum(_row_count(row, "marks") for row in rows)
        total_prefixes = sum(_row_count(row, "prefixes") for row in rows)
        print(
            "CERTIFY-JSON:"
            + json.dumps(
                {
                    "configuration": json.loads(json.dumps(asdict(configuration))),
                    "storage": "flush-honoring-disk.v1",
                    "scenarios": rows,
                    "total_marks": total_marks,
                    "total_prefixes": total_prefixes,
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
        "command", choices=("check", "build-replay", "self-check", "self-test", "run")
    )
    parser.add_argument("--scenario")
    parser.add_argument("--all", action="store_true", dest="all_scenarios")
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--target", type=Path, default=_REPOSITORY_ROOT)
    parser.add_argument("--declare-cap", type=int)
    parser.add_argument("--accel", choices=("tcg", "kvm"), default="tcg")
    args = parser.parse_args()
    if args.command == "build-replay":
        print(ensure_replay_log(Path(__file__).resolve().parents[2] / ".certify"))
        return 0
    if args.command == "self-check":
        from .self_check import run

        return run()
    if args.command == "self-test":
        return _self_test(args.accel)
    if args.command == "run":
        return _run_scenarios(
            _selected_scenarios(args.scenario, args.all_scenarios),
            args.trials,
            args.target,
            args.declare_cap,
            args.accel,
        )

    missing = prerequisites.check()
    missing_set = set(missing)
    for name in prerequisites.names():
        print(f"{'MISSING' if name in missing_set else 'ok'}: {name}")
    return int(bool(missing))


if __name__ == "__main__":
    raise SystemExit(main())
