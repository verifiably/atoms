import unicodedata

import pytest

import atoms.core.paths as paths_module
from atoms.core.errors import SpecValidationError
from atoms.core.paths import portability_equivalence_key, require_rel_path


def test_accepts_ordinary_project_relative_paths():
    for good in ("a", "a.txt", "docs/index.md", "a/b/c/d.bin", "café.txt", ".hidden", "a b/c"):
        assert require_rel_path("path", good) == good


@pytest.mark.parametrize(
    ("bad", "reason"),
    [
        ("", "empty"),
        ("/a", "leading"),
        ("a/", "trailing"),
        ("a//b", "empty component"),
        ("./a", "'.'"),
        ("a/./b", "'.'"),
        ("../a", "'..'"),
        ("a/../b", "'..'"),
        ("a\x00b", "NUL"),
    ],
)
def test_rejects_malformed_paths(bad, reason):
    with pytest.raises(SpecValidationError, match="path"):
        require_rel_path("path", bad)


def test_rejects_unpaired_surrogates():
    # A str may hold a lone surrogate, which passes every other rule but makes
    # canonical_bytes raise UnicodeEncodeError. Compilation must refuse it here.
    with pytest.raises(SpecValidationError, match="UTF-8"):
        require_rel_path("path", "a\ud800b")


def test_rejects_scratch_sigil_in_any_component():
    for bad in (".#~x", "a/.#~b", "a/.#~b/c.txt", ".#~a/b"):
        with pytest.raises(SpecValidationError, match="scratch"):
            require_rel_path("path", bad)


def test_persistent_names_resembling_the_sigil_are_accepted():
    for good in (".hidden", "#notsigil", "~backup", ".#nottilde", "a/.#b"):
        assert require_rel_path("path", good) == good


def test_error_names_the_kind():
    with pytest.raises(SpecValidationError, match="destination"):
        require_rel_path("destination", "../escape")


def test_equivalence_key_folds_case_and_normalization():
    assert portability_equivalence_key("docs/A.md") == portability_equivalence_key("docs/a.md")
    nfc = unicodedata.normalize("NFC", "café.txt")
    nfd = unicodedata.normalize("NFD", "café.txt")
    assert nfc != nfd
    assert portability_equivalence_key(nfc) == portability_equivalence_key(nfd)


def test_equivalence_key_separates_genuinely_distinct_paths():
    assert portability_equivalence_key("a/b") != portability_equivalence_key("a/c")
    assert portability_equivalence_key("a/b") != portability_equivalence_key("ab")


def test_old_path_equivalence_key_is_not_exposed():
    assert not hasattr(paths_module, "path_equivalence_key")
