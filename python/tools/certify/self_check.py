"""Manual executable checks for certification-tool-only boundaries."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any
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
    # Real scheduling, workspaces and record writing; only the expensive guest is fake.
    for jobs in (1, 3):
        serial = _check_sweep(jobs)
        parallel = _check_sweep(jobs, reverse=True)
        if serial != parallel:
            raise AssertionError("completion order changed the certification record")
        for fault in (
            "configuration", "mkfs_command", "mount_command", "log_format_version",
            "cache", "fatal", "missing", "violations", "exception",
        ):
            _check_sweep(jobs, fault=fault)
    serial = _check_sweep(1)
    for jobs in (3, None, 99):
        if serial != _check_sweep(jobs):
            raise AssertionError("parallel and serial records differ")


def _check_sweep(
    jobs: int | None, *, reverse: bool = False, fault: str | None = None
) -> dict[str, object]:
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
    workspaces: dict[str, Path] = {}
    lock = threading.Lock()
    expected_jobs = min(9, jobs if jobs is not None else 3)
    barrier = threading.Barrier(expected_jobs, timeout=5)
    active = peak = 0
    finished: list[str] = []

    def image(path: Path, _size: int) -> Path:
        path.touch()
        return path

    def run_guest(*args: object, **kwargs: object) -> guest.GuestResult:
        nonlocal active, peak
        data, log = args[2:4]
        assert isinstance(data, Path) and isinstance(log, Path)
        arguments = kwargs["guest_arguments"]
        assert isinstance(arguments, tuple)
        scenario = arguments[arguments.index("--scenario") + 1]
        index = cli.CERTIFICATION_SCENARIOS.index(scenario)
        assert log.parent == data.parent and log != data
        with lock:
            if data.parent in workspaces.values():
                raise AssertionError("guests shared a writable workspace")
            workspaces[scenario] = data.parent
            active += 1
            peak = max(peak, active)
        try:
            # First batch must overlap; a serial implementation fails this barrier.
            if index < expected_jobs:
                barrier.wait()
            offset = index % expected_jobs
            time.sleep(0.005 * (expected_jobs - offset if reverse else offset + 1))
            if not data.exists() or not log.exists():
                raise AssertionError("live guest images were removed")
            row: dict[str, object] = {
                "scenario": scenario, "marks": 1, "prefixes": 2, "violations": 0,
                "violation_details": [], "declared_cap": None,
            }
            final: dict[str, object] = {
                "configuration": encoded_configuration,
                "storage": "flush-honoring-disk.v1",
                "mkfs_command": ["mkfs.ext4", "-F", "/dev/vda"],
                "mount_command": ["mount", "/dev/mapper/certify", "/run/atoms-certify-volume"],
                "log_format_version": "1",
            }
            cache = "writeback"
            records: tuple[dict[str, object], ...] = (row, final)
            # Corrupt a middle guest, so checking only the retained last guest fails.
            if index == 1 and fault is not None:
                if fault == "exception":
                    raise guest.GuestRunError("injected guest failure")
                if fault == "cache":
                    cache = "none"
                elif fault == "fatal":
                    records = (row, {"fatal": "injected"}, final)
                elif fault == "missing":
                    records = (final,)
                elif fault == "violations":
                    row["violations"] = 1
                else:
                    final[fault] = {
                        "configuration": encoded_configuration | {"kernel_identifier": "wrong"},
                        "mkfs_command": ["mkfs.ext4", "-F", "-q", "/dev/vda"],
                        "mount_command": ["mount", "-o", "sync", "/dev/mapper/certify", "/volume"],
                        "log_format_version": "2",
                    }[fault]
            command = (
                "qemu-system-x86_64", "-drive", f"file={data},cache={cache}",
                "-drive", f"file={log},cache={cache}",
            )
            return guest.GuestResult(command, "", records)
        finally:
            with lock:
                active -= 1
                finished.append(scenario)

    writer = cli.record.write

    def write_record(path: Path, **evidence: Any) -> None:
        if active or any(workspace.exists() for workspace in workspaces.values()):
            raise AssertionError("record writing preceded guest cleanup")
        if set(finished) != set(cli.CERTIFICATION_SCENARIOS):
            raise AssertionError("record writing preceded the complete matrix")
        command = evidence["qemu_command"]
        assert isinstance(command, tuple)
        if str(workspaces[cli.CERTIFICATION_SCENARIOS[-1]]) not in command[2]:
            raise AssertionError("retained command depended on completion order")
        writer(path, **evidence)

    with tempfile.TemporaryDirectory() as temporary:
        destination = Path(temporary) / "certification"
        with (
            patch.object(cli.os, "sched_getaffinity", return_value=set(range(6))),
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
                    cli.CERTIFICATION_SCENARIOS, 1, Path("/target"), None, "kvm",
                    destination, jobs=jobs,
                )
            except guest.GuestRunError:
                if fault is None:
                    raise
            else:
                if fault is not None:
                    raise AssertionError(f"accepted {fault} from a middle guest")
        if active or any(workspace.exists() for workspace in workspaces.values()):
            raise AssertionError("sweep returned before guest cleanup")
        if peak != expected_jobs:
            raise AssertionError(f"expected {expected_jobs} concurrent guests, observed {peak}")
        if fault is not None:
            if destination.exists():
                raise AssertionError("failed evidence created a record destination")
            return {}
        document = json.loads(next(destination.glob("*.json")).read_text())
        if [row["name"] for row in document["scenarios"]] != list(cli.CERTIFICATION_SCENARIOS):
            raise AssertionError("scenario order changed")
        # Only ephemeral host image paths differ between these deterministic fake runs.
        document["harness"]["qemu_command"] = [
            part.replace(str(workspaces[cli.CERTIFICATION_SCENARIOS[-1]]), "WORK")
            for part in document["harness"]["qemu_command"]
        ]
        return document


def _check_queued_guests_are_cancelled() -> None:
    from atoms.fs.volume import VolumeConfiguration

    from . import __main__ as cli

    # Hold submitted work pending at the executor boundary; inspect real Future states.
    futures: list[Future[object]] = [Future() for _ in range(9)]
    failure = guest.GuestRunError("injected first guest failure")
    futures[0].set_exception(failure)
    configuration = VolumeConfiguration(
        "linux", "linux-4", "test-kernel", "ext4",
        ("async", "barrier=1", "data=ordered"),
        ("compat=0x1", "incompat=0x4", "ro_compat=0x2"),
    )
    with tempfile.TemporaryDirectory() as temporary:
        destination = Path(temporary) / "certification"
        with (
            patch.object(cli, "ThreadPoolExecutor") as executor,
            patch.object(cli, "_target_configuration", return_value=configuration),
            patch.object(cli.prerequisites, "check", return_value=[]),
            patch.object(cli, "_build_guest_initramfs", return_value=Path("/initramfs")),
            patch.object(cli.guest, "checkout_commit", return_value="clean-commit"),
        ):
            executor.return_value.__enter__.return_value.submit.side_effect = futures
            try:
                cli._run_scenarios(
                    cli.CERTIFICATION_SCENARIOS, 1, Path("/target"), None, "kvm",
                    destination, jobs=1,
                )
            except guest.GuestRunError as caught:
                assert caught is failure
            else:
                raise AssertionError("guest failure did not propagate")
        assert not destination.exists()
        if not all(future.cancelled() for future in futures[1:]):
            raise AssertionError("queued guests were not cancelled")


def _check_jobs_validation() -> None:
    from . import __main__ as cli

    with patch.object(cli, "_target_configuration", side_effect=AssertionError("late refusal")):
        invalid_jobs: tuple[Any, ...] = (0, -1, True, 1.5)
        for jobs in invalid_jobs:
            try:
                cli._run_scenarios(("minimal-create",), 1, Path("/target"), None, "kvm", jobs=jobs)
            except ValueError:
                pass
            else:
                raise AssertionError(f"accepted invalid {jobs=}")
    for argv in (("run", "--all", "--jobs", "0"), ("self-test", "--jobs", "2")):
        with patch.object(sys, "argv", ["certify", *argv]), contextlib.redirect_stderr(io.StringIO()):
            try:
                cli.main()
            except SystemExit as caught:
                assert caught.code == 2
            else:
                raise AssertionError("invalid --jobs did not fail at argument parsing")


def _check_parallel_serial_output() -> None:
    class Stream(io.StringIO):
        def __init__(self) -> None:
            super().__init__()
            self.writing = threading.Lock()

        def write(self, value: str) -> int:
            if not self.writing.acquire(blocking=False):
                raise AssertionError("guest console writes overlapped")
            try:
                time.sleep(0.001)
                return super().write(value)
            finally:
                self.writing.release()

    stream = Stream()
    command = [sys.executable, "-c", "for i in range(20): print(i)"]
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: guest._run_streaming(command, stream), range(3)))
    expected = "".join(f"{i}\n" for i in range(20))
    assert results == [expected] * 3
    assert sorted(stream.getvalue().splitlines()) == sorted(expected.splitlines() * 3)


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
        ("guest failure cancels queued work", _check_queued_guests_are_cancelled),
        ("positive run-only job bound", _check_jobs_validation),
        ("parallel guest console lines remain intact", _check_parallel_serial_output),
        ("QEMU failure terminates and reaps", _check_qemu_failure_ownership),
    )
    for name, check in checks:
        check()
        print(f"ok: {name}")
    return 0
