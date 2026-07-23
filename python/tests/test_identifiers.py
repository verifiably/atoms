import pytest

from atoms.core.errors import SpecValidationError
from atoms.core.identifiers import (
    is_valid_identifier,
    require_valid_identifier,
)


def test_accepts_bounded_ascii_identifiers():
    for good in ("e07", "effect_1", "A-B-C", "x" * 64):
        assert is_valid_identifier(good)
        assert require_valid_identifier("effect_id", good) == good


@pytest.mark.parametrize(
    "bad",
    ["", "a/b", "a.b", "a b", "x" * 65, "e\x00", "café", "e#1"],
)
def test_rejects_unsafe_identifiers(bad):
    assert not is_valid_identifier(bad)
    with pytest.raises(SpecValidationError, match="effect_id"):
        require_valid_identifier("effect_id", bad)
