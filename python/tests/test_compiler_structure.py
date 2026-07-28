from dataclasses import dataclass

import pytest

from atoms.core.compiler import CompiledSpec, compile_spec
from atoms.core.effects import CreateFileNoClobber, DeletePath, ReplaceFile
from atoms.core.errors import SpecValidationError
from atoms.core.fingerprint import ABSENT, DirectoryState, FileState, SymlinkState
from atoms.core.spec import Dependency, SurfaceEntry
from tests.support import EMPTY, F, valid_spec


def test_a_valid_spec_compiles():
    compiled = compile_spec(valid_spec())
    assert isinstance(compiled, CompiledSpec)
    assert compiled.spec == valid_spec()
    assert [t.path for t in compiled.timelines] == ["a.txt"]


def test_compiled_spec_is_frozen():
    compiled = compile_spec(valid_spec())
    with pytest.raises(Exception):  # noqa: B017
        compiled.spec = None  # type: ignore[misc]


# --- phase 1: top-level structure ---


def test_non_transaction_spec_is_rejected():
    with pytest.raises(SpecValidationError, match="TransactionSpec"):
        compile_spec({"schema_version": 1})  # type: ignore[arg-type]


def test_unknown_schema_version_is_rejected():
    with pytest.raises(SpecValidationError, match="schema_version"):
        compile_spec(valid_spec(schema_version=999))


def test_bool_schema_version_is_rejected():
    # bool subclasses int; True == 1 must not pass as the schema version.
    with pytest.raises(SpecValidationError, match="schema_version"):
        compile_spec(valid_spec(schema_version=True))


def test_empty_effect_sequence_is_rejected():
    # Phases 10-13 are all vacuously satisfied by an empty spec, so phase 1 is the
    # only place it can be caught (authority design §5.4).
    with pytest.raises(SpecValidationError, match="at least one effect"):
        compile_spec(valid_spec(effects=(), initial_surface=(), final_surface=()))


def test_bad_consumer_tag_is_rejected():
    with pytest.raises(SpecValidationError, match="consumer_tag"):
        compile_spec(valid_spec(consumer_tag="not a valid tag"))


@pytest.mark.parametrize(
    "digest",
    ["", "sha256:" + "0" * 63, "sha256:" + "0" * 65, "sha256:" + "A" * 64, "md5:" + "0" * 64, "0" * 64],
)
def test_bad_intent_digest_is_rejected(digest):
    with pytest.raises(SpecValidationError, match="intent_digest"):
        compile_spec(valid_spec(intent_digest=digest))


def test_non_tuple_containers_are_rejected():
    for field in ("initial_surface", "final_surface", "effects", "dependencies"):
        with pytest.raises(SpecValidationError, match=field):
            compile_spec(valid_spec(**{field: ["not", "a", "tuple"]}))


# --- phase 1: nested structure ---


def test_non_surface_entry_in_a_surface_is_rejected():
    with pytest.raises(SpecValidationError, match="initial_surface"):
        compile_spec(valid_spec(initial_surface=("a.txt",)))


def test_non_str_surface_path_is_rejected():
    with pytest.raises(SpecValidationError, match="path"):
        compile_spec(valid_spec(initial_surface=(SurfaceEntry(path=42, state=ABSENT),)))  # type: ignore[arg-type]


def test_non_path_state_in_a_surface_is_rejected():
    with pytest.raises(SpecValidationError, match="state"):
        compile_spec(valid_spec(initial_surface=(SurfaceEntry(path="a.txt", state="absent"),)))  # type: ignore[arg-type]


def test_non_effect_in_effects_is_rejected():
    # Must be caught before build_timelines, whose singledispatch raises TypeError.
    with pytest.raises(SpecValidationError, match="effects"):
        compile_spec(valid_spec(effects=("not an effect",)))


def test_non_str_effect_path_is_rejected():
    with pytest.raises(SpecValidationError, match="path"):
        compile_spec(valid_spec(effects=(CreateFileNoClobber(effect_id="e1", path=42, post=F),)))  # type: ignore[arg-type]


