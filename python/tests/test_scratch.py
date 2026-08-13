import unicodedata
from dataclasses import replace

import pytest

from atoms.core.compiler import compile_spec
from atoms.core.effects import CreateFileNoClobber
from atoms.core.errors import ProtocolError, SpecValidationError
from atoms.core.scratch import (
    CHAIN_LEAF,
    SCRATCH_ROLES,
    SCRATCH_SIGIL,
    aliases_scratch_sigil,
    is_engine_reserved_leaf,
    is_scratch_leaf,
    leaf_of,
    scratch_leaf,
)
from tests.support import F, valid_spec


def test_sigil_is_letter_free_punctuation():
    assert SCRATCH_SIGIL == ".#~"
    assert all(not c.isalpha() for c in SCRATCH_SIGIL)


def test_leaf_of_takes_last_component():
    assert leaf_of("a/b/c.txt") == "c.txt"
    assert leaf_of("solo") == "solo"


def test_scratch_leaf_builds_expected_shape():
    name = scratch_leaf("deadbeef", "e07", "staging")
    assert name == ".#~deadbeef.e07.staging"
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
        assert is_engine_reserved_leaf(s) == aliases_scratch_sigil(s)


def test_scratch_leaf_rejects_unsafe_effect_id():
    # A malformed consumer effect_id must fail here, not when a *at syscall
    # later receives a multi-component or over-long scratch name.
    with pytest.raises(SpecValidationError, match="effect_id"):
        scratch_leaf("deadbeef", "bad/id", "staging")
    with pytest.raises(SpecValidationError):
        scratch_leaf("deadbeef", "x" * 65, "staging")


def test_chain_leaf_is_engine_reserved_but_not_scratch():
    assert CHAIN_LEAF == ".#~chain"
    assert is_engine_reserved_leaf(CHAIN_LEAF)
    assert not is_scratch_leaf(CHAIN_LEAF)


def test_builder_and_classifier_share_one_closed_vocabulary():
    for role in sorted(SCRATCH_ROLES):
        assert is_scratch_leaf(scratch_leaf("deadbeef", "e07", role))
    with pytest.raises(ProtocolError):
        scratch_leaf("deadbeef", "e07", "stage")


def test_roles_equal_the_recovery_enum():
    from atoms.core.recovery.model import ScratchRole

    assert SCRATCH_ROLES == frozenset(role.value for role in ScratchRole)


def test_scratch_grammar_validates_identifiers():
    assert not is_scratch_leaf(".#~notthree")
    assert not is_scratch_leaf(".#~a.b")
    assert not is_scratch_leaf(".#~a.b.badrole")
    assert not is_scratch_leaf(".#~bad/id.e07.staging")
    assert not is_scratch_leaf(f".#~{'x' * 300}.e07.staging")


def test_a2_still_refuses_a_declared_chain_component():
    spec = replace(
        valid_spec(),
        effects=(CreateFileNoClobber("e1", ".#~chain/f.txt", F),),
    )
    with pytest.raises(SpecValidationError):
        compile_spec(spec)
