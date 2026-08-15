"""Data-declared exerciser scenarios (design §6): spec builder, seed world, payloads.

Scenario surfaces are shaped to the documented consumer shapes — corpus writes and
archive/import moves — without importing any consumer. `family` is a coverage
assertion only; A3 remains the matrix's oracle.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from atoms.core.spec import TransactionSpec
from tests.capture_support import AFTER, DictPayloads, digest_of
from tests.coordinator_support import (
    BEFORE,
    create_file_spec,
    delete_spec,
    directory_spec,
    move_spec,
    replace_spec,
)


@dataclass(frozen=True)
class Scenario:
    name: str
    family: str  # "commit" | "rollback" | "refusal" — coverage-only
    build_spec: Callable[[], TransactionSpec]
    payloads: Callable[[], DictPayloads]
    seed_world: Callable[[Path], None]
    drift: Callable[[Path], None] | None = None
    inject_failure: str | None = None  # effect module attribute to fail, e.g. "replace_file"


def _seed_none(project: Path) -> None:
    return None


def _seed_replace(project: Path) -> None:
    directory = project / "d"
    directory.mkdir()
    (directory / "f.txt").write_bytes(BEFORE)


def _seed_delete(project: Path) -> None:
    directory = project / "d"
    directory.mkdir()
    (directory / "f.txt").write_bytes(BEFORE)


def _seed_move(project: Path) -> None:
    directory = project / "d"
    directory.mkdir()
    (directory / "source.txt").write_bytes(BEFORE)


def _seed_create(project: Path) -> None:
    (project / "d").mkdir()


def _with_after() -> DictPayloads:
    return DictPayloads({digest_of(AFTER): AFTER})


def _without_payloads() -> DictPayloads:
    return DictPayloads({})


SCENARIOS: tuple[Scenario, ...] = (
    Scenario("minimal-create", "commit", create_file_spec, _with_after, _seed_create),
    Scenario("minimal-replace", "commit", replace_spec, _with_after, _seed_replace),
    Scenario("minimal-delete", "commit", delete_spec, _without_payloads, _seed_delete),
    Scenario("minimal-move", "commit", move_spec, _without_payloads, _seed_move),
    Scenario("minimal-mkdir", "commit", directory_spec, _with_after, _seed_none),
)


def scenario(name: str) -> Scenario:
    for entry in SCENARIOS:
        if entry.name == name:
            return entry
    raise KeyError(name)


def run_clean(entry: Scenario, ingredients, monkeypatch):
    """Register the root, seed the world, and run the transaction clean."""
    from atoms.coordinator.commands import register_root, run_transaction
    from tests.test_coordinator_commands import _enable_commands

    backend, project_root, metadata_root, storage = ingredients
    _enable_commands(ingredients, monkeypatch)
    entry.seed_world(Path(project_root))
    register_root(
        backend,
        project_root,
        metadata_root,
        storage,
        genesis_payload=b"exerciser-genesis",
        registered_surface=(),
    )
    return run_transaction(
        backend,
        project_root,
        metadata_root,
        storage,
        entry.build_spec(),
        entry.payloads(),
    )