def test_non_str_effect_id_is_rejected():
    with pytest.raises(SpecValidationError, match="effect_id"):
        compile_spec(valid_spec(effects=(CreateFileNoClobber(effect_id=7, path="a.txt", post=F),)))  # type: ignore[arg-type]


def test_non_path_state_in_an_effect_field_is_rejected():
    with pytest.raises(SpecValidationError, match="post"):
        compile_spec(valid_spec(effects=(CreateFileNoClobber(effect_id="e1", path="a.txt", post="F"),)))  # type: ignore[arg-type]


def test_non_dependency_in_dependencies_is_rejected():
    with pytest.raises(SpecValidationError, match="dependencies"):
        compile_spec(valid_spec(dependencies=(("e1", "e2"),)))


def test_non_str_dependency_endpoint_is_rejected():
    with pytest.raises(SpecValidationError, match="dependencies"):
        compile_spec(valid_spec(dependencies=(Dependency(before="e1", after=2),)))  # type: ignore[arg-type]


# --- phase 1: the model is closed, so a subclass is not a member ---
#
# Each of these passes an `isinstance` gate. Without the exact-type rule, the first two
# reach a `type(...)`-keyed field table and raise KeyError, and the third reaches
# `require_rel_path` and raises whatever the subclass chose to raise. All three would
# break the "nothing but SpecValidationError escapes" contract.


@dataclass(frozen=True, slots=True)
class _EffectSubclass(CreateFileNoClobber):
    pass


class _StateSubclass(FileState):
    pass


class _PathSubclass(str):
    def startswith(self, *args, **kwargs):  # pragma: no cover - phase 1 refuses first
        raise RuntimeError("a str subclass reached the path grammar")


def test_effect_subclass_is_rejected():
    with pytest.raises(SpecValidationError, match="five effect variants"):
        compile_spec(valid_spec(effects=(_EffectSubclass(effect_id="e1", path="a.txt", post=F),)))


def test_path_state_subclass_is_rejected():
    bad = _StateSubclass(content_hash=F.content_hash, mode=0o644, byte_len=3)
    with pytest.raises(SpecValidationError, match="four path states"):
        compile_spec(valid_spec(final_surface=(SurfaceEntry(path="a.txt", state=bad),)))


def test_str_subclass_is_rejected_before_a_later_phase_calls_a_method_on_it():
    evil = _PathSubclass("a.txt")
    with pytest.raises(SpecValidationError, match="must be a string"):
        compile_spec(valid_spec(effects=(CreateFileNoClobber(effect_id="e1", path=evil, post=F),)))


def test_tuple_subclass_container_is_rejected():
    # A tuple subclass may yield different members on each pass, so validating the pass
    # phase 1 sees would not bind the pass a later phase reads.
    class _TupleSubclass(tuple):
        pass

    with pytest.raises(SpecValidationError, match="effects must be a tuple"):
        compile_spec(valid_spec(effects=_TupleSubclass(valid_spec().effects)))


# --- phase 2: fingerprints ---


@pytest.mark.parametrize(
    "content_hash",
    ["", "sha256:" + "0" * 63, "sha256:" + "F" * 64, "md5:" + "0" * 64, "0" * 64],
)
def test_bad_content_hash_is_rejected(content_hash):
    bad = FileState(content_hash=content_hash, mode=0o644, byte_len=1)
    with pytest.raises(SpecValidationError, match="content_hash"):
        compile_spec(valid_spec(final_surface=(SurfaceEntry(path="a.txt", state=bad),)))


def test_negative_byte_len_is_rejected():
    bad = FileState(content_hash=F.content_hash, mode=0o644, byte_len=-1)
    with pytest.raises(SpecValidationError, match="byte_len"):
        compile_spec(valid_spec(final_surface=(SurfaceEntry(path="a.txt", state=bad),)))


def test_bool_byte_len_is_rejected():
    bad = FileState(content_hash=F.content_hash, mode=0o644, byte_len=True)
    with pytest.raises(SpecValidationError, match="byte_len"):
        compile_spec(valid_spec(final_surface=(SurfaceEntry(path="a.txt", state=bad),)))


