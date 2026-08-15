"""Fresh-process SIGKILL convergence across executor barriers."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from atoms.coordinator.commands import register_root
from atoms.fs.linux import LinuxBackend
from tests.coordinator_child import STORAGE
from tests.test_coordinator_commands import _enable_commands

ROOT = Path(__file__).resolve().parents[1]

BACKEND_CUTS = (
    ("replace-exchange", "replace", "exchange", "f.txt->.#~", 0),
    ("create-transfer", "create", "transfer_noclobber", ".staging->f.txt", 0),
    ("delete-transfer", "delete", "transfer_noclobber", "f.txt->.#~", 0),
    ("move-anchor", "move", "link_anchor", "source.txt->.#~", 0),
    ("move-transfer", "move", "transfer_noclobber", "source.txt->destination.txt", 0),
    ("mkdir-transfer", "mkdir", "transfer_noclobber", "->d", 0),
    ("registration-chain", "create", "transfer_noclobber", ".#~stage->", 1),
    ("settlement-chain", "create", "transfer_noclobber", ".#~stage->", 2),
)

STORE_BARRIERS = (
    "prepared",
    "registration-binding",
    "applying",
    "e1-started",
    "e1-done",
    "applied",
    "committed",
    "settlement-binding",
    "detach",
)


def _roots(ext4_volume: Path, name: str) -> tuple[Path, Path]:
    project = ext4_volume / f"project-{name}"
    metadata = ext4_volume / f"metadata-{name}"
    project.mkdir()
    return project, metadata


def _prepare(project: Path, metadata: Path, variant: str, monkeypatch) -> None:
    backend = LinuxBackend()
    ingredients = (backend, str(project), str(metadata), STORAGE)
    _enable_commands(ingredients, monkeypatch)
    if variant == "mkdir":
        pass
    else:
        directory = project / "d"
        directory.mkdir()
        if variant in {"replace", "delete"}:
            (directory / "f.txt").write_bytes(b"before")
        elif variant == "move":
            (directory / "source.txt").write_bytes(b"before")
    register_root(backend, str(project), str(metadata), STORAGE, b"root", ())


def _child(project: Path, metadata: Path, config: dict, *, killed=False):
    environment = os.environ.copy()
    environment["ATOMS_EXECUTE_CONFIG"] = json.dumps(config)
    completed = subprocess.run(
        [sys.executable, "-m", "tests.execute_child", str(project), str(metadata)],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if killed:
        assert completed.returncode == -signal.SIGKILL
        return {}
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def _recover(project: Path, metadata: Path, config: dict | None = None) -> dict:
    """One recovery in a fresh process. `config` reaches the child as
    `ATOMS_COORDINATOR_CONFIG`; the kill matrix passes none, and the persistence-cut
    placement arm passes the keys it needs (`projection`, `torn_blobs`)."""
    environment = os.environ.copy()
    if config is not None:
        environment["ATOMS_COORDINATOR_CONFIG"] = json.dumps(config)
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "tests.coordinator_child",
            str(project),
            str(metadata),
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def _world(project: Path) -> dict[str, tuple[str, bytes | None]]:
    found = {}
    for path in sorted(project.rglob("*")):
        relative = path.relative_to(project)
        if relative.parts[0] == ".#~chain":
            continue
        if path.is_dir():
            found[str(relative)] = ("directory", None)
        elif path.is_file():
            found[str(relative)] = ("file", path.read_bytes())
        else:
            found[str(relative)] = ("other", None)
    return found


def _assert_terminal(
    project: Path,
    metadata: Path,
    variant: str,
    *,
    committed: bool,
    expected: dict | None = None,
) -> None:
    """Recover twice and require convergence on the expected terminal world.

    `expected` names that world directly, for a caller whose scenario is not one of the
    five single-effect kill-matrix variants the table below spells out: the exerciser's
    compound scenarios (`tests/conftest.py`'s `exerciser_kill_matrix`) observe their two
    terminal worlds -- the seeded one and the rehearsal's finished one -- instead of
    hardcoding a surface. Everything else about the contract is the same for both
    callers, which is why they share this function rather than forking it. (`variant` is
    then used only to look the world up, so it goes unread on the `expected` path.)

    The torn-blob assertion is explicit rather than inherited from a child crash: a
    durable `blob` row whose file is missing used to kill `coordinator_child`, and the
    persistence-cut placement arm needs that tolerated for *reconstructed* worlds. It is
    now gated off by default there, and asserted here, so the loudness stays exactly
    where it was earned.
    """
    first = _recover(project, metadata)
    world = _world(project)
    second = _recover(project, metadata)
    assert second == first
    assert _world(project) == world
    assert first["lease"]["active"] is None
    assert None not in first["durable"]["blobs"].values(), first["durable"]["blobs"]
    if expected is not None:
        assert world == expected
        return
    expected = {
        "create": {"d": ("directory", None), **({"d/f.txt": ("file", b"after")} if committed else {})},
        "replace": {"d": ("directory", None), "d/f.txt": ("file", b"after" if committed else b"before")},
        "delete": {"d": ("directory", None), **({} if committed else {"d/f.txt": ("file", b"before")})},
        "move": {"d": ("directory", None), ("d/destination.txt" if committed else "d/source.txt"): ("file", b"before")},
        "mkdir": ({"d": ("directory", None), "d/f.txt": ("file", b"after")} if committed else {}),
    }[variant]
    assert world == expected


def _effect_event_indexes(events: list[str], method: str, variant: str) -> list[int]:
    commits = [index for index, event in enumerate(events) if event == "commit"]
    windows = [(commits[3], commits[4])]
    if variant == "mkdir":
        windows.append((commits[5], commits[6]))
    return [
        index
        for start, stop in windows
        for index in range(start + 1, stop)
        if events[index].startswith(method)
    ]


@pytest.mark.parametrize(
    ("variant", "method"),
    [
        (variant, method)
        for variant in ("create", "replace", "delete", "move", "mkdir")
        for method in ("flush_file", "flush_directory")
        if method == "flush_directory" or variant in {"create", "replace", "mkdir"}
    ],
)
@pytest.mark.parametrize("side", ["before", "after"])
def test_every_forward_effect_flush_barrier_converges(
    ext4_volume, monkeypatch, variant, method, side
) -> None:
    rehearsal_project, rehearsal_metadata = _roots(
        ext4_volume, f"flush-rehearsal-{variant}-{method}-{side}"
    )
    _prepare(rehearsal_project, rehearsal_metadata, variant, monkeypatch)
    events = _child(
        rehearsal_project,
        rehearsal_metadata,
        {
            "record": True,
            "variant": variant,
            "store_cut": "record",
            "countdown": 10_000,
        },
    )["events"]
    targets = _effect_event_indexes(events, method, variant)
    assert targets
    for ordinal, target in enumerate(targets):
        countdown = sum(
            event.startswith(method) for event in events[: target + 1]
        )
        project, metadata = _roots(
            ext4_volume, f"flush-{variant}-{method}-{side}-{ordinal}"
        )
        _prepare(project, metadata, variant, monkeypatch)
        _child(
            project,
            metadata,
            {
                "variant": variant,
                "method": method,
                "countdown": countdown if side == "before" else -countdown,
            },
            killed=True,
        )
        _assert_terminal(project, metadata, variant, committed=False)


@pytest.mark.parametrize("side", ["before", "after"])
def test_every_chain_append_barrier_converges(ext4_volume, monkeypatch, side) -> None:
    rehearsal_project, rehearsal_metadata = _roots(
        ext4_volume, f"chain-all-rehearsal-{side}"
    )
    _prepare(rehearsal_project, rehearsal_metadata, "create", monkeypatch)
    events = _child(
        rehearsal_project,
        rehearsal_metadata,
        {"record": True, "variant": "create"},
    )["events"]
    transfers = [
        index
        for index, event in enumerate(events)
        if event.startswith("transfer_noclobber:.#~stage->")
    ]
    assert len(transfers) == 2
    for append_index, transfer in enumerate(transfers):
        create = max(
            index
            for index in range(transfer)
            if events[index] == "create_exclusive:.#~stage"
        )
        flush_file = max(
            index
            for index in range(create, transfer)
            if events[index] == "flush_file"
        )
        flush_directory = next(
            index
            for index in range(transfer + 1, len(events))
            if events[index] == "flush_directory"
        )
        for site, target in (
            ("create", create),
            ("file-fsync", flush_file),
            ("transfer", transfer),
            ("directory-fsync", flush_directory),
        ):
            method = events[target].partition(":")[0]
            countdown = sum(
                event.startswith(method) for event in events[: target + 1]
            )
            project, metadata = _roots(
                ext4_volume,
                f"chain-{append_index}-{site}-{side}",
            )
            _prepare(project, metadata, "create", monkeypatch)
            _child(
                project,
                metadata,
                {
                    "variant": "create",
                    "method": method,
                    "countdown": countdown if side == "before" else -countdown,
                },
                killed=True,
            )
            _assert_terminal(
                project,
                metadata,
                "create",
                committed=append_index == 1,
            )


@pytest.mark.parametrize("umask", [0o022, 0o777], ids=["umask-022", "umask-777"])
@pytest.mark.parametrize("cut", ["before-repair", "after-repair"])
def test_mkdir_scaffold_cuts_follow_the_survivors_mode(
    ext4_volume, monkeypatch, umask, cut
) -> None:
    method = "mkdir_child" if cut == "before-repair" else "repair_entry_mode"
    rehearsal_project, rehearsal_metadata = _roots(
        ext4_volume, f"mkdir-rehearsal-{cut}-{umask:o}"
    )
    _prepare(rehearsal_project, rehearsal_metadata, "mkdir", monkeypatch)
    events = _child(
        rehearsal_project,
        rehearsal_metadata,
        {"record": True, "variant": "mkdir", "umask": umask},
    )["events"]
    matches = [index for index, event in enumerate(events) if event.startswith(method)]
    assert matches
    chosen = matches[-1]
    countdown = sum(event.startswith(method) for event in events[: chosen + 1])

    project, metadata = _roots(ext4_volume, f"mkdir-cut-{cut}-{umask:o}")
    _prepare(project, metadata, "mkdir", monkeypatch)
    _child(
        project,
        metadata,
        {
            "variant": "mkdir",
            "method": method,
            "countdown": -countdown,
            "umask": umask,
        },
        killed=True,
    )
    if cut == "before-repair" and umask == 0o777:
        first = _child(project, metadata, {"recover": True})
        second = _child(project, metadata, {"recover": True})
        assert first == second
        assert first["halted"] is True
        assert first["active"][0] == "halted"
    else:
        _assert_terminal(project, metadata, "mkdir", committed=False)


@pytest.mark.parametrize(
    ("name", "variant", "method", "selector", "selection"),
    BACKEND_CUTS,
    ids=[case[0] for case in BACKEND_CUTS],
)
@pytest.mark.parametrize("side", ["before", "after"])
def test_backend_and_chain_barrier_cuts_converge(
    ext4_volume,
    monkeypatch,
    name,
    variant,
    method,
    selector,
    selection,
    side,
) -> None:
    rehearsal_project, rehearsal_metadata = _roots(
        ext4_volume, f"rehearsal-{name}-{side}"
    )
    _prepare(rehearsal_project, rehearsal_metadata, variant, monkeypatch)
    events = _child(
        rehearsal_project,
        rehearsal_metadata,
        {"record": True, "variant": variant},
    )["events"]
    matches = [
        index
        for index, event in enumerate(events)
        if event.startswith(method)
        and selector in event
        and (
            name != "mkdir-transfer"
            or (
                event.rpartition(":")[2].startswith(".#~")
                and event.rpartition(":")[2].endswith(".e1.work->d")
            )
        )
    ]
    assert matches and len(matches) >= selection, [
        event for event in events if event.startswith(method)
    ]
    chosen = matches[selection - 1] if selection else matches[-1]
    countdown = sum(event.startswith(method) for event in events[: chosen + 1])

    project, metadata = _roots(ext4_volume, f"cut-{name}-{side}")
    _prepare(project, metadata, variant, monkeypatch)
    _child(
        project,
        metadata,
        {
            "variant": variant,
            "method": method,
            "countdown": countdown if side == "before" else -countdown,
        },
        killed=True,
    )
    _assert_terminal(
        project,
        metadata,
        variant,
        committed=name == "settlement-chain",
    )


@pytest.mark.parametrize("barrier", STORE_BARRIERS)
@pytest.mark.parametrize("side", ["before", "after"])
def test_store_commit_barrier_cuts_converge(
    ext4_volume, monkeypatch, barrier, side
) -> None:
    index = STORE_BARRIERS.index(barrier) + 1
    rehearsal_project, rehearsal_metadata = _roots(
        ext4_volume, f"store-rehearsal-{barrier}-{side}"
    )
    _prepare(rehearsal_project, rehearsal_metadata, "create", monkeypatch)
    rehearsal = _child(
        rehearsal_project,
        rehearsal_metadata,
        {
            "variant": "create",
            "store_cut": "record",
            "countdown": 10_000,
        },
    )
    assert rehearsal["events"] == ["commit"] * len(STORE_BARRIERS)

    project, metadata = _roots(ext4_volume, f"store-cut-{barrier}-{side}")
    _prepare(project, metadata, "create", monkeypatch)
    _child(
        project,
        metadata,
        {"variant": "create", "store_cut": side, "countdown": index},
        killed=True,
    )
    committed = index >= (8 if side == "before" else 7)
    _assert_terminal(project, metadata, "create", committed=committed)
