import unicodedata

import pytest

from atoms.core.compiler import compile_spec
from atoms.core.effects import (
    CreateDirectory,
    CreateFileNoClobber,
    DeletePath,
    MoveNoClobber,
    ReplaceFile,
)
from atoms.core.errors import SpecValidationError
from atoms.core.fingerprint import ABSENT
from atoms.core.spec import Dependency, SurfaceEntry, build_spec
from tests.support import DIGEST, D, F, G, L, valid_spec


def _spec(initial, final, effects, dependencies=()):
    return build_spec(
        consumer_tag="cnsmr",
        intent_digest=DIGEST,
        initial_surface=initial,
        final_surface=final,
        effects=effects,
        dependencies=dependencies,
    )


# --- phase 3: path grammar applied to effects and surfaces ---


def test_malformed_effect_path_is_rejected():
    with pytest.raises(SpecValidationError, match="path"):
        compile_spec(valid_spec(
            effects=(CreateFileNoClobber(effect_id="e1", path="../escape", post=F),),
        ))


def test_malformed_surface_path_is_rejected():
    with pytest.raises(SpecValidationError, match="path"):
        compile_spec(valid_spec(
            initial_surface=(SurfaceEntry(path="/absolute", state=ABSENT),),
        ))


def test_scratch_alias_in_an_effect_path_is_rejected():
    with pytest.raises(SpecValidationError, match="scratch"):
        compile_spec(valid_spec(
            effects=(CreateFileNoClobber(effect_id="e1", path=".#~sneaky", post=F),),
        ))


def test_surrogate_in_an_effect_path_is_rejected():
    with pytest.raises(SpecValidationError, match="UTF-8"):
        compile_spec(valid_spec(
            effects=(CreateFileNoClobber(effect_id="e1", path="a\ud800b", post=F),),
        ))


def test_both_move_endpoints_are_grammar_checked():
    # Overriding only `effects` keeps the surfaces well-formed, so the refusal must
    # name the effect's destination field rather than a surface entry.
    with pytest.raises(SpecValidationError, match="destination"):
        compile_spec(valid_spec(
            effects=(MoveNoClobber(effect_id="e1", source="s", destination="../escape", source_pre=F),),
        ))


# --- phase 4: alias distinctness ---


def test_case_variant_paths_are_rejected():
    with pytest.raises(SpecValidationError, match="alias"):
        compile_spec(_spec(
            {"docs/a.md": ABSENT, "docs/A.md": ABSENT},
            {"docs/a.md": F, "docs/A.md": F},
            (
                CreateFileNoClobber(effect_id="e1", path="docs/a.md", post=F),
                CreateFileNoClobber(effect_id="e2", path="docs/A.md", post=F),
            ),
        ))


def test_normalization_variant_paths_are_rejected():
    nfc = unicodedata.normalize("NFC", "café.txt")
    nfd = unicodedata.normalize("NFD", "café.txt")
    assert nfc != nfd
    with pytest.raises(SpecValidationError, match="alias"):
        compile_spec(_spec(
            {nfc: ABSENT, nfd: ABSENT},
            {nfc: F, nfd: F},
            (
                CreateFileNoClobber(effect_id="e1", path=nfc, post=F),
                CreateFileNoClobber(effect_id="e2", path=nfd, post=F),
            ),
        ))


def test_the_create_delete_recreate_counterexample_is_rejected():
    # Design §5.4: every no-clobber operation in this sequence succeeds, so a suite
    # exercising only mutation-time guards would pass while the declared final states
    # (x absent, X present) are unsatisfiable on one entry.
    with pytest.raises(SpecValidationError, match="alias"):
        compile_spec(_spec(
            {"x": ABSENT, "X": ABSENT},
            {"x": ABSENT, "X": G},
            (
                CreateFileNoClobber(effect_id="e1", path="x", post=F),
                DeletePath(effect_id="e2", path="x", pre=F),
                CreateFileNoClobber(effect_id="e3", path="X", post=G),
            ),
        ))


def test_ancestor_case_variants_are_rejected():
    with pytest.raises(SpecValidationError, match="alias"):
        compile_spec(_spec(
            {"a/x": ABSENT, "A/x": ABSENT},
            {"a/x": F, "A/x": F},
            (
                CreateFileNoClobber(effect_id="e1", path="a/x", post=F),
                CreateFileNoClobber(effect_id="e2", path="A/x", post=F),
            ),
        ))


def test_genuinely_distinct_paths_compile():
    # 'a' is not declared: an undeclared ancestor carries no constraint, and declaring
    # it without an effect touching it would fail the coverage rule of Task 5.
    compile_spec(_spec(
        {"b": ABSENT, "a/x": ABSENT},
        {"b": F, "a/x": F},
        (
            CreateFileNoClobber(effect_id="e1", path="b", post=F),
            CreateFileNoClobber(effect_id="e2", path="a/x", post=F),
        ),
    ))


# --- phase 5: effect ID and variant shape ---


@pytest.mark.parametrize("bad_id", ["", "a/b", "a.b", "x" * 65, "e 1", "café"])
def test_unsafe_effect_id_is_rejected(bad_id):
    with pytest.raises(SpecValidationError, match="effect_id"):
        compile_spec(valid_spec(
            effects=(CreateFileNoClobber(effect_id=bad_id, path="a.txt", post=F),),
        ))


