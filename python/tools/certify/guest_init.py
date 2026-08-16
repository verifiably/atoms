"""Root-only certification driver executed inside the direct-kernel guest."""

from __future__ import annotations

import argparse
import base64
import json
import os
import platform
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import traceback
from dataclasses import asdict
from pathlib import Path

import pytest

from atoms.core.scratch import is_scratch_leaf
from atoms.fs.linux import LinuxBackend
from atoms.fs.platform import BACKEND_REVISION
from atoms.fs.volume import (
    FeatureMasks,
    StorageProfile,
    VolumeConfiguration,
    build_configuration,
    kernel_identifier,
    read_mountinfo,
    resolve_ext4_feature_masks,
    resolve_mount_entry,
)

from .images import _mkfs_features, clone
from .replay import replay_prefix

_FAST_COMMIT = 0x400
_ORPHAN_FILE = 0x1000
_MOUNT = "/run/certify-mount"
_UMOUNT = "/run/certify-umount"
_LOG_MAGIC = 0x6A736677736872
_LOG_VERSION = 1
_LOG_FLUSH_OR_FUA = 0x3
_LOG_DISCARD = 0x4
_UNDECLARED_PREFIX_LIMIT = 2000


def _run(command: list[str]) -> str:
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"{command[0]} failed with exit {result.returncode}: {detail}")
    return result.stdout.strip()


def _emit(document: dict[str, object]) -> None:
    print(f"CERTIFY-JSON:{json.dumps(document, ensure_ascii=True, sort_keys=True)}", flush=True)


def _cmdline() -> dict[str, str]:
    values: dict[str, str] = {}
    for parameter in Path("/proc/cmdline").read_text(encoding="ascii").split():
        key, separator, value = parameter.partition("=")
        if separator:
            values[key] = value
    return values


def _decode_json(value: str, name: str) -> object:
    try:
        return json.loads(base64.urlsafe_b64decode(value).decode())
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as caught:
        raise ValueError(f"invalid {name} on kernel command line") from caught


def verify_identity(parameters: dict[str, str]) -> None:
    """Refuse unless the guest is exactly the host identity selected by the driver."""
    raw = parameters.get("certify_identity")
    if raw is None:
        raise ValueError("missing certify_identity on kernel command line")
    expected = _decode_json(raw, "certify_identity")
    if not isinstance(expected, dict):
        raise TypeError("certify_identity must decode to an object")
    checkout = expected.get("checkout")
    if not isinstance(checkout, str) or not checkout.startswith("/"):
        raise ValueError("identity checkout must be an absolute path")
    excludes_file = expected.get("git_excludes_file")
    if excludes_file is not None and (
        not isinstance(excludes_file, str)
        or not Path(excludes_file).is_absolute()
        or not Path(excludes_file).is_file()
    ):
        raise ValueError("identity git excludes file must be an absolute regular file")
    git = ["git", "-c", f"safe.directory={checkout}"]
    if excludes_file is not None:
        git += ["-c", f"core.excludesFile={excludes_file}"]
    actual: dict[str, object] = {
        "backend_revision": BACKEND_REVISION,
        "checkout": checkout,
        "commit": _run([*git, "-C", checkout, "rev-parse", "HEAD"]),
        "git_excludes_file": excludes_file,
        "kernel": platform.release(),
        "python_executable": sys.executable,
        "python_version": sys.version,
    }
    status = _run([*git, "-C", checkout, "status", "--porcelain"])
    if status:
        actual["checkout_status"] = status
    if actual != expected:
        raise RuntimeError(
            f"guest identity mismatch: expected {expected!r}, observed {actual!r}"
        )


def _fixture_masks(work: Path, features: str) -> FeatureMasks:
    image = work / f"fixture-{features.replace(',', '-').replace('^', 'no')}.img"
    with image.open("wb") as stream:
        stream.truncate(32 * 1024 * 1024)
    _run(["mkfs.ext4", "-q", "-F", "-O", features, os.fspath(image)])
    mountpoint = work / f"mount-{image.stem}"
    mountpoint.mkdir()
    loop = _run(["losetup", "--find", "--show", os.fspath(image)])
    try:
        _run([_MOUNT, loop, os.fspath(mountpoint)])
        try:
            descriptor = os.open(mountpoint, os.O_RDONLY | os.O_DIRECTORY)
            try:
                return resolve_ext4_feature_masks(descriptor)
            finally:
                os.close(descriptor)
        finally:
            _run([_UMOUNT, os.fspath(mountpoint)])
    finally:
        _run(["losetup", "--detach", loop])


