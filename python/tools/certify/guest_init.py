"""Root-only certification driver executed inside the direct-kernel guest."""

from __future__ import annotations

import argparse
import base64
import json
import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path

from atoms.fs.platform import BACKEND_REVISION
from atoms.fs.volume import FeatureMasks, resolve_ext4_feature_masks

from .images import clone
from .replay import replay_prefix

_FAST_COMMIT = 0x400
_ORPHAN_FILE = 0x1000


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
    actual: dict[str, object] = {
        "backend_revision": BACKEND_REVISION,
        "checkout": checkout,
        "commit": _run(["git", "-C", checkout, "rev-parse", "HEAD"]),
        "kernel": platform.release(),
        "python_executable": sys.executable,
        "python_version": sys.version,
    }
    status = _run(["git", "-C", checkout, "status", "--porcelain"])
    if status:
        actual["checkout_clean"] = False
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
    _run(["mount", "-o", "loop", os.fspath(image), os.fspath(mountpoint)])
    try:
        descriptor = os.open(mountpoint, os.O_RDONLY | os.O_DIRECTORY)
        try:
            return resolve_ext4_feature_masks(descriptor)
        finally:
            os.close(descriptor)
    finally:
        _run(["umount", os.fspath(mountpoint)])


def resolver_cross_check(work: Path) -> None:
    """Prove both optional compat bits through mounted ioctl fixtures."""
    plain = _fixture_masks(work, "^fast_commit,^orphan_file")
    fast = _fixture_masks(work, "fast_commit,^orphan_file")
    orphan = _fixture_masks(work, "^fast_commit,orphan_file")
    if fast.compat ^ plain.compat != _FAST_COMMIT:
        raise RuntimeError("fast_commit fixture did not flip exactly compat bit 0x400")
    if orphan.compat ^ plain.compat != _ORPHAN_FILE:
        raise RuntimeError("orphan_file fixture did not flip exactly compat bit 0x1000")
    if (fast.incompat, fast.ro_compat) != (plain.incompat, plain.ro_compat):
        raise RuntimeError("fast_commit fixture changed a non-compat feature mask")
    if (orphan.incompat, orphan.ro_compat) != (plain.incompat, plain.ro_compat):
        raise RuntimeError("orphan_file fixture changed a non-compat feature mask")


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
    _run(["mount", "-o", "loop", os.fspath(clone_image), os.fspath(mountpoint)])
    try:
        first = mountpoint / "first"
        second = mountpoint / "second"
        return (
            first.read_bytes() if first.exists() else None,
            second.read_bytes() if second.exists() else None,
        )
    finally:
        _run(["umount", os.fspath(mountpoint)])


def replay_self_verification(work: Path, data_device: Path, log_device: Path) -> None:
    """Prove mark-bounded replay and fresh-clone isolation with known payloads."""
    sectors = _run(["blockdev", "--getsz", os.fspath(data_device)])
    name = "certify-self-test"
    mapper = Path("/dev/mapper") / name
    _run(
        [
            "dmsetup",
            "create",
            name,
            "--table",
            f"0 {sectors} log-writes {data_device} {log_device}",
        ]
    )
    baseline = work / "self-test-baseline.img"
    mountpoint = work / "self-test-mount"
    mountpoint.mkdir()
    try:
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
        _run(["mount", os.fspath(mapper), os.fspath(mountpoint)])
        _write_pattern(mountpoint, "first", b"first-pattern")
        _run(["dmsetup", "message", name, "0", "mark", "first"])
        _write_pattern(mountpoint, "second", b"second-pattern")
        _run(["dmsetup", "message", name, "0", "mark", "second"])
        _run(["umount", os.fspath(mountpoint)])
    finally:
        if mapper.exists():
            subprocess.run(["umount", os.fspath(mountpoint)], check=False, capture_output=True)
            subprocess.run(["dmsetup", "remove", name], check=False, capture_output=True)

    expected = {
        "baseline": (None, None),
        "first": (b"first-pattern", None),
        "second": (b"first-pattern", b"second-pattern"),
    }
    clones: list[Path] = []
    try:
        for mark, wanted in expected.items():
            target = clone(baseline)
            clones.append(target)
            replay_prefix(log_device, target, end_mark=mark, end_entry=None)
            if _read_replayed(target, mountpoint) != wanted:
                raise RuntimeError(f"replay self-verification failed at mark {mark}")
        if len({target.stat().st_ino for target in clones}) != len(clones):
            raise RuntimeError("replay clones are not distinct files")
    finally:
        for target in clones:
            target.unlink(missing_ok=True)


def _device(parameters: dict[str, str], name: str) -> Path:
    value = parameters.get(name)
    if value is None or not value.startswith("/dev/") or any(c.isspace() for c in value):
        raise ValueError(f"invalid {name} on kernel command line")
    device = Path(value)
    if not device.is_block_device():
        raise ValueError(f"{name} is not a block device: {device}")
    return device


def main() -> int:
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
    args = parser.parse_args(arguments)
    try:
        with tempfile.TemporaryDirectory(prefix="atoms-certify-") as temporary:
            work = Path(temporary)
            resolver_cross_check(work)
            replay_self_verification(
                work,
                _device(parameters, "data_device"),
                _device(parameters, "log_device"),
            )
        if not args.self_test:
            raise NotImplementedError("scenario workload is not implemented")
    except Exception as caught:  # noqa: BLE001 - the serial fatal record is the boundary.
        _emit({"fatal": "self-test", "detail": str(caught)})
        return 1
    _emit({"self_test": "ok"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
