"""Tier 1 — the schema's agreement with the enums it is generated from (design §11.1)."""

from __future__ import annotations

import pytest

from atoms.core.effects import (
    CreateDirectory,
    CreateFileNoClobber,
    DeletePath,
    Effect,
    MoveNoClobber,
    ReplaceFile,
)
from atoms.core.errors import ProtocolError
from atoms.core.recovery.model import (
    CommitDecision,
    JournalState,
    RollbackResult,
    TransactionState,
)
from atoms.core.recovery.plan import EffectVariant
from atoms.store.schema import (
    APPLICATION_ID,
    EFFECT_VARIANTS,
    EXPECTED_CATALOG,
    SCHEMA_STATEMENTS,
    SCHEMA_VERSION,
    check_list,
    variant_of,
)

EFFECT_MEMBERS = (
    ReplaceFile,
    CreateFileNoClobber,
    DeletePath,
    MoveNoClobber,
    CreateDirectory,
)


@pytest.mark.parametrize(
    ("members", "column"),
    [
        (TransactionState, "state"),
        (CommitDecision, "committed"),
        (RollbackResult, "rollback_result"),
        (JournalState, "journal_state"),
        (EffectVariant, "variant"),
    ],
)
def test_every_enum_member_appears_in_the_check_list_for_its_column(members, column):
    """The CHECK lists are generated, not retyped, so a new member cannot silently
    diverge from the value the engine writes (design §6.2)."""
    rendered = check_list(members)
    for member in members:
        assert f"'{member.value}'" in rendered
    joined = " ".join(SCHEMA_STATEMENTS)
    assert rendered in joined, f"{column} CHECK list is not the generated one"


def test_the_check_list_is_sorted_and_quoted_once():
    assert check_list(CommitDecision) == "'committed', 'uncommitted'"


def test_effect_variants_covers_every_member_of_the_effect_union():
    """Totality in one direction: every effect the engine can carry has a variant."""
    assert set(EFFECT_VARIANTS) == set(EFFECT_MEMBERS)


def test_effect_variants_covers_every_variant_exactly_once():
    """Totality in the other: a sixth EffectVariant breaks the build, not the store."""
    assert sorted(v.value for v in EFFECT_VARIANTS.values()) == sorted(
        v.value for v in EffectVariant
    )
    assert len(set(EFFECT_VARIANTS.values())) == len(EFFECT_VARIANTS)


def test_the_effect_union_has_exactly_the_five_members_the_mapping_names():
    """Guards the mapping's totality claim itself: if a sixth effect type lands, this
    fails before the mapping test does and says why."""
    assert set(Effect.__args__) == set(EFFECT_MEMBERS)


def test_variant_of_looks_up_by_exact_type_not_isinstance():
    class Sneaky(ReplaceFile):
        pass

    effect = Sneaky(
        effect_id="e1",
        path="a",
        pre=ReplaceFile.__dataclass_fields__["pre"].type,  # type: ignore[arg-type]
        post=ReplaceFile.__dataclass_fields__["post"].type,  # type: ignore[arg-type]
    )
    with pytest.raises(ProtocolError) as caught:
        variant_of(effect)
    assert "Sneaky" in str(caught.value)


def test_variant_of_never_derives_the_value_from_the_class_name():
    """variant_name() returns type(effect).__name__ -- 'ReplaceFile' -- while the column
    accepts EffectVariant's values -- 'replace_file'. The two spellings read as the same
    thing and are not (design §7.1)."""
    for effect_type, variant in EFFECT_VARIANTS.items():
        assert variant.value != effect_type.__name__


def test_the_ddl_is_a_tuple_of_single_statements():
    """No statement may be executed by executescript, so none may carry a second one
    (design §5.1 step 5)."""
    for statement in SCHEMA_STATEMENTS:
        body = statement.strip()
        assert body.endswith(";"), body[:40]
        assert body.count(";") == 1 or body.startswith("CREATE TRIGGER"), body[:40]


def test_the_expected_catalog_matches_the_ddl_object_names():
    names = {name for _kind, name, _tbl, _sql in EXPECTED_CATALOG}
    assert {"transaction_record", "effect", "blob", "active"} <= names
    assert "transaction_record_spec_json_is_write_once" in names


def test_the_expected_catalog_carries_implicit_autoindexes_with_no_sql():
    """SQLite lists an implicit autoindex per non-INTEGER PRIMARY KEY with sql=NULL.
    Set equality means an extra object fails as loudly as a missing one (design §5.2)."""
    autoindexes = {
        (kind, name, tbl, sql)
        for kind, name, tbl, sql in EXPECTED_CATALOG
        if name.startswith("sqlite_autoindex_")
    }
    assert autoindexes, "no autoindex rows in the expected catalog"
    assert all(sql is None for _kind, _name, _tbl, sql in autoindexes)


def test_the_stored_ddl_has_its_terminal_semicolon_stripped():
    """SQLite strips it, so the expected catalog must too or every reopen refuses."""
    for _kind, _name, _tbl, sql in EXPECTED_CATALOG:
        if sql is not None:
            assert not sql.rstrip().endswith(";")


def test_the_version_constants_are_what_the_store_writes():
    assert SCHEMA_VERSION == 3
    assert APPLICATION_ID == int.from_bytes(b"atms", "big")
