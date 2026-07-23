import json
from pathlib import Path

from atoms.core.canonical import canonical_bytes, canonical_json, canonical_obj
from atoms.core.effects import (
    CreateDirectory,
    CreateFileNoClobber,
    DeletePath,
    MoveNoClobber,
    ReplaceFile,
)
from atoms.core.fingerprint import ABSENT, DirectoryState, FileState, SymlinkState
from atoms.core.spec import Dependency, SurfaceEntry, TransactionSpec, build_spec

F = FileState(content_hash="sha256:" + "5" * 64, mode=0o644, byte_len=7)
G = DirectoryState(mode=0o755)
L = SymlinkState(target="café/target", mode=0o777)


def _spec_two_orderings():
    forward = build_spec(
        consumer_tag="c",
        intent_digest="sha256:" + "0" * 64,
        initial_surface={"a": ABSENT, "b": ABSENT},
        final_surface={"a": F, "b": F},
        effects=(
            CreateFileNoClobber(effect_id="e1", path="a", post=F),
            CreateFileNoClobber(effect_id="e2", path="b", post=F),
        ),
    )
    reverse = build_spec(
        consumer_tag="c",
        intent_digest="sha256:" + "0" * 64,
        initial_surface={"b": ABSENT, "a": ABSENT},
        final_surface={"b": F, "a": F},
        effects=(
            CreateFileNoClobber(effect_id="e1", path="a", post=F),
            CreateFileNoClobber(effect_id="e2", path="b", post=F),
        ),
    )
    return forward, reverse


def test_canonical_json_is_valid_and_stable():
    forward, reverse = _spec_two_orderings()
    assert canonical_json(forward) == canonical_json(reverse)
    parsed = json.loads(canonical_json(forward))
    assert parsed["schema_version"] == 1


def test_canonical_json_has_no_incidental_whitespace():
    forward, _ = _spec_two_orderings()
    s = canonical_json(forward)
    assert ", " not in s and ": " not in s


def test_bytes_are_utf8_of_json():
    forward, _ = _spec_two_orderings()
    assert canonical_bytes(forward) == canonical_json(forward).encode("utf-8")


def test_states_and_effects_carry_type_discriminators():
    spec = build_spec(
        consumer_tag="c",
        intent_digest="sha256:" + "0" * 64,
        initial_surface={"s": F, "d": ABSENT},
        final_surface={"s": ABSENT, "d": F},
        effects=(
            MoveNoClobber(
                effect_id="m",
                source="s",
                destination="d",
                source_pre=F,
            ),
        ),
    )
    obj = canonical_obj(spec)
    assert obj["effects"][0]["type"] == "MoveNoClobber"
    assert obj["initial_surface"][0]["state"]["type"] in {"absent", "file"}


def test_two_replace_specs_differing_only_in_hash_differ():
    g = FileState(content_hash="sha256:" + "6" * 64, mode=0o644, byte_len=7)
    a = build_spec(
        consumer_tag="c",
        intent_digest="sha256:" + "0" * 64,
        initial_surface={"x": F},
        final_surface={"x": g},
        effects=(ReplaceFile(effect_id="e", path="x", pre=F, post=g),),
    )
    b = build_spec(
        consumer_tag="c",
        intent_digest="sha256:" + "0" * 64,
        initial_surface={"x": F},
        final_surface={"x": F},
        effects=(ReplaceFile(effect_id="e", path="x", pre=F, post=F),),
    )
    assert canonical_json(a) != canonical_json(b)


