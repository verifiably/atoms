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


def _self_test() -> int:
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
        )
        print(result.serial, end="")
        if not result.records or result.records[-1] != {"self_test": "ok"}:
            raise guest.GuestRunError("guest did not report a successful self-test")
        if (_digest(data), _digest(log)) != before:
            raise guest.GuestRunError("scratch self-test mutated an attached certification device")
    return 0


def _run_scenario(scenario: str, trials: int, target: Path) -> int:
    if not scenario:
        raise ValueError("--scenario is required")
    if trials != 1:
        raise ValueError("Task 3 supports exactly one trial")
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
        data = build_log_image(work / "data.img", 128)
        log = build_log_image(work / "log.img", 512)
        result = guest.run(
            Path("/boot/vmlinuz-linux"),
            initramfs,
            data,
            log,
            shared_root=Path("/"),
            guest_arguments=(
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
            ),
        )
        print(result.serial, end="")
        if any("fatal" in record for record in result.records):
            raise guest.GuestRunError("guest reported a fatal certification error")
        expected = json.loads(json.dumps(asdict(configuration)))
        if not result.records or result.records[-1].get("configuration") != expected:
            raise guest.GuestRunError("guest configuration does not equal the selected target")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command", choices=("check", "build-replay", "self-check", "self-test", "run")
    )
    parser.add_argument("--scenario")
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--target", type=Path, default=_REPOSITORY_ROOT)
    args = parser.parse_args()
    if args.command == "build-replay":
        print(ensure_replay_log(Path(__file__).resolve().parents[2] / ".certify"))
        return 0
    if args.command == "self-check":
        from .self_check import run

        return run()
    if args.command == "self-test":
        return _self_test()
    if args.command == "run":
        return _run_scenario(args.scenario, args.trials, args.target)

    missing = prerequisites.check()
    missing_set = set(missing)
    for name in prerequisites.names():
        print(f"{'MISSING' if name in missing_set else 'ok'}: {name}")
    return int(bool(missing))


if __name__ == "__main__":
    raise SystemExit(main())
