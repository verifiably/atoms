"""The public batch path-state capture (design §9)."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from atoms.chain.model import GenesisEntry, decode_entry, state_to_json
from atoms.coordinator.commands import capture_states
from atoms.core.errors import PreconditionRefused
from atoms.core.fingerprint import (
    ABSENT,
    DirectoryState,
    FileState,
    SymlinkState,
)
from atoms.fs.linux import LinuxBackend
from tests.test_coordinator_commands import (
    _durable_entries,
    _enable_commands,
    _register,
)

PAYLOAD = b"captured bytes"


def _populate(root: Path) -> None:
    (root / "dir").mkdir(mode=0o750)
    (root / "dir" / "child.txt").write_bytes(b"a child")
    (root / "file.bin").write_bytes(PAYLOAD)
    (root / "file.bin").chmod(0o640)
    (root / "link").symlink_to("dir")
    (root / "plain").write_bytes(b"not a directory")


def test_capture_states_returns_exactly_the_named_paths_in_the_given_order(
    tmp_path: Path,
) -> None:
    _populate(tmp_path)
    paths = ("link", "absent", "file.bin", "dir", "plain/below", "missing/leaf")

    captured = capture_states(LinuxBackend(), str(tmp_path), paths)

    assert tuple(path for path, _ in captured) == paths
    assert dict(captured) == {
        "link": SymlinkState(target="dir", mode=0o777),
        "absent": ABSENT,
        "file.bin": FileState(
            content_hash="sha256:" + hashlib.sha256(PAYLOAD).hexdigest(),
            mode=0o640,
            byte_len=len(PAYLOAD),
        ),
        "dir": DirectoryState(mode=0o750),
        "plain/below": ABSENT,
        "missing/leaf": ABSENT,
    }


def test_capture_states_walks_no_directory(tmp_path: Path) -> None:
    _populate(tmp_path)

    captured = capture_states(LinuxBackend(), str(tmp_path), ("dir",))

    assert captured == (("dir", DirectoryState(mode=0o750)),)


def test_capture_states_returns_the_empty_tuple_for_no_paths(tmp_path: Path) -> None:
    assert capture_states(LinuxBackend(), str(tmp_path), ()) == ()


@pytest.mark.parametrize(
    "paths",
    [("a", "a"), ("/absolute",), ("../escape",), ("",), ["a"], ("a", 1)],
)
def test_capture_states_refuses_an_inadmissible_path_tuple(tmp_path: Path, paths) -> None:
    with pytest.raises(PreconditionRefused):
        capture_states(LinuxBackend(), str(tmp_path), paths)


def test_capture_states_refuses_an_entry_outside_the_closed_vocabulary(
    tmp_path: Path,
) -> None:
    """Design §9.3: the vocabulary is closed by authority §6; widening is a design act."""
    os.mkfifo(tmp_path / "pipe")

    with pytest.raises(PreconditionRefused, match="pipe"):
        capture_states(LinuxBackend(), str(tmp_path), ("pipe",))


def test_capture_states_agrees_with_the_registration_baseline(
    coordinator_on, monkeypatch
) -> None:
    """Design §9.1: one `_capture_path`, so there is no second summary model."""
    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    backend, project_root, _, _ = ingredients
    _populate(Path(project_root))
    surface = ("absent", "dir", "file.bin", "link")

    digest = _register(ingredients, b"root", surface)
    captured = capture_states(backend, project_root, surface)

    genesis = decode_entry(_durable_entries(project_root)[digest])[1]
    assert type(genesis) is GenesisEntry
    assert genesis.baseline == tuple(
        (path, state_to_json(state)) for path, state in captured
    )
