from pathlib import Path

import pytest

from atoms.core.canonical import (
    canonical_bytes,
    canonical_obj,
    from_canonical_bytes,
    from_canonical_json,
    from_canonical_obj,
)
from atoms.core.effects import ReplaceFile
from atoms.core.errors import SpecValidationError
from atoms.core.fingerprint import ABSENT, FileState
from atoms.core.spec import build_spec
from tests.test_canonical import _all_variants_spec

F = FileState(content_hash="sha256:" + "5" * 64, mode=0o644, byte_len=7)


class _AllowedNameStrSubclass(str):
    pass


class _AllowedNameEqualityMasquerader:
    def __init__(self, value: str) -> None:
        self.value = value

    def __hash__(self) -> int:
        return hash(self.value)

    def __eq__(self, other: object) -> bool:
        return other == self.value


class _ExplodingEqualityKey:
    def __hash__(self) -> int:
        return hash("type")

    def __eq__(self, other: object) -> bool:
        raise AssertionError(f"unexpected equality comparison with {other!r}")


def _minimal_obj(**overrides: object) -> dict[object, object]:
    obj: dict[object, object] = {
        "schema_version": 1,
        "consumer_tag": "c",
        "intent_digest": "sha256:" + "0" * 64,
        "initial_surface": [],
        "final_surface": [],
        "effects": [],
        "dependencies": [],
    }
    obj.update(overrides)
    return obj


def test_round_trip_of_every_variant_is_identity():
    spec = _all_variants_spec()
    assert from_canonical_bytes(canonical_bytes(spec)) == spec


def test_round_trip_through_the_committed_golden_fixture():
    fixture = Path(__file__).with_name("fixtures") / "spec_all_variants.canonical.json"
    assert from_canonical_bytes(fixture.read_bytes()) == _all_variants_spec()


def test_reconstruction_preserves_every_stored_sequence_order():
    obj = canonical_obj(_all_variants_spec())
    for field in (
        "initial_surface",
        "final_surface",
        "effects",
        "dependencies",
    ):
        obj[field].reverse()

    decoded = from_canonical_obj(obj)

    assert [entry.path for entry in decoded.initial_surface] == [
        entry["path"] for entry in obj["initial_surface"]
    ]
    assert [entry.path for entry in decoded.final_surface] == [
        entry["path"] for entry in obj["final_surface"]
    ]
    assert [effect.effect_id for effect in decoded.effects] == [
        effect["effect_id"] for effect in obj["effects"]
    ]
    assert [
        (dependency.before, dependency.after)
        for dependency in decoded.dependencies
    ] == [
        (dependency["before"], dependency["after"])
        for dependency in obj["dependencies"]
    ]


def test_unknown_schema_version_is_rejected():
    spec = build_spec(
        consumer_tag="c",
        intent_digest="sha256:" + "0" * 64,
        initial_surface={"a": F},
        final_surface={"a": F},
        effects=(),
    )
    obj = canonical_obj(spec)
    obj["schema_version"] = 999
    with pytest.raises(SpecValidationError, match="schema_version"):
        from_canonical_obj(obj)


def test_unknown_effect_discriminator_is_rejected():
    obj = canonical_obj(
        build_spec(
            consumer_tag="c",
            intent_digest="sha256:" + "0" * 64,
            initial_surface={"a": ABSENT},
            final_surface={"a": F},
            effects=(),
        )
    )
    obj["effects"] = [{"type": "Frobnicate", "effect_id": "e", "path": "a"}]
    with pytest.raises(SpecValidationError, match="Frobnicate"):
        from_canonical_obj(obj)


def test_missing_state_field_is_rejected():
    with pytest.raises(SpecValidationError, match="content_hash"):
        from_canonical_obj(
            _minimal_obj(
                initial_surface=[
                    {
                        "path": "a",
                        "state": {"type": "file", "mode": 420, "byte_len": 1},
                    }
                ],
            )
        )


def test_extra_field_is_rejected():
    with pytest.raises(SpecValidationError, match="unexpected"):
        from_canonical_obj(_minimal_obj(surprise=True))


def test_duplicate_json_key_is_rejected():
    dup = (
        '{"schema_version":1,"schema_version":1,"consumer_tag":"c","intent_digest":"x",'
        '"initial_surface":[],"final_surface":[],"effects":[],"dependencies":[]}'
    )
    with pytest.raises(SpecValidationError, match="duplicate"):
        from_canonical_json(dup)


def test_non_object_top_level_is_rejected():
    with pytest.raises(SpecValidationError, match="spec must be an object"):
        from_canonical_obj([1, 2, 3])


@pytest.mark.parametrize("value", [True, 1.0])
def test_schema_version_requires_an_exact_integer(value):
    with pytest.raises(SpecValidationError, match="schema_version must be an integer"):
        from_canonical_obj(_minimal_obj(schema_version=value))


