"""Manual executable checks for certification-tool-only boundaries."""

from __future__ import annotations

import contextlib
import io
import json
import os
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


def _check_scenario_selection_and_cap() -> None:
    from . import __main__ as cli

    expected = (
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
    if cli.CERTIFICATION_SCENARIOS != expected:
        raise AssertionError(f"certification scenarios changed: {cli.CERTIFICATION_SCENARIOS}")
    if cli._selected_scenarios("minimal-create", False) != ("minimal-create",):
        raise AssertionError("single-scenario selection changed")
    if cli._selected_scenarios(None, True) != cli.CERTIFICATION_SCENARIOS:
        raise AssertionError("full-matrix selection changed")
    if "drift-blocker" in cli.CERTIFICATION_SCENARIOS or "refusal-capability" in (
        cli.CERTIFICATION_SCENARIOS
    ):
        raise AssertionError("a refusal-only scenario entered certification")
    for prefixes, cap in ((2001, None), (10, 9)):
        try:
            guest_init._check_prefix_budget(prefixes, cap)
        except ValueError:
            pass
        else:
            raise AssertionError(f"prefix budget {prefixes=} {cap=} was accepted")
    guest_init._check_prefix_budget(2000, None)
    guest_init._check_prefix_budget(10, 10)


def _check_acceleration_and_serial_parsing() -> None:
    if guest._acceleration_arguments("tcg") != ("-accel", "tcg"):
        raise AssertionError("TCG acceleration argv changed")
    if guest._acceleration_arguments("kvm") != ("-accel", "kvm"):
        raise AssertionError("KVM acceleration argv changed")
    try:
        guest._acceleration_arguments("auto")
    except ValueError:
        pass
    else:
        raise AssertionError("silent acceleration selection was accepted")
    serial = 'boot\nCERTIFY-JSON:{"scenario":"minimal-create","violations":0}\n'

    class Process:
        stdout = io.StringIO(serial)

        @staticmethod
        def wait() -> int:
            return 0

    streamed = io.StringIO()
    with patch.object(guest.subprocess, "Popen", return_value=Process()):
        retained = guest._run_streaming(["qemu-system-x86_64"], streamed)
    if retained != serial or streamed.getvalue() != serial:
        raise AssertionError("streamed serial was not retained exactly")
    if guest._parse_records(retained) != (
        {"scenario": "minimal-create", "violations": 0},
    ):
        raise AssertionError("streamed serial record parsing changed")


def _check_scenario_row_types_and_bonus() -> None:
    from . import __main__ as cli

    valid: dict[str, object] = {
        "scenario": "minimal-create",
        "marks": 1,
        "prefixes": 2,
        "violations": 0,
        "declared_cap": 10,
    }
    if cli._validate_scenario_row(valid, "minimal-create", 10) is not valid:
        raise AssertionError("a valid scenario row was not preserved")
    for field in ("marks", "prefixes", "violations", "declared_cap"):
        mutated = valid | {field: False}
        try:
            cli._validate_scenario_row(mutated, "minimal-create", 10)
        except guest.GuestRunError:
            pass
        else:
            raise AssertionError(f"boolean {field} was accepted")
    if not guest_init._same_inode_95({(1, 2)}, {(1, 2)}):
        raise AssertionError("the §9.5 same-inode tuple was missed")
    if guest_init._same_inode_95({(1, 2)}, {(1, 3)}):
        raise AssertionError("different directory inodes were tagged as §9.5")


def _check_record_boundaries_and_guest_evidence() -> None:
    from atoms.fs.volume import VolumeConfiguration

    from . import __main__ as cli

    configuration = VolumeConfiguration(
        backend_id="linux",
        backend_revision="linux-4",
        kernel_identifier="test-kernel",
        filesystem_type="ext4",
        barrier_options=("async", "barrier=1", "data=ordered"),
        durability_features=("compat=0x1", "incompat=0x4", "ro_compat=0x2"),
    )
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        destination = root / "certification"
        if cli._record_directory(destination, True, 1) != destination:
            raise AssertionError("a new record directory was not preserved")
        for all_scenarios, trials in ((False, 1), (True, 2)):
            try:
                cli._record_directory(destination, all_scenarios, trials)
            except ValueError:
                pass
            else:
                raise AssertionError("partial certification accepted --record")
        malformed = root / "file"
        malformed.write_text("not a directory", encoding="utf-8")
        try:
            cli._record_directory(malformed, True, 1)
        except ValueError:
            pass
        else:
            raise AssertionError("a record file was accepted as a destination directory")
        output = cli._record_path(destination, configuration, "2026-08-16")
        if output != destination / "2026-08-16-ext4-linux-test-kernel.json":
            raise AssertionError(f"unexpected certification filename: {output}")
        destination.mkdir()
        output.touch()
        try:
            cli._record_path(destination, configuration, "2026-08-16")
        except FileExistsError:
            pass
        else:
            raise AssertionError("an existing certification record was accepted")

    final = {
        "configuration": json.loads(json.dumps(cli.asdict(configuration))),
        "storage": "flush-honoring-disk.v1",
        "mkfs_command": ["mkfs.ext4", "-F", "/dev/vda"],
        "mount_command": ["/run/certify-mount", "/dev/mapper/certify", "/volume"],
        "log_format_version": "1",
    }
    mkfs, mount, log_format = cli._guest_harness_evidence(final, configuration)
    if mkfs != tuple(final["mkfs_command"]) or mount != tuple(final["mount_command"]):
        raise AssertionError("guest commands were reconstructed instead of retained")
    if log_format != "1":
        raise AssertionError("guest log format changed")
    qemu = (
        "qemu-system-x86_64",
        "-drive",
        "file=data,format=raw,if=virtio,cache=writeback",
        "-drive",
        "file=log,format=raw,if=virtio,cache=writeback",
    )
    if cli._qemu_cache_mode(qemu) != "writeback":
        raise AssertionError("QEMU cache mode was not derived from the retained command")


def _check_record_lands_only_after_the_complete_matrix() -> None:
    from atoms.fs.volume import VolumeConfiguration

    from . import __main__ as cli

    configuration = VolumeConfiguration(
        backend_id="linux",
        backend_revision="linux-4",
        kernel_identifier="test-kernel",
        filesystem_type="ext4",
        barrier_options=("async", "barrier=1", "data=ordered"),
        durability_features=("compat=0x1", "incompat=0x4", "ro_compat=0x2"),
    )
    encoded_configuration = json.loads(json.dumps(cli.asdict(configuration)))
    def image(path: Path, _size: int) -> Path:
        path.touch()
        return path

    for corrupt_final in (False, True):
        calls: list[str] = []
        workspaces: list[Path] = []

        def run_guest(
            *_args: object,
            _calls: list[str] = calls,
            _corrupt_final: bool = corrupt_final,
            _workspaces: list[Path] = workspaces,
            **kwargs: object,
        ) -> guest.GuestResult:
            data, log = _args[2:4]
            assert isinstance(data, Path) and isinstance(log, Path)
            if any(workspace.exists() for workspace in _workspaces):
                raise AssertionError("a previous scenario workspace survived into the next guest")
            scenario_work = data.parent
            assert log.parent == scenario_work
            _workspaces.append(scenario_work)
            arguments = kwargs["guest_arguments"]
            assert isinstance(arguments, tuple)
            scenario = arguments[arguments.index("--scenario") + 1]
            assert isinstance(scenario, str)
            _calls.append(scenario)
            row: dict[str, object] = {
                "scenario": scenario,
                "marks": 1,
                "prefixes": 2,
                "violations": 0,
                "violation_details": [],
                "declared_cap": None,
            }
            final_configuration = encoded_configuration
            if _corrupt_final and scenario == cli.CERTIFICATION_SCENARIOS[-1]:
                final_configuration = encoded_configuration | {"kernel_identifier": "wrong"}
            final = {
                "configuration": final_configuration,
                "storage": "flush-honoring-disk.v1",
                "mkfs_command": ["mkfs.ext4", "-F", "/dev/vda"],
                "mount_command": ["mount", "/dev/mapper/certify", "/volume"],
                "log_format_version": "1",
            }
            qemu_command = (
                "qemu-system-x86_64",
                "-drive",
                f"file={data},cache=writeback",
                "-drive",
                f"file={log},cache=writeback",
            )
            return guest.GuestResult(qemu_command, "", (row, final))

        def write_record(
            _path: Path,
            *,
            _calls: list[str] = calls,
            _workspaces: list[Path] = workspaces,
            **evidence: object,
        ) -> None:
            _calls.append("write")
            scenarios = evidence["scenarios"]
            if not isinstance(scenarios, list) or len(scenarios) != 9:
                raise AssertionError("record writer did not receive all nine rows")
            if any(workspace.exists() for workspace in _workspaces):
                raise AssertionError("a scenario workspace survived until record writing")
            command = evidence["qemu_command"]
            if not isinstance(command, tuple) or os.fspath(_workspaces[-1]) not in command[2]:
                raise AssertionError("final QEMU evidence did not survive workspace cleanup")

        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "certification"
            with (
                patch.object(cli, "_target_configuration", return_value=configuration),
                patch.object(cli.prerequisites, "check", return_value=[]),
                patch.object(cli, "_build_guest_initramfs", return_value=Path("/initramfs")),
                patch.object(cli, "build_log_image", side_effect=image),
                patch.object(cli.guest, "run", side_effect=run_guest),
                patch.object(cli.guest, "checkout_commit", return_value="clean-commit"),
                patch.object(cli.record, "write", side_effect=write_record),
                patch.object(cli, "_version", return_value="version"),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                try:
                    cli._run_scenarios(
                        cli.CERTIFICATION_SCENARIOS,
                        1,
                        Path("/target"),
                        None,
                        "kvm",
                        destination,
                    )
                except guest.GuestRunError:
                    if not corrupt_final:
                        raise
                else:
                    if corrupt_final:
                        raise AssertionError("a wrong final guest configuration was accepted")
            expected = list(cli.CERTIFICATION_SCENARIOS)
            if corrupt_final:
                if calls != expected or destination.exists():
                    raise AssertionError("failed evidence created a record destination")
            elif calls != [*expected, "write"] or not destination.is_dir():
                raise AssertionError(f"record was not written last: {calls}")


def _check_qemu_failure_ownership() -> None:
    class Process:
        def __init__(self, returncode: int) -> None:
            self.returncode = returncode
            self.stdout = io.StringIO("already-streamed\n")
            self.calls: list[object] = []

        def poll(self) -> int | None:
            self.calls.append("poll")
            return None

        def terminate(self) -> None:
            self.calls.append("terminate")

        def kill(self) -> None:
            self.calls.append("kill")

        def wait(self, timeout: float | None = None) -> int:
            self.calls.append(("wait", timeout))
            if timeout is not None:
                raise guest.subprocess.TimeoutExpired("qemu-system-x86_64", timeout)
            return self.returncode

    process = Process(1)

    class BrokenStream(io.StringIO):
        def write(self, value: str) -> int:
            raise OSError("injected stream failure")

    with patch.object(guest.subprocess, "Popen", return_value=process):
        try:
            guest._run_streaming(["qemu-system-x86_64"], BrokenStream())
        except OSError as caught:
            if str(caught) != "injected stream failure":
                raise
        else:
            raise AssertionError("stream failure did not propagate")
    if process.calls != ["poll", "terminate", ("wait", 5.0), "kill", ("wait", None)]:
        raise AssertionError(f"QEMU was not terminated and reaped: {process.calls}")

    exited = Process(7)
    exited.poll = lambda: 7  # type: ignore[method-assign]
    with patch.object(guest.subprocess, "Popen", return_value=exited):
        try:
            guest._run_streaming(["qemu-system-x86_64"], io.StringIO())
        except guest.GuestRunError as caught:
            if str(caught) != "qemu-system-x86_64 failed with exit 7":
                raise AssertionError(f"non-concise QEMU diagnostic: {caught}") from caught
        else:
            raise AssertionError("nonzero QEMU exit was accepted")


def run() -> int:
    """Run checks that deliberately remain outside the collected pytest suite."""
    checks = (
        ("generated init mount-tool contract", _check_init),
        ("verified replay-log rebuild precedes guest build", _check_boot_rebuild),
        ("recovery child trusts its identity-verified parent", _check_recovery_child),
        ("mapper creation rolls back after node failure", _check_mapper_rollback),
        ("scenario selection and declared prefix cap", _check_scenario_selection_and_cap),
        ("acceleration argv and serial parsing", _check_acceleration_and_serial_parsing),
        ("scenario row types and §9.5 bonus detection", _check_scenario_row_types_and_bonus),
        ("record boundaries and exact guest evidence", _check_record_boundaries_and_guest_evidence),
        ("record lands only after the complete matrix", _check_record_lands_only_after_the_complete_matrix),
        ("QEMU failure terminates and reaps", _check_qemu_failure_ownership),
    )
    for name, check in checks:
        check()
        print(f"ok: {name}")
    return 0
