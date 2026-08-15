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