def test_replace_file_rejects_a_non_file_state():
    with pytest.raises(SpecValidationError, match="pre"):
        compile_spec(valid_spec(
            initial_surface=(SurfaceEntry(path="a.txt", state=D),),
            final_surface=(SurfaceEntry(path="a.txt", state=F),),
            effects=(ReplaceFile(effect_id="e1", path="a.txt", pre=D, post=F),),  # type: ignore[arg-type]
        ))


def test_delete_path_accepts_file_and_symlink_but_not_directory():
    compile_spec(_spec({"f": F}, {"f": ABSENT}, (DeletePath(effect_id="e1", path="f", pre=F),)))
    compile_spec(_spec({"l": L}, {"l": ABSENT}, (DeletePath(effect_id="e1", path="l", pre=L),)))
    with pytest.raises(SpecValidationError, match="pre"):
        compile_spec(_spec({"d": D}, {"d": ABSENT}, (DeletePath(effect_id="e1", path="d", pre=D),)))  # type: ignore[arg-type]


def test_create_directory_rejects_a_non_directory_state():
    with pytest.raises(SpecValidationError, match="post"):
        compile_spec(_spec({"d": ABSENT}, {"d": F}, (CreateDirectory(effect_id="e1", path="d", post=F),)))  # type: ignore[arg-type]


def test_move_with_equal_source_and_destination_is_rejected():
    with pytest.raises(SpecValidationError, match="destination"):
        compile_spec(_spec(
            {"s": F}, {"s": F},
            (MoveNoClobber(effect_id="e1", source="s", destination="s", source_pre=F),),
        ))


def test_replace_file_with_equal_pre_and_post_is_permitted():
    compile_spec(_spec({"a": F}, {"a": F}, (ReplaceFile(effect_id="e1", path="a", pre=F, post=F),)))


# --- phase 6: duplicate effect IDs ---


def test_duplicate_effect_ids_are_rejected():
    with pytest.raises(SpecValidationError, match="duplicate"):
        compile_spec(_spec(
            {"a": ABSENT, "b": ABSENT},
            {"a": F, "b": F},
            (
                CreateFileNoClobber(effect_id="dup", path="a", post=F),
                CreateFileNoClobber(effect_id="dup", path="b", post=F),
            ),
        ))


def test_portability_equivalent_effect_ids_are_rejected():
    with pytest.raises(SpecValidationError, match="portability-equivalent effect_id"):
        compile_spec(
            _spec(
                {"a": ABSENT, "b": ABSENT},
                {"a": F, "b": F},
                (
                    CreateFileNoClobber(effect_id="e1", path="a", post=F),
                    CreateFileNoClobber(effect_id="E1", path="b", post=F),
                ),
            )
        )


def test_duplicate_ids_are_refused_before_dependencies_are_validated():
    # The forced phase 6 -> phase 7 order, locked. Phase 7 resolves each endpoint through
    # {effect_id: index}, which silently keeps only the last effect carrying a duplicated
    # ID, so every dependency verdict about that ID would be arbitrary. This specification
    # violates both rules at once; the duplicate is what must surface.
    with pytest.raises(SpecValidationError, match="duplicate effect_id"):
        compile_spec(_spec(
            {"a": ABSENT, "b": ABSENT},
            {"a": F, "b": F},
            (
                CreateFileNoClobber(effect_id="dup", path="a", post=F),
                CreateFileNoClobber(effect_id="dup", path="b", post=F),
            ),
            [("dup", "ghost")],
        ))


# --- phase 7: dependencies ---


def _two_effect_spec(dependencies):
    return _spec(
        {"a": ABSENT, "b": ABSENT},
        {"a": F, "b": F},
        (
            CreateFileNoClobber(effect_id="e1", path="a", post=F),
            CreateFileNoClobber(effect_id="e2", path="b", post=F),
        ),
        dependencies,
    )


def test_dependency_agreeing_with_the_effect_order_is_accepted():
    compile_spec(_two_effect_spec([("e1", "e2")]))


def test_dependency_naming_an_unknown_effect_is_rejected():
    with pytest.raises(SpecValidationError, match="ghost"):
        compile_spec(_two_effect_spec([("e1", "ghost")]))


def test_self_dependency_is_rejected():
    with pytest.raises(SpecValidationError, match="itself"):
        compile_spec(_two_effect_spec([("e1", "e1")]))


def test_dependency_contradicting_the_effect_order_is_rejected():
    with pytest.raises(SpecValidationError, match="order"):
        compile_spec(_two_effect_spec([("e2", "e1")]))


def test_duplicate_dependency_is_rejected():
    spec = _two_effect_spec([])
    duplicated = Dependency(before="e1", after="e2")
    with pytest.raises(SpecValidationError, match="duplicate"):
        compile_spec(valid_spec(
            initial_surface=spec.initial_surface,
            final_surface=spec.final_surface,
            effects=spec.effects,
            dependencies=(duplicated, duplicated),
        ))


# --- phase 8: surface well-formedness ---


def test_duplicate_surface_path_is_rejected_not_deduplicated():
    with pytest.raises(SpecValidationError, match="duplicate"):
        compile_spec(valid_spec(
            initial_surface=(
                SurfaceEntry(path="a.txt", state=ABSENT),
                SurfaceEntry(path="a.txt", state=F),
            ),
        ))


def test_duplicate_surface_refuses_before_surface_maps_collapse_it():
    with pytest.raises(SpecValidationError, match="duplicate path"):
        compile_spec(
            valid_spec(
                initial_surface=(
                    SurfaceEntry(path="a.txt", state=ABSENT),
                    SurfaceEntry(path="a.txt", state=F),
                ),
                final_surface=(SurfaceEntry(path="a.txt", state=F),),
            )
        )
