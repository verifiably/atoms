from atoms.core.capabilities import ALWAYS_REQUIRED, Capability
from atoms.core.effects import CreateFileNoClobber, ReplaceFile
from atoms.core.fingerprint import ABSENT, FileState
from atoms.core.spec import (
    SCHEMA_VERSION,
    Dependency,
    SurfaceEntry,
    TransactionSpec,
    build_spec,
)

F = FileState(content_hash="sha256:" + "4" * 64, mode=0o644, byte_len=1)


def _spec(initial, final, effects, deps=()):
    return build_spec(
        consumer_tag="test",
        intent_digest="sha256:" + "0" * 64,
        initial_surface=initial,
        final_surface=final,
        effects=tuple(effects),
        dependencies=deps,
    )


def test_build_spec_sets_schema_version_and_is_frozen():
    spec = _spec({"a": F}, {"a": F}, [ReplaceFile(effect_id="e1", path="a", pre=F, post=F)])
    assert spec.schema_version == SCHEMA_VERSION
    assert isinstance(spec, TransactionSpec)


def test_surfaces_are_canonicalized_sorted_by_path():
    spec = _spec(
        {"b": ABSENT, "a": ABSENT},
        {"b": F, "a": F},
        [
            CreateFileNoClobber(effect_id="e1", path="a", post=F),
            CreateFileNoClobber(effect_id="e2", path="b", post=F),
        ],
    )
    assert spec.initial_surface == (
        SurfaceEntry(path="a", state=ABSENT),
        SurfaceEntry(path="b", state=ABSENT),
    )
    assert [e.path for e in spec.final_surface] == ["a", "b"]


def test_build_is_order_independent_for_surface_inputs():
    a = _spec(
        {"a": ABSENT, "b": ABSENT},
        {"a": F, "b": F},
        [
            CreateFileNoClobber(effect_id="e1", path="a", post=F),
            CreateFileNoClobber(effect_id="e2", path="b", post=F),
        ],
    )
    b = _spec(
        {"b": ABSENT, "a": ABSENT},
        {"b": F, "a": F},
        [
            CreateFileNoClobber(effect_id="e1", path="a", post=F),
            CreateFileNoClobber(effect_id="e2", path="b", post=F),
        ],
    )
    assert a == b


def test_dependencies_are_sorted():
    spec = _spec(
        {"a": F, "b": F},
        {"a": F, "b": F},
        [
            ReplaceFile(effect_id="e1", path="a", pre=F, post=F),
            ReplaceFile(effect_id="e2", path="b", pre=F, post=F),
        ],
        deps=[("e2", "e1"), ("e1", "e2")],
    )
    assert spec.dependencies == (
        Dependency(before="e1", after="e2"),
        Dependency(before="e2", after="e1"),
    )


def test_required_capabilities_derives_from_effects():
    spec = _spec({"a": F}, {"a": F}, [ReplaceFile(effect_id="e1", path="a", pre=F, post=F)])
    caps = spec.required_capabilities()
    assert ALWAYS_REQUIRED <= caps
    assert Capability.ATOMIC_EXCHANGE in caps
