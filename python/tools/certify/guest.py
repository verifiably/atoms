"""Initramfs construction and direct-kernel QEMU guest execution."""

from __future__ import annotations

import base64
import json
import os
import platform
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

_CHECKOUT = Path(__file__).resolve().parents[3]
_UNSAFE_CMDLINE_CHARACTERS = frozenset("'\"\\\0")


@dataclass(frozen=True, slots=True)
class GuestResult:
    command: tuple[str, ...]
    serial: str
    records: tuple[dict[str, object], ...]


class GuestRunError(RuntimeError):
    """QEMU did not produce a valid successful certification guest run."""


def _run(command: list[str], *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        raise GuestRunError(f"{command[0]} failed with exit {result.returncode}: {result.stdout}")
    return result


def _init_script(python: Path) -> str:
    return (
        """#!/bin/sh
set -eu
mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev
mount -t tmpfs tmpfs /run
cp /usr/bin/mount /run/certify-mount
cp /usr/bin/umount /run/certify-umount
for parameter in $(cat /proc/cmdline); do
    case "$parameter" in checkout=*) checkout=${parameter#checkout=} ;; esac
done
[ -n "${checkout:-}" ]
case "$checkout" in /*) ;; *) exit 1 ;; esac
case "$checkout" in *[!A-Za-z0-9_./-]*) exit 1 ;; esac
for module in 9p 9pnet_virtio virtio_pci virtio_blk dm-log-writes dm-mod loop; do
    modprobe "$module"
done
mkdir -p /root9p
mount -t 9p -o ro,trans=virtio,version=9p2000.L root9p /root9p
mkdir -p /root9p/dev /root9p/proc /root9p/sys /root9p/run /root9p/tmp
mount --bind /dev /root9p/dev
mount --bind /proc /root9p/proc
mount --bind /sys /root9p/sys
mount --bind /run /root9p/run
mount -t tmpfs tmpfs /root9p/tmp
exec chroot /root9p /bin/sh -c 'cd "$1/python" && exec "$2" -m tools.certify.guest_init' sh "$checkout" """
        + shlex.quote(os.fspath(python))
        + "\n"
    )


def _check_init_contract(script: str) -> None:
    mount = "cp /usr/bin/mount /run/certify-mount"
    umount = "cp /usr/bin/umount /run/certify-umount"
    try:
        run_index = script.index("mount -t tmpfs tmpfs /run")
        mount_index = script.index(mount)
        umount_index = script.index(umount)
        bind_index = script.index("mount --bind /run /root9p/run")
    except ValueError as caught:
        raise GuestRunError("generated init omits the privileged mount-tool contract") from caught
    if not run_index < mount_index < umount_index < bind_index:
        raise GuestRunError("generated init orders the privileged mount-tool contract incorrectly")


def build_initramfs(work: Path) -> Path:
    """Build the base-hook initramfs that starts the shared-root guest driver."""
    workspace = Path(work)
    if workspace.exists() and not workspace.is_dir():
        raise ValueError(f"work path is not a directory: {workspace}")
    workspace.mkdir(parents=True, exist_ok=True)
    hookdir = workspace / "initcpio"
    install = hookdir / "install"
    install.mkdir(parents=True, exist_ok=True)
    base = install / "base"
    if not base.exists():
        base.symlink_to("/usr/lib/initcpio/install/base")
    elif not base.is_symlink() or base.readlink() != Path("/usr/lib/initcpio/install/base"):
        raise ValueError(f"unexpected base hook at {base}")
    init = workspace / "certify-init"
    python = Path(sys.executable)
    if not python.is_absolute() or not python.is_file():
        raise ValueError(f"Python executable is not an absolute regular file: {python}")
    script = _init_script(python)
    _check_init_contract(script)
    init.write_text(script, encoding="utf-8")
    init.chmod(0o755)
    (install / "certify").write_text(
        """build() {
    add_file "$CERTIFY_INIT" /init 755
}
""",
        encoding="utf-8",
    )
    config = workspace / "mkinitcpio.conf"
    config.write_text(
        """MODULES=(9p 9pnet_virtio virtio_pci virtio_blk dm-log-writes dm-mod loop)
HOOKS=(base certify)
COMPRESSION=gzip
""",
        encoding="utf-8",
    )
    output = workspace / "initramfs.cpio.gz"
    env = os.environ | {"CERTIFY_INIT": os.fspath(init)}
    _run(
        [
            "mkinitcpio",
            "--nocolor",
            "--nopost",
            "-D",
            os.fspath(hookdir),
            "-c",
            os.fspath(config),
            "-g",
            os.fspath(output),
            "-k",
            platform.release(),
        ],
        env=env,
    )
    if not output.is_file() or output.stat().st_size == 0:
        raise GuestRunError(f"mkinitcpio produced no initramfs at {output}")
    return output


def _qemu_path(path: Path, name: str) -> str:
    value = os.fspath(path)
    if "," in value:
        raise ValueError(f"{name} path cannot contain a comma")
    return value


def _checkout_path(shared_root: Path) -> str:
    root = shared_root.resolve()
    checkout = _CHECKOUT.resolve()
    if root != Path("/"):
        raise ValueError("shared_root must be the host root directory")
    if not checkout.is_relative_to(root) or not (checkout / "python").is_dir():
        raise ValueError(f"checkout is not available beneath shared_root: {checkout}")
    value = os.fspath(checkout)
    if any(character.isspace() or character in _UNSAFE_CMDLINE_CHARACTERS for character in value):
        raise ValueError(f"checkout path is unsafe for the kernel command line: {checkout}")
    return value


def _acceleration_arguments(acceleration: str) -> tuple[str, str]:
    if acceleration not in {"tcg", "kvm"}:
        raise ValueError("acceleration must be exactly 'tcg' or 'kvm'")
    return "-accel", acceleration


def _parse_records(serial: str) -> tuple[dict[str, object], ...]:
    records: list[dict[str, object]] = []
    for line in serial.splitlines():
        if not line.startswith("CERTIFY-JSON:"):
            continue
        try:
            record = json.loads(line.removeprefix("CERTIFY-JSON:"))
        except json.JSONDecodeError as caught:
            raise GuestRunError(f"invalid guest JSON: {line}") from caught
        if not isinstance(record, dict):
            raise GuestRunError("guest JSON record must be an object")
        records.append(record)
    return tuple(records)


def _run_streaming(command: list[str], stream: TextIO) -> str:
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    assert process.stdout is not None
    lines: list[str] = []
    for line in process.stdout:
        lines.append(line)
        stream.write(line)
        stream.flush()
    returncode = process.wait()
    serial = "".join(lines)
    if returncode != 0:
        raise GuestRunError(f"{command[0]} failed with exit {returncode}: {serial}")
    return serial


def run(
    kernel: Path,
    initramfs: Path,
    data_image: Path,
    log_image: Path,
    *,
    shared_root: Path,
    memory_mib: int = 2048,
    guest_arguments: tuple[str, ...] = (),
    acceleration: str = "tcg",
    stream: TextIO | None = None,
) -> GuestResult:
    """Boot one certification guest and parse its machine-readable serial records."""
    if not isinstance(memory_mib, int) or isinstance(memory_mib, bool) or memory_mib <= 0:
        raise ValueError("memory_mib must be a positive integer")
    required = (Path(kernel), Path(initramfs), Path(data_image), Path(log_image))
    if any(not item.is_file() for item in required):
        raise ValueError("kernel, initramfs, data_image, and log_image must be regular files")
    root = Path(shared_root)
    if not root.is_dir():
        raise ValueError(f"shared_root is not a directory: {root}")
    if os.path.samestat(data_image.stat(), log_image.stat()):
        raise ValueError("data_image and log_image must be distinct files")
    kernel_path, initramfs_path, data_path, log_path = (
        _qemu_path(item, name)
        for item, name in zip(
            required,
            ("kernel", "initramfs", "data_image", "log_image"),
            strict=True,
        )
    )
    root_path = _qemu_path(root, "shared_root")
    checkout = _checkout_path(root)
    status = _run(["git", "status", "--porcelain"]).stdout
    if status:
        raise GuestRunError("atoms checkout must be clean before guest boot")
    commit = _run(["git", "rev-parse", "HEAD"]).stdout.strip()
    excludes_result = subprocess.run(
        ["git", "config", "--path", "--get", "core.excludesfile"],
        check=False,
        capture_output=True,
        text=True,
    )
    if excludes_result.returncode not in {0, 1}:
        raise GuestRunError(f"git config failed: {excludes_result.stderr.strip()}")
    excludes_file = excludes_result.stdout.strip() or None
    from atoms.fs.platform import BACKEND_REVISION

    identity = {
        "backend_revision": BACKEND_REVISION,
        "checkout": checkout,
        "commit": commit,
        "git_excludes_file": excludes_file,
        "kernel": platform.release(),
        "python_executable": sys.executable,
        "python_version": sys.version,
    }
    encoded_identity = base64.urlsafe_b64encode(
        json.dumps(identity, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
    ).decode()
    encoded_arguments = base64.urlsafe_b64encode(
        json.dumps(guest_arguments, ensure_ascii=True, separators=(",", ":")).encode()
    ).decode()
    command = [
        "qemu-system-x86_64",
        *_acceleration_arguments(acceleration),
        "-nographic",
        "-no-reboot",
        "-m",
        str(memory_mib),
        "-kernel",
        kernel_path,
        "-initrd",
        initramfs_path,
        "-append",
        (
            "console=ttyS0 rootfstype=9p root=root9p rootflags=trans=virtio,version=9p2000.L,ro "
            "panic=-1 data_device=/dev/vda log_device=/dev/vdb "
            f"checkout={checkout} certify_identity={encoded_identity} "
            f"certify_arguments={encoded_arguments}"
        ),
        "-fsdev",
        f"local,id=root9p,path={root_path},security_model=none,readonly=on",
        "-device",
        "virtio-9p-pci,fsdev=root9p,mount_tag=root9p",
        "-drive",
        f"file={data_path},format=raw,if=virtio,cache=writeback",
        "-drive",
        f"file={log_path},format=raw,if=virtio,cache=writeback",
        "-serial",
        "mon:stdio",
    ]
    serial = _run_streaming(command, sys.stdout if stream is None else stream)
    return GuestResult(tuple(command), serial, _parse_records(serial))