def _all_variants_spec():
    """One spec exercising every effect variant, every path-state, dependencies,
    and non-ASCII data — the fixture the durable-format tests lock."""
    return build_spec(
        consumer_tag="cnsmr",
        intent_digest="sha256:" + "0" * 64,
        initial_surface={
            "café.txt": F,
            "new.txt": ABSENT,
            "del.txt": F,
            "lnk": L,
            "src": F,
            "dst": ABSENT,
            "dir": ABSENT,
        },
        final_surface={
            "café.txt": F,
            "new.txt": F,
            "del.txt": ABSENT,
            "lnk": ABSENT,
            "src": ABSENT,
            "dst": F,
            "dir": G,
        },
        effects=(
            ReplaceFile(effect_id="e1", path="café.txt", pre=F, post=F),
            CreateFileNoClobber(effect_id="e2", path="new.txt", post=F),
            DeletePath(effect_id="e3", path="del.txt", pre=F),
            DeletePath(effect_id="e4", path="lnk", pre=L),
            MoveNoClobber(
                effect_id="e5",
                source="src",
                destination="dst",
                source_pre=F,
            ),
            CreateDirectory(effect_id="e6", path="dir", post=G),
        ),
        dependencies=[("e2", "e5"), ("e1", "e3")],
    )


def test_every_effect_and_state_variant_encodes_with_exact_discriminators():
    obj = canonical_obj(_all_variants_spec())
    by_id = {e["effect_id"]: e for e in obj["effects"]}
    file_obj = {
        "type": "file",
        "content_hash": F.content_hash,
        "mode": 0o644,
        "byte_len": 7,
    }
    assert by_id["e1"] == {
        "type": "ReplaceFile",
        "effect_id": "e1",
        "path": "café.txt",
        "pre": file_obj,
        "post": file_obj,
    }
    assert by_id["e2"] == {
        "type": "CreateFileNoClobber",
        "effect_id": "e2",
        "path": "new.txt",
        "post": file_obj,
    }
    assert by_id["e3"] == {
        "type": "DeletePath",
        "effect_id": "e3",
        "path": "del.txt",
        "pre": file_obj,
    }
    assert by_id["e4"]["pre"] == {
        "type": "symlink",
        "target": "café/target",
        "mode": 0o777,
    }
    assert by_id["e5"] == {
        "type": "MoveNoClobber",
        "effect_id": "e5",
        "source": "src",
        "destination": "dst",
        "source_pre": file_obj,
    }
    assert by_id["e6"]["post"] == {"type": "directory", "mode": 0o755}
    assert "absent" in {
        e["state"]["type"] for e in obj["initial_surface"]
    }
    # dependencies emitted in sorted order regardless of input order
    assert obj["dependencies"] == [
        {"before": "e1", "after": "e3"},
        {"before": "e2", "after": "e5"},
    ]


def test_non_ascii_is_preserved_literally_not_escaped():
    s = canonical_json(_all_variants_spec())
    assert "café.txt" in s and "café/target" in s
    assert "\\u" not in s  # ensure_ascii=False keeps non-ASCII literal


def test_serializer_canonicalizes_a_directly_constructed_unsorted_spec():
    # F3: canonical bytes are a pure function of content even when a caller bypasses
    # build_spec and constructs TransactionSpec with unsorted set-like fields.
    ordered = _all_variants_spec()
    shuffled = TransactionSpec(
        schema_version=ordered.schema_version,
        consumer_tag=ordered.consumer_tag,
        intent_digest=ordered.intent_digest,
        initial_surface=tuple(reversed(ordered.initial_surface)),
        final_surface=tuple(reversed(ordered.final_surface)),
        effects=ordered.effects,  # order is semantic — left as-is
        dependencies=tuple(reversed(ordered.dependencies)),
    )
    assert canonical_bytes(shuffled) == canonical_bytes(ordered)
    assert isinstance(shuffled.initial_surface[0], SurfaceEntry)
    assert isinstance(shuffled.dependencies[0], Dependency)


def test_golden_bytes_lock_the_durable_contract():
    # Exact-byte fixture, generated once from the encoder and committed at
    # tests/fixtures/spec_all_variants.canonical.json, then locked here so any
    # tag or field-spelling drift fails. Regenerate intentionally only when the
    # durable format version changes.
    fixture = (
        Path(__file__).with_name("fixtures")
        / "spec_all_variants.canonical.json"
    )
    assert canonical_bytes(_all_variants_spec()) == fixture.read_bytes()
