from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tools.certify import guest


def _assert_privileged_mount_contract(script: str) -> None:
    run_mount = "cp /usr/bin/mount /run/certify-mount"
    run_umount = "cp /usr/bin/umount /run/certify-umount"
    assert run_mount in script
    assert run_umount in script
    assert script.index("mount -t tmpfs tmpfs /run") < script.index(run_mount)
    assert script.index(run_umount) < script.index("mount --bind /run /root9p/run")


def test_initramfs_exposes_normalized_mount_tools_in_writable_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(
        command: list[str], *, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        del env
        output = Path(command[command.index("-g") + 1])
        output.write_bytes(b"initramfs")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(guest, "_run", fake_run)
    guest.build_initramfs(tmp_path)
    script = (tmp_path / "certify-init").read_text(encoding="utf-8")

    _assert_privileged_mount_contract(script)
    with pytest.raises(AssertionError):
        _assert_privileged_mount_contract(
            script.replace("cp /usr/bin/mount /run/certify-mount", "")
        )
