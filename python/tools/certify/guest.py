"""Initramfs construction and direct-kernel QEMU guest execution."""

from __future__ import annotations

import json
import os
import platform
import subprocess
from dataclasses import dataclass
from pathlib import Path

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
    init.write_text(
        """#!/bin/sh
set -eu
mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev
mount -t tmpfs tmpfs /run
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
exec chroot /root9p /bin/sh -c 'cd "$1" && exec python -m tools.certify.guest_init' sh "$checkout"
""",
        encoding="utf-8",
    )
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


def run(
    kernel: Path,
    initramfs: Path,
    data_image: Path,
    log_image: Path,
    *,
    shared_root: Path,
    memory_mib: int = 2048,
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
    command = [
        "qemu-system-x86_64",
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
            f"data_device=/dev/vda log_device=/dev/vdb checkout={checkout}"
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
    result = _run(command)
    records: list[dict[str, object]] = []
    for line in result.stdout.splitlines():
        if not line.startswith("CERTIFY-JSON:"):
            continue
        try:
            record = json.loads(line.removeprefix("CERTIFY-JSON:"))
        except json.JSONDecodeError as caught:
            raise GuestRunError(f"invalid guest JSON: {line}") from caught
        if not isinstance(record, dict):
            raise GuestRunError("guest JSON record must be an object")
        records.append(record)
    return GuestResult(tuple(command), result.stdout, tuple(records))
