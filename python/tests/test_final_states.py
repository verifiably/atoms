"""`TransactionOutcome.final_states` — the complete verified final surface,
returned typed (2026-08-24 holdings read/evidence design §5)."""

from __future__ import annotations

from pathlib import Path

from atoms.chain.model import RegisteredEntry, state_to_json
from atoms.coordinator.commands import read_chain, run_transaction
from atoms.core.effects import MoveNoClobber
from atoms.core.fingerprint import ABSENT, DirectoryState
from atoms.core.spec import build_spec
from tests.capture_support import DictPayloads, digest_of
from tests.coordinator_support import (
    AFTER,
    BEFORE,
    POST,
    PRE,
    create_file_spec,
    delete_spec,
    directory_spec,
    replace_spec,
)
from tests.test_coordinator_commands import _enable_commands, _register


def test_run_transaction_returns_the_complete_final_surface_typed(
    coordinator_on, monkeypatch
) -> None:
    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    backend, project_root, metadata_root, storage = ingredients
    (Path(project_root) / "d").mkdir()
    _register(ingredients, b"root", ())

    outcome = run_transaction(
        backend,
        project_root,
        metadata_root,
        storage,
        create_file_spec(),
        DictPayloads({digest_of(AFTER): AFTER}),
    )

    assert outcome.final_states == (("d/f.txt", POST),)


def test_final_states_is_complete_while_the_chain_carries_the_registered_subset(
    coordinator_on, monkeypatch
) -> None:
    """A move registered on its destination alone: the return carries both
    locations, the registration entry exactly the registered one, the overlap
    agreeing row-for-row (design §5's subset distinction)."""
    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    backend, project_root, metadata_root, storage = ingredients
    directory = Path(project_root) / "d"
    directory.mkdir()
    _register(ingredients, b"root", ())
    (directory / "source.txt").write_bytes(BEFORE)
    (directory / "source.txt").chmod(0o644)
    spec = build_spec(
        consumer_tag="test",
        intent_digest="sha256:" + "8" * 64,
        initial_surface={"d/source.txt": PRE, "d/destination.txt": ABSENT},
        final_surface={"d/source.txt": ABSENT, "d/destination.txt": PRE},
        effects=[
            MoveNoClobber(
                effect_id="e1",
                source="d/source.txt",
                destination="d/destination.txt",
                source_pre=PRE,
            )
        ],
        registered_paths=("d/destination.txt",),
    )

    outcome = run_transaction(
        backend, project_root, metadata_root, storage, spec, DictPayloads({})
    )

    assert outcome.final_states == (
        ("d/destination.txt", PRE),
        ("d/source.txt", ABSENT),
    )
    view = read_chain(backend, project_root, metadata_root, storage)
    registered = next(
        entry
        for _, entry in view.entries
        if type(entry) is RegisteredEntry and entry.txid == outcome.txid
    )
    assert registered.final == (
        ("d/destination.txt", state_to_json(PRE)),
    )


def test_final_states_carries_the_delete_rows_verified_absence(
    coordinator_on, monkeypatch
) -> None:
    """A deletion's row is the verified `AbsentState` (design §5)."""
    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    backend, project_root, metadata_root, storage = ingredients
    directory = Path(project_root) / "d"
    directory.mkdir()
    _register(ingredients, b"root", ())
    (directory / "f.txt").write_bytes(BEFORE)
    (directory / "f.txt").chmod(0o644)

    outcome = run_transaction(
        backend, project_root, metadata_root, storage, delete_spec(), DictPayloads({})
    )

    assert outcome.final_states == (("d/f.txt", ABSENT),)


def test_final_states_carries_a_replace_rows_verified_post_state(
    coordinator_on, monkeypatch
) -> None:
    """The fifth effect variant: a replacement's row is the verified post
    `FileState` (design §5)."""
    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    backend, project_root, metadata_root, storage = ingredients
    directory = Path(project_root) / "d"
    directory.mkdir()
    _register(ingredients, b"root", ())
    (directory / "f.txt").write_bytes(BEFORE)
    (directory / "f.txt").chmod(0o644)

    outcome = run_transaction(
        backend,
        project_root,
        metadata_root,
        storage,
        replace_spec(),
        DictPayloads({digest_of(AFTER): AFTER}),
    )

    assert outcome.final_states == (("d/f.txt", POST),)


def test_final_states_carries_a_directory_row_like_any_other(
    coordinator_on, monkeypatch
) -> None:
    """A `CreateDirectory` row is an ordinary `DirectoryState` — present,
    unspecial, ignorable by consumers that do not read it (design §5)."""
    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    backend, project_root, metadata_root, storage = ingredients
    _register(ingredients, b"root", ())

    outcome = run_transaction(
        backend,
        project_root,
        metadata_root,
        storage,
        directory_spec(),
        DictPayloads({digest_of(AFTER): AFTER}),
    )

    assert dict(outcome.final_states)["d"] == DirectoryState(mode=0o755)
    assert dict(outcome.final_states)["d/f.txt"] == POST
