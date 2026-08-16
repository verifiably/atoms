"""Data-declared exerciser scenarios (design §6): spec builder, seed world, payloads.

Scenario surfaces are shaped to the documented consumer shapes — corpus writes and
archive/import moves — without importing any consumer. `family` is a coverage
assertion only; A3 remains the matrix's oracle.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from atoms.core.effects import CreateDirectory, CreateFileNoClobber, MoveNoClobber, ReplaceFile
from atoms.core.fingerprint import ABSENT
from atoms.core.spec import TransactionSpec, build_spec
from tests.capture_support import AFTER, DictPayloads, digest_of
from tests.coordinator_support import (
    BEFORE,
    DIRECTORY_POST,
    POST,
    PRE,
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


def _seed_corpus_write(project: Path) -> None:
    (project / "index.txt").write_bytes(BEFORE)


def _corpus_write_spec() -> TransactionSpec:
    """Two nested new directories + two creates beneath them + a replace (§12.1).

    Regression pointer: both `CreateDirectory` effects are freshly created and directly
    nested, the shape that forward execution could not run until
    `_register_planned_children` was called from `_apply_effect`
    (`fix(coordinator): register planned children as walk stops on forward mkdir
    publication`).
    """
    return build_spec(
        consumer_tag="test",
        intent_digest="sha256:" + "8" * 64,
        initial_surface={
            "data": ABSENT,
            "data/records": ABSENT,
            "data/records/one.txt": ABSENT,
            "data/records/two.txt": ABSENT,
            "index.txt": PRE,
        },
        final_surface={
            "data": DIRECTORY_POST,
            "data/records": DIRECTORY_POST,
            "data/records/one.txt": POST,
            "data/records/two.txt": POST,
            "index.txt": POST,
        },
        effects=[
            CreateDirectory(effect_id="e1", path="data", post=DIRECTORY_POST),
            CreateDirectory(effect_id="e2", path="data/records", post=DIRECTORY_POST),
            CreateFileNoClobber(effect_id="e3", path="data/records/one.txt", post=POST),
            CreateFileNoClobber(effect_id="e4", path="data/records/two.txt", post=POST),
            ReplaceFile(effect_id="e5", path="index.txt", pre=PRE, post=POST),
        ],
    )


def _seed_archive_move(project: Path) -> None:
    (project / "archive").mkdir()
    (project / "f.txt").write_bytes(BEFORE)


def _archive_move_spec() -> TransactionSpec:
    """Move f.txt -> archive/f.txt, then re-create f.txt: the archive/import shape.

    A repeated path: f.txt appears in the move (as vacated source) and the create
    (as the new occupant). Requires the archive/ directory to pre-exist via seed.
    """
    return build_spec(
        consumer_tag="test",
        intent_digest="sha256:" + "9" * 64,
        initial_surface={
            "f.txt": PRE,
            "archive/f.txt": ABSENT,
        },
        final_surface={
            "f.txt": POST,
            "archive/f.txt": PRE,
        },
        effects=[
            MoveNoClobber(
                effect_id="e1", source="f.txt", destination="archive/f.txt", source_pre=PRE
            ),
            CreateFileNoClobber(effect_id="e2", path="f.txt", post=POST),
        ],
    )


def _seed_caught_rollback_move(project: Path) -> None:
    directory = project / "d"
    directory.mkdir()
    (directory / "source.txt").write_bytes(BEFORE)
    (project / "other.txt").write_bytes(BEFORE)


def _caught_rollback_move_spec() -> TransactionSpec:
    """A move that lands, then a second effect that fails -- forces rollback to
    re-move the landed move (`JournalState.UNDO_STARTED` reverse traffic, design §9.4).
    """
    return build_spec(
        consumer_tag="test",
        intent_digest="sha256:" + "d" * 64,
        initial_surface={
            "d/source.txt": PRE,
            "d/destination.txt": ABSENT,
            "other.txt": PRE,
        },
        final_surface={
            "d/source.txt": ABSENT,
            "d/destination.txt": PRE,
            "other.txt": POST,
        },
        effects=[
            MoveNoClobber(
                effect_id="e1",
                source="d/source.txt",
                destination="d/destination.txt",
                source_pre=PRE,
            ),
            ReplaceFile(effect_id="e2", path="other.txt", pre=PRE, post=POST),
        ],
    )


def _drift_delete_target(project: Path) -> None:
    """Occupy the DeletePath target with a foreign file, for the recovery matrix (Task 6)."""
    (project / "d" / "f.txt").write_bytes(b"drift-foreign")


SCENARIOS: tuple[Scenario, ...] = (
    Scenario("minimal-create", "commit", create_file_spec, _with_after, _seed_create),
    Scenario("minimal-replace", "commit", replace_spec, _with_after, _seed_replace),
    Scenario("minimal-delete", "commit", delete_spec, _without_payloads, _seed_delete),
    Scenario("minimal-move", "commit", move_spec, _without_payloads, _seed_move),
    Scenario("minimal-mkdir", "commit", directory_spec, _with_after, _seed_none),
    Scenario(
        "corpus-write", "commit", _corpus_write_spec, _with_after, _seed_corpus_write
    ),
    Scenario(
        "archive-move", "commit", _archive_move_spec, _with_after, _seed_archive_move
    ),
    Scenario(
        "caught-rollback",
        "rollback",
        replace_spec,
        _with_after,
        _seed_replace,
        inject_failure="replace_file",
    ),
    Scenario(
        "caught-rollback-move",
        "rollback",
        _caught_rollback_move_spec,
        _with_after,
        _seed_caught_rollback_move,
        inject_failure="replace_file",
    ),
    Scenario(
        "drift-blocker",
        "rollback",
        delete_spec,
        _without_payloads,
        _seed_delete,
        drift=_drift_delete_target,
    ),
    Scenario(
        "refusal-capability",
        "refusal",
        replace_spec,
        _with_after,
        _seed_none,
    ),
)


def scenario(name: str) -> Scenario:
    for entry in SCENARIOS:
        if entry.name == name:
            return entry
    raise KeyError(name)


def setup_clean(entry: Scenario, ingredients, monkeypatch) -> None:
    """Enable commands, seed the world, and register the root -- the unrecorded bootstrap.

    Split out of `run_clean` so a caller that wants to record durability units (the
    persistence-cut model, `tests/persistence_model.py`) can seed and register with the
    plain backend, then swap in a recording backend for the transaction alone: design
    §4.1 makes bootstrap publication seed-owned, so it must never appear as recorded
    entry-unit enumeration.
    """
    from atoms.coordinator.commands import register_root
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


def transact(entry: Scenario, ingredients):
    """Run just the transaction half, against already-seeded, already-registered ingredients."""
    from atoms.coordinator.commands import run_transaction

    backend, project_root, metadata_root, storage = ingredients
    return run_transaction(
        backend,
        project_root,
        metadata_root,
        storage,
        entry.build_spec(),
        entry.payloads(),
    )


def run_clean(entry: Scenario, ingredients, monkeypatch):
    """Register the root, seed the world, and run the transaction clean."""
    setup_clean(entry, ingredients, monkeypatch)
    return transact(entry, ingredients)


def run_refused(entry: Scenario, ingredients, monkeypatch):
    """Like `run_clean`, but the caller expects `run_transaction` to raise.

    Setup is identical -- enable commands, seed the world, register the root -- and
    the refusal (e.g. `CapabilityUnavailable`) is left to propagate to the caller
    untouched, before any admission-side capture or mutation occurs.
    """
    return run_clean(entry, ingredients, monkeypatch)


def _latest_txid(metadata_root: str) -> str:
    import sqlite3
    from contextlib import closing

    with closing(sqlite3.connect(Path(metadata_root) / "atoms.db")) as connection:
        return connection.execute(
            "SELECT txid FROM transaction_record ORDER BY rowid DESC LIMIT 1"
        ).fetchone()[0]


def serialize_projection(projection: tuple | None) -> dict | None:
    """The canonical durable projection in a JSON round-trippable shape.

    The projection tuple is `(state, committed, rollback_result, halt_diagnostic,
    journals, active)` -- `tests/persistence_model.py`'s `_inspect` and
    `_model_projection` both build it, and so does `_durable_projection` below. A tuple
    of enums, `HaltDiagnostic` dataclasses and `EffectJournalState`s does not survive a
    pipe, and design §8's placement axis compares an in-process cell against the same
    cell recovered in a **fresh process**, so this is the one shape that crosses.

    Every field is spelled out; nothing reflects over the dataclasses, so a field A3
    grows does not silently start or stop being compared across the placement boundary
    (ledger #12's rule, applied to the test-side serialization).

    The halt diagnostic goes through production's own `encode_diagnostic`, which is what
    the store itself persists: **token-free** by construction (slots, paths, content
    hashes, enum values -- no inode number, no txid, no absolute path), so two
    placements' diagnostics compare EXACTLY rather than up to renaming. Every
    identity-bearing comparison -- approval evidence, world trees, hard-link groups --
    stays in-process, where `realign_durable_identities` and `_normalized_tree`
    alpha-rename it; none of them appears here.

    `state` keeps the enum's member NAME ("COMMITTED", "ROLLED_BACK"), the spelling
    `_durable_projection` has always returned.
    """
    if projection is None:
        return None
    from atoms.store.records import encode_diagnostic

    state, committed, rollback_result, halt_diagnostic, journals, active = projection
    return {
        "state": state.name,
        "committed": committed.name,
        "rollback_result": None if rollback_result is None else rollback_result.name,
        "halt_diagnostic": (
            None if halt_diagnostic is None else encode_diagnostic(halt_diagnostic)
        ),
        "journals": [[journal.effect_id, journal.state.name] for journal in journals],
        "active": active,
    }


def _durable_projection(ingredients, txid: str) -> dict:
    """The canonical durable projection of one transaction record, through a fresh binding.

    Mirrors `test_coordinator_conformance.py`'s `_durable_projection` idiom, but returns
    `serialize_projection`'s dict so a caller outside that module can compare against it
    without importing the model types -- and so a caller in another *process* can print
    it.
    """
    from atoms.fs.binding import bind_project_volume
    from atoms.fs.lock import acquire_project_lock
    from atoms.store.connection import open_store
    from tests.fs_support import build_test_allowlist

    backend, project_root, metadata_root, storage = ingredients
    with acquire_project_lock(backend, metadata_root) as lock:
        allowlist = build_test_allowlist(lock, project_root, storage)
        with bind_project_volume(
            project_root, lock, allowlist=allowlist, storage=storage
        ) as binding, open_store(binding) as store:
            record = store.read_record(txid)
            active = store.read_active()
    assert record is not None
    document = serialize_projection(
        (
            record.state,
            record.committed,
            record.rollback_result,
            record.halt_diagnostic,
            record.journals,
            active is not None,
        )
    )
    assert document is not None
    return document


def transact_caught(entry: Scenario, ingredients, monkeypatch) -> dict:
    """Run just the transaction half of `run_caught`, against seeded, registered ingredients.

    Erratum 1 (binding): the injected failure lands the rollback durably, and only
    then does the injected exception propagate to the caller -- so this catches
    exactly that exception and returns the canonical durable projection, never a
    synthesized `TransactionOutcome`.
    """
    from atoms.coordinator import execute
    from atoms.coordinator.commands import run_transaction

    backend, project_root, metadata_root, storage = ingredients

    assert entry.inject_failure is not None
    module = getattr(execute, entry.inject_failure)
    calls = {"n": 0}

    def failing(*args, **kwargs):
        calls["n"] += 1
        raise OSError("exerciser-injected failure")

    monkeypatch.setattr(module, "apply", failing)

    try:
        run_transaction(
            backend,
            project_root,
            metadata_root,
            storage,
            entry.build_spec(),
            entry.payloads(),
        )
    except OSError as caught:
        assert str(caught) == "exerciser-injected failure"
    else:
        raise AssertionError("expected the injected failure to propagate")
    assert calls["n"] > 0

    return _durable_projection(ingredients, _latest_txid(metadata_root))


def run_caught(entry: Scenario, ingredients, monkeypatch) -> dict:
    """Register the root, seed the world, and run a transaction whose effect fails."""
    setup_clean(entry, ingredients, monkeypatch)
    return transact_caught(entry, ingredients, monkeypatch)