def resolver_cross_check(work: Path) -> None:
    """Prove both optional compat bits through mounted ioctl fixtures."""
    plain = _fixture_masks(work, "^fast_commit,^orphan_file")
    fast = _fixture_masks(work, "fast_commit,^orphan_file")
    orphan = _fixture_masks(work, "^fast_commit,orphan_file")
    if fast.compat ^ plain.compat != _FAST_COMMIT:
        raise RuntimeError("fast_commit fixture did not flip exactly compat bit 0x400")
    if orphan.compat ^ plain.compat != _ORPHAN_FILE:
        raise RuntimeError("orphan_file fixture did not flip exactly compat bit 0x1000")


def _write_pattern(mountpoint: Path, name: str, payload: bytes) -> None:
    target = mountpoint / name
    with target.open("wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    directory = os.open(mountpoint, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _read_replayed(clone_image: Path, mountpoint: Path) -> tuple[bytes | None, bytes | None]:
    _run([_MOUNT, "-o", "loop", os.fspath(clone_image), os.fspath(mountpoint)])
    try:
        first = mountpoint / "first"
        second = mountpoint / "second"
        return (
            first.read_bytes() if first.exists() else None,
            second.read_bytes() if second.exists() else None,
        )
    finally:
        _run([_UMOUNT, os.fspath(mountpoint)])


def replay_self_verification(work: Path) -> None:
    """Prove mark-bounded replay and fresh-clone isolation with known payloads."""
    scratch_data = work / "self-test-data.img"
    scratch_log = work / "self-test-log.img"
    for image, size_mib in ((scratch_data, 64), (scratch_log, 128)):
        with image.open("wb") as stream:
            stream.truncate(size_mib * 1024 * 1024)
    data_device = Path(_run(["losetup", "--find", "--show", os.fspath(scratch_data)]))
    try:
        log_device = Path(_run(["losetup", "--find", "--show", os.fspath(scratch_log)]))
        try:
            _replay_self_verification(work, data_device, log_device)
        finally:
            _run(["losetup", "--detach", os.fspath(log_device)])
    finally:
        _run(["losetup", "--detach", os.fspath(data_device)])


def _replay_self_verification(work: Path, data_device: Path, log_device: Path) -> None:
    sectors = _run(["blockdev", "--getsz", os.fspath(data_device)])
    name = "certify-self-test"
    baseline = work / "self-test-baseline.img"
    mountpoint = work / "self-test-mount"
    mountpoint.mkdir()
    mapper_created = False
    mounted = False
    try:
        mapper = _create_mapper(
            name,
            f"0 {sectors} log-writes {data_device} {log_device}",
        )
        mapper_created = True
        _run(["mkfs.ext4", "-q", "-F", "-O", "^fast_commit,^orphan_file", os.fspath(mapper)])
        _run(["dmsetup", "message", name, "0", "mark", "baseline"])
        _run(["blockdev", "--flushbufs", os.fspath(mapper)])
        _run(
            [
                "dd",
                f"if={data_device}",
                f"of={baseline}",
                "bs=4M",
                "status=none",
            ]
        )
        _run([_MOUNT, os.fspath(mapper), os.fspath(mountpoint)])
        mounted = True
        _write_pattern(mountpoint, "first", b"first-pattern")
        _run(["dmsetup", "message", name, "0", "mark", "first"])
        _write_pattern(mountpoint, "second", b"second-pattern")
        _run(["dmsetup", "message", name, "0", "mark", "second"])
        _run([_UMOUNT, os.fspath(mountpoint)])
        mounted = False
    finally:
        try:
            if mounted:
                _run([_UMOUNT, os.fspath(mountpoint)])
        finally:
            if mapper_created:
                _remove_mapper(name)

    expected = {
        "baseline": (None, None),
        "first": (b"first-pattern", None),
        "second": (b"first-pattern", b"second-pattern"),
    }
    clone_names: set[Path] = set()
    for mark, wanted in expected.items():
        target = clone(baseline)
        try:
            if target in clone_names:
                raise RuntimeError("replay clone path was reused")
            clone_names.add(target)
            replay_prefix(log_device, target, end_mark=mark, end_entry=None)
            if _read_replayed(target, mountpoint) != wanted:
                raise RuntimeError(f"replay self-verification failed at mark {mark}")
        finally:
            target.unlink(missing_ok=True)


def _device(parameters: dict[str, str], name: str) -> Path:
    value = parameters.get(name)
    if value is None or not value.startswith("/dev/") or any(c.isspace() for c in value):
        raise ValueError(f"invalid {name} on kernel command line")
    device = Path(value)
    if not device.is_block_device():
        raise ValueError(f"{name} is not a block device: {device}")
    return device


def _log_entries(log_device: Path) -> tuple[int, ...]:
    """Return each completion-ordered entry's flags from a dm-log-writes v1 log."""
    with log_device.open("rb", buffering=0) as stream:
        header = stream.read(32)
        if len(header) != 32:
            raise RuntimeError("dm-log-writes log has a short superblock")
        magic, version, count, sector_size = struct.unpack_from("<QQQI", header)
        if magic != _LOG_MAGIC or version != _LOG_VERSION:
            raise RuntimeError(
                f"unsupported dm-log-writes header magic={magic:#x} version={version}"
            )
        if sector_size < 512 or sector_size > 4096 or sector_size & (sector_size - 1):
            raise RuntimeError(f"invalid dm-log-writes sector size {sector_size}")
        position = sector_size
        flags_seen: list[int] = []
        for index in range(count):
            stream.seek(position)
            sector = stream.read(sector_size)
            if len(sector) != sector_size:
                raise RuntimeError(f"short dm-log-writes entry sector at index {index}")
            _sector, sectors, flags, data_len = struct.unpack_from("<QQQQ", sector)
            if data_len > sector_size - 32:
                raise RuntimeError(f"oversized dm-log-writes entry data at index {index}")
            if not flags and not sectors:
                raise RuntimeError(f"empty dm-log-writes entry at index {index}")
            flags_seen.append(flags)
            position += sector_size
            if sectors and not flags & _LOG_DISCARD:
                position += sectors * sector_size
    return tuple(flags_seen)


def _check_prefix_budget(prefixes: int, declared_cap: int | None) -> None:
    if declared_cap is not None and declared_cap <= 0:
        raise ValueError("declared prefix cap must be a positive integer")
    if declared_cap is None and prefixes > _UNDECLARED_PREFIX_LIMIT:
        raise ValueError(
            f"observed {prefixes} prefixes exceeds 2000; rerun with --declare-cap N"
        )
    if declared_cap is not None and prefixes > declared_cap:
        raise ValueError(
            f"observed {prefixes} prefixes exceeds declared maximum {declared_cap}"
        )


def _same_inode_95(
    live_directories: set[tuple[int, int]], work_directories: set[tuple[int, int]]
) -> bool:
    return not live_directories.isdisjoint(work_directories)


def _directory_identity(path: Path) -> tuple[int, int] | None:
    info = path.stat(follow_symlinks=False)
    if not stat.S_ISDIR(info.st_mode):
        return None
    return info.st_dev, info.st_ino


def _has_same_inode_95(volume: Path) -> bool:
    project = volume / "project"
    work = volume / "metadata" / "work"
    live = {
        identity
        for path in project.rglob("*")
        if (identity := _directory_identity(path)) is not None
    }
    scratch = {
        identity
        for txid in work.iterdir()
        if _directory_identity(txid) is not None
        for path in txid.iterdir()
        if is_scratch_leaf(path.name)
        and path.name.endswith(".work")
        and (identity := _directory_identity(path)) is not None
    }
    return _same_inode_95(live, scratch)


def _create_mapper(
    name: str,
    table: str,
    *,
    major: int | None = None,
    minor: int | None = None,
) -> Path:
    command = ["dmsetup", "create", name, "--table", table]
    if major is not None and minor is not None:
        command += ["--major", str(major), "--minor", str(minor)]
    elif major is not None or minor is not None:
        raise ValueError("mapper major and minor must be specified together")
    _run(command)
    try:
        _run(["dmsetup", "mknodes", name])
        mapper = Path("/dev/mapper") / name
        if not mapper.is_block_device():
            raise RuntimeError(f"dmsetup did not create a block device at {mapper}")
        return mapper
    except Exception:
        _remove_mapper(name)
        raise


def _remove_mapper(name: str) -> None:
    _run(["dmsetup", "remove", name])
    _run(["dmsetup", "mknodes"])


def _zero_device(device: Path, size: int) -> None:
    zero = b"\0" * (1024 * 1024)
    with device.open("wb", buffering=0) as stream:
        remaining = size
        while remaining:
            block = zero[: min(len(zero), remaining)]
            stream.write(block)
            remaining -= len(block)
        os.fsync(stream.fileno())


def _mount_device(device: Path, mountpoint: Path, options: str) -> None:
    command = [_MOUNT]
    if options:
        command += ["-o", options]
    _run([*command, os.fspath(device), os.fspath(mountpoint)])


def _resolve_configuration(mountpoint: Path) -> VolumeConfiguration:
    descriptor = os.open(mountpoint, os.O_RDONLY | os.O_DIRECTORY)
    try:
        entry = resolve_mount_entry(descriptor, read_mountinfo())
        return build_configuration(entry, kernel_identifier(), directory_fd=descriptor)
    finally:
        os.close(descriptor)


def _recover_cell(volume: Path) -> dict[str, object]:
    from atoms.core.recovery import HaltPlan, TransactionState
    from atoms.fs.lock import acquire_project_lock
    from tests.fs_support import build_test_allowlist
    from tests.persistence_model import (
        _enter_lease,
        _external_differences,
        _inspect,
        _model_projection,
        _occupied_slots,
        _Refused,
        _scratch_survivors,
        inaccessible_paths,
        world_digest,
        world_tree,
    )

    project = volume / "project"
    metadata = volume / "metadata"
    storage = StorageProfile(profile_id="flush-honoring-disk.v1")
    backend = LinuxBackend()
    with acquire_project_lock(backend, str(metadata)) as lock:
        allowlist = build_test_allowlist(lock, str(project), storage)
    before = world_tree(project, metadata)
    inaccessible = inaccessible_paths(before)
    failures: list[str] = []
    captured: list[tuple] = []
    with pytest.MonkeyPatch.context() as monkeypatch:
        refusal: str | None = None
        halted = False
        try:
            kind, _ = _enter_lease(
                project, metadata, storage, monkeypatch, captured, allowlist
            )
            halted = kind == "halted"
        except _Refused as refused:
            refusal = str(refused)
            if not inaccessible:
                failures.append(f"unexplained refusal with every mode accessible: {refusal}")

        world = world_tree(project, metadata)
        digest = world_digest(project, metadata)
        if refusal is not None:
            second_refusal: str | None = None
            try:
                _enter_lease(project, metadata, storage, monkeypatch, [], allowlist)
            except _Refused as again:
                second_refusal = str(again)
            if second_refusal != refusal or world_digest(project, metadata) != digest:
                failures.append(
                    f"first pass refused {refusal!r}; second pass gave {second_refusal!r}"
                )
            return {"violations": failures, "classified": 0}

        if len(captured) > 1:
            failures.append(f"recovery classified {len(captured)} times in one entry")
        facts = _inspect(project, metadata, storage, allowlist)
        projection = facts["projection"]
        record = facts["record"]
        if halted and not captured and (
            record is None
            or (record.assembly_halt is None and record.state is not TransactionState.HALTED)
        ):
            failures.append("a halt was raised without a durable halt record")
        if projection is not None and not captured and not halted:
            assert record is not None
            if facts["active"] or record.state not in {
                TransactionState.COMMITTED,
                TransactionState.ROLLED_BACK,
            }:
                failures.append(
                    "a durable record was neither classified nor settled: "
                    f"state={record.state.name} active={facts['active']}"
                )
        if projection is None:
            if captured:
                failures.append("recovery classified a world holding no durable record")
            if differences := _external_differences(before, world):
                failures.append(f"external state not preserved: {differences}")
        elif captured:
            plan = captured[0][1]
            if projection != _model_projection(*captured[0]):
                failures.append("durable projection disagrees with A3 fixed point")
            if type(plan) is HaltPlan:
                assert record is not None
                if record.halt_diagnostic != plan.diagnostic:
                    failures.append(
                        "the persisted halt diagnostic is not the plan's: "
                        f"{record.halt_diagnostic!r} != {plan.diagnostic!r}"
                    )

        chain = facts["chain"]
        failures.extend(chain["failures"])
        if chain["present"] and record is not None:
            if record.registration_digest is not None and (
                record.registration_digest not in chain["registrations"]
            ):
                failures.append(
                    f"record registration digest {record.registration_digest} is not a "
                    "durable chain entry"
                )
            if record.settlement_digest is not None and (
                record.settlement_digest not in chain["settlements"]
            ):
                failures.append(
                    f"record settlement digest {record.settlement_digest} is not a "
                    "durable chain entry"
                )
        if not facts["active"] and not halted:
            if scratch := _scratch_survivors(world):
                failures.append(f"scratch survived a terminal state: {scratch}")
            if occupied := _occupied_slots(world):
                failures.append(f"workspace slots are not empty: {occupied}")
            if chain["present"] and chain["survivors"]:
                failures.append("a chain staging survivor outlived a terminal state")
        if facts["unindexed_blobs"]:
            failures.append(f"unindexed blobs survived reclamation: {facts['unindexed_blobs']}")

        second_violation: str | None = None
        try:
            _enter_lease(project, metadata, storage, monkeypatch, [], allowlist)
        except _Refused as refused:
            second_violation = f"the second pass refused where the first resolved: {refused}"
        if second_violation is None:
            again = _inspect(project, metadata, storage, allowlist)
            if again["projection"] != projection:
                second_violation = (
                    f"the second pass moved the projection: {projection!r} -> "
                    f"{again['projection']!r}"
                )
            elif world_digest(project, metadata) != digest:
                second_violation = "the second pass moved the world"
        if second_violation is not None:
            failures.append(second_violation)
    return {"violations": failures, "classified": len(captured)}


def _recover_subprocess(volume: Path) -> dict[str, object]:
    result = subprocess.run(
        [sys.executable, "-m", "tools.certify.guest_init", "--recover", os.fspath(volume)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"recovery subprocess failed: {result.stderr.strip()}")
    try:
        document = json.loads(result.stdout)
    except json.JSONDecodeError as caught:
        raise RuntimeError(f"recovery subprocess emitted invalid JSON: {result.stdout!r}") from caught
    if not isinstance(document, dict):
        raise TypeError("recovery subprocess result must be an object")
    return document


def run_scenario(
    work: Path,
    data_device: Path,
    log_device: Path,
    *,
    name: str,
    masks: FeatureMasks,
    mount_options: str,
    declared_cap: int | None,
) -> tuple[dict[str, object], VolumeConfiguration]:
    """Record and exhaustively replay one scenario's completion-ordered trace."""
    from tests.exerciser import scenario, setup_clean, transact, transact_caught

    entry = scenario(name)
    if entry.family not in {"commit", "rollback"} or entry.drift is not None:
        raise ValueError(f"scenario is not certifiable: {name}")
    data_bytes = int(_run(["blockdev", "--getsize64", os.fspath(data_device)]))
    log_bytes = int(_run(["blockdev", "--getsize64", os.fspath(log_device)]))
    _zero_device(log_device, log_bytes)
    _run(["mkfs.ext4", "-q", "-F", "-O", _mkfs_features(masks), os.fspath(data_device)])

    volume = work / "volume"
    volume.mkdir()
    sectors = _run(["blockdev", "--getsz", os.fspath(data_device)])
    mapper_name = "certify"
    monkeypatch = pytest.MonkeyPatch()
    mapper_created = False
    mounted = False
    try:
        mapper = _create_mapper(
            mapper_name,
            f"0 {sectors} log-writes {data_device} {log_device}",
        )
        mapper_created = True
        device_number = mapper.stat().st_rdev
        major, minor = os.major(device_number), os.minor(device_number)
        _mount_device(mapper, volume, mount_options)
        mounted = True
        project = volume / "project"
        project.mkdir()
        metadata = volume / "metadata"
        storage = StorageProfile(profile_id="flush-honoring-disk.v1")
        ingredients = (LinuxBackend(), str(project), str(metadata), storage)
        setup_clean(entry, ingredients, monkeypatch)
    finally:
        try:
            if mounted:
                _run([_UMOUNT, os.fspath(volume)])
        finally:
            if mapper_created:
                _remove_mapper(mapper_name)

    _zero_device(log_device, log_bytes)

    baseline = work / "baseline.img"
    with data_device.open("rb", buffering=0) as source, baseline.open("wb", buffering=0) as sink:
        remaining = data_bytes
        while remaining:
            block = source.read(min(4 * 1024 * 1024, remaining))
            if not block:
                raise RuntimeError("short read while cloning the workload baseline")
            sink.write(block)
            remaining -= len(block)
        sink.flush()
        os.fsync(sink.fileno())

    mapper_created = False
    workload_mounted = False
    try:
        mapper = _create_mapper(
            mapper_name,
            f"0 {sectors} log-writes {data_device} {log_device}",
            major=major,
            minor=minor,
        )
        mapper_created = True
        _mount_device(mapper, volume, mount_options)
        workload_mounted = True
        configuration = _resolve_configuration(volume)
        if configuration.durability_features != (
            f"compat={masks.compat:#x}",
            f"incompat={masks.incompat:#x}",
            f"ro_compat={masks.ro_compat:#x}",
        ):
            raise RuntimeError("mounted workload feature masks do not equal the selected target")
        _run(["dmsetup", "message", mapper_name, "0", "mark", "scenario-start"])
        if entry.family == "commit":
            outcome = transact(entry, ingredients)
            if outcome.outcome.name != "COMMITTED":
                raise RuntimeError(f"clean scenario returned {outcome.outcome.name}")
        else:
            projection = transact_caught(entry, ingredients, monkeypatch)
            if projection["state"] != "ROLLED_BACK":
                raise RuntimeError(f"caught scenario returned {projection['state']}")
        _run(["dmsetup", "message", mapper_name, "0", "mark", "scenario-end"])
    finally:
        try:
            monkeypatch.undo()
        finally:
            try:
                if workload_mounted:
                    _run([_UMOUNT, os.fspath(volume)])
            finally:
                if mapper_created:
                    _remove_mapper(mapper_name)

    flags = _log_entries(log_device)
    prefixes = len(flags) + 1
    _check_prefix_budget(prefixes, declared_cap)
    violations: list[str] = []
    classified = 0
    clone_names: set[Path] = set()
    for prefix in range(len(flags) + 1):
        target = clone(baseline)
        loop: Path | None = None
        replay_mapper_created = False
        replay_mounted = False
        try:
            if target in clone_names:
                raise RuntimeError("replay clone path was reused")
            clone_names.add(target)
            replay_prefix(log_device, target, end_mark=None, end_entry=prefix)
            loop = Path(_run(["losetup", "--find", "--show", os.fspath(target)]))
            replay_mapper = _create_mapper(
                mapper_name,
                f"0 {sectors} linear {loop} 0",
                major=major,
                minor=minor,
            )
            replay_mapper_created = True
            _mount_device(replay_mapper, volume, mount_options)
            replay_mounted = True
            if _has_same_inode_95(volume):
                _emit({"bonus": "same-inode-9.5"})
            result = _recover_subprocess(volume)
            found = result.get("violations")
            if not isinstance(found, list) or not all(isinstance(item, str) for item in found):
                raise RuntimeError("recovery subprocess returned malformed violations")
            violations.extend(f"prefix {prefix}: {item}" for item in found)
            cell_classified = result.get("classified")
            if not isinstance(cell_classified, int):
                raise TypeError("recovery subprocess returned malformed classified count")
            classified += cell_classified
            _emit(
                {
                    "progress": {
                        "scenario": name,
                        "prefix": prefix,
                        "prefixes": prefixes,
                    }
                }
            )
        finally:
            try:
                if replay_mounted:
                    _run([_UMOUNT, os.fspath(volume)])
            finally:
                try:
                    if replay_mapper_created:
                        _remove_mapper(mapper_name)
                finally:
                    try:
                        if loop is not None:
                            _run(["blockdev", "--flushbufs", os.fspath(loop)])
                    finally:
                        try:
                            if loop is not None:
                                _run(["losetup", "--detach", os.fspath(loop)])
                        finally:
                            target.unlink(missing_ok=True)
    if classified == 0:
        violations.append("no replay prefix reached A3 classification")
    return (
        {
            "scenario": name,
            "marks": sum(bool(flags_value & _LOG_FLUSH_OR_FUA) for flags_value in flags),
            "prefixes": prefixes,
            "violations": len(violations),
            "violation_details": violations,
            "declared_cap": declared_cap,
        },
        configuration,
    )


def main() -> int:
    if sys.argv[1:2] == ["--recover"]:
        if len(sys.argv) != 3:
            raise ValueError("--recover requires exactly one mounted volume path")
        # The verified parent execs this exact interpreter and module for each first pass.
        # Repeating its 9p Git scan here preserves no additional crash boundary.
        print(json.dumps(_recover_cell(Path(sys.argv[2])), ensure_ascii=True, sort_keys=True))
        return 0

    parameters = _cmdline()
    try:
        verify_identity(parameters)
    except Exception as caught:  # noqa: BLE001 - the serial fatal record is the boundary.
        _emit({"fatal": "identity", "detail": str(caught)})
        return 1
    raw_arguments = parameters.get("certify_arguments")
    if raw_arguments is None:
        _emit({"fatal": "arguments", "detail": "missing certify_arguments"})
        return 1
    arguments = _decode_json(raw_arguments, "certify_arguments")
    if not isinstance(arguments, list) or not all(isinstance(item, str) for item in arguments):
        _emit({"fatal": "arguments", "detail": "certify_arguments must be strings"})
        return 1
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--scenario")
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--compat", type=int)
    parser.add_argument("--incompat", type=int)
    parser.add_argument("--ro-compat", type=int)
    parser.add_argument("--mount-options")
    parser.add_argument("--declare-cap", type=int)
    args = parser.parse_args(arguments)
    try:
        with tempfile.TemporaryDirectory(prefix="atoms-certify-") as temporary:
            work = Path(temporary)
            self_test_work = work / "self-test"
            self_test_work.mkdir()
            resolver_cross_check(self_test_work)
            data_device = _device(parameters, "data_device")
            log_device = _device(parameters, "log_device")
            replay_self_verification(self_test_work)
            shutil.rmtree(self_test_work)
            if args.self_test:
                _emit({"self_test": "ok"})
                return 0
            values = (args.scenario, args.compat, args.incompat, args.ro_compat, args.mount_options)
            if any(value is None for value in values):
                raise ValueError("scenario, all feature masks, and mount options are required")
            if args.trials != 1:
                raise ValueError("Task 3 requires exactly one trial")
            assert args.scenario is not None
            assert args.compat is not None
            assert args.incompat is not None
            assert args.ro_compat is not None
            assert args.mount_options is not None
            report, configuration = run_scenario(
                work,
                data_device,
                log_device,
                name=args.scenario,
                masks=FeatureMasks(args.compat, args.incompat, args.ro_compat),
                mount_options=args.mount_options,
                declared_cap=args.declare_cap,
            )
            _emit(report)
            if report["violations"]:
                raise RuntimeError(f"scenario replay violations: {report['violation_details']}")
            _emit(
                {
                    "configuration": asdict(configuration),
                    "storage": "flush-honoring-disk.v1",
                    "scenario": args.scenario,
                    "marks": report["marks"],
                    "prefixes": report["prefixes"],
                    "declared_cap": args.declare_cap,
                }
            )
    except Exception:  # noqa: BLE001 - the serial fatal record is the boundary.
        _emit(
            {
                "fatal": "self-test" if args.self_test else "workload",
                "detail": traceback.format_exc(),
            }
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
