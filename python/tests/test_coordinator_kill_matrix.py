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


def _recover(project: Path, metadata: Path) -> dict:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "tests.coordinator_child",
            str(project),
            str(metadata),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
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
    project: Path, metadata: Path, variant: str, *, committed: bool
) -> None:
    first = _recover(project, metadata)
    world = _world(project)
    second = _recover(project, metadata)
    assert second == first
    assert _world(project) == world
    assert first["lease"]["active"] is None
    expected = {
        "create": {"d": ("directory", None), **({"d/f.txt": ("file", b"after")} if committed else {})},
        "replace": {"d": ("directory", None), "d/f.txt": ("file", b"after" if committed else b"before")},
        "delete": {"d": ("directory", None), **({} if committed else {"d/f.txt": ("file", b"before")})},
        "move": {"d": ("directory", None), ("d/destination.txt" if committed else "d/source.txt"): ("file", b"before")},
        "mkdir": ({"d": ("directory", None), "d/f.txt": ("file", b"after")} if committed else {}),
    }[variant]
    assert world == expected


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
    matches = [index for index, event in enumerate(events) if event.startswith(method) and selector in event]
    assert len(matches) >= selection, [
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
