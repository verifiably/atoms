"""The exerciser scenario library: coverage-only families and clean-run conformance."""

import pytest

from tests.exerciser import SCENARIOS, scenario

MINIMAL = ("minimal-create", "minimal-replace", "minimal-delete", "minimal-move", "minimal-mkdir")


def test_the_library_names_each_variant_minimal_scenario():
    names = {entry.name for entry in SCENARIOS}
    assert set(MINIMAL) <= names
    assert all(scenario(name).family == "commit" for name in MINIMAL)


@pytest.mark.parametrize("name", MINIMAL)
def test_each_minimal_scenario_commits_clean(name, exerciser_run):
    _ingredients, outcome = exerciser_run(name)
    assert outcome.outcome.name == "COMMITTED"


def test_compound_scenarios_commit_clean(exerciser_run):
    for name in ("corpus-write", "archive-move"):
        _, outcome = exerciser_run(name)
        assert outcome.outcome.name == "COMMITTED"


def test_caught_rollback_completes_durably_then_propagates(coordinator_on, monkeypatch):
    """Erratum 1: rollback lands durably; the injected exception reaches the caller."""
    from tests.exerciser import run_caught, scenario

    ingredients = coordinator_on()
    projection = run_caught(scenario("caught-rollback"), ingredients, monkeypatch)
    assert projection["state"] == "ROLLED_BACK"
    assert projection["active"] is False


def test_capability_refusal_precedes_metadata_and_mutation(coordinator_on, monkeypatch):
    """Design §6: a refusal has no recovery row — nothing durable may exist."""
    import sqlite3
    from pathlib import Path

    from atoms.core.capabilities import Capability
    from atoms.core.errors import CapabilityUnavailable
    from tests.exerciser import run_refused, scenario
    from tests.fs_support import RestrictedBackend

    ingredients = coordinator_on()
    _backend, project_root, metadata_root, storage = ingredients
    restricted = RestrictedBackend(set(Capability) - {Capability.ATOMIC_EXCHANGE})
    with pytest.raises(CapabilityUnavailable):
        run_refused(
            scenario("refusal-capability"),
            (restricted, project_root, metadata_root, storage),
            monkeypatch,
        )
    db = Path(metadata_root) / "atoms.db"
    if db.exists():
        with sqlite3.connect(db) as connection:
            rows = connection.execute("SELECT COUNT(*) FROM transaction_record").fetchone()
        assert rows[0] == 0
    assert sorted(p.name for p in Path(project_root).iterdir() if p.name != ".#~chain") == []
