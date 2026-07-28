import sys

import pytest

import atoms.core.compiler as compiler_module
import atoms.core.paths as paths_module
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
from atoms.core.spec import build_spec
from tests.support import DIGEST, D, F, G, L


def _spec(initial, final, effects, dependencies=()):
    return build_spec(
        consumer_tag="cnsmr",
        intent_digest=DIGEST,
        initial_surface=initial,
        final_surface=final,
        effects=effects,
        dependencies=dependencies,
    )


# --- phase 9: continuity, wired through compile_spec ---


def test_repeated_path_absent_file_absent_file_compiles():
    compiled = compile_spec(
        _spec(
            {"p": ABSENT},
            {"p": G},
            (
                CreateFileNoClobber(effect_id="e1", path="p", post=F),
                DeletePath(effect_id="e2", path="p", pre=F),
                CreateFileNoClobber(effect_id="e3", path="p", post=G),
            ),
        )
    )
    (timeline,) = compiled.timelines
    assert [o.effect_id for o in timeline.occurrences] == ["e1", "e2", "e3"]


def test_discontinuous_timeline_is_rejected():
    with pytest.raises(SpecValidationError, match="discontinuous"):
        compile_spec(
            _spec(
                {"p": ABSENT},
                {"p": ABSENT},
                (
                    CreateFileNoClobber(effect_id="e1", path="p", post=F),
                    DeletePath(effect_id="e2", path="p", pre=G),
                ),
            )
        )


# --- phase 10: exact coverage ---


def test_surface_path_with_no_effect_is_rejected():
    with pytest.raises(SpecValidationError, match="no effect mutates"):
        compile_spec(
            _spec(
                {"a": ABSENT, "unused": F},
                {"a": F, "unused": F},
                (CreateFileNoClobber(effect_id="e1", path="a", post=F),),
            )
        )


def test_effect_path_missing_from_the_initial_surface_is_rejected():
    with pytest.raises(SpecValidationError, match="initial_surface omits"):
        compile_spec(
            _spec(
                {},
                {"a": F},
                (CreateFileNoClobber(effect_id="e1", path="a", post=F),),
            )
        )


def test_effect_path_missing_from_the_final_surface_is_rejected():
    with pytest.raises(SpecValidationError, match="final_surface"):
        compile_spec(
            _spec(
                {"a": ABSENT},
                {},
                (CreateFileNoClobber(effect_id="e1", path="a", post=F),),
            )
        )


def test_move_declares_both_endpoints_on_both_surfaces():
    compile_spec(
        _spec(
            {"s": F, "d": ABSENT},
            {"s": ABSENT, "d": F},
            (MoveNoClobber(effect_id="e1", source="s", destination="d", source_pre=F),),
        )
    )


# --- phase 11: timeline endpoints ---


def test_initial_surface_disagreeing_with_the_first_precondition_is_rejected():
    with pytest.raises(SpecValidationError, match="initial"):
        compile_spec(
            _spec(
                {"a": G},
                {"a": G},
                (ReplaceFile(effect_id="e1", path="a", pre=F, post=G),),
            )
        )


def test_final_surface_disagreeing_with_the_last_postcondition_is_rejected():
    with pytest.raises(SpecValidationError, match="final"):
        compile_spec(
            _spec(
                {"a": F},
                {"a": F},
                (ReplaceFile(effect_id="e1", path="a", pre=F, post=G),),
            )
        )


# --- phases 12-13: iterative tree construction ---


def test_prefix_materializing_ancestors_api_is_absent():
    assert not hasattr(compiler_module, "ancestors")
    assert not hasattr(paths_module, "ancestors")


def test_tree_validation_has_no_python_recursion_depth_limit():
    depth = sys.getrecursionlimit() + 100
    path = "/".join(f"d{i}" for i in range(depth))
    compile_spec(
        _spec(
            {path: ABSENT},
            {path: F},
            (CreateFileNoClobber(effect_id="e1", path=path, post=F),),
        )
    )


# --- phase 12: surface tree consistency ---


def test_descendant_of_an_absent_ancestor_must_be_absent():
    with pytest.raises(SpecValidationError, match="beneath"):
        compile_spec(
            _spec(
                {"d": ABSENT, "d/f": F},
                {"d": D, "d/f": ABSENT},
                (
                    CreateDirectory(effect_id="e1", path="d", post=D),
                    DeletePath(effect_id="e2", path="d/f", pre=F),
                ),
            )
        )


def test_descendant_of_a_file_ancestor_must_be_absent():
    with pytest.raises(SpecValidationError, match="beneath"):
        compile_spec(
            _spec(
                {"p": F, "p/q": F},
                {"p": ABSENT, "p/q": ABSENT},
                (
                    DeletePath(effect_id="e1", path="p/q", pre=F),
                    DeletePath(effect_id="e2", path="p", pre=F),
                ),
            )
        )