@pytest.mark.parametrize("mode", [-1, 0o10000, 0o100644])
def test_out_of_range_mode_is_rejected(mode):
    bad = DirectoryState(mode=mode)
    with pytest.raises(SpecValidationError, match="mode"):
        compile_spec(valid_spec(final_surface=(SurfaceEntry(path="a.txt", state=bad),)))


def test_bool_mode_is_rejected():
    with pytest.raises(SpecValidationError, match="mode"):
        compile_spec(valid_spec(final_surface=(SurfaceEntry(path="a.txt", state=DirectoryState(mode=True)),)))


def test_setuid_setgid_and_sticky_modes_are_accepted():
    # 0..0o7777 admits setgid directories, which are a legitimate postcondition.
    for mode in (0o755, 0o2755, 0o4755, 0o1777, 0o7777):
        state = FileState(content_hash=F.content_hash, mode=mode, byte_len=3)
        compile_spec(valid_spec(
            final_surface=(SurfaceEntry(path="a.txt", state=state),),
            effects=(CreateFileNoClobber(effect_id="e1", path="a.txt", post=state),),
        ))


def test_empty_symlink_target_is_rejected():
    bad = SymlinkState(target="", mode=0o777)
    with pytest.raises(SpecValidationError, match="target"):
        compile_spec(valid_spec(initial_surface=(SurfaceEntry(path="a.txt", state=bad),)))


def test_symlink_target_with_surrogate_is_rejected():
    bad = SymlinkState(target="t\ud800", mode=0o777)
    with pytest.raises(SpecValidationError, match="UTF-8"):
        compile_spec(valid_spec(initial_surface=(SurfaceEntry(path="a.txt", state=bad),)))


def test_symlink_target_with_nul_is_rejected():
    # Symlink targets are opaque rather than project-relative, but NUL still cannot
    # reach the filesystem representation.
    bad = SymlinkState(target="a\x00b", mode=0o777)
    with pytest.raises(SpecValidationError, match="NUL"):
        compile_spec(valid_spec(initial_surface=(SurfaceEntry(path="a.txt", state=bad),)))


def test_symlink_target_may_be_absolute_or_contain_dotdot():
    # A target is opaque bytes the engine fingerprints, not a path it resolves (§6),
    # so the project-relative grammar must NOT apply to it.
    for target in ("/etc/passwd", "../../elsewhere", "./x"):
        state = SymlinkState(target=target, mode=0o777)
        compile_spec(valid_spec(
            initial_surface=(SurfaceEntry(path="lnk", state=state),),
            final_surface=(SurfaceEntry(path="lnk", state=ABSENT),),
            effects=(DeletePath(effect_id="e1", path="lnk", pre=state),),
        ))


def test_empty_file_hash_and_byte_len_must_agree():
    mismatched = FileState(content_hash=EMPTY.content_hash, mode=0o644, byte_len=7)
    with pytest.raises(SpecValidationError, match="empty"):
        compile_spec(valid_spec(
            final_surface=(SurfaceEntry(path="a.txt", state=mismatched),),
            effects=(CreateFileNoClobber(effect_id="e1", path="a.txt", post=mismatched),),
        ))
    other = FileState(content_hash="sha256:" + "9" * 64, mode=0o644, byte_len=0)
    with pytest.raises(SpecValidationError, match="empty"):
        compile_spec(valid_spec(
            final_surface=(SurfaceEntry(path="a.txt", state=other),),
            effects=(CreateFileNoClobber(effect_id="e1", path="a.txt", post=other),),
        ))


def test_genuine_empty_file_is_accepted():
    compile_spec(valid_spec(
        final_surface=(SurfaceEntry(path="a.txt", state=EMPTY),),
        effects=(CreateFileNoClobber(effect_id="e1", path="a.txt", post=EMPTY),),
    ))


def test_replace_file_states_are_both_checked():
    bad = FileState(content_hash="nope", mode=0o644, byte_len=1)
    with pytest.raises(SpecValidationError, match="content_hash"):
        compile_spec(valid_spec(
            initial_surface=(SurfaceEntry(path="a.txt", state=F),),
            final_surface=(SurfaceEntry(path="a.txt", state=bad),),
            effects=(ReplaceFile(effect_id="e1", path="a.txt", pre=F, post=bad),),
        ))
