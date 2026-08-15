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


def test_clean_and_caught_whole_cell_subprocess_placement(exerciser_child):
    """Design §6's whole-cell placement: setup, transaction, and projection in a child.

    The clean scenario must reach `COMMITTED` and the caught one `ROLLED_BACK` (erratum
    1: the rollback lands durably *before* the injected exception propagates, so the
    child prints a real projection and never synthesizes an outcome), and both must
    detach. The fixture itself performs the mandatory second pass -- a fresh lease over
    the child's roots, asserting no recovery action and an unmoved projection.
    """
    for name, family in (("minimal-create", "commit"), ("caught-rollback", "rollback")):
        projection = exerciser_child(name)
        assert projection["active"] is False
        assert (projection["state"] == "COMMITTED") == (family == "commit")
        assert (projection["state"] == "ROLLED_BACK") == (family == "rollback")


def test_sigkill_arm_covers_the_compound_scenarios(exerciser_kill_matrix):
    """Design §13.4's SIGKILL extension over the multi-effect exerciser scenarios.

    `exerciser_kill_matrix` rehearses the scenario under the recording backend, kills a
    fresh child immediately after every recorded flush/exchange/transfer, and applies the
    kill matrix's own `_assert_terminal` contract per cut. It returns the number of cuts
    driven, which must be nonzero -- an event filter that matched nothing would otherwise
    make this test pass while killing nobody.
    """
    for name in ("corpus-write", "archive-move"):
        assert exerciser_kill_matrix(name) > 0


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