def test_ancestor_type_change_is_permitted():
    # The valid transition a stricter rule would wrongly reject: p/q is absent
    # initially precisely BECAUSE p is a file then.
    compiled = compile_spec(
        _spec(
            {"p": F, "p/q": ABSENT},
            {"p": D, "p/q": G},
            (
                DeletePath(effect_id="e1", path="p", pre=F),
                CreateDirectory(effect_id="e2", path="p", post=D),
                CreateFileNoClobber(effect_id="e3", path="p/q", post=G),
            ),
        )
    )
    assert [t.path for t in compiled.timelines] == ["p", "p/q"]


def test_symlink_ancestor_type_change_is_permitted():
    compile_spec(
        _spec(
            {"p": L, "p/q": ABSENT},
            {"p": D, "p/q": G},
            (
                DeletePath(effect_id="e1", path="p", pre=L),
                CreateDirectory(effect_id="e2", path="p", post=D),
                CreateFileNoClobber(effect_id="e3", path="p/q", post=G),
            ),
        )
    )


def test_undeclared_ancestor_carries_no_constraint():
    # Whether 'deep' exists and is a directory is a live filesystem question (A4).
    compile_spec(
        _spec(
            {"deep/nested/f": ABSENT},
            {"deep/nested/f": F},
            (CreateFileNoClobber(effect_id="e1", path="deep/nested/f", post=F),),
        )
    )


def test_the_rule_applies_to_the_final_surface_too():
    with pytest.raises(SpecValidationError, match="beneath"):
        compile_spec(
            _spec(
                {"p": ABSENT, "p/q": ABSENT},
                {"p": F, "p/q": G},
                (
                    CreateFileNoClobber(effect_id="e1", path="p", post=F),
                    CreateFileNoClobber(effect_id="e2", path="p/q", post=G),
                ),
            )
        )


# --- phase 13: created-directory ancestor ordering ---


def test_directory_creation_must_precede_its_descendants():
    with pytest.raises(SpecValidationError, match="before"):
        compile_spec(
            _spec(
                {"d": ABSENT, "d/f": ABSENT},
                {"d": D, "d/f": F},
                (
                    CreateFileNoClobber(effect_id="e1", path="d/f", post=F),
                    CreateDirectory(effect_id="e2", path="d", post=D),
                ),
            )
        )


def test_outer_to_inner_directory_creation_is_required():
    with pytest.raises(SpecValidationError, match="before"):
        compile_spec(
            _spec(
                {"a": ABSENT, "a/b": ABSENT},
                {"a": D, "a/b": D},
                (
                    CreateDirectory(effect_id="e1", path="a/b", post=D),
                    CreateDirectory(effect_id="e2", path="a", post=D),
                ),
            )
        )


def test_correctly_ordered_nested_creation_compiles():
    compile_spec(
        _spec(
            {"a": ABSENT, "a/b": ABSENT, "a/b/f": ABSENT},
            {"a": D, "a/b": D, "a/b/f": F},
            (
                CreateDirectory(effect_id="e1", path="a", post=D),
                CreateDirectory(effect_id="e2", path="a/b", post=D),
                CreateFileNoClobber(effect_id="e3", path="a/b/f", post=F),
            ),
        )
    )


def test_ordering_applies_to_a_move_destination_beneath_a_created_directory():
    with pytest.raises(SpecValidationError, match="before"):
        compile_spec(
            _spec(
                {"s": F, "d": ABSENT, "d/t": ABSENT},
                {"s": ABSENT, "d": D, "d/t": F},
                (
                    MoveNoClobber(
                        effect_id="e1",
                        source="s",
                        destination="d/t",
                        source_pre=F,
                    ),
                    CreateDirectory(effect_id="e2", path="d", post=D),
                ),
            )
        )


# --- all five variants together ---


def test_a_spec_using_every_variant_compiles():
    compiled = compile_spec(
        _spec(
            {
                "dir": ABSENT,
                "repl.txt": F,
                "new.txt": ABSENT,
                "del.txt": F,
                "lnk": L,
                "src": F,
                "dst": ABSENT,
            },
            {
                "dir": D,
                "repl.txt": G,
                "new.txt": F,
                "del.txt": ABSENT,
                "lnk": ABSENT,
                "src": ABSENT,
                "dst": F,
            },
            (
                CreateDirectory(effect_id="e1", path="dir", post=D),
                ReplaceFile(effect_id="e2", path="repl.txt", pre=F, post=G),
                CreateFileNoClobber(effect_id="e3", path="new.txt", post=F),
                DeletePath(effect_id="e4", path="del.txt", pre=F),
                DeletePath(effect_id="e5", path="lnk", pre=L),
                MoveNoClobber(
                    effect_id="e6",
                    source="src",
                    destination="dst",
                    source_pre=F,
                ),
            ),
            [("e1", "e3")],
        )
    )
    assert [t.path for t in compiled.timelines] == [
        "del.txt",
        "dir",
        "dst",
        "lnk",
        "new.txt",
        "repl.txt",
        "src",
    ]
