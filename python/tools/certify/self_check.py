"""Manual executable checks for certification-tool-only boundaries."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from . import guest, guest_init


def _check_init() -> None:
    script = guest._init_script(Path(sys.executable))
    guest._check_init_contract(script)
    try:
        guest._check_init_contract(script.replace("cp /usr/bin/mount /run/certify-mount", ""))
    except guest.GuestRunError:
        return
    raise AssertionError("mount-tool contract mutation was accepted")


def _check_boot_rebuild() -> None:
    from . import __main__ as cli

    calls: list[str] = []
    with tempfile.TemporaryDirectory() as temporary:
        work = Path(temporary)
        with (
            patch.object(
                cli,
                "ensure_replay_log",
                lambda _work: calls.append("rebuild") or Path("/replay-log"),
            ),
            patch.object(
                cli.guest,
                "build_initramfs",
                lambda _work: calls.append("initramfs") or Path("/initramfs"),
            ),
        ):
            cli._build_guest_initramfs(work)
    if calls != ["rebuild", "initramfs"]:
        raise AssertionError(f"guest build did not rebuild replay-log first: {calls}")


def _check_recovery_child() -> None:
    output = io.StringIO()
    with (
        patch.object(sys, "argv", ["guest_init", "--recover", "/volume"]),
        patch.object(
            guest_init,
            "verify_identity",
            side_effect=AssertionError("redundant identity scan"),
        ),
        patch.object(
            guest_init,
            "_recover_cell",
            return_value={"classified": 1, "violations": []},
        ),
        contextlib.redirect_stdout(output),
    ):
        if guest_init.main() != 0:
            raise AssertionError("recovery child returned nonzero")
    if json.loads(output.getvalue()) != {"classified": 1, "violations": []}:
        raise AssertionError("recovery child emitted an unexpected result")


def _check_mapper_rollback() -> None:
    calls: list[list[str]] = []

    def fail_mknodes(command: list[str]) -> str:
        calls.append(command)
        if command == ["dmsetup", "mknodes", "certify-self-check"]:
            raise RuntimeError("injected mknodes failure")
        return ""

    with patch.object(guest_init, "_run", fail_mknodes):
        try:
            guest_init._create_mapper(
                "certify-self-check",
                "0 1 linear /dev/null 0",
            )
        except RuntimeError as caught:
            if str(caught) != "injected mknodes failure":
                raise
        else:
            raise AssertionError("mapper fail injection did not fail")
    if ["dmsetup", "remove", "certify-self-check"] not in calls:
        raise AssertionError(f"mapper was not rolled back: {calls}")


def run() -> int:
    """Run checks that deliberately remain outside the collected pytest suite."""
    checks = (
        ("generated init mount-tool contract", _check_init),
        ("verified replay-log rebuild precedes guest build", _check_boot_rebuild),
        ("recovery child trusts its identity-verified parent", _check_recovery_child),
        ("mapper creation rolls back after node failure", _check_mapper_rollback),
    )
    for name, check in checks:
        check()
        print(f"ok: {name}")
    return 0
