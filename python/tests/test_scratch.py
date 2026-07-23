import unicodedata

import pytest

from atoms.core.errors import SpecValidationError
from atoms.core.scratch import (
    SCRATCH_SIGIL,
    aliases_scratch_sigil,
    is_scratch_leaf,
    leaf_of,
    scratch_leaf,
)


def test_sigil_is_letter_free_punctuation():
    assert SCRATCH_SIGIL == ".#~"
    assert all(not c.isalpha() for c in SCRATCH_SIGIL)


def test_leaf_of_takes_last_component():
    assert leaf_of("a/b/c.txt") == "c.txt"
    assert leaf_of("solo") == "solo"


def test_scratch_leaf_builds_expected_shape():
    name = scratch_leaf("deadbeef", "e07", "stage")
    assert name == ".#~deadbeef.e07.stage"
    assert is_scratch_leaf(name)


def test_persistent_names_are_not_scratch():
    for name in ("index.md", ".hidden", "#notsigil", "~backup", ".#nottilde"):
        assert not is_scratch_leaf(name)
        assert not aliases_scratch_sigil(name)


def test_letter_free_sigil_has_no_case_or_normalization_alias():
    # The core invariant: because the sigil is letter-free ASCII punctuation with no
    # case or NFC/NFD variant, the plain prefix test and the equivalence-aware test
    # agree on EVERY input — so no persistent leaf can alias scratch through folding.
    samples = [
        "index.md",
        ".#~x",
        "A" * 3,
        "café",  # NFC vs NFD differ, but not at the prefix
        unicodedata.normalize("NFD", "café"),
        ".#~" + unicodedata.normalize("NFD", "é"),
        "K" + " elvin",  # KELVIN SIGN casefolds to 'k'
    ]
    for s in samples:
        assert is_scratch_leaf(s) == aliases_scratch_sigil(s)


def test_scratch_leaf_rejects_unsafe_effect_id():
    # A malformed consumer effect_id must fail here, not when a *at syscall
    # later receives a multi-component or over-long scratch name.
    with pytest.raises(SpecValidationError, match="effect_id"):
        scratch_leaf("deadbeef", "bad/id", "stage")
    with pytest.raises(SpecValidationError):
        scratch_leaf("deadbeef", "e07", "x" * 65)
