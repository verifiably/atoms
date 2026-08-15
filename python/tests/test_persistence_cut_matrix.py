"""The persistence-cut sweep: A3 agreement over every reconstructible survivor (design §5)."""

from __future__ import annotations

import pytest

MINIMAL = ("minimal-create", "minimal-replace", "minimal-delete", "minimal-move", "minimal-mkdir")


@pytest.mark.parametrize("name", MINIMAL)
def test_every_cell_of_the_minimal_scenarios_agrees_with_a3(name, cut_matrix) -> None:
    report = cut_matrix(name)
    assert report.cells > 0
    assert report.classified_cells > 0, (
        "no cell reached classify_recovery: the sweep would assert nothing about A3"
    )
    assert report.disagreements == ()
    assert report.second_pass_violations == ()
    assert report.side_assertion_failures == ()


def test_the_later_placement_modes_are_declared_but_not_yet_built(cut_matrix) -> None:
    """The `Sweeper` accepts Task 7's and Task 9's keywords and refuses to pretend.

    A silently ignored `subprocess_subset=True` would let Task 7's placement test pass
    against the in-process arm it exists to contrast with.
    """
    with pytest.raises(NotImplementedError):
        cut_matrix("minimal-create", subprocess_subset=True)
    with pytest.raises(NotImplementedError):
        cut_matrix("minimal-create", sabotage="drop-barrier")