def test_string_field_with_wrong_type_is_rejected():
    with pytest.raises(SpecValidationError, match="consumer_tag must be a string"):
        from_canonical_obj(_minimal_obj(consumer_tag=5))


def test_effects_not_a_list_is_rejected():
    with pytest.raises(SpecValidationError, match="effects must be an array"):
        from_canonical_obj(_minimal_obj(effects="nope"))


def test_surface_entry_not_an_object_is_rejected():
    with pytest.raises(SpecValidationError, match=r"initial_surface\[0\] must be an object"):
        from_canonical_obj(_minimal_obj(initial_surface=["a"]))


def test_missing_surface_path_is_rejected():
    with pytest.raises(SpecValidationError, match="path"):
        from_canonical_obj(
            _minimal_obj(
                initial_surface=[{"state": {"type": "absent"}}],
            )
        )


def test_mode_bool_is_rejected():
    with pytest.raises(SpecValidationError, match="mode must be an integer"):
        from_canonical_obj(
            _minimal_obj(
                initial_surface=[
                    {
                        "path": "a",
                        "state": {"type": "directory", "mode": True},
                    }
                ],
            )
        )


def test_effect_field_rejects_incompatible_state_kind():
    obj = canonical_obj(
        build_spec(
            consumer_tag="c",
            intent_digest="sha256:" + "0" * 64,
            initial_surface={"a": F},
            final_surface={"a": F},
            effects=(ReplaceFile(effect_id="e", path="a", pre=F, post=F),),
        )
    )
    obj["effects"][0]["pre"] = {"type": "absent"}
    with pytest.raises(SpecValidationError, match="pre"):
        from_canonical_obj(obj)


def test_malformed_json_is_rejected():
    with pytest.raises(SpecValidationError, match="malformed canonical JSON"):
        from_canonical_json("{not json")


def test_malformed_utf8_is_rejected():
    with pytest.raises(SpecValidationError, match="not valid UTF-8"):
        from_canonical_bytes(b"\xff\xfe")


@pytest.mark.parametrize(
    ("field", "discriminator"),
    [
        ("initial_surface", []),
        ("initial_surface", {}),
    ],
)
def test_unhashable_state_discriminator_raises_only_spec_validation_error(
    field, discriminator
):
    obj = _minimal_obj(
        **{
            field: [
                {
                    "path": "a",
                    "state": {"type": discriminator},
                }
            ]
        }
    )
    with pytest.raises(SpecValidationError, match="discriminator"):
        from_canonical_obj(obj)


@pytest.mark.parametrize("discriminator", [[], {}])
def test_unhashable_effect_discriminator_raises_only_spec_validation_error(
    discriminator,
):
    obj = _minimal_obj(effects=[{"type": discriminator}])
    with pytest.raises(SpecValidationError, match="discriminator"):
        from_canonical_obj(obj)


def test_unexpected_mixed_type_object_keys_raise_only_spec_validation_error():
    obj = _minimal_obj()
    obj[1] = "integer key"
    obj[None] = "none key"
    with pytest.raises(SpecValidationError, match="unexpected"):
        from_canonical_obj(obj)


@pytest.mark.parametrize(
    "key",
    [
        _AllowedNameStrSubclass("schema_version"),
        _AllowedNameEqualityMasquerader("schema_version"),
    ],
)
def test_allowed_name_non_builtin_string_keys_are_rejected(key):
    obj = _minimal_obj()
    obj[key] = obj.pop("schema_version")

    with pytest.raises(SpecValidationError, match="object keys"):
        from_canonical_obj(obj)


def test_nested_non_string_key_is_rejected_before_discriminator_membership():
    obj = _minimal_obj(
        initial_surface=[
            {
                "path": "a",
                "state": {_ExplodingEqualityKey(): "absent"},
            }
        ]
    )

    with pytest.raises(SpecValidationError, match="object keys"):
        from_canonical_obj(obj)


@pytest.mark.parametrize(
    "malformed",
    [
        _minimal_obj(initial_surface=()),
        _minimal_obj(initial_surface=[{"path": "a", "state": None}]),
        _minimal_obj(dependencies=[{"before": [], "after": "e"}]),
        _minimal_obj(effects=[None]),
    ],
)
def test_representative_malformed_values_raise_only_spec_validation_error(malformed):
    with pytest.raises(SpecValidationError):
        from_canonical_obj(malformed)


def test_non_string_json_input_raises_only_spec_validation_error():
    with pytest.raises(SpecValidationError):
        from_canonical_json(b"{}")  # type: ignore[arg-type]


def test_non_bytes_input_raises_only_spec_validation_error():
    with pytest.raises(SpecValidationError):
        from_canonical_bytes(bytearray(b"{}"))  # type: ignore[arg-type]
