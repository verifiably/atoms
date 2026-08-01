# A5a Durable Metadata Store Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `atoms/store/`, the SQLite-WAL durable metadata store as a *mechanism* — creation and
reopen under a verified `metadata_root`, the pinned connection profile, the schema, typed record
read/write, content-addressed blob promotion, and per-txid workspaces — with no lease, no recovery
judgment, and no transition legality.

**Architecture:** Five modules under a new `atoms/store/` package. `schema.py` is pure: DDL statements,
version constants, enum-derived `CHECK` lists, the effect-variant mapping, and the expected catalog.
`connection.py` owns creation, reopen, the pinned profile, the authorizer, the liveness gate, `Store`,
and `_StoreTransaction`. `records.py` owns typed row read/write, the cross-row coherence predicate used
by both the loader and the pre-COMMIT barrier, and the private halt-diagnostic codec. `blobs.py` owns
the digest-to-leaf mapping, blob reading, promotion, and unindexed-blob reclamation. `workspace.py`
owns caller-name validation and the `Workspace` resource.

**Tech Stack:** Python 3.11+, stdlib only (`sqlite3`, `os`, `hashlib`, `json`, `dataclasses`), `pytest`,
`ruff`, `pyright`. Builds on A4a's `ProjectBinding` and `Backend`; A1's `TransactionSpec`,
`canonical_json`, and `is_valid_identifier`; A2's `compile_spec`; A3's `TransactionState`,
`CommitDecision`, `JournalState`, `RollbackResult`, `EffectJournalState`, and `HaltDiagnostic`.

**Design:** [`2026-07-31-a5a-metadata-store-design.md`](2026-07-31-a5a-metadata-store-design.md).
**Authority:** [`2026-07-23-recoverable-fs-effect-engine-design.md`](2026-07-23-recoverable-fs-effect-engine-design.md).
Where this plan and either document disagree, the design wins over this plan and the authority wins
over both.

## Global Constraints

- Work from `~/d/atoms/python`. Gates are `uv run pytest`, `uv run ruff check`, `uv run pyright`.
- **Ruff's default rule set is much broader than `E4/E7/E9/F`** — isort (`I001`), bugbear, flake8-simplify,
  bandit (`S`), `RUF012`, blind-except (`BLE001`). The code below is written to pass it as given. Run
  `uv run ruff check <file>` right after creating each file rather than only at the task gate.
- **One expected transient:** ruff's isort classifies a module as first-party by *path existence*, so a
  test importing `atoms.store.*` before that module exists reports `I001`. Between "write the failing
  test" and "write the module" this is expected; **do not reorder imports to satisfy it.**
- **pyright type-checks the tests** — `[tool.pyright]` sets no `include`.
- **Every fixture lands in `tests/conftest.py`.** Task 13 extends the existing fixture-registry guard to
  `test_store_*.py`; until then, still put fixtures there.
- **`atoms.fs` and `atoms.core` never import `atoms.store`** (design §4.2). The dependency runs
  `store -> fs -> core`.
- **No raw `os.fsync` anywhere in `atoms/store/`.** Every durability barrier goes through
  `Backend.flush_file` or `Backend.flush_directory` (design §5.1 step 3, §8.1).
- **No blanket `OSError` handler**, extending the existing guard to the new package.
- **`executescript` is never called in the package** (design §5.1 step 5). It issues a `COMMIT` before
  running, which under `isolation_level=None` ends the explicit transaction initialization depends on.
  Task 13 proves this over *parsed calls*, not over source text, so the docstrings that explain the ban
  are allowed to name it.
- **Every SQL string is a module-level constant.** The first argument of every `execute`/`executemany`
  call must be a bare `Name` that resolves to a module-level `str` assignment — in that module or
  imported from another `atoms.store` module — with one exception, the loop variable of the `for` over
  `SCHEMA_STATEMENTS` (design §11.6, criterion 37). Attribute access is not a resolution and is refused.
- **Exactly one statement in the package writes `blob`**: promotion's step 6 `INSERT`.
- **SQLite >= 3.37** required; a lower version and a `TEMP_STORE=0` build both raise
  `CapabilityUnavailable`.
- **Every operation gates on `binding.backend` before each barrier**, including before each mutating
  syscall. The only exemptions are `ROLLBACK`, `Store.close`, and `Workspace.close` (design §5.4).
- Filepaths in docs and comments use `~/d/atoms/...`.
- Conventional commits. No AI-attribution trailer or footer.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `src/atoms/store/__init__.py` | **Create.** Re-exports `__all__` exactly: `Store`, `StoredRecord`, `StagedBlob`, `Workspace`, `open_store`. |
| `src/atoms/store/py.typed` | **Create.** Empty marker, matching `atoms/core/` and `atoms/fs/`. |
| `src/atoms/store/schema.py` | **Create.** `SCHEMA_VERSION`, `APPLICATION_ID`, enum-derived `CHECK` lists, `SCHEMA_STATEMENTS`, `EXPECTED_CATALOG`, `EFFECT_VARIANTS`. Pure — no `sqlite3`, no I/O. |
| `src/atoms/store/errors.py` | **Create.** `MetadataStoreInvalid`, `SQLITE_CORRUPT`, `SQLITE_NOTADB`, and `translated(...)`, the one narrow result-code translation shape of design §9.1. |
| `src/atoms/store/connection.py` | **Create.** Creation (§5.1), reopen (§5.2), the pinned profile (§5.3), the authorizer, the liveness gate (§5.4), `open_store`, `Store`, `_StoreTransaction` (§7.1, §7.7, §7.8). |
| `src/atoms/store/records.py` | **Create.** `StoredRecord`, typed writes, the journal vector (§7.5), the coherence predicate (§7.6), the pre-COMMIT barrier (§7.7), reads (§7.4), and the private halt-diagnostic codec (§6.4). |
| `src/atoms/store/workspace.py` | **Create.** §5.5 name validation, the `Workspace` resource, create/reopen/list/remove (§8.3), and §8.5's refusals for `staging/` and `work/`. |
| `src/atoms/store/blobs.py` | **Create.** `digest_to_leaf`/`leaf_to_digest`, `StagedBlob`, `open_blob` (§7.2), promotion (§8.1, §8.2, §8.4), unindexed reclamation (§7.3), and §8.5's refusals for `blobs/sha256/`. |
| `tests/store_support.py` | **Create.** Spec, diagnostic, and manifest builders shared by every tier. |
| `tests/conftest.py` | **Modify.** `store_on`, `opened_store`, `staged_workspace`. |
| `tests/test_store_schema.py` | **Create.** Tier 1 — schema/enum agreement, `EFFECT_VARIANTS` totality, digest mapping. |
| `tests/test_store_open.py` | **Create.** Tier 3 — creation, reopen, crash cuts, the pinned profile. |
| `tests/test_store_records.py` | **Create.** Tier 2 — typed writes, reads, the predicate, the barrier, the codec. |
| `tests/test_store_workspace.py` | **Create.** Tier 4 — workspaces and their refusals. |
| `tests/test_store_blobs.py` | **Create.** Tier 4 — promotion, blob reading, reclamation. |
| `tests/test_store_liveness.py` | **Create.** Tier 4 — the gate inventory, one case per site. |
| `tests/test_store_process.py` | **Create.** Tier 5 — fresh-process durability. |
| `tests/store_child.py` | **Create.** The subprocess tier 5 reads from: binds the volume itself and prints what a fresh process sees. |
| `tests/test_store_architecture.py` | **Create.** Tier 6 — import direction, surface, SQL inventory, guards. |
| `docs/deferred-obligation-ledger.md` | **Modify.** Ledger #22 discharge record. |
| `AGENTS.md` | **Modify.** A5 status line. |

**Why `errors.py` is separate from `connection.py`.** `MetadataStoreInvalid` and the translation shape
are used by all four other modules; putting them in `connection.py` would make `blobs.py` and
`records.py` import the module that imports *them*. The dependency runs
`connection -> {records, blobs, workspace} -> {schema, errors}` with no cycle.

**Why the halt-diagnostic codec is inside `records.py`.** Design §4.1: "starts as private helpers in
`records.py` and is not exported... It moves to `store/diagnostic.py` only if `records.py` becomes
unwieldy." Do not pre-split it.

Thirteen tasks. Each ends with a deliverable a reviewer could reject while approving its neighbour.

**Tasks 1 and 2 are pure and come first**, because every later task imports their constants and the
translation shape. Tasks 3 and 4 open the store; Task 5 puts the `Store` object and its transaction
around them; Tasks 6-8 fill in records; Tasks 9-12 fill in workspaces and blobs; Task 13 is the
whole-package guard and the status synchronization.

---

## Task 1: The schema module

**Files:**
- Create: `src/atoms/store/__init__.py`
- Create: `src/atoms/store/py.typed`
- Create: `src/atoms/store/schema.py`
- Create: `tests/test_store_schema.py`

**Interfaces:**
- Consumes: `TransactionState`, `CommitDecision`, `JournalState`, `RollbackResult` from
  `atoms.core.recovery.model`; `EffectVariant` from `atoms.core.recovery.plan`; the `Effect` union and
  its five members from `atoms.core.effects`; `ProtocolError` from `atoms.core.errors`.
- Produces: `SCHEMA_VERSION: int`, `APPLICATION_ID: int`, `SCHEMA_STATEMENTS: tuple[str, ...]`,
  `EXPECTED_CATALOG: frozenset[tuple[str, str, str, str | None]]`,
  `EFFECT_VARIANTS: Mapping[type[Effect], EffectVariant]`,
  `variant_of(effect: Effect) -> EffectVariant`, `check_list(members) -> str`.

**Why the DDL is a tuple, not one script.** Design §5.1 step 5: `executescript` issues a `COMMIT`
before it runs, so under the pinned `isolation_level=None` it would end the transaction that makes
initialization atomic — measured, `in_transaction` goes from `True` to `False` across the call while
the `application_id` written before it stays durable. The statements are executed one at a time inside
one explicit transaction, and the per-statement inventory is also what Task 13's blob-writer rule is
stated over.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_store_schema.py`:

```python
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
    assert SCHEMA_VERSION == 1
    assert APPLICATION_ID == int.from_bytes(b"atms", "big")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_store_schema.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'atoms.store'`.

- [ ] **Step 3: Create the package**

```bash
mkdir -p src/atoms/store
touch src/atoms/store/py.typed
```

Create `src/atoms/store/__init__.py`:

```python
"""A5a — the durable metadata store (design §4.1).

The public surface is exactly the five names below. `_StoreTransaction` is obtained only
by entering `Store.transaction()` and is deliberately absent.
"""

from __future__ import annotations

from atoms.store.blobs import StagedBlob
from atoms.store.connection import Store, open_store
from atoms.store.records import StoredRecord
from atoms.store.workspace import Workspace

__all__ = ("StagedBlob", "Store", "StoredRecord", "Workspace", "open_store")
```

This import block will not resolve until Task 12. Write it now anyway — it is the surface
Task 13 asserts against, and every intermediate task's gate runs `uv run pytest`, which
imports `atoms.store.schema` directly and never touches `__init__`.

**If the intermediate gates do fail on the unresolved import**, replace the body with
`__all__ = ()` and a `# filled in by Task 12` comment for the duration, and restore it in
Task 12 Step 3. Do not leave the comment behind.

- [ ] **Step 4: Write the schema module**

Create `src/atoms/store/schema.py`:

```python
"""The durable schema and the facts generated from A1/A3's enums (design §6).

Pure: no sqlite3 import, no connection, no I/O. Everything here is either a constant or a
function of an enum, so a new enum member changes the DDL rather than diverging from it.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import TypeVar

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

SCHEMA_VERSION = 1
APPLICATION_ID = int.from_bytes(b"atms", "big")

_EnumT = TypeVar("_EnumT", bound=Enum)


def check_list(members: type[_EnumT]) -> str:
    """Render an enum as the value list of a CHECK constraint.

    Sorted so the rendered DDL is a function of the member set alone: a reordering of the
    enum's declaration must not change the stored schema text, because the catalog
    comparison at reopen is exact.
    """
    return ", ".join(f"'{member.value}'" for member in sorted(members, key=lambda m: m.value))


SCHEMA_STATEMENTS: tuple[str, ...] = (
    f"""CREATE TABLE transaction_record (
    txid            TEXT PRIMARY KEY,
    spec_json       TEXT NOT NULL,
    state           TEXT NOT NULL CHECK (state IN ({check_list(TransactionState)})),
    committed       TEXT NOT NULL CHECK (committed IN ({check_list(CommitDecision)})),
    rollback_result TEXT          CHECK (rollback_result IN ({check_list(RollbackResult)})),
    halt_diagnostic TEXT
) STRICT;""",
    f"""CREATE TABLE effect (
    txid          TEXT NOT NULL REFERENCES transaction_record(txid),
    effect_id     TEXT NOT NULL,
    variant       TEXT NOT NULL CHECK (variant IN ({check_list(EffectVariant)})),
    journal_state TEXT NOT NULL CHECK (journal_state IN ({check_list(JournalState)})),
    PRIMARY KEY (txid, effect_id)
) STRICT;""",
    """CREATE TABLE blob (
    digest   TEXT PRIMARY KEY,
    byte_len INTEGER NOT NULL CHECK (byte_len >= 0)
) STRICT;""",
    """CREATE TABLE active (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 0),
    txid      TEXT NOT NULL REFERENCES transaction_record(txid)
) STRICT;""",
    """CREATE TRIGGER transaction_record_spec_json_is_write_once
BEFORE UPDATE OF spec_json ON transaction_record
BEGIN
    SELECT RAISE(ABORT, 'spec_json is write-once');
END;""",
)


def _catalog_row(statement: str) -> tuple[str, str, str, str]:
    """Derive one (type, name, tbl_name, sql) row from a DDL statement.

    `sql` is the statement with its terminal semicolon stripped, because that is what
    SQLite stores -- measured. Deriving the expected catalog from the same text the store
    was created from is what makes the comparison a schema check rather than a
    transcription check.
    """
    body = statement.strip().rstrip(";")
    head = body.split("(", 1)[0].split()
    kind = head[1].lower()
    if kind == "trigger":
        name = head[2]
        table = body.split(" ON ", 1)[1].split()[0]
        return ("trigger", name, table, body)
    name = head[2]
    return ("table", name, name, body)


EXPECTED_CATALOG: frozenset[tuple[str, str, str, str | None]] = frozenset(
    [_catalog_row(statement) for statement in SCHEMA_STATEMENTS]
    + [
        ("index", "sqlite_autoindex_transaction_record_1", "transaction_record", None),
        ("index", "sqlite_autoindex_effect_1", "effect", None),
        ("index", "sqlite_autoindex_blob_1", "blob", None),
    ]
)

EFFECT_VARIANTS: Mapping[type[Effect], EffectVariant] = {
    ReplaceFile: EffectVariant.REPLACE_FILE,
    CreateFileNoClobber: EffectVariant.CREATE_FILE_NO_CLOBBER,
    DeletePath: EffectVariant.DELETE_PATH,
    MoveNoClobber: EffectVariant.MOVE_NO_CLOBBER,
    CreateDirectory: EffectVariant.CREATE_DIRECTORY,
}


def variant_of(effect: Effect) -> EffectVariant:
    """The `effect.variant` column value for one effect.

    Lookup is by `type(effect)`, exact -- not isinstance, which would accept a subclass
    and record it as its base. `variant_name` is deliberately unused: it returns
    `type(effect).__name__` ('ReplaceFile') while the column accepts EffectVariant's
    values ('replace_file'), and deriving one from the other by case transformation would
    make the stored value depend on a class name with no reason to keep matching.
    """
    variant = EFFECT_VARIANTS.get(type(effect))
    if variant is None:
        raise ProtocolError(
            f"{type(effect).__name__} has no EffectVariant; add it to EFFECT_VARIANTS "
            "in atoms/store/schema.py beside the CHECK list it must agree with"
        )
    return variant
```

`active` has an `INTEGER PRIMARY KEY`, which is a rowid alias and gets **no** autoindex —
that is why only three autoindex rows are listed.

- [ ] **Step 5: Run the tests and the gates**

```bash
uv run pytest tests/test_store_schema.py -v
uv run ruff check src/atoms/store tests/test_store_schema.py
uv run pyright
```
Expected: PASS, `All checks passed!`, `0 errors`.

If `test_the_expected_catalog_carries_implicit_autoindexes_with_no_sql` fails at reopen in
Task 4 rather than here, the autoindex names are wrong for this SQLite build; get them from
`SELECT type, name, tbl_name, sql FROM sqlite_schema` on a freshly created store and correct
the literals rather than loosening the comparison.

- [ ] **Step 6: Commit**

```bash
git add src/atoms/store tests/test_store_schema.py
git commit -m "feat(store): generate the durable schema from the recovery enums"
```

---

## Task 2: The SQLite error contract

**Files:**
- Create: `src/atoms/store/errors.py`
- Create: `tests/test_store_errors.py`

**Interfaces:**
- Consumes: `AtomsError` from `atoms.core.errors`.
- Produces: `MetadataStoreInvalid`, `SQLITE_CORRUPT: int`, `SQLITE_NOTADB: int`,
  `translated(context: str)` — a context manager implementing design §9.1's shape.

**Why a context manager rather than a decorator.** §9.1 requires the `except` to enclose **one narrow
operation, not a whole protocol**, so the guard has to be placeable around a single `execute` inside a
function that does many. A decorator can only wrap a whole function, which is exactly the scope the
design forbids.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_store_errors.py`:

```python
"""Design §9.1 -- narrow, result-code translation with a bare default re-raise."""

from __future__ import annotations

import sqlite3

import pytest

from atoms.core.errors import AtomsError
from atoms.store.errors import (
    SQLITE_CORRUPT,
    SQLITE_NOTADB,
    MetadataStoreInvalid,
    translated,
)


def test_metadata_store_invalid_is_an_atoms_error():
    assert issubclass(MetadataStoreInvalid, AtomsError)


def test_a_garbage_file_translates_to_metadata_store_invalid(tmp_path):
    """Reading a non-database raises exactly sqlite3.DatabaseError with SQLITE_NOTADB --
    not OperationalError, so no narrower except clause can reach it."""
    path = tmp_path / "garbage.db"
    path.write_bytes(b"this is not a database" * 64)
    connection = sqlite3.connect(path)
    with pytest.raises(MetadataStoreInvalid) as caught:
        with translated("reading the schema"):
            connection.execute("SELECT count(*) FROM sqlite_schema").fetchone()
    assert isinstance(caught.value.__cause__, sqlite3.DatabaseError)
    assert "reading the schema" in str(caught.value)


def test_an_operational_error_propagates_unchanged(tmp_path):
    connection = sqlite3.connect(tmp_path / "ok.db")
    with pytest.raises(sqlite3.OperationalError) as caught:
        with translated("selecting"):
            connection.execute("SELECT * FROM nope")
    assert not isinstance(caught.value, MetadataStoreInvalid)


def test_a_programming_error_carrying_no_result_code_propagates(tmp_path):
    """sqlite3.ProgrammingError is a DatabaseError with no sqlite_errorcode attribute at
    all. Reading the attribute directly would raise AttributeError from inside the
    handler, replacing a clear message with a failure in the error path."""
    connection = sqlite3.connect(tmp_path / "closed.db")
    connection.close()
    with pytest.raises(sqlite3.ProgrammingError) as caught:
        with translated("using a closed connection"):
            connection.execute("SELECT 1")
    assert not hasattr(caught.value, "sqlite_errorcode")


def test_an_extended_result_code_still_matches_its_primary(tmp_path):
    """Discrimination is on `code & 0xFF`, so SQLITE_CORRUPT_VTAB and friends match --
    the idiom A4a already uses for SQLITE_BUSY at probe.py:367."""
    extended = SQLITE_CORRUPT | (1 << 8)
    error = sqlite3.DatabaseError("synthetic")
    error.sqlite_errorcode = extended  # type: ignore[attr-defined]
    with pytest.raises(MetadataStoreInvalid):
        with translated("synthetic"):
            raise error


def test_an_unknown_result_code_keeps_its_class_and_traceback():
    error = sqlite3.DatabaseError("unknown")
    error.sqlite_errorcode = 0x7F  # type: ignore[attr-defined]
    with pytest.raises(sqlite3.DatabaseError) as caught:
        with translated("synthetic"):
            raise error
    assert caught.value is error


def test_the_translated_codes_are_the_two_the_design_names():
    assert (SQLITE_CORRUPT, SQLITE_NOTADB) == (11, 26)


def test_a_non_sqlite_exception_is_untouched():
    with pytest.raises(ValueError):
        with translated("synthetic"):
            raise ValueError("not sqlite's problem")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_store_errors.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'atoms.store.errors'`.

- [ ] **Step 3: Write the module**

Create `src/atoms/store/errors.py`:

```python
"""The one refusal A5a adds, and the narrow translation that produces it (design §9)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from atoms.core.errors import AtomsError

SQLITE_CORRUPT = 11
SQLITE_NOTADB = 26


class MetadataStoreInvalid(AtomsError):
    """The durable metadata store cannot be safely interpreted.

    Corruption and forward incompatibility share one type because they share one correct
    response -- stop and preserve evidence -- and the message distinguishes them. Raised
    by the store layer, never as a substitute for ProtocolError, which tells a caller to
    fix its call (authority §11).
    """


@contextmanager
def translated(context: str) -> Iterator[None]:
    """Translate SQLITE_CORRUPT and SQLITE_NOTADB from ONE narrow operation.

    Wrap a single statement, never a protocol: the point of the narrow scope is that the
    result codes the handler can plausibly see are bounded. The default is a bare `raise`,
    so an unrecognized code keeps its own class *and* its traceback and a future SQLite
    code is propagated rather than guessed at.

    The catch has to be sqlite3.DatabaseError, because that is the class SQLite raises for
    the condition being translated -- a garbage file raises exactly DatabaseError with
    sqlite_errorname SQLITE_NOTADB, measured, so no narrower clause reaches it.
    """
    try:
        yield
    except sqlite3.DatabaseError as caught:
        code = getattr(caught, "sqlite_errorcode", None)
        if code is not None and (code & 0xFF) in (SQLITE_CORRUPT, SQLITE_NOTADB):
            raise MetadataStoreInvalid(
                f"the metadata store cannot be interpreted while {context}: {caught}"
            ) from caught
        raise
```

`getattr` is not defensive padding: `sqlite3.ProgrammingError` is a `DatabaseError` carrying
**no** `sqlite_errorcode` attribute, so a direct read would raise `AttributeError` from inside
the handler.

- [ ] **Step 4: Run the tests and the gates**

```bash
uv run pytest tests/test_store_errors.py -v
uv run ruff check src/atoms/store/errors.py tests/test_store_errors.py
uv run pyright
```
Expected: PASS, `All checks passed!`, `0 errors`.

`ruff` may flag `TRY301`/`BLE001` style issues if the handler is rewritten; the form above —
one `except` on a concrete class, a conditional `raise ... from`, and a bare `raise` — passes
as written.

- [ ] **Step 5: Commit**

```bash
git add src/atoms/store/errors.py tests/test_store_errors.py
git commit -m "feat(store): translate corruption by result code, never by exception class"
```

---

## Task 3: Creation

**Files:**
- Create: `src/atoms/store/connection.py`
- Modify: `tests/conftest.py`
- Create: `tests/store_support.py`
- Create: `tests/test_store_open.py`

**Interfaces:**
- Consumes: `ProjectBinding` (`binding.backend`, `binding.metadata_root_fd`,
  `binding.verified_metadata_path(name)`), `Backend.flush_file`, `Backend.flush_directory`,
  `CapabilityUnavailable`, `ProtocolError`, and Task 1's `SCHEMA_STATEMENTS`, `SCHEMA_VERSION`,
  `APPLICATION_ID`; Task 2's `translated`, `MetadataStoreInvalid`.
- Produces: `DATABASE_NAME`, `SIDECAR_NAMES`, `MINIMUM_SQLITE`, `require_platform(binding)`,
  `gate(binding)`, `create_store(binding) -> sqlite3.Connection`, `apply_persistent_profile`,
  `apply_connection_profile`, `install_authorizer`, `publish_entry(binding, fd)`.

**The five creation steps** (design §5.1), each of which Task 4 must be able to resume from:

1. Preflight **all four** entries — `atoms.db`, `-wal`, `-shm`, `-journal` — every one absent.
2. Gate, then `openat(O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC | O_RDWR, 0o600)`.
3. Gate, `fchmod` to the exact mode, `flush_file`, `flush_directory` on `metadata_root_fd`, close.
4. Open through `verified_metadata_path`, gate, apply the pinned profile including the WAL transition.
5. One explicit transaction: every `SCHEMA_STATEMENTS` entry, `user_version`, `application_id`; gate;
   COMMIT.

- [ ] **Step 1: Write the shared support module**

Create `tests/store_support.py`:

```python
"""Builders shared by every store tier."""

from __future__ import annotations

import hashlib
import os
import sqlite3

from atoms.core.effects import CreateFileNoClobber, ReplaceFile
from atoms.core.fingerprint import ABSENT, FileState
from atoms.core.spec import build_spec

DATABASE_ENTRIES = ("atoms.db", "atoms.db-wal", "atoms.db-shm", "atoms.db-journal")
SHARED_DIGEST = "sha256:" + "a" * 64


def file_state(content: bytes, mode: int = 0o644) -> FileState:
    return FileState(
        content_hash="sha256:" + hashlib.sha256(content).hexdigest(),
        mode=mode,
        byte_len=len(content),
    )


def digest_of(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def one_effect_spec(effect_id: str = "e1", content: bytes = b"after"):
    """A minimal spec that compile_spec accepts: one CreateFileNoClobber."""
    post = file_state(content)
    return build_spec(
        consumer_tag="test",
        intent_digest="sha256:" + "0" * 64,
        initial_surface={"a.txt": ABSENT},
        final_surface={"a.txt": post},
        effects=[CreateFileNoClobber(effect_id=effect_id, path="a.txt", post=post)],
    )


def replace_spec(effect_id: str = "e1", before: bytes = b"before", after: bytes = b"after"):
    pre, post = file_state(before), file_state(after)
    return build_spec(
        consumer_tag="test",
        intent_digest="sha256:" + "1" * 64,
        initial_surface={"a.txt": pre},
        final_surface={"a.txt": post},
        effects=[ReplaceFile(effect_id=effect_id, path="a.txt", pre=pre, post=post)],
    )


def duplicate_effect_spec():
    """Two effects sharing one effect_id, for design §7.7's poison rule.

    build_spec does not validate and insert_record does not compile, so the `effect`
    PRIMARY KEY is what refuses -- on the *second* INSERT, after the record row and the
    first effect row of the same method have already been written. That is the shape the
    poison rule exists for.
    """
    post = file_state(b"after")
    return build_spec(
        consumer_tag="test",
        intent_digest="sha256:" + "2" * 64,
        initial_surface={"a.txt": ABSENT, "b.txt": ABSENT},
        final_surface={"a.txt": post, "b.txt": post},
        effects=[
            CreateFileNoClobber(effect_id="dup", path="a.txt", post=post),
            CreateFileNoClobber(effect_id="dup", path="b.txt", post=post),
        ],
    )


def two_length_spec():
    """One digest declared at two byte_lens across the initial surface.

    compile_spec accepts this -- measured -- because it never compares two paths'
    fingerprints to each other. A hash and a length are both properties of the same
    bytes, so the store refuses it under §7.6.
    """
    pre_a = FileState(content_hash=SHARED_DIGEST, mode=0o644, byte_len=5)
    pre_b = FileState(content_hash=SHARED_DIGEST, mode=0o644, byte_len=6)
    post_a, post_b = file_state(b"one"), file_state(b"two")
    return build_spec(
        consumer_tag="test",
        intent_digest="sha256:" + "3" * 64,
        initial_surface={"a.txt": pre_a, "b.txt": pre_b},
        final_surface={"a.txt": post_a, "b.txt": post_b},
        effects=[
            ReplaceFile(effect_id="e1", path="a.txt", pre=pre_a, post=post_a),
            ReplaceFile(effect_id="e2", path="b.txt", pre=pre_b, post=post_b),
        ],
    )


def stage(workspace, name: str, content: bytes) -> None:
    """Write one capture into staging/<txid>/ through the borrowed anchor, as A6 does."""
    fd = os.open(name, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600, dir_fd=workspace.staging_fd)
    try:
        os.write(fd, content)
    finally:
        os.close(fd)


def entries_of(fd: int) -> set[str]:
    return set(os.listdir(fd))


def raw_connect(binding) -> sqlite3.Connection:
    """A connection that bypasses the store, for asserting on durable rows directly."""
    return sqlite3.connect(binding.verified_metadata_path("atoms.db"), isolation_level=None)
```

Replace the `__import__` line with a plain `from atoms.core.fingerprint import ABSENT, FileState`
import at the top and use `ABSENT` — it is written inline above only to keep the two imports
visible together; the `__import__` form will trip ruff.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_store_open.py`:

```python
"""Tier 3 -- creation and reopen (design §11.3)."""

from __future__ import annotations

import os
import sqlite3

import pytest

from atoms.core.errors import CapabilityUnavailable, ProtocolError
from atoms.store.connection import create_store, gate
from atoms.store.errors import MetadataStoreInvalid
from atoms.store.schema import APPLICATION_ID, EXPECTED_CATALOG, SCHEMA_VERSION
from tests.store_support import DATABASE_ENTRIES, raw_connect


def test_creation_produces_a_completed_store(store_on):
    with store_on() as binding:
        connection = create_store(binding)
        try:
            assert connection.execute("PRAGMA application_id").fetchone()[0] == APPLICATION_ID
            assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
            assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
            catalog = frozenset(
                (kind, name, tbl, None if sql is None else sql.rstrip().rstrip(";"))
                for kind, name, tbl, sql in connection.execute(
                    "SELECT type, name, tbl_name, sql FROM sqlite_schema"
                )
            )
            assert catalog == EXPECTED_CATALOG
        finally:
            connection.close()


def test_creation_sets_the_exact_mode_regardless_of_umask(store_on):
    """openat's mode is a request the umask subtracts from; step 3 makes it exact."""
    previous = os.umask(0o077)
    try:
        with store_on() as binding:
            create_store(binding).close()
            mode = os.stat("atoms.db", dir_fd=binding.metadata_root_fd).st_mode & 0o777
            assert mode == 0o600
    finally:
        os.umask(previous)


def test_a_second_concurrent_creation_raises_rather_than_reinitializing(store_on):
    with store_on() as binding:
        create_store(binding).close()
        with pytest.raises(MetadataStoreInvalid):
            create_store(binding)


@pytest.mark.parametrize("sidecar", ["atoms.db-wal", "atoms.db-shm", "atoms.db-journal"])
def test_creation_refuses_a_sidecar_surviving_without_the_database(store_on, sidecar):
    """O_EXCL covers atoms.db alone. Without this preflight, creation is the only path
    with no sidecar check while reopen has one -- and a planted -journal symlink is
    silently unlinked by the first WAL transition, measured (design §5.1 step 1)."""
    with store_on() as binding:
        fd = os.open(sidecar, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600,
                     dir_fd=binding.metadata_root_fd)
        os.close(fd)
        with pytest.raises(MetadataStoreInvalid) as caught:
            create_store(binding)
        assert sidecar in str(caught.value)
        assert sidecar in os.listdir(binding.metadata_root_fd), "the sidecar was destroyed"


@pytest.mark.parametrize("entry", DATABASE_ENTRIES)
def test_creation_refuses_a_symlink_at_any_of_the_four_entries(store_on, entry):
    with store_on() as binding:
        os.symlink("/etc/passwd", entry, dir_fd=binding.metadata_root_fd)
        with pytest.raises(MetadataStoreInvalid):
            create_store(binding)


def test_the_connection_profile_is_read_back(store_on):
    with store_on() as binding:
        connection = create_store(binding)
        try:
            assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2
            assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
            assert connection.execute("PRAGMA temp_store").fetchone()[0] == 2
            assert connection.execute("PRAGMA trusted_schema").fetchone()[0] == 0
        finally:
            connection.close()


def test_the_authorizer_denies_attach(store_on, tmp_path):
    with store_on() as binding:
        connection = create_store(binding)
        try:
            with pytest.raises(sqlite3.DatabaseError):
                connection.execute(f"ATTACH DATABASE '{tmp_path / 'other.db'}' AS other")
        finally:
            connection.close()


def test_an_old_sqlite_refuses_with_capability_unavailable(store_on, monkeypatch):
    monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 36, 0))
    with store_on() as binding:
        with pytest.raises(CapabilityUnavailable) as caught:
            create_store(binding)
        assert "3.37" in str(caught.value)


def test_a_temp_store_zero_build_refuses_with_capability_unavailable(store_on, monkeypatch):
    """temp_store=MEMORY is inert under TEMP_STORE=0 and the read-back still reports
    success, so the compile option is the only evidence (design §5.3)."""
    import atoms.store.connection as connection_module

    monkeypatch.setattr(connection_module, "_compile_options", lambda _c: frozenset({"TEMP_STORE=0"}))
    with store_on() as binding:
        with pytest.raises(CapabilityUnavailable) as caught:
            create_store(binding)
        assert "TEMP_STORE" in str(caught.value)


def test_the_ddl_transaction_is_atomic(store_on, monkeypatch):
    """A cut inside step 5 must leave (0, 0, empty) -- never a partial schema with a
    committed application_id, which is what executescript produces (design §5.1 step 5)."""
    import atoms.store.connection as connection_module

    real = connection_module._execute_schema

    def cut(connection):
        real(connection)
        raise KeyboardInterrupt("cut inside step 5")

    monkeypatch.setattr(connection_module, "_execute_schema", cut)
    with store_on() as binding:
        with pytest.raises(KeyboardInterrupt):
            create_store(binding)
        raw = raw_connect(binding)
        try:
            assert raw.execute("PRAGMA application_id").fetchone()[0] == 0
            assert raw.execute("PRAGMA user_version").fetchone()[0] == 0
            assert raw.execute("SELECT count(*) FROM sqlite_schema").fetchone()[0] == 0
        finally:
            raw.close()


def test_the_gate_refuses_a_released_lock(store_on):
    with store_on() as binding:
        create_store(binding).close()
    with pytest.raises(ProtocolError):
        gate(binding)
```

- [ ] **Step 3: Add the fixture**

Append to `tests/conftest.py`:

```python
@pytest.fixture
def store_on(ext4_bound_volume):
    """A bound ext4 volume with an empty metadata root, ready for store creation."""
    return ext4_bound_volume
```

If `ext4_bound_volume` is not the name in this checkout, use whatever `resolver_on` and
`approval_context` build on — `grep -n "def ext4_bound_volume" tests/conftest.py` — and keep
`store_on` as the single indirection so later tasks name only `store_on`.

- [ ] **Step 4: Run the tests to verify they fail**

Run: `uv run pytest tests/test_store_open.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'atoms.store.connection'`.

- [ ] **Step 5: Write the creation half of the module**

Create `src/atoms/store/connection.py`:

```python
"""Opening the store: creation, reopen, the pinned profile, and liveness (design §5)."""

from __future__ import annotations

import os
import sqlite3

from atoms.core.errors import CapabilityUnavailable, ProtocolError
from atoms.fs.binding import ProjectBinding
from atoms.store.errors import MetadataStoreInvalid, translated
from atoms.store.schema import APPLICATION_ID, SCHEMA_STATEMENTS, SCHEMA_VERSION

DATABASE_NAME = "atoms.db"
SIDECAR_NAMES = ("atoms.db-wal", "atoms.db-shm", "atoms.db-journal")
DATABASE_ENTRIES = (DATABASE_NAME, *SIDECAR_NAMES)
MINIMUM_SQLITE = (3, 37, 0)
DATABASE_MODE = 0o600

_SET_USER_VERSION = f"PRAGMA user_version = {SCHEMA_VERSION}"
_SET_APPLICATION_ID = f"PRAGMA application_id = {APPLICATION_ID}"
_BEGIN_IMMEDIATE = "BEGIN IMMEDIATE"
_COMMIT = "COMMIT"
_ROLLBACK = "ROLLBACK"
_READ_APPLICATION_ID = "PRAGMA application_id"
_READ_USER_VERSION = "PRAGMA user_version"
_READ_JOURNAL_MODE = "PRAGMA journal_mode"
_SET_JOURNAL_MODE_WAL = "PRAGMA journal_mode = WAL"
_READ_COMPILE_OPTIONS = "PRAGMA compile_options"
_QUICK_CHECK = "PRAGMA quick_check"
_FOREIGN_KEY_CHECK = "PRAGMA foreign_key_check"
_READ_CATALOG = "SELECT type, name, tbl_name, sql FROM sqlite_schema"

_CONNECTION_PRAGMAS: tuple[tuple[str, str, str, object], ...] = (
    ("synchronous", "PRAGMA synchronous = FULL", "PRAGMA synchronous", 2),
    ("foreign_keys", "PRAGMA foreign_keys = ON", "PRAGMA foreign_keys", 1),
    ("temp_store", "PRAGMA temp_store = MEMORY", "PRAGMA temp_store", 2),
    ("trusted_schema", "PRAGMA trusted_schema = OFF", "PRAGMA trusted_schema", 0),
)


def gate(binding: ProjectBinding) -> None:
    """The liveness gate (design §5.4).

    A connection that outlives its binding or its lock is a connection writing to a volume
    nothing holds. `binding.backend` calls `_require_active`, which raises ProtocolError
    when the binding is closed *or* when the lock was released before it
    (`binding.py:110`) -- the idiom approve_for_project already uses at `approval.py:116`.
    """
    backend = binding.backend
    del backend


def require_platform(binding: ProjectBinding) -> None:
    """Refuse a build that cannot supply the semantics the schema depends on."""
    if sqlite3.sqlite_version_info < MINIMUM_SQLITE:
        raise CapabilityUnavailable(
            f"STRICT tables require SQLite >= 3.37; this build is "
            f"{sqlite3.sqlite_version}"
        )
    if not os.path.isdir("/proc/self/fd"):
        raise CapabilityUnavailable(
            "/proc/self/fd is required to repair a database whose mode the umask reduced "
            "below readability (design §5.2 step 2); there is no path-based fallback"
        )
    del binding


def _compile_options(connection: sqlite3.Connection) -> frozenset[str]:
    with translated("reading compile options"):
        return frozenset(row[0] for row in connection.execute(_READ_COMPILE_OPTIONS))


def _require_temp_store_capable(connection: sqlite3.Connection) -> None:
    if "TEMP_STORE=0" in _compile_options(connection):
        raise CapabilityUnavailable(
            "this SQLite build reports TEMP_STORE=0, under which temp_store=MEMORY is "
            "inert and its read-back still reports success"
        )


def _preflight_entries(binding: ProjectBinding, *, require_absent: bool) -> dict[str, os.stat_result]:
    """fstatat each of the four entries, no-follow. Any symlink refuses."""
    seen: dict[str, os.stat_result] = {}
    for name in DATABASE_ENTRIES:
        try:
            info = os.stat(name, dir_fd=binding.metadata_root_fd, follow_symlinks=False)
        except FileNotFoundError:
            continue
        if not os.path.stat.S_ISREG(info.st_mode):  # type: ignore[attr-defined]
            raise MetadataStoreInvalid(
                f"{name} under metadata_root is not a regular file; a symlink there "
                "redirects SQLite's own recovery"
            )
        seen[name] = info
    if require_absent and seen:
        raise MetadataStoreInvalid(
            "cannot create a store beside surviving database entries "
            f"{sorted(seen)}; a sidecar with no database is an invalid store shape"
        )
    return seen


def publish_entry(binding: ProjectBinding, fd: int) -> None:
    """Design §5.1 step 3 -- make the mode exact and the directory entry durable.

    SQLite did not create this entry, so nothing in its durability contract promises the
    entry survives power loss: a record could be durable inside a file the directory does
    not name. Every flush goes through the Backend, which is where F_FULLFSYNC lives on
    macOS.
    """
    gate(binding)
    os.fchmod(fd, DATABASE_MODE)
    binding.backend.flush_file(fd)
    binding.backend.flush_directory(binding.metadata_root_fd)


def install_authorizer(connection: sqlite3.Connection) -> None:
    def authorize(action: int, *_rest: object) -> int:
        if action in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH):
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    connection.set_authorizer(authorize)


def apply_connection_profile(connection: sqlite3.Connection) -> None:
    """The connection-local half of design §5.3, read back one at a time.

    foreign_keys is per-connection and off by default, and journal_mode and foreign_keys
    both fail silently rather than raising -- a store running without them is a store
    whose durability and referential claims are false.
    """
    _require_temp_store_capable(connection)
    for name, setter, reader, expected in _CONNECTION_PRAGMAS:
        with translated(f"setting {name}"):
            connection.execute(setter)
        with translated(f"reading back {name}"):
            observed = connection.execute(reader).fetchone()[0]
        if observed != expected:
            raise CapabilityUnavailable(
                f"PRAGMA {name} would not take: asked for {expected!r}, got {observed!r}"
            )


def apply_persistent_profile(binding: ProjectBinding, connection: sqlite3.Connection) -> None:
    """The persistent half: the WAL transition, which rewrites the database file."""
    gate(binding)
    with translated("setting journal_mode"):
        mode = connection.execute(_SET_JOURNAL_MODE_WAL).fetchone()[0]
    if mode != "wal":
        raise CapabilityUnavailable(
            f"the volume would not accept WAL journalling; journal_mode is {mode!r}"
        )


def _execute_schema(connection: sqlite3.Connection) -> None:
    """Every DDL statement, one execute() each.

    Never executescript: it issues a COMMIT before running, which under
    isolation_level=None ends the transaction this is called inside -- measured,
    in_transaction goes True to False across the call while the application_id written
    before it stays durable.
    """
    for statement in SCHEMA_STATEMENTS:
        with translated("creating the schema"):
            connection.execute(statement)


def _connect(binding: ProjectBinding) -> sqlite3.Connection:
    connection = sqlite3.connect(
        binding.verified_metadata_path(DATABASE_NAME), isolation_level=None
    )
    install_authorizer(connection)
    return connection


def create_store(binding: ProjectBinding) -> sqlite3.Connection:
    """Design §5.1. Returns a connection to a completed store."""
    require_platform(binding)
    _preflight_entries(binding, require_absent=True)

    gate(binding)
    fd = os.open(
        DATABASE_NAME,
        os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_RDWR,
        DATABASE_MODE,
        dir_fd=binding.metadata_root_fd,
    )
    try:
        publish_entry(binding, fd)
    finally:
        os.close(fd)

    connection = _connect(binding)
    try:
        apply_persistent_profile(binding, connection)
        apply_connection_profile(connection)
        initialize_schema(binding, connection)
    except BaseException:
        connection.close()
        raise
    return connection


def initialize_schema(binding: ProjectBinding, connection: sqlite3.Connection) -> None:
    """Design §5.1 step 5 -- one explicit transaction, gated immediately before COMMIT."""
    with translated("beginning initialization"):
        connection.execute(_BEGIN_IMMEDIATE)
    try:
        _execute_schema(connection)
        with translated("stamping the schema version"):
            connection.execute(_SET_USER_VERSION)
        with translated("stamping the application id"):
            connection.execute(_SET_APPLICATION_ID)
        gate(binding)
    except BaseException:
        connection.execute(_ROLLBACK)
        raise
    with translated("committing initialization"):
        connection.execute(_COMMIT)
```

`os.path.stat.S_ISREG` is not a real attribute — import `stat` at the top and call
`stat.S_ISREG(info.st_mode)`. The `# type: ignore` above is there only to mark the line;
write the real import.

- [ ] **Step 6: Run the tests and the gates**

```bash
uv run pytest tests/test_store_open.py -v
uv run ruff check src/atoms/store tests/test_store_open.py tests/store_support.py
uv run pyright
```
Expected: PASS, `All checks passed!`, `0 errors`.

- [ ] **Step 7: Commit**

```bash
git add src/atoms/store/connection.py tests/test_store_open.py tests/store_support.py tests/conftest.py
git commit -m "feat(store): create the database through an exclusive, published protocol"
```

---

## Task 4: Reopen, the zero-length repair, and the crash cuts

**Files:**
- Modify: `src/atoms/store/connection.py`
- Modify: `tests/test_store_open.py`

**Interfaces:**
- Consumes: Task 3's `create_store` internals — `_preflight_entries`, `publish_entry`,
  `apply_persistent_profile`, `apply_connection_profile`, `initialize_schema`, `gate`,
  `require_platform`; Task 1's `EXPECTED_CATALOG`.
- Produces: `reopen_store(binding) -> sqlite3.Connection`, `repair_unpublished(binding)`,
  `classify(connection) -> Verdict`, `Verdict` (an enum: `COMPLETED`, `RESUMABLE`).

**The reopen order is load-bearing** (design §5.2). Identity comes from *reads* first, and the
journal-mode rule is applied second, by the verdict those reads select. Demanding `wal` before reading
the version would make the protocol refuse the one state it exists to resume — a zero-length file
reports `journal_mode = delete`, measured.

**Step 2 is the repair, and it runs before SQLite is involved at all.** `openat`'s mode is a request
the umask *subtracts* from: under `0o277` the file lands at `0o400` and under `0o777` at `0o000`.
Creation is unaffected, because step 3 holds the descriptor step 2 opened and an open descriptor's
access is already resolved — but the resume path has no such descriptor and must obtain one from a
name. Measured at `0o000`: `O_RDONLY | O_NOFOLLOW` and `O_RDWR` both fail `EACCES`, so there is nothing
to `fchmod`; `O_PATH | O_NOFOLLOW` opens regardless of mode but `fchmod` on it fails `EBADF`; `chmod`
through `/proc/self/fd/<n>` on that same descriptor succeeds, and `O_RDWR` succeeds afterwards.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_store_open.py`:

```python
from atoms.store.connection import Verdict, classify, open_database, reopen_store


def _cut_after_creating(binding, *, umask: int) -> None:
    """Reproduce §5.1's cut between step 2 and step 3: the file exists, unpublished."""
    previous = os.umask(umask)
    try:
        fd = os.open(
            "atoms.db",
            os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_RDWR,
            0o600,
            dir_fd=binding.metadata_root_fd,
        )
        os.close(fd)
    finally:
        os.umask(previous)


def _cut_after_publishing(binding) -> None:
    """The cut between step 3 and step 4: published, zero length, still delete mode."""
    _cut_after_creating(binding, umask=0o022)


def _cut_after_wal(binding) -> None:
    """The cut between step 4 and step 5: WAL set, schema still empty."""
    _cut_after_publishing(binding)
    connection = raw_connect(binding)
    try:
        connection.execute("PRAGMA journal_mode = WAL")
    finally:
        connection.close()


@pytest.mark.parametrize("umask", [0o277, 0o777])
def test_the_unpublished_cut_resumes_to_the_exact_mode(store_on, umask):
    """0o277 leaves 0o400 and 0o777 leaves 0o000, and they fail differently: at 0o400
    SQLite falls back to a read-only open so classification succeeds and the first write
    raises 'attempt to write a readonly database'; at 0o000 nothing opens at all. Only
    the second distinguishes §5.2 step 2's O_PATH route from a plain fchmod."""
    with store_on() as binding:
        _cut_after_creating(binding, umask=umask)
        connection = reopen_store(binding)
        try:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
            assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        finally:
            connection.close()
        mode = os.stat("atoms.db", dir_fd=binding.metadata_root_fd).st_mode & 0o777
        assert mode == 0o600


@pytest.mark.parametrize("cut", ["published", "wal"])
def test_every_later_creation_cut_resumes(store_on, cut):
    with store_on() as binding:
        {"published": _cut_after_publishing, "wal": _cut_after_wal}[cut](binding)
        connection = reopen_store(binding)
        try:
            assert connection.execute("PRAGMA application_id").fetchone()[0] == APPLICATION_ID
            assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
            assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        finally:
            connection.close()


def test_a_resumed_store_equals_an_uninterrupted_one(store_on):
    """Schema, version, and mode -- the third is where the unpublished cut is caught."""
    with store_on() as binding:
        create_store(binding).close()
        reference = frozenset(raw_connect(binding).execute("SELECT type, name, tbl_name, sql FROM sqlite_schema"))
        reference_mode = os.stat("atoms.db", dir_fd=binding.metadata_root_fd).st_mode & 0o777
    with store_on() as binding:
        _cut_after_publishing(binding)
        reopen_store(binding).close()
        resumed = frozenset(raw_connect(binding).execute("SELECT type, name, tbl_name, sql FROM sqlite_schema"))
        resumed_mode = os.stat("atoms.db", dir_fd=binding.metadata_root_fd).st_mode & 0o777
    assert resumed == reference
    assert resumed_mode == reference_mode


def test_a_completed_store_has_its_journal_mode_queried_and_never_set(store_on, monkeypatch):
    with store_on() as binding:
        create_store(binding).close()
        import atoms.store.connection as connection_module

        def refuse(*_args, **_kwargs):
            raise AssertionError("a completed store must never have journal_mode set")

        monkeypatch.setattr(connection_module, "apply_persistent_profile", refuse)
        reopen_store(binding).close()


def test_a_non_empty_delete_mode_database_is_refused_and_stays_in_delete_mode(store_on):
    """The assertion that would have caught the original defect: reopen must not convert
    a database while deciding whether to refuse it (design §5.2 step 3)."""
    with store_on() as binding:
        raw = raw_connect(binding)
        try:
            raw.execute("CREATE TABLE foreign_thing (a TEXT)")
        finally:
            raw.close()
        with pytest.raises(MetadataStoreInvalid):
            reopen_store(binding)
        raw = raw_connect(binding)
        try:
            assert raw.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        finally:
            raw.close()


@pytest.mark.parametrize(
    ("application_id", "user_version", "create_table", "fragment"),
    [
        (APPLICATION_ID, SCHEMA_VERSION + 1, True, "store version"),
        (APPLICATION_ID, 99, True, "store version"),
        (0, 0, True, "version zero"),
        (12345, 0, False, "not this engine"),
    ],
)
def test_every_version_table_row_produces_its_verdict(
    store_on, application_id, user_version, create_table, fragment
):
    with store_on() as binding:
        raw = raw_connect(binding)
        try:
            if create_table:
                raw.execute("CREATE TABLE something (a TEXT)")
            raw.execute(f"PRAGMA application_id = {application_id}")
            raw.execute(f"PRAGMA user_version = {user_version}")
        finally:
            raw.close()
        with pytest.raises(MetadataStoreInvalid) as caught:
            reopen_store(binding)
        assert fragment.split()[0] in str(caught.value).lower()


def test_a_same_version_wrong_schema_store_is_refused(store_on):
    with store_on() as binding:
        create_store(binding).close()
        raw = raw_connect(binding)
        try:
            raw.execute("CREATE TABLE extra (a TEXT)")
        finally:
            raw.close()
        with pytest.raises(MetadataStoreInvalid) as caught:
            reopen_store(binding)
        assert "schema" in str(caught.value).lower()


def test_a_failing_integrity_check_refuses(store_on, monkeypatch):
    with store_on() as binding:
        create_store(binding).close()
        import atoms.store.connection as connection_module

        monkeypatch.setattr(
            connection_module, "_integrity_findings", lambda _c: ("page 4 is malformed",)
        )
        with pytest.raises(MetadataStoreInvalid) as caught:
            reopen_store(binding)
        assert "malformed" in str(caught.value)


def test_reopen_refuses_a_symlinked_sidecar_before_sqlite_opens_anything(store_on):
    with store_on() as binding:
        create_store(binding).close()
        os.symlink("/etc/passwd", "atoms.db-journal", dir_fd=binding.metadata_root_fd)
        with pytest.raises(MetadataStoreInvalid):
            reopen_store(binding)
        assert os.readlink("atoms.db-journal", dir_fd=binding.metadata_root_fd) == "/etc/passwd"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_store_open.py -k "resume or verdict or delete_mode or symlinked" -v`
Expected: FAIL with `ImportError: cannot import name 'reopen_store'`.

- [ ] **Step 3: Add reopen to the module**

Append to `src/atoms/store/connection.py`:

```python
class Verdict(Enum):
    COMPLETED = "completed"
    RESUMABLE = "resumable"


def repair_unpublished(binding: ProjectBinding) -> None:
    """Design §5.2 step 2 -- re-run §5.1 step 3 before SQLite is involved at all.

    Ordering this after classification made it unreachable: at 0o400 SQLite falls back to
    a read-only open, so the classification reads succeed and the first write of the
    resume fails 'attempt to write a readonly database'; at 0o000 nothing opens at all.
    Three failures, one cause -- the repair was issued from a position that could no
    longer carry it out.

    O_PATH is the only open that succeeds at mode 0o000, and fchmod on it fails EBADF, so
    the chmod goes through /proc/self/fd. That is race-free for the reason it exists: the
    descriptor pins the inode step 1 stat'd, so nothing between the two can substitute a
    symlink or a different file.
    """
    gate(binding)
    path_fd = os.open(
        DATABASE_NAME,
        os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC,
        dir_fd=binding.metadata_root_fd,
    )
    try:
        os.chmod(f"/proc/self/fd/{path_fd}", DATABASE_MODE)
    finally:
        os.close(path_fd)
    fd = os.open(
        DATABASE_NAME,
        os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC,
        dir_fd=binding.metadata_root_fd,
    )
    try:
        publish_entry(binding, fd)
    finally:
        os.close(fd)


def _integrity_findings(connection: sqlite3.Connection) -> tuple[str, ...]:
    with translated("checking integrity"):
        quick = tuple(row[0] for row in connection.execute(_QUICK_CHECK))
        foreign = tuple(str(row) for row in connection.execute(_FOREIGN_KEY_CHECK))
    return tuple(finding for finding in quick if finding != "ok") + foreign


def _catalog(connection: sqlite3.Connection) -> frozenset[tuple[str, str, str, str | None]]:
    with translated("reading the catalog"):
        rows = connection.execute(_READ_CATALOG).fetchall()
    return frozenset(
        (kind, name, tbl, None if sql is None else sql.rstrip().rstrip(";"))
        for kind, name, tbl, sql in rows
    )


def classify(connection: sqlite3.Connection) -> Verdict:
    """Identity, version, and schema -- from reads alone (design §5.2 step 4)."""
    with translated("reading identity"):
        application_id = connection.execute(_READ_APPLICATION_ID).fetchone()[0]
        user_version = connection.execute(_READ_USER_VERSION).fetchone()[0]
    catalog = _catalog(connection)

    if application_id == APPLICATION_ID:
        if user_version != SCHEMA_VERSION:
            raise MetadataStoreInvalid(
                f"incompatible store version {user_version}; this build knows "
                f"{SCHEMA_VERSION}. A newer store is not corrupt -- it is unreadable by "
                "this build -- and both mean stop, do not interpret this"
            )
        if catalog != EXPECTED_CATALOG:
            missing = EXPECTED_CATALOG - catalog
            extra = catalog - EXPECTED_CATALOG
            raise MetadataStoreInvalid(
                f"schema does not match version {SCHEMA_VERSION}: missing "
                f"{sorted(name for _k, name, _t, _s in missing)}, unexpected "
                f"{sorted(name for _k, name, _t, _s in extra)}"
            )
        return Verdict.COMPLETED

    if application_id == 0 and user_version == 0 and not catalog:
        return Verdict.RESUMABLE

    if application_id == 0:
        raise MetadataStoreInvalid(
            "version zero with a non-empty schema is not an initialization this engine "
            f"interrupted: user_version={user_version}, {len(catalog)} schema objects"
        )
    raise MetadataStoreInvalid(
        f"application_id {application_id} is not this engine's ({APPLICATION_ID})"
    )


def open_database(binding: ProjectBinding) -> sqlite3.Connection:
    """Design §5.2, steps 1 through 6."""
    require_platform(binding)
    seen = _preflight_entries(binding, require_absent=False)
    if DATABASE_NAME not in seen:
        raise MetadataStoreInvalid(
            f"{DATABASE_NAME} is absent under metadata_root; "
            f"surviving entries {sorted(seen)}"
        )
    if seen[DATABASE_NAME].st_size == 0:
        repair_unpublished(binding)

    connection = _connect(binding)
    try:
        verdict = classify(connection)
        if verdict is Verdict.RESUMABLE:
            apply_persistent_profile(binding, connection)
            apply_connection_profile(connection)
            initialize_schema(binding, connection)
        else:
            with translated("reading journal_mode"):
                mode = connection.execute(_READ_JOURNAL_MODE).fetchone()[0]
            if mode != "wal":
                raise MetadataStoreInvalid(
                    f"a completed store must already be in WAL; this one reports "
                    f"{mode!r}. Converting it would rewrite a database on a guess"
                )
            apply_connection_profile(connection)
        findings = _integrity_findings(connection)
        if findings:
            raise MetadataStoreInvalid(
                "integrity check failed: " + "; ".join(findings)
            )
    except BaseException:
        connection.close()
        raise
    return connection


def reopen_store(binding: ProjectBinding) -> sqlite3.Connection:
    """Alias naming the caller's intent; `open_database` is the shared implementation."""
    return open_database(binding)
```

Add `from enum import Enum` and `from atoms.store.schema import EXPECTED_CATALOG` to the
module's imports.

**`apply_connection_profile` runs on both branches but in different places.** On the resumable
branch it runs *after* the WAL transition because `initialize_schema` needs `foreign_keys` on;
on the completed branch it runs after the mode is verified. Do not hoist it above `classify`
— `trusted_schema = OFF` is a connection setting, but `foreign_keys = ON` changes what a read
of a foreign database can trigger, and §5.2's whole ordering is that nothing is applied before
the verdict.

- [ ] **Step 4: Run the tests and the gates**

```bash
uv run pytest tests/test_store_open.py -v
uv run ruff check src/atoms/store/connection.py tests/test_store_open.py
uv run pyright
```
Expected: PASS, `All checks passed!`, `0 errors`.

- [ ] **Step 5: Commit**

```bash
git add src/atoms/store/connection.py tests/test_store_open.py
git commit -m "feat(store): resume an interrupted initialization from any of its four cuts"
```

---

## Task 5: The Store object and its transaction

**Files:**
- Modify: `src/atoms/store/connection.py`
- Create: `tests/test_store_liveness.py`
- Modify: `tests/conftest.py`

**Interfaces:**
- Consumes: Task 3/4's `create_store`, `open_database`, `gate`, `DATABASE_NAME`.
- Produces: `open_store(binding: ProjectBinding) -> Store`; `Store` with `transaction()`, `close()`,
  `__enter__`/`__exit__`, and the internal `_require_live()`, `_require_no_transaction()`,
  `_connection`; `_StoreTransaction` with `_require_current()`, `_mutating()`, `_poison()`,
  `_require_not_poisoned()`, `_spend()`, and the empty barrier hook `_run_barrier()` that Task 8
  fills in.

**This task's tests use only this task's surface.** `_StoreTransaction` has no public method until
Task 7, so ownership is asserted through `_require_current()` — which *is* this task's deliverable —
and reads through `read_active()` arrive with the reader in Task 8, which extends this same file. The
gate below therefore runs the whole file with no `-k` filter: a filter that hides tests calling methods
that do not exist yet is a gate that cannot fail for the reason it was written.

**`_StoreTransaction` is private and stays private.** No caller constructs one or annotates against
one — it is obtained only by entering `Store.transaction()`, used inside that block, and dead after.
Task 13 asserts the exported surface is exactly `__all__`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_store_liveness.py`:

```python
"""Design §5.4, §7.7, and §7.8 -- the liveness gate, transaction ownership, and close."""

from __future__ import annotations

import pytest

from atoms.core.errors import ProtocolError
from atoms.store.connection import open_store


def test_open_store_creates_then_reopens(store_on):
    with store_on() as binding:
        with open_store(binding) as store:
            with store.transaction():
                pass
        with open_store(binding) as store:
            with store.transaction():
                pass


def test_a_nested_transaction_is_refused(opened_store):
    with opened_store.transaction():
        with pytest.raises(ProtocolError) as caught:
            with opened_store.transaction():
                pass
    assert "nest" in str(caught.value).lower()


def test_a_retained_transaction_is_dead_after_commit(opened_store):
    with opened_store.transaction() as txn:
        pass
    with pytest.raises(ProtocolError):
        txn._require_current()


def test_a_retained_transaction_is_dead_after_rollback(opened_store):
    with pytest.raises(RuntimeError):
        with opened_store.transaction() as txn:
            raise RuntimeError("caller failure")
    with pytest.raises(ProtocolError):
        txn._require_current()


def test_a_stale_transaction_cannot_write_into_a_later_one(opened_store):
    with opened_store.transaction() as first:
        pass
    with opened_store.transaction():
        with pytest.raises(ProtocolError) as caught:
            first._require_current()
    assert "spent" in str(caught.value).lower() or "current" in str(caught.value).lower()


def test_a_poisoned_transaction_refuses_to_commit(opened_store):
    """Design §7.7. The mechanism here; Task 7 proves it end to end through a real
    mutating method whose failure the caller catches."""
    with pytest.raises(ProtocolError) as caught:
        with opened_store.transaction() as txn:
            txn._poison(RuntimeError("a mutating method raised"))
    assert "poison" in str(caught.value).lower()
    assert isinstance(caught.value.__cause__, RuntimeError)


def test_a_poisoned_transaction_leaves_no_open_transaction(opened_store):
    with pytest.raises(ProtocolError):
        with opened_store.transaction() as txn:
            txn._poison(RuntimeError("a mutating method raised"))
    with opened_store.transaction():
        pass


def test_close_is_idempotent(opened_store):
    opened_store.close()
    opened_store.close()


def test_close_rolls_back_and_spends_an_open_transaction(opened_store):
    entered = opened_store.transaction()
    txn = entered.__enter__()
    opened_store.close()
    with pytest.raises(ProtocolError):
        txn._require_current()


def test_a_transaction_after_close_raises_protocol_error_not_sqlite(opened_store):
    opened_store.close()
    with pytest.raises(ProtocolError):
        with opened_store.transaction():
            pass


def test_the_store_exposes_no_connection(opened_store):
    assert not any(
        name for name in dir(opened_store) if "connect" in name and not name.startswith("_")
    )


def test_a_released_lock_refuses_every_operation(store_on):
    with store_on() as binding:
        store = open_store(binding)
    with pytest.raises(ProtocolError):
        with store.transaction():
            pass
    store.close()


def test_close_still_succeeds_on_a_dead_binding(store_on):
    with store_on() as binding:
        store = open_store(binding)
    store.close()
```

- [ ] **Step 2: Add the fixture**

Append to `tests/conftest.py`:

```python
@pytest.fixture
def opened_store(store_on):
    """A live Store over a fresh ext4 project, closed on exit."""
    from atoms.store.connection import open_store

    with store_on() as binding:
        store = open_store(binding)
        try:
            yield store
        finally:
            store.close()
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_store_liveness.py -v`
Expected: FAIL with `ImportError: cannot import name 'open_store'`.

- [ ] **Step 4: Add Store and _StoreTransaction**

Append to `src/atoms/store/connection.py`:

```python
_STORE_TOKEN = object()


class _StoreTransaction:
    """The only object that can write a record row (design §7.1).

    Every method verifies that this object is the store's current active transaction and
    that it is not spent. A retained object must not be able to write into a later
    transaction, and 'the caller should not do that' is not the same as 'the caller
    cannot'.
    """

    __slots__ = ("_poisoned_by", "_spent", "_store", "_touched")

    def __init__(self, store: Store, *, _construction_token: object | None = None) -> None:
        if _construction_token is not _STORE_TOKEN:
            raise TypeError("transactions are created only by Store.transaction()")
        self._store = store
        self._spent = False
        self._touched: set[str] = set()
        self._poisoned_by: BaseException | None = None

    def _require_current(self) -> Store:
        if self._spent:
            raise ProtocolError(
                "this transaction is spent; obtain a new one from Store.transaction()"
            )
        store = self._store
        if store._active_transaction is not self:
            raise ProtocolError(
                "this transaction is not the store's current active transaction"
            )
        store._require_live()
        return store

    def _poison(self, cause: BaseException) -> None:
        """Record that a mutating method failed after SQLite may already have written.

        First cause wins: the one that broke the transaction explains it better than
        whatever the caller did next.
        """
        if self._poisoned_by is None:
            self._poisoned_by = cause

    def _require_not_poisoned(self) -> None:
        cause = self._poisoned_by
        if cause is not None:
            raise ProtocolError(
                "this transaction is poisoned: a write raised "
                f"{type(cause).__name__} and was caught inside the transaction block. "
                "SQLite rolls back the failing statement, not the transaction, so "
                "whatever ran before it is still there; committing would make a "
                "partial write durable"
            ) from cause

    @contextmanager
    def _mutating(self) -> Iterator[Store]:
        """Every public method of this class runs its work inside this.

        Two rules in one place instead of thirteen: the object is the store's current
        transaction, and a failure poisons what is left. Task 13 asserts every public
        method opens with it, so the rule is checked rather than remembered.
        """
        store = self._require_current()
        try:
            yield store
        except BaseException as caught:
            self._poison(caught)
            raise

    def _spend(self) -> None:
        self._spent = True

    def _run_barrier(self) -> None:
        """The pre-COMMIT barrier. Task 8 fills this in; here it is deliberately empty so
        that Task 5's transaction semantics can be reviewed on their own."""


class Store:
    """The durable metadata store as a mechanism (design §7).

    Owns the connection and closes it in close(). Borrows the binding's metadata_root
    descriptor and never closes it -- that stays A4a's.
    """

    __slots__ = ("_active_transaction", "_binding", "_closed", "_connection")

    def __init__(
        self,
        binding: ProjectBinding,
        connection: sqlite3.Connection,
        *,
        _construction_token: object | None = None,
    ) -> None:
        if _construction_token is not _STORE_TOKEN:
            raise TypeError("Store values are created only by open_store")
        self._binding = binding
        self._connection = connection
        self._closed = False
        self._active_transaction: _StoreTransaction | None = None

    def _require_live(self) -> None:
        if self._closed:
            raise ProtocolError("this Store is closed")
        gate(self._binding)

    def _require_no_transaction(self) -> None:
        if self._active_transaction is not None:
            raise ProtocolError(
                "a write transaction is already open on this store; A5b chooses one "
                "barrier per lease step, and a nested BEGIN would mean two callers each "
                "believe they own the boundary"
            )

    @contextmanager
    def transaction(self) -> Iterator[_StoreTransaction]:
        self._require_live()
        self._require_no_transaction()
        with translated("beginning a transaction"):
            self._connection.execute(_BEGIN_IMMEDIATE)
        txn = _StoreTransaction(self, _construction_token=_STORE_TOKEN)
        self._active_transaction = txn
        try:
            yield txn
            txn._require_not_poisoned()
            txn._run_barrier()
            gate(self._binding)
            with translated("committing"):
                self._connection.execute(_COMMIT)
        except BaseException:
            # Reached from four places: the caller's body, the poison check, the
            # barrier, and a COMMIT that failed. The last is why this is a state test
            # rather than an `else:` branch -- a failed COMMIT leaves the transaction
            # OPEN (measured: `in_transaction` is still true afterwards and the next
            # BEGIN IMMEDIATE raises "cannot start a transaction within a
            # transaction"), so a store that spent its transaction object and cleared
            # its slot on that path would refuse every later transaction, reporting a
            # caller error for a condition it created itself.
            if self._connection.in_transaction:
                self._connection.execute(_ROLLBACK)
            raise
        finally:
            txn._spend()
            self._active_transaction = None

    def close(self) -> None:
        """Idempotent, and exempt from the liveness gate (design §7.8).

        A store whose binding died must still be closable: gating close would strand the
        connection it holds, which is the opposite of what the gate is for.
        """
        if self._closed:
            return
        self._closed = True
        txn = self._active_transaction
        if txn is not None:
            try:
                self._connection.execute(_ROLLBACK)
            except sqlite3.Error:
                pass
            txn._spend()
            self._active_transaction = None
        self._connection.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def open_store(binding: ProjectBinding) -> Store:
    """Create the store if `atoms.db` is absent, otherwise reopen it (design §5)."""
    require_platform(binding)
    try:
        os.stat(DATABASE_NAME, dir_fd=binding.metadata_root_fd, follow_symlinks=False)
    except FileNotFoundError:
        connection = create_store(binding)
    else:
        connection = open_database(binding)
    return Store(binding, connection, _construction_token=_STORE_TOKEN)
```

Add `from contextlib import contextmanager` and `from collections.abc import Iterator` to
the imports. `Store.read_active` and `Store.read_record` arrive in Task 8, which extends this same
test file with the read-side liveness cases; nothing in this task's tests calls them, so this task's
gate runs the file whole.

**The `except sqlite3.Error: pass` inside `close()` is the one swallow in the package**, and
it is bounded: a rollback on a connection whose transaction SQLite already ended has nothing
to undo, and raising there would prevent the close that releases the file. Task 13's guard
allows it by name and nothing else.

- [ ] **Step 5: Run the tests and the gates**

```bash
uv run pytest tests/test_store_liveness.py -v
uv run ruff check src/atoms/store/connection.py tests/test_store_liveness.py
uv run pyright
```
Expected: PASS, `All checks passed!`, `0 errors`.

- [ ] **Step 6: Commit**

```bash
git add src/atoms/store/connection.py tests/test_store_liveness.py tests/conftest.py
git commit -m "feat(store): make an out-of-transaction record write unreachable"
```

---

## Task 6: The halt-diagnostic codec

**Files:**
- Create: `src/atoms/store/records.py`
- Create: `tests/test_store_records.py`
- Modify: `tests/store_support.py`

**Interfaces:**
- Consumes: `HaltDiagnostic`, `DiagnosticEntry`, `DiagnosticIdentityRelation`,
  `EffectJournalState`, `HaltReason`, `OperatorAction`, `IdentityRelation`, `FileBuildRelation`,
  `TransactionState`, `CommitDecision`, `JournalState` from `atoms.core.recovery.model`;
  `AbsentState`, `FileState`, `DirectoryState`, `SymlinkState`, `PathState` from
  `atoms.core.fingerprint`; Task 2's `MetadataStoreInvalid`.
- Produces: `encode_diagnostic(diagnostic: HaltDiagnostic) -> str`,
  `decode_diagnostic(text: str) -> HaltDiagnostic` — both private to `records.py`, neither exported.

**Why the codec is explicit rather than generic.** A3 owns the semantic value; A5a owns its durable
encoding, and the two must be able to change independently. A `dataclasses.asdict` round-trip would
silently start persisting any field A3 adds — including, one day, one it should not. Ledger #12 forbids
serializing snapshot-local identity tokens, and `HaltDiagnostic` is durable *precisely because* A3
keeps it `EntryIdentity`-free by construction. The test asserts that property against the encoded form.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_store_records.py`:

```python
"""Tier 2 -- the record layer over an in-memory-shaped store (design §11.2)."""

from __future__ import annotations

import json

import pytest

from atoms.core.fingerprint import AbsentState, DirectoryState, FileState, SymlinkState
from atoms.core.recovery.model import (
    CommitDecision,
    DiagnosticEntry,
    DiagnosticIdentityRelation,
    EffectJournalState,
    FileBuildRelation,
    HaltDiagnostic,
    HaltReason,
    IdentityRelation,
    JournalState,
    OperatorAction,
    TransactionState,
)
from atoms.store.errors import MetadataStoreInvalid
from atoms.store.records import decode_diagnostic, encode_diagnostic
from tests.store_support import every_diagnostic_shape


@pytest.mark.parametrize("diagnostic", every_diagnostic_shape(), ids=lambda d: d.reason.value)
def test_the_diagnostic_round_trips_exactly(diagnostic):
    assert decode_diagnostic(encode_diagnostic(diagnostic)) == diagnostic


@pytest.mark.parametrize("diagnostic", every_diagnostic_shape(), ids=lambda d: d.reason.value)
def test_the_encoded_form_contains_no_entry_identity(diagnostic):
    """Ledger #12: a snapshot-local identity token must never become durable. A3 keeps
    HaltDiagnostic token-free by construction, which is what makes it storable at all."""
    text = encode_diagnostic(diagnostic)
    assert "identity" not in text or "identity_relations" in text
    assert "entry-identity" not in text
    assert "_token" not in text


def test_every_halt_reason_and_operator_action_encodes():
    """Parametrized over the enums, so a new member fails here rather than at the first
    halt a real recovery produces."""
    for reason in HaltReason:
        for action in OperatorAction:
            diagnostic = HaltDiagnostic(
                pre_halt_state=TransactionState.APPLYING,
                commit_decision=CommitDecision.UNCOMMITTED,
                journals=(),
                projected_transaction_state=TransactionState.HALTED,
                projected_journals=(),
                effect_id=None,
                paths=(),
                expected=(),
                observed=(),
                identity_relations=(),
                reason=reason,
                operator_action=action,
            )
            assert decode_diagnostic(encode_diagnostic(diagnostic)) == diagnostic


@pytest.mark.parametrize(
    "state",
    [
        AbsentState(),
        FileState(content_hash="sha256:" + "a" * 64, mode=0o644, byte_len=3),
        DirectoryState(mode=0o755),
        SymlinkState(target="../elsewhere", mode=0o777),
    ],
)
def test_every_path_state_variant_round_trips_inside_a_diagnostic_entry(state):
    diagnostic = HaltDiagnostic(
        pre_halt_state=TransactionState.APPLYING,
        commit_decision=CommitDecision.UNCOMMITTED,
        journals=(EffectJournalState(effect_id="e1", state=JournalState.STARTED),),
        projected_transaction_state=TransactionState.HALTED,
        projected_journals=(),
        effect_id="e1",
        paths=("a.txt",),
        expected=(
            DiagnosticEntry(
                slot="expected", state=state, has_unmodeled_child=None,
                file_build_relation=None,
            ),
        ),
        observed=(
            DiagnosticEntry(
                slot="observed", state=state, has_unmodeled_child=True,
                file_build_relation=FileBuildRelation.STRICT_PREFIX,
            ),
        ),
        identity_relations=(
            DiagnosticIdentityRelation(
                left_slot="expected", right_slot="observed",
                relation=IdentityRelation.DIFFERENT,
            ),
        ),
        reason=HaltReason.PLAN_PRECONDITION_CHANGED,
        operator_action=OperatorAction.INSPECT_PRESERVED_EVIDENCE,
    )
    assert decode_diagnostic(encode_diagnostic(diagnostic)) == diagnostic


@pytest.mark.parametrize(
    "mutate",
    [
        lambda obj: obj.pop("reason"),
        lambda obj: obj.update({"reason": "no_such_reason"}),
        lambda obj: obj.update({"unexpected": 1}),
        lambda obj: obj.update({"pre_halt_state": "not_a_state"}),
        lambda obj: obj.update({"journals": [{"effect_id": "e1"}]}),
    ],
)
def test_a_malformed_diagnostic_payload_refuses(mutate):
    """A missing field, an extra one, and an unknown enum member all refuse -- design §9
    lists a malformed payload as a MetadataStoreInvalid shape."""
    diagnostic = every_diagnostic_shape()[0]
    obj = json.loads(encode_diagnostic(diagnostic))
    mutate(obj)
    with pytest.raises(MetadataStoreInvalid):
        decode_diagnostic(json.dumps(obj))


def test_a_duplicate_key_in_the_payload_refuses():
    diagnostic = every_diagnostic_shape()[0]
    text = encode_diagnostic(diagnostic)
    doubled = text[:-1] + ', "reason": "directory_not_empty"}'
    with pytest.raises(MetadataStoreInvalid):
        decode_diagnostic(doubled)
```

- [ ] **Step 2: Add the diagnostic builders**

Append to `tests/store_support.py`:

```python
from atoms.core.fingerprint import AbsentState, DirectoryState, SymlinkState
from atoms.core.recovery.model import (
    CommitDecision,
    DiagnosticEntry,
    DiagnosticIdentityRelation,
    EffectJournalState,
    FileBuildRelation,
    HaltDiagnostic,
    HaltReason,
    IdentityRelation,
    JournalState,
    OperatorAction,
    TransactionState,
)


def every_diagnostic_shape() -> tuple[HaltDiagnostic, ...]:
    """One diagnostic per structurally distinct shape: empty tuples, populated tuples,
    a null effect_id, and every optional field on both settings."""
    populated = HaltDiagnostic(
        pre_halt_state=TransactionState.APPLYING,
        commit_decision=CommitDecision.COMMITTED,
        journals=(
            EffectJournalState(effect_id="e1", state=JournalState.DONE),
            EffectJournalState(effect_id="e2", state=JournalState.UNDO_STARTED),
        ),
        projected_transaction_state=TransactionState.HALTED,
        projected_journals=(EffectJournalState(effect_id="e1", state=JournalState.DONE),),
        effect_id="e2",
        paths=("a.txt", "dir/b.txt"),
        expected=(
            DiagnosticEntry(
                slot="pre", state=file_state(b"x"), has_unmodeled_child=False,
                file_build_relation=FileBuildRelation.EXACT,
            ),
        ),
        observed=(
            DiagnosticEntry(
                slot="post", state=DirectoryState(mode=0o750), has_unmodeled_child=True,
                file_build_relation=None,
            ),
            DiagnosticEntry(
                slot="link", state=SymlinkState(target="x", mode=0o777),
                has_unmodeled_child=None, file_build_relation=FileBuildRelation.DIVERGED,
            ),
        ),
        identity_relations=(
            DiagnosticIdentityRelation(
                left_slot="pre", right_slot="post", relation=IdentityRelation.SAME
            ),
        ),
        reason=HaltReason.EFFECT_TUPLE_UNATTRIBUTABLE,
        operator_action=OperatorAction.REPAIR_DURABLE_METADATA,
    )
    empty = HaltDiagnostic(
        pre_halt_state=TransactionState.ROLLING_BACK,
        commit_decision=CommitDecision.UNCOMMITTED,
        journals=(),
        projected_transaction_state=TransactionState.HALTED,
        projected_journals=(),
        effect_id=None,
        paths=(),
        expected=(DiagnosticEntry(
            slot="only", state=AbsentState(), has_unmodeled_child=None,
            file_build_relation=None,
        ),),
        observed=(),
        identity_relations=(),
        reason=HaltReason.DIRECTORY_NOT_EMPTY,
        operator_action=OperatorAction.INSPECT_PRESERVED_EVIDENCE,
    )
    return (populated, empty)


def matching_diagnostic(effect_id: str = "only") -> HaltDiagnostic:
    """A diagnostic that AGREES with a one_effect_spec record's durable rows.

    §7.6 compares a stored diagnostic's commit_decision and journal vector against the
    record row and the effect rows, so a diagnostic assembled at random cannot be
    committed at all. A test that needs a *coherent* HALTED record on disk -- to then
    break one specific thing about it -- needs this one.
    """
    return HaltDiagnostic(
        pre_halt_state=TransactionState.PREPARED,
        commit_decision=CommitDecision.UNCOMMITTED,
        journals=(EffectJournalState(effect_id=effect_id, state=JournalState.PENDING),),
        projected_transaction_state=TransactionState.HALTED,
        projected_journals=(),
        effect_id=effect_id,
        paths=("a.txt",),
        expected=(),
        observed=(),
        identity_relations=(),
        reason=HaltReason.DIRECTORY_NOT_EMPTY,
        operator_action=OperatorAction.INSPECT_PRESERVED_EVIDENCE,
    )
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_store_records.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'atoms.store.records'`.

- [ ] **Step 4: Write the codec**

Create `src/atoms/store/records.py`:

```python
"""Typed record read/write and the durable encoding of A3's halt diagnostic (design §6.4, §7)."""

from __future__ import annotations

import json
from typing import Any

from atoms.core.fingerprint import (
    AbsentState,
    DirectoryState,
    FileState,
    PathState,
    SymlinkState,
)
from atoms.core.recovery.model import (
    CommitDecision,
    DiagnosticEntry,
    DiagnosticIdentityRelation,
    EffectJournalState,
    FileBuildRelation,
    HaltDiagnostic,
    HaltReason,
    IdentityRelation,
    JournalState,
    OperatorAction,
    TransactionState,
)
from atoms.store.errors import MetadataStoreInvalid

_DIAGNOSTIC_FIELDS = (
    "pre_halt_state",
    "commit_decision",
    "journals",
    "projected_transaction_state",
    "projected_journals",
    "effect_id",
    "paths",
    "expected",
    "observed",
    "identity_relations",
    "reason",
    "operator_action",
)


def _refuse(message: str) -> None:
    raise MetadataStoreInvalid(f"halt diagnostic payload is malformed: {message}")


def _state_obj(state: PathState) -> dict[str, Any]:
    if isinstance(state, AbsentState):
        return {"kind": "absent"}
    if isinstance(state, FileState):
        return {
            "kind": "file",
            "content_hash": state.content_hash,
            "mode": state.mode,
            "byte_len": state.byte_len,
        }
    if isinstance(state, DirectoryState):
        return {"kind": "directory", "mode": state.mode}
    return {"kind": "symlink", "target": state.target, "mode": state.mode}


def _entry_obj(entry: DiagnosticEntry) -> dict[str, Any]:
    return {
        "slot": entry.slot,
        "state": _state_obj(entry.state),
        "has_unmodeled_child": entry.has_unmodeled_child,
        "file_build_relation": (
            None if entry.file_build_relation is None else entry.file_build_relation.value
        ),
    }


def encode_diagnostic(diagnostic: HaltDiagnostic) -> str:
    """Explicit, field by field. Nothing here reflects over the dataclass, so a field A3
    adds does not silently become durable (ledger #12)."""
    return json.dumps(
        {
            "pre_halt_state": diagnostic.pre_halt_state.value,
            "commit_decision": diagnostic.commit_decision.value,
            "journals": [
                {"effect_id": j.effect_id, "state": j.state.value}
                for j in diagnostic.journals
            ],
            "projected_transaction_state": diagnostic.projected_transaction_state.value,
            "projected_journals": [
                {"effect_id": j.effect_id, "state": j.state.value}
                for j in diagnostic.projected_journals
            ],
            "effect_id": diagnostic.effect_id,
            "paths": list(diagnostic.paths),
            "expected": [_entry_obj(e) for e in diagnostic.expected],
            "observed": [_entry_obj(e) for e in diagnostic.observed],
            "identity_relations": [
                {
                    "left_slot": r.left_slot,
                    "right_slot": r.right_slot,
                    "relation": r.relation.value,
                }
                for r in diagnostic.identity_relations
            ],
            "reason": diagnostic.reason.value,
            "operator_action": diagnostic.operator_action.value,
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    seen: dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            _refuse(f"duplicate key {key!r}")
        seen[key] = value
    return seen


def _member(enum_type: Any, value: Any, label: str) -> Any:
    try:
        return enum_type(value)
    except ValueError:
        _refuse(f"{value!r} is not a {label}")


def _decode_state(obj: Any) -> PathState:
    if not isinstance(obj, dict):
        _refuse("a path state must be an object")
    kind = obj.get("kind")
    if kind == "absent":
        _require_keys(obj, {"kind"})
        return AbsentState()
    if kind == "file":
        _require_keys(obj, {"kind", "content_hash", "mode", "byte_len"})
        return FileState(
            content_hash=obj["content_hash"], mode=obj["mode"], byte_len=obj["byte_len"]
        )
    if kind == "directory":
        _require_keys(obj, {"kind", "mode"})
        return DirectoryState(mode=obj["mode"])
    if kind == "symlink":
        _require_keys(obj, {"kind", "target", "mode"})
        return SymlinkState(target=obj["target"], mode=obj["mode"])
    _refuse(f"{kind!r} is not a path-state kind")
    raise AssertionError("unreachable")


def _require_keys(obj: dict[str, Any], expected: set[str]) -> None:
    if set(obj) != expected:
        _refuse(f"expected keys {sorted(expected)}, got {sorted(obj)}")


def _decode_journal(obj: Any) -> EffectJournalState:
    if not isinstance(obj, dict):
        _refuse("a journal entry must be an object")
    _require_keys(obj, {"effect_id", "state"})
    return EffectJournalState(
        effect_id=obj["effect_id"], state=_member(JournalState, obj["state"], "JournalState")
    )


def _decode_entry(obj: Any) -> DiagnosticEntry:
    if not isinstance(obj, dict):
        _refuse("a diagnostic entry must be an object")
    _require_keys(obj, {"slot", "state", "has_unmodeled_child", "file_build_relation"})
    relation = obj["file_build_relation"]
    return DiagnosticEntry(
        slot=obj["slot"],
        state=_decode_state(obj["state"]),
        has_unmodeled_child=obj["has_unmodeled_child"],
        file_build_relation=(
            None if relation is None
            else _member(FileBuildRelation, relation, "FileBuildRelation")
        ),
    )


def _decode_relation(obj: Any) -> DiagnosticIdentityRelation:
    if not isinstance(obj, dict):
        _refuse("an identity relation must be an object")
    _require_keys(obj, {"left_slot", "right_slot", "relation"})
    return DiagnosticIdentityRelation(
        left_slot=obj["left_slot"],
        right_slot=obj["right_slot"],
        relation=_member(IdentityRelation, obj["relation"], "IdentityRelation"),
    )


def decode_diagnostic(text: str) -> HaltDiagnostic:
    try:
        obj = json.loads(text, object_pairs_hook=_no_duplicate_keys)
    except json.JSONDecodeError as caught:
        raise MetadataStoreInvalid(
            f"halt diagnostic payload is not valid JSON: {caught}"
        ) from caught
    if not isinstance(obj, dict):
        _refuse("the payload must be an object")
    _require_keys(obj, set(_DIAGNOSTIC_FIELDS))
    return HaltDiagnostic(
        pre_halt_state=_member(TransactionState, obj["pre_halt_state"], "TransactionState"),
        commit_decision=_member(CommitDecision, obj["commit_decision"], "CommitDecision"),
        journals=tuple(_decode_journal(j) for j in obj["journals"]),
        projected_transaction_state=_member(
            TransactionState, obj["projected_transaction_state"], "TransactionState"
        ),
        projected_journals=tuple(_decode_journal(j) for j in obj["projected_journals"]),
        effect_id=obj["effect_id"],
        paths=tuple(obj["paths"]),
        expected=tuple(_decode_entry(e) for e in obj["expected"]),
        observed=tuple(_decode_entry(e) for e in obj["observed"]),
        identity_relations=tuple(_decode_relation(r) for r in obj["identity_relations"]),
        reason=_member(HaltReason, obj["reason"], "HaltReason"),
        operator_action=_member(OperatorAction, obj["operator_action"], "OperatorAction"),
    )
```

`_refuse` returns `None` but always raises; pyright will not narrow on that, so the two
call sites that need a value afterwards (`_decode_state`, `_member`) end with an explicit
`raise AssertionError("unreachable")` or return inside the `try`. Keep those.

- [ ] **Step 5: Run the tests and the gates**

```bash
uv run pytest tests/test_store_records.py -v
uv run ruff check src/atoms/store/records.py tests/test_store_records.py tests/store_support.py
uv run pyright
```
Expected: PASS, `All checks passed!`, `0 errors`.

- [ ] **Step 6: Commit**

```bash
git add src/atoms/store/records.py tests/test_store_records.py tests/store_support.py
git commit -m "feat(store): encode the halt diagnostic explicitly, field by field"
```

---

## Task 7: Typed record writes

**Files:**
- Modify: `src/atoms/store/records.py`
- Modify: `src/atoms/store/connection.py`
- Modify: `tests/test_store_records.py`

**Interfaces:**
- Consumes: `canonical_json` from `atoms.core.canonical`; `variant_of` from `atoms.store.schema`;
  Task 5's `_StoreTransaction._require_current`.
- Produces, all on `_StoreTransaction`: `insert_record(txid, spec)`,
  `set_transaction_state(txid, state)`, `set_commit_decision(txid, decision)`,
  `set_journal_state(txid, effect_id, state)`, `set_rollback_result(txid, result)`,
  `set_halt_diagnostic(txid, diagnostic)`, `set_active(txid | None)`. Plus the module-level SQL
  constants they issue.

**`insert_record` takes no `variants` argument.** Every effect's variant is already in the spec, so a
separate mapping would be a second copy of a fact the first argument carries — and Task 8's predicate
has to *check* that the `effect` rows agree with `spec_json` anyway, so the argument's only reachable
effect is to disagree with the spec and be rejected. Where `spec_json` is the authority, A5a derives
rather than accepts.

**The journal vector is never read back by row order.** `(txid, effect_id)` is the primary key, so
SQLite returns rows in `effect_id` order — `z, a, m` comes back `a, m, z`, measured. Task 8
reconstructs the vector by walking `spec_json`; the schema carries no ordinal column because storing
one would make the order a second fact to keep in agreement with the spec.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_store_records.py`:

```python
import sqlite3

from atoms.core.canonical import canonical_json
from atoms.core.errors import ProtocolError
from atoms.core.recovery.model import RollbackResult
from tests.store_support import (
    duplicate_effect_spec,
    one_effect_spec,
    raw_connect,
    replace_spec,
)


def test_insert_record_stores_the_canonical_encoding(opened_store, store_binding):
    spec = one_effect_spec()
    with opened_store.transaction() as txn:
        txn.insert_record("tx1", spec)
    raw = raw_connect(store_binding)
    try:
        stored = raw.execute(
            "SELECT spec_json, state, committed FROM transaction_record WHERE txid = ?",
            ("tx1",),
        ).fetchone()
    finally:
        raw.close()
    assert stored == (canonical_json(spec), "prepared", "uncommitted")


def test_insert_record_derives_every_effect_row_from_the_spec(opened_store, store_binding):
    with opened_store.transaction() as txn:
        txn.insert_record("tx1", one_effect_spec(effect_id="only"))
    raw = raw_connect(store_binding)
    try:
        rows = raw.execute(
            "SELECT effect_id, variant, journal_state FROM effect WHERE txid = ?", ("tx1",)
        ).fetchall()
    finally:
        raw.close()
    assert rows == [("only", "create_file_no_clobber", "pending")]


def test_spec_json_is_write_once_at_the_database(opened_store, store_binding):
    with opened_store.transaction() as txn:
        txn.insert_record("tx1", one_effect_spec())
    raw = raw_connect(store_binding)
    try:
        with pytest.raises(Exception) as caught:
            raw.execute("UPDATE transaction_record SET spec_json = '{}' WHERE txid = 'tx1'")
        assert "write-once" in str(caught.value)
    finally:
        raw.close()


def test_a_duplicate_txid_is_refused(opened_store):
    with opened_store.transaction() as txn:
        txn.insert_record("tx1", one_effect_spec())
    with pytest.raises(Exception):
        with opened_store.transaction() as txn:
            txn.insert_record("tx1", one_effect_spec())


def test_a_caught_write_failure_cannot_be_committed(opened_store, store_binding):
    """Design §7.7's poison rule, end to end.

    The duplicate effect_id fails the *second* INSERT INTO effect. By then the record
    row and the first effect row -- written by the same method call -- are already in
    the transaction, and SQLite rolls back only the failing statement: measured,
    `in_transaction` stays true, the earlier rows stay visible, and the COMMIT makes
    them durable. A caller that catches the failure inside the block and carries on must
    not be able to commit that half-written record.
    """
    with pytest.raises(ProtocolError) as caught:
        with opened_store.transaction() as txn:
            with pytest.raises(sqlite3.IntegrityError):
                txn.insert_record("tx1", duplicate_effect_spec())
    assert "poison" in str(caught.value).lower()
    raw = raw_connect(store_binding)
    try:
        assert raw.execute("SELECT count(*) FROM transaction_record").fetchone()[0] == 0
        assert raw.execute("SELECT count(*) FROM effect").fetchone()[0] == 0
    finally:
        raw.close()


def test_a_caught_write_failure_does_not_break_the_next_transaction(opened_store):
    """The poisoned transaction rolls back cleanly, so the store is still usable."""
    with pytest.raises(ProtocolError):
        with opened_store.transaction() as txn:
            with pytest.raises(sqlite3.IntegrityError):
                txn.insert_record("tx1", duplicate_effect_spec())
    with opened_store.transaction() as txn:
        txn.insert_record("tx1", one_effect_spec())


@pytest.mark.parametrize("bad", [3, None, b"tx", "../escape", "", "x" * 65])
def test_every_setter_validates_the_txid(opened_store, bad):
    with opened_store.transaction() as txn:
        with pytest.raises(ProtocolError):
            txn.set_transaction_state(bad, TransactionState.APPLYING)


def test_setting_a_journal_state_updates_exactly_one_row(opened_store, store_binding):
    with opened_store.transaction() as txn:
        txn.insert_record("tx1", one_effect_spec(effect_id="only"))
        txn.set_journal_state("tx1", "only", JournalState.STARTED)
    raw = raw_connect(store_binding)
    try:
        assert raw.execute(
            "SELECT journal_state FROM effect WHERE txid = 'tx1'"
        ).fetchone() == ("started",)
    finally:
        raw.close()


def test_setting_a_journal_state_for_an_unknown_effect_refuses(opened_store):
    with pytest.raises(ProtocolError) as caught:
        with opened_store.transaction() as txn:
            txn.insert_record("tx1", one_effect_spec(effect_id="only"))
            txn.set_journal_state("tx1", "ghost", JournalState.STARTED)
    assert "ghost" in str(caught.value)


def test_set_active_enforces_the_single_active_row(opened_store, store_binding):
    with opened_store.transaction() as txn:
        txn.insert_record("tx1", one_effect_spec())
        txn.insert_record("tx2", replace_spec())
        txn.set_active("tx1")
        txn.set_active("tx2")
    raw = raw_connect(store_binding)
    try:
        assert raw.execute("SELECT singleton, txid FROM active").fetchall() == [(0, "tx2")]
    finally:
        raw.close()


def test_set_active_none_clears_the_row(opened_store, store_binding):
    with opened_store.transaction() as txn:
        txn.insert_record("tx1", one_effect_spec())
        txn.set_active("tx1")
        txn.set_active(None)
    raw = raw_connect(store_binding)
    try:
        assert raw.execute("SELECT count(*) FROM active").fetchone() == (0,)
    finally:
        raw.close()


def test_set_active_refuses_a_transaction_that_does_not_exist(opened_store):
    """foreign_keys=ON makes active.txid a real reference, so `active` can never name a
    transaction that does not exist -- the shape authority §7.3 promises recovery will
    never see (design §6.2)."""
    with pytest.raises(Exception):
        with opened_store.transaction() as txn:
            txn.set_active("never-inserted")


@pytest.mark.parametrize("value", [42.5, "abc", b"x"])
def test_strict_typing_refuses_a_value_that_cannot_convert(opened_store, store_binding, value):
    """STRICT coerces losslessly -- '42' and 42.0 both store as integer 42 -- so the test
    uses the values that actually raise (design §6.2)."""
    raw = raw_connect(store_binding)
    try:
        with pytest.raises(Exception):
            raw.execute("INSERT INTO blob (digest, byte_len) VALUES (?, ?)", ("sha256:" + "a" * 64, value))
    finally:
        raw.close()
```

- [ ] **Step 2: Add the `store_binding` fixture**

Append to `tests/conftest.py`:

```python
@pytest.fixture
def store_binding(request):
    """The ProjectBinding behind `opened_store`, for asserting on durable rows directly.

    Depends on the same fixture instance rather than building a second volume, so the two
    always name one store.
    """
    return request.getfixturevalue("opened_store")._binding
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_store_records.py -k "insert_record or setter or set_active" -v`
Expected: FAIL with `AttributeError: '_StoreTransaction' object has no attribute 'insert_record'`.

- [ ] **Step 4: Add the name gate and the writers**

Append to `src/atoms/store/records.py`:

```python
from atoms.core.canonical import canonical_json
from atoms.core.errors import ProtocolError
from atoms.core.identifiers import is_valid_identifier
from atoms.core.spec import TransactionSpec
from atoms.store.schema import variant_of

INSERT_RECORD = (
    "INSERT INTO transaction_record "
    "(txid, spec_json, state, committed, rollback_result, halt_diagnostic) "
    "VALUES (?, ?, ?, ?, NULL, NULL)"
)
INSERT_EFFECT = (
    "INSERT INTO effect (txid, effect_id, variant, journal_state) VALUES (?, ?, ?, ?)"
)
UPDATE_STATE = "UPDATE transaction_record SET state = ? WHERE txid = ?"
UPDATE_COMMITTED = "UPDATE transaction_record SET committed = ? WHERE txid = ?"
UPDATE_ROLLBACK_RESULT = "UPDATE transaction_record SET rollback_result = ? WHERE txid = ?"
UPDATE_HALT_DIAGNOSTIC = "UPDATE transaction_record SET halt_diagnostic = ? WHERE txid = ?"
UPDATE_JOURNAL_STATE = (
    "UPDATE effect SET journal_state = ? WHERE txid = ? AND effect_id = ?"
)
UPSERT_ACTIVE = (
    "INSERT INTO active (singleton, txid) VALUES (0, ?) "
    "ON CONFLICT(singleton) DO UPDATE SET txid = excluded.txid"
)
DELETE_ACTIVE = "DELETE FROM active"


def require_identifier(label: str, value: object) -> str:
    """Design §5.5, in two steps and in this order.

    `require_valid_identifier` raises SpecValidationError for '../x' and a raw TypeError
    for 3, None, and b'tx' -- measured for all four -- because it hands its argument
    straight to a compiled pattern. A TypeError from inside a validator is
    indistinguishable from a bug in A5a's own code, and it is the response to the exact
    input a hostile caller supplies. So A5a reuses A1's *predicate* and supplies its own
    refusal.

    Step 1 is what makes step 2 total: is_valid_identifier is only safe to call once the
    argument is known to be a str. Exact type, per the A4b precedent at approval.py:96 --
    a str subclass passes an isinstance gate and can then behave differently at the
    syscall.
    """
    if type(value) is not str:
        raise ProtocolError(
            f"{label} must be exactly str, got {type(value).__name__}"
        )
    if not is_valid_identifier(value):
        raise ProtocolError(
            f"{label} {value!r} is not 1-64 characters of [A-Za-z0-9_-]"
        )
    return value
```

Append to `src/atoms/store/connection.py`, inside `_StoreTransaction`:

```python
    def insert_record(self, txid: str, spec: TransactionSpec) -> None:
        with self._mutating() as store:
            require_identifier("txid", txid)
            if type(spec) is not TransactionSpec:
                raise ProtocolError(
                    f"spec must be exactly TransactionSpec, got {type(spec).__name__}"
                )
            with translated("inserting a record"):
                store._connection.execute(
                    INSERT_RECORD,
                    (
                        txid,
                        canonical_json(spec),
                        TransactionState.PREPARED.value,
                        CommitDecision.UNCOMMITTED.value,
                    ),
                )
                for effect in spec.effects:
                    store._connection.execute(
                        INSERT_EFFECT,
                        (txid, effect.effect_id, variant_of(effect).value,
                         JournalState.PENDING.value),
                    )
            self._touched.add(txid)

    def _set_column(self, statement: str, txid: str, value: object) -> None:
        with self._mutating() as store:
            require_identifier("txid", txid)
            with translated("updating a record"):
                cursor = store._connection.execute(statement, (value, txid))
            if cursor.rowcount != 1:
                raise ProtocolError(
                    f"no transaction_record row for txid {txid!r}"
                )
            self._touched.add(txid)

    def set_transaction_state(self, txid: str, state: TransactionState) -> None:
        self._set_column(UPDATE_STATE, txid, state.value)

    def set_commit_decision(self, txid: str, decision: CommitDecision) -> None:
        self._set_column(UPDATE_COMMITTED, txid, decision.value)

    def set_rollback_result(self, txid: str, result: RollbackResult) -> None:
        self._set_column(UPDATE_ROLLBACK_RESULT, txid, result.value)

    def set_halt_diagnostic(self, txid: str, diagnostic: HaltDiagnostic) -> None:
        self._set_column(UPDATE_HALT_DIAGNOSTIC, txid, encode_diagnostic(diagnostic))

    def set_journal_state(self, txid: str, effect_id: str, state: JournalState) -> None:
        with self._mutating() as store:
            require_identifier("txid", txid)
            require_identifier("effect_id", effect_id)
            with translated("updating a journal state"):
                cursor = store._connection.execute(
                    UPDATE_JOURNAL_STATE, (state.value, txid, effect_id)
                )
            if cursor.rowcount != 1:
                raise ProtocolError(
                    f"no effect row for txid {txid!r} effect_id {effect_id!r}"
                )
            self._touched.add(txid)

    def set_active(self, txid: str | None) -> None:
        with self._mutating() as store:
            if txid is None:
                with translated("clearing the active transaction"):
                    store._connection.execute(DELETE_ACTIVE)
                return
            require_identifier("txid", txid)
            with translated("setting the active transaction"):
                store._connection.execute(UPSERT_ACTIVE, (txid,))
            self._touched.add(txid)
```

**Every method above opens with `with self._mutating() as store:`** and none calls `_require_current`
directly. That context manager is design §7.7's poison rule (Task 5): it checks ownership on the way in
and, on the way out, records the first exception that escaped so the transaction cannot commit even if
the caller swallows it. The four one-line setters delegate to `_set_column`, which opens it for them;
Task 13's guard accepts exactly that shape and nothing looser.

Add the corresponding imports to `connection.py`: `TransactionSpec`, `TransactionState`,
`CommitDecision`, `JournalState`, `RollbackResult`, `HaltDiagnostic`, `canonical_json`, and
from `atoms.store.records` the SQL constants, `require_identifier`, `encode_diagnostic`,
`variant_of`.

`records.py` must not import `connection.py` — the SQL and the validators live in `records.py`
and the methods that issue them live on the transaction object, which is `connection.py`'s.

- [ ] **Step 5: Run the tests and the gates**

```bash
uv run pytest tests/test_store_records.py -v
uv run ruff check src/atoms/store tests/test_store_records.py tests/conftest.py
uv run pyright
```
Expected: PASS, `All checks passed!`, `0 errors`.

- [ ] **Step 6: Commit**

```bash
git add src/atoms/store tests/test_store_records.py tests/conftest.py
git commit -m "feat(store): derive every effect row from the spec it was inserted with"
```

---

## Task 8: Reads, the coherence predicate, and the pre-COMMIT barrier

**Files:**
- Modify: `src/atoms/store/records.py`
- Modify: `src/atoms/store/connection.py`
- Modify: `tests/test_store_records.py`
- Modify: `tests/test_store_liveness.py`

**Interfaces:**
- Consumes: `compile_spec` from `atoms.core.compiler`; `from_canonical_json` from
  `atoms.core.canonical`; Task 7's writers.
- Produces: `StoredRecord` (frozen); `coherence_findings(connection, txid) -> tuple[str, ...]`;
  `journal_vector(spec, rows) -> tuple[EffectJournalState, ...]`; `Store.read_record(txid)`,
  `Store.read_active()`; a filled-in `_StoreTransaction._run_barrier`.

**One predicate, two verdicts.** On a load a violation is durable state that cannot be interpreted:
`MetadataStoreInvalid`. On a write the same violation is a caller assembling an incoherent record,
caught before anything is durable: `ProtocolError`. So the shared code returns a structured finding and
each site raises its own type — rather than the predicate raising and the write path catching and
relabelling. Authority §11 settles it: `MetadataStoreInvalid` is "raised by the store layer, never as a
substitute for `ProtocolError`."

**The exit sequence is gate → validate → gate → COMMIT.** Validation runs `compile_spec` over every
touched spec and re-reads rows; on a large transaction that is unbounded work, and a single gate
*before* it would prove the lock was held when the exit began rather than when the barrier ran.

**Every read is one transaction, and every exit closes it.** Two `Store` objects may share one live
`ProjectBinding`, and with one writer committing between a reader's queries a transaction-free reader
saw `state = PREPARED` from before the commit and a journal state of `started` from after it — measured.
A read's most likely ending is not `COMMIT` but a decode failure raised from Python after the queries
have run; leaving on that path without a `ROLLBACK` leaves `in_transaction` true, and the next
`BEGIN IMMEDIATE` fails with "cannot start a transaction within a transaction" — also measured.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_store_records.py`:

```python
from atoms.store.errors import MetadataStoreInvalid


def test_a_record_round_trips_through_the_store(opened_store):
    spec = replace_spec(effect_id="e1")
    with opened_store.transaction() as txn:
        txn.insert_record("tx1", spec)
        txn.set_active("tx1")
    record = opened_store.read_record("tx1")
    assert record is not None
    assert record.txid == "tx1"
    assert record.spec == spec
    assert record.state is TransactionState.PREPARED
    assert record.committed is CommitDecision.UNCOMMITTED
    assert record.rollback_result is None
    assert record.halt_diagnostic is None
    assert record.journals == (EffectJournalState(effect_id="e1", state=JournalState.PENDING),)
    assert opened_store.read_active() == record


def test_reading_an_unknown_txid_returns_none(opened_store):
    assert opened_store.read_record("nope") is None


def test_reading_with_no_active_transaction_returns_none(opened_store):
    assert opened_store.read_active() is None


def test_the_journal_vector_follows_spec_order_not_row_order(opened_store):
    """(txid, effect_id) is the primary key, so SQLite returns z,a,m as a,m,z -- measured.
    The vector is reconstructed by walking spec_json (design §7.5)."""
    from atoms.core.effects import CreateFileNoClobber
    from atoms.core.fingerprint import ABSENT
    from atoms.core.spec import build_spec
    from tests.store_support import file_state

    posts = {name: file_state(name.encode()) for name in ("z", "a", "m")}
    spec = build_spec(
        consumer_tag="test",
        intent_digest="sha256:" + "2" * 64,
        initial_surface={f"{n}.txt": ABSENT for n in posts},
        final_surface={f"{n}.txt": posts[n] for n in posts},
        effects=[
            CreateFileNoClobber(effect_id=n, path=f"{n}.txt", post=posts[n])
            for n in ("z", "a", "m")
        ],
    )
    with opened_store.transaction() as txn:
        txn.insert_record("tx1", spec)
    record = opened_store.read_record("tx1")
    assert record is not None
    assert tuple(j.effect_id for j in record.journals) == tuple(
        e.effect_id for e in spec.effects
    )


@pytest.mark.parametrize(
    ("corrupt", "fragment"),
    [
        ("UPDATE transaction_record SET state = 'rolled_back' WHERE txid = 'tx1'", "rollback_result"),
        ("UPDATE transaction_record SET state = 'halted' WHERE txid = 'tx1'", "halt_diagnostic"),
        ("DELETE FROM effect WHERE txid = 'tx1'", "effect"),
        ("UPDATE effect SET variant = 'delete_path' WHERE txid = 'tx1'", "variant"),
        ("INSERT INTO effect VALUES ('tx1', 'extra', 'delete_path', 'pending')", "effect"),
    ],
)
def test_every_cross_row_rule_refuses_on_a_load(opened_store, store_binding, corrupt, fragment):
    with opened_store.transaction() as txn:
        txn.insert_record("tx1", replace_spec(effect_id="e1"))
    raw = raw_connect(store_binding)
    try:
        raw.execute(corrupt)
    finally:
        raw.close()
    with pytest.raises(MetadataStoreInvalid) as caught:
        opened_store.read_record("tx1")
    assert fragment in str(caught.value)


def test_a_non_canonical_spec_json_refuses_on_a_load(opened_store, store_binding):
    """A record whose stored bytes are not the canonical encoding of what they decode to
    is not a record this engine wrote (design §6.3)."""
    with opened_store.transaction() as txn:
        txn.insert_record("tx1", one_effect_spec())
    raw = raw_connect(store_binding)
    try:
        stored = raw.execute(
            "SELECT spec_json FROM transaction_record WHERE txid = 'tx1'"
        ).fetchone()[0]
        raw.execute("PRAGMA writable_schema = OFF")
        raw.execute("DELETE FROM effect WHERE txid = 'tx1'")
        raw.execute("DELETE FROM transaction_record WHERE txid = 'tx1'")
        raw.execute(
            "INSERT INTO transaction_record VALUES "
            "('tx1', ?, 'prepared', 'uncommitted', NULL, NULL)",
            (" " + stored,),
        )
    finally:
        raw.close()
    with pytest.raises(MetadataStoreInvalid) as caught:
        opened_store.read_record("tx1")
    assert "canonical" in str(caught.value)


def test_the_same_violation_is_a_protocol_error_on_a_write(opened_store, store_binding):
    """Rejected at the barrier, before anything is durable, so the store is exactly as
    valid as it was -- and the caller is told to fix its call, not to preserve evidence."""
    with pytest.raises(ProtocolError) as caught:
        with opened_store.transaction() as txn:
            txn.insert_record("tx1", one_effect_spec())
            txn.set_transaction_state("tx1", TransactionState.ROLLED_BACK)
    assert "rollback_result" in str(caught.value)
    assert opened_store.read_record("tx1") is None


def test_a_refused_commit_leaves_the_pre_transaction_record(opened_store):
    with opened_store.transaction() as txn:
        txn.insert_record("tx1", one_effect_spec())
    with pytest.raises(ProtocolError):
        with opened_store.transaction() as txn:
            txn.set_transaction_state("tx1", TransactionState.HALTED)
    record = opened_store.read_record("tx1")
    assert record is not None
    assert record.state is TransactionState.PREPARED


def test_a_multi_step_sequence_that_stays_coherent_commits(opened_store):
    with opened_store.transaction() as txn:
        txn.insert_record("tx1", one_effect_spec())
    with opened_store.transaction() as txn:
        txn.set_transaction_state("tx1", TransactionState.ROLLED_BACK)
        txn.set_rollback_result("tx1", RollbackResult.RESTORED)
    record = opened_store.read_record("tx1")
    assert record is not None
    assert record.rollback_result is RollbackResult.RESTORED


def test_the_barrier_validates_only_structure_not_transition_legality(opened_store):
    """set_transaction_state(txid, APPLIED) on a record A3 would never advance that way
    still commits; that judgment is A3's, and A5b's to enforce (ledger #12)."""
    with opened_store.transaction() as txn:
        txn.insert_record("tx1", one_effect_spec())
    with opened_store.transaction() as txn:
        txn.set_transaction_state("tx1", TransactionState.APPLIED)
    record = opened_store.read_record("tx1")
    assert record is not None
    assert record.state is TransactionState.APPLIED


def test_one_digest_declared_at_two_lengths_refuses(opened_store, store_binding):
    """Design §7.6, criterion 23. compile_spec accepts a spec whose initial surface
    declares one digest at two byte_lens -- measured -- so the store is where it is
    caught, and only if referenced_digests keeps both references. Collapsed into a
    mapping, whichever reference the dict kept would agree with the row and the other
    would vanish."""
    raw = raw_connect(store_binding)
    try:
        raw.execute(
            "INSERT INTO blob (digest, byte_len) VALUES (?, ?)", (SHARED_DIGEST, 6)
        )
    finally:
        raw.close()
    with pytest.raises(ProtocolError) as caught:
        with opened_store.transaction() as txn:
            txn.insert_record("tx1", two_length_spec())
    assert "byte_len" in str(caught.value)


def test_a_halted_record_missing_an_effect_row_reports_findings(
    opened_store, store_binding
):
    """Design §7.6: a finding that depends on an earlier one is skipped once that one
    fires. The diagnostic's journal vector is indexed by every effect_id in spec_json,
    so comparing it against effect rows that no longer cover spec_json raises KeyError
    -- out of the very function whose job is to report the finding, as an exception type
    the store does not promise."""
    with opened_store.transaction() as txn:
        txn.insert_record("tx1", one_effect_spec(effect_id="only"))
        txn.set_transaction_state("tx1", TransactionState.HALTED)
        txn.set_halt_diagnostic("tx1", matching_diagnostic("only"))
    raw = raw_connect(store_binding)
    try:
        raw.execute("DELETE FROM effect WHERE txid = 'tx1'")
    finally:
        raw.close()
    with pytest.raises(MetadataStoreInvalid) as caught:
        opened_store.read_record("tx1")
    assert "effect row missing for effect_id 'only'" in str(caught.value)


def test_a_read_inside_a_write_transaction_is_refused(opened_store):
    with opened_store.transaction() as txn:
        txn.insert_record("tx1", one_effect_spec())
        with pytest.raises(ProtocolError):
            opened_store.read_record("tx1")


def test_a_failed_read_leaves_no_open_transaction(opened_store, store_binding):
    with opened_store.transaction() as txn:
        txn.insert_record("tx1", one_effect_spec())
    raw = raw_connect(store_binding)
    try:
        raw.execute("DELETE FROM effect WHERE txid = 'tx1'")
    finally:
        raw.close()
    with pytest.raises(MetadataStoreInvalid):
        opened_store.read_record("tx1")
    with opened_store.transaction() as txn:
        txn.set_active(None)


def test_a_read_takes_one_snapshot_across_a_concurrent_commit(store_on):
    """Design §7.4, criterion 15: one snapshot per read, proved against a writer that
    commits **between the queries of a single load** -- not before it and not after it.

    Reading once, committing, and reading again proves only that a later read sees later
    data, which an unwrapped read also does. What has to be excluded is the torn read: a
    load whose record row predates the commit and whose effect rows postdate it. The
    commit is injected from sqlite3's trace callback on the reader's own connection,
    which fires immediately before each statement, so the interleaving is deterministic
    rather than a race the test hopes to win.

    Measured against this exact shape: inside a deferred BEGIN the load returns
    (prepared, pending) and a later read returns (applying, started); with the same
    injection and no read transaction the load returns (prepared, **started**) -- the
    torn pair. Two Store objects may share one live ProjectBinding, and each holds its
    own connection.
    """
    from atoms.store.connection import open_store

    with store_on() as binding:
        with open_store(binding) as writer, open_store(binding) as reader:
            with writer.transaction() as txn:
                txn.insert_record("tx1", one_effect_spec(effect_id="e1"))

            fired: list[str] = []

            def commit_between(statement: str) -> None:
                # The trace callback receives the EXPANDED statement -- 'txid = ''tx1'''
                # rather than 'txid = ?' (measured), so this matches the table, not the
                # constant. Fire once: the load queries `effect` more than once.
                if "FROM effect" not in statement or fired:
                    return
                fired.append(statement)
                with writer.transaction() as inner:
                    inner.set_journal_state("tx1", "e1", JournalState.STARTED)
                    inner.set_transaction_state("tx1", TransactionState.APPLYING)

            reader._connection.set_trace_callback(commit_between)
            try:
                during = reader.read_record("tx1")
            finally:
                reader._connection.set_trace_callback(None)
            assert fired, "the trace callback never saw the effect query"
            after = reader.read_record("tx1")

    assert during is not None and after is not None
    assert (during.state, during.journals[0].state) == (
        TransactionState.PREPARED, JournalState.PENDING
    )
    assert (after.state, after.journals[0].state) == (
        TransactionState.APPLYING, JournalState.STARTED
    )
```

Append to `tests/test_store_liveness.py`:

```python
def test_the_lock_released_between_the_last_write_and_the_exit_rolls_back(store_on):
    """The case the per-operation gate misses. §5.4's late gate closes it, and the
    assertion is on the durable state, not just the exception."""
    from atoms.store.connection import open_store
    from tests.store_support import one_effect_spec, raw_connect

    with store_on(withhold=False) as binding:
        store = open_store(binding)
        with pytest.raises(ProtocolError):
            with store.transaction() as txn:
                txn.insert_record("tx1", one_effect_spec())
                binding.__exit__(None, None, None)
        store.close()
    raw = raw_connect(binding)
    try:
        assert raw.execute("SELECT count(*) FROM transaction_record").fetchone() == (0,)
    finally:
        raw.close()


def test_the_lock_released_during_a_load_returns_no_record(store_on, monkeypatch):
    from atoms.store import records as records_module
    from atoms.store.connection import open_store
    from tests.store_support import one_effect_spec

    with store_on() as binding:
        store = open_store(binding)
        with store.transaction() as txn:
            txn.insert_record("tx1", one_effect_spec())

        real = records_module.coherence_findings

        def release_then_check(connection, txid):
            binding.__exit__(None, None, None)
            return real(connection, txid)

        monkeypatch.setattr(records_module, "coherence_findings", release_then_check)
        with pytest.raises(ProtocolError):
            store.read_record("tx1")
        store.close()
```

`store_on(withhold=...)` mirrors `approval_context`'s existing parameter; check its signature
and use the same spelling. If the fixture cannot release the lock mid-test, add a
`release_lock(binding)` helper to `tests/store_support.py` that calls whatever
`ext4_bound_volume` uses internally, and route both tests through it.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_store_records.py -k "round_trips or cross_row or barrier" -v`
Expected: FAIL with `AttributeError: 'Store' object has no attribute 'read_record'`.

- [ ] **Step 3: Add the predicate and the reader**

Append to `src/atoms/store/records.py`:

```python
from dataclasses import dataclass

from atoms.core.canonical import from_canonical_json
from atoms.core.compiler import compile_spec
from atoms.core.errors import SpecValidationError

SELECT_RECORD = (
    "SELECT spec_json, state, committed, rollback_result, halt_diagnostic "
    "FROM transaction_record WHERE txid = ?"
)
SELECT_EFFECTS = "SELECT effect_id, variant, journal_state FROM effect WHERE txid = ?"
SELECT_BLOB = "SELECT byte_len FROM blob WHERE digest = ?"
SELECT_ACTIVE = "SELECT txid FROM active"


@dataclass(frozen=True, slots=True)
class StoredRecord:
    """A plain value, not a live cursor (design §7.1)."""

    txid: str
    spec: TransactionSpec
    state: TransactionState
    committed: CommitDecision
    rollback_result: RollbackResult | None
    halt_diagnostic: HaltDiagnostic | None
    journals: tuple[EffectJournalState, ...]


def referenced_digests(spec: TransactionSpec) -> tuple[tuple[str, int], ...]:
    """Every (digest, byte_len) this record needs a blob for, deduplicated but not merged.

    The **initial** surface only. Blobs are rollback material: authority §7.3 step 3 has
    A6 stream the captured *pre*-state of each path into staging and step 4 promote it.
    A final-surface hash names content the transaction will produce, for which no blob
    exists or should -- including it here would make every record refuse until something
    promoted a blob for content that does not exist yet.

    **Pairs, not a mapping.** A hash and a length are both properties of the same bytes,
    so two paths carrying one digest must carry one length -- but compile_spec accepts a
    spec where they do not (measured: two initial entries at digest X with byte_len 5 and
    6 compile clean). Keyed by digest, the second reference silently overwrites the first
    and §7.6 then checks the blob row against whichever survived. As pairs both survive,
    at most one can match the row, and the disagreement refuses with no special case.

    Public rather than private because connection.py's pre-COMMIT barrier reads it too.
    """
    return tuple(sorted({
        (entry.state.content_hash, entry.state.byte_len)
        for entry in spec.initial_surface
        if isinstance(entry.state, FileState)
    }))


def coherence_findings(connection: Any, txid: str) -> tuple[str, ...]:
    """Design §7.6, as a list of findings rather than a raise.

    One function used from two sides, and the two sides mean different things: a load
    raises MetadataStoreInvalid, a write raises ProtocolError. The predicate returns; each
    site chooses.

    These are checks for malformed persisted input, not transition judgments. A journal
    vector that is legal SQL and legal encoding but describes an impossible history is
    left for A3 to classify -- pre-empting it here would put a second classifier in the
    store.
    """
    row = connection.execute(SELECT_RECORD, (txid,)).fetchone()
    if row is None:
        return ()
    spec_json, state_value, committed_value, rollback_value, diagnostic_text = row
    findings: list[str] = []

    try:
        spec = from_canonical_json(spec_json)
    except (SpecValidationError, ValueError) as caught:
        return (f"spec_json does not decode: {caught}",)
    if canonical_json(spec) != spec_json:
        findings.append(
            "spec_json is not the canonical encoding of what it decodes to"
        )
    try:
        compile_spec(spec)
    except SpecValidationError as caught:
        findings.append(f"spec_json is a spec A2 would refuse: {caught}")

    rows = connection.execute(SELECT_EFFECTS, (txid,)).fetchall()
    stored = {effect_id: (variant, journal) for effect_id, variant, journal in rows}
    declared = {effect.effect_id: variant_of(effect).value for effect in spec.effects}
    covered = set(declared) == set(stored)
    for effect_id in sorted(set(declared) - set(stored)):
        findings.append(f"effect row missing for effect_id {effect_id!r}")
    for effect_id in sorted(set(stored) - set(declared)):
        findings.append(f"effect row {effect_id!r} is not in spec_json")
    for effect_id in sorted(set(declared) & set(stored)):
        if stored[effect_id][0] != declared[effect_id]:
            findings.append(
                f"effect {effect_id!r} has variant {stored[effect_id][0]!r}, "
                f"spec_json says {declared[effect_id]!r}"
            )

    for digest, byte_len in referenced_digests(spec):
        blob = connection.execute(SELECT_BLOB, (digest,)).fetchone()
        if blob is None:
            findings.append(f"no blob row for referenced digest {digest!r}")
        elif blob[0] != byte_len:
            findings.append(
                f"blob {digest!r} has byte_len {blob[0]}, the record declares {byte_len}"
            )

    state = TransactionState(state_value)
    if (state is TransactionState.ROLLED_BACK) != (rollback_value is not None):
        findings.append(
            f"rollback_result must be present exactly when state is rolled_back; "
            f"state={state_value!r}, rollback_result={rollback_value!r}"
        )
    if (state is TransactionState.HALTED) != (diagnostic_text is not None):
        findings.append(
            f"halt_diagnostic must be present exactly when state is halted; "
            f"state={state_value!r}"
        )
    if diagnostic_text is not None:
        diagnostic = decode_diagnostic(diagnostic_text)
        if diagnostic.commit_decision.value != committed_value:
            findings.append(
                "the diagnostic's commit_decision disagrees with the durable row"
            )
        # Only when coverage holds. _journal_vector indexes `stored` by every effect_id
        # in spec_json, so on a record that is already missing an effect row it would
        # raise KeyError -- turning a reported finding into an exception of a type the
        # store does not promise, out of the loader that exists to report findings.
        if covered:
            if diagnostic.journals != _journal_vector(spec, stored):
                findings.append(
                    "the diagnostic's journal vector disagrees with the durable rows"
                )
        else:
            findings.append(
                "the diagnostic's journal vector cannot be compared: the effect rows do "
                "not cover spec_json"
            )

    active = connection.execute(SELECT_ACTIVE).fetchone()
    if active is not None:
        exists = connection.execute(SELECT_RECORD, (active[0],)).fetchone()
        if exists is None:
            findings.append(f"active names txid {active[0]!r}, which has no record")

    return tuple(findings)


def _journal_vector(
    spec: TransactionSpec, stored: dict[str, tuple[str, str]]
) -> tuple[EffectJournalState, ...]:
    """Walk spec_json, never row order.

    (txid, effect_id) is the primary key, so SQLite returns rows in effect_id order --
    'z', 'a', 'm' comes back 'a', 'm', 'z', measured. The schema carries no ordinal
    column because that would make the order a second fact to keep in agreement.
    """
    return tuple(
        EffectJournalState(
            effect_id=effect.effect_id,
            state=JournalState(stored[effect.effect_id][1]),
        )
        for effect in spec.effects
    )


def load_record(connection: Any, txid: str) -> StoredRecord | None:
    """Assemble one record. The caller has already opened the read transaction."""
    row = connection.execute(SELECT_RECORD, (txid,)).fetchone()
    if row is None:
        return None
    findings = coherence_findings(connection, txid)
    if findings:
        raise MetadataStoreInvalid(
            f"the record for txid {txid!r} cannot be interpreted: " + "; ".join(findings)
        )
    spec_json, state_value, committed_value, rollback_value, diagnostic_text = row
    spec = from_canonical_json(spec_json)
    rows = connection.execute(SELECT_EFFECTS, (txid,)).fetchall()
    stored = {effect_id: (variant, journal) for effect_id, variant, journal in rows}
    return StoredRecord(
        txid=txid,
        spec=spec,
        state=TransactionState(state_value),
        committed=CommitDecision(committed_value),
        rollback_result=(
            None if rollback_value is None else RollbackResult(rollback_value)
        ),
        halt_diagnostic=(
            None if diagnostic_text is None else decode_diagnostic(diagnostic_text)
        ),
        journals=_journal_vector(spec, stored),
    )
```

Append to `src/atoms/store/connection.py`, on `Store`:

```python
    @contextmanager
    def _read_transaction(self) -> Iterator[sqlite3.Connection]:
        """One deferred read transaction, and every exit closes it (design §7.4).

        BEGIN deferred rather than IMMEDIATE: a reader takes no write lock and must not
        block A5b's writer.
        """
        self._require_live()
        if self._active_transaction is not None:
            raise ProtocolError(
                "a public read while this store owns a write transaction would report as "
                "durable a row a rollback erases"
            )
        with translated("beginning a read"):
            self._connection.execute(_BEGIN_DEFERRED)
        try:
            yield self._connection
        except BaseException:
            self._connection.execute(_ROLLBACK)
            raise
        else:
            with translated("ending a read"):
                self._connection.execute(_COMMIT)

    def read_record(self, txid: str) -> StoredRecord | None:
        require_identifier("txid", txid)
        with self._read_transaction() as connection:
            record = load_record(connection, txid)
        self._require_live()
        return record

    def read_active(self) -> StoredRecord | None:
        with self._read_transaction() as connection:
            row = connection.execute(SELECT_ACTIVE).fetchone()
            record = None if row is None else load_record(connection, row[0])
        self._require_live()
        return record
```

Add `_BEGIN_DEFERRED = "BEGIN"` beside the other statement constants.

Fill in `_StoreTransaction._run_barrier`:

```python
    def _run_barrier(self) -> None:
        """Design §7.7 -- the same predicate, before COMMIT, over every touched txid.

        The exit sequence is gate -> validate -> gate -> COMMIT, and Store.transaction()
        runs the second gate. Validating only on read would be the wrong half: the store
        would accept the write, make it durable, and report the defect to whichever
        process reopens the database, with the writer long gone. A5a would have
        manufactured the corruption it reports.
        """
        store = self._require_current()
        for txid in sorted(self._touched):
            findings = coherence_findings(store._connection, txid)
            if findings:
                raise ProtocolError(
                    f"the record for txid {txid!r} would not be coherent: "
                    + "; ".join(findings)
                )
        for digest in sorted(digest for digests in self._promoted.values() for digest in digests):
            if store._connection.execute(SELECT_BLOB, (digest,)).fetchone() is None:
                raise ProtocolError(
                    f"digest {digest!r} was promoted in this transaction but has no "
                    "blob row"
                )
```

`self._promoted` is a `dict[str, set[str]]` on `_StoreTransaction` — **workspace txid to the digests
promoted from it** — initialized to `{}` beside `_touched`, with `"_promoted"` added to that class's
`__slots__`. Task 11 populates it. It is keyed by txid
rather than flat because criterion 27 binds each promoted digest to *the record for that workspace's
txid*: against one merged set, a blob promoted from `tx1`'s workspace would be justified by a record
`tx2` written in the same transaction, which is a different claim and a weaker one.

The loop above is written now so that Task 11 adds no code to the barrier — but note that with
promotion writing its own rows (Task 11), it can never fire; it is retained only as the barrier's
statement of the invariant, and Task 11 replaces it with the *record-reference* check the design
actually requires. Do not leave both.

- [ ] **Step 4: Run the tests and the gates**

```bash
uv run pytest tests/test_store_records.py tests/test_store_liveness.py -v
uv run ruff check src/atoms/store tests/test_store_records.py tests/test_store_liveness.py
uv run pyright
```
Expected: PASS, `All checks passed!`, `0 errors`.

**Every test in `test_store_records.py` that commits a `replace_spec` record** now needs a `blob`
row that no task has written yet — `replace_spec`'s `pre` is a `FileState` whose content hash has no
row, so `coherence_findings` reports "no blob row for referenced digest" and the barrier refuses.
Run the file, take the list from the failures rather than from this paragraph, and **insert the rows
directly** in each for now:

```python
    raw = raw_connect(store_binding)
    try:
        raw.execute(
            "INSERT INTO blob (digest, byte_len) VALUES (?, ?)", (digest_of(b"before"), 6)
        )
    finally:
        raw.close()
```

Only the **initial**-surface digest needs a row — `replace_spec`'s `pre` — because that is the
one `referenced_digests` reports. `one_effect_spec` starts from `ABSENT` and needs none, which
is why the tests that use it commit without any blob at all.

Task 11 replaces those inserts with a real promotion; leave a `# Task 11: promote instead`
comment on each so they are easy to find, and remove the comments there.

- [ ] **Step 5: Commit**

```bash
git add src/atoms/store tests/test_store_records.py tests/test_store_liveness.py
git commit -m "feat(store): run one coherence predicate on both sides of the store"
```

---

## Task 9: Workspaces

**Files:**
- Create: `src/atoms/store/workspace.py`
- Modify: `src/atoms/store/connection.py`
- Create: `tests/test_store_workspace.py`
- Modify: `tests/conftest.py`

**Interfaces:**
- Consumes: `require_identifier` from `records.py`; `gate` from `connection.py`;
  `Backend.flush_directory`.
- Produces: `Workspace` (a resource, token-guarded, unfrozen); `create_workspace(store, txid)`,
  `reopen_workspace(store, txid)`, `list_workspaces(store)`, `remove_workspace(store, workspace)`,
  and the `Store` methods that delegate to them.

**`Workspace` is a resource, not a value, so it is not frozen** — the distinction `ProjectBinding`
already draws in the same words at `binding.py:81`. It owns two descriptors and has at least two
mutable states; freezing it would force the spent flags out through `object.__setattr__`, writing to a
frozen object to record that it changed.

**Both descriptors are exposed, as borrowed anchors.** Authority §7.3 step 1 has A6 stream captures
into `staging/<txid>/` and §9.5 has A7 build `CreateDirectory.WORK` out of `work/<txid>/`; neither is
expressible through create/remove/promote alone. Owned by the workspace, borrowed by the consumer,
never closed by the consumer.

**Reopening accepts three dispositions**, because a successful promotion removes `staging/<txid>/` and
leaves work-only, and a crash between the two `mkdir`s or the two `rmdir`s leaves either side alone.
The missing half is modeled as **spent** rather than as a different kind of workspace.

| On disk | `staging_fd` | `work_fd` |
| --- | --- | --- |
| both | live | live |
| `staging/<txid>/` only | live | spent |
| `work/<txid>/` only | spent | live |
| neither | `ProtocolError` — nothing to reopen |

- [ ] **Step 1: Write the failing tests**

First add the descriptor helper to `tests/store_support.py` — every test from here on that opens
`staging`, `work`, or `blobs/sha256` directly uses it, and a bare `os.open` in an assertion leaks the
descriptor for the rest of the session. Add `from contextlib import contextmanager` to that file:

```python
@contextmanager
def child_dir(root_fd: int, name: str):
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC, dir_fd=root_fd)
    try:
        yield fd
    finally:
        os.close(fd)
```

Then create `tests/test_store_workspace.py`:

```python
"""Tier 4 -- workspaces over a real ext4 metadata root (design §8.3, §8.5)."""

from __future__ import annotations

import copy
import os
import pickle

import pytest

from atoms.core.errors import ProtocolError
from atoms.store.errors import MetadataStoreInvalid
from atoms.store.workspace import Workspace
from tests.store_support import child_dir, stage


def test_creation_makes_both_directories(opened_store, store_binding):
    with opened_store.create_workspace("tx1") as workspace:
        assert workspace.txid == "tx1"
        for parent in ("staging", "work"):
            with child_dir(store_binding.metadata_root_fd, parent) as parent_fd:
                assert "tx1" in os.listdir(parent_fd)


def test_creation_refuses_when_either_directory_exists(opened_store):
    opened_store.create_workspace("tx1").close()
    with pytest.raises(ProtocolError):
        opened_store.create_workspace("tx1")


def test_the_anchors_are_usable_by_a6_and_a7(opened_store):
    with opened_store.create_workspace("tx1") as workspace:
        stage(workspace, "capture", b"bytes")
        os.mkdir("built", dir_fd=workspace.work_fd)
        assert "capture" in os.listdir(workspace.staging_fd)
        assert "built" in os.listdir(workspace.work_fd)


def test_reading_an_anchor_after_close_raises_rather_than_returning_a_stale_integer(opened_store):
    workspace = opened_store.create_workspace("tx1")
    workspace.close()
    with pytest.raises(ProtocolError):
        workspace.staging_fd
    with pytest.raises(ProtocolError):
        workspace.work_fd


def test_close_is_idempotent(opened_store):
    workspace = opened_store.create_workspace("tx1")
    workspace.close()
    workspace.close()


@pytest.mark.parametrize("disposition", ["both", "staging", "work"])
def test_reopen_accepts_every_legal_disposition(opened_store, store_binding, disposition):
    opened_store.create_workspace("tx1").close()
    root = store_binding.metadata_root_fd
    if disposition == "staging":
        os.rmdir("work/tx1", dir_fd=root)
    if disposition == "work":
        os.rmdir("staging/tx1", dir_fd=root)
    with opened_store.reopen_workspace("tx1") as workspace:
        if disposition in ("both", "staging"):
            assert isinstance(workspace.staging_fd, int)
        else:
            with pytest.raises(ProtocolError):
                workspace.staging_fd
        if disposition in ("both", "work"):
            assert isinstance(workspace.work_fd, int)
        else:
            with pytest.raises(ProtocolError):
                workspace.work_fd


def test_reopen_refuses_when_neither_directory_exists(opened_store):
    with pytest.raises(ProtocolError):
        opened_store.reopen_workspace("tx1")


def test_reopen_refuses_a_symlink_at_a_workspace_name(opened_store, store_binding, tmp_path):
    """Design §8.3. reopen is reachable without the enumeration that would have caught
    this -- A5b arrives holding a txid read out of a record -- so the check has to be in
    the open. Reproduced with the flags this call would otherwise use: the symlink opened
    cleanly and listed the target's contents, and that descriptor is a mutation anchor A6
    stages captures through.
    """
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "someone-elses-file").write_bytes(b"x")
    with child_dir(store_binding.metadata_root_fd, "staging") as parent_fd:
        os.symlink(str(outside), "tx1", dir_fd=parent_fd)
    with pytest.raises(MetadataStoreInvalid) as caught:
        opened_store.reopen_workspace("tx1")
    assert "not a directory" in str(caught.value)
    with child_dir(store_binding.metadata_root_fd, "staging") as parent_fd:
        assert "tx1" in os.listdir(parent_fd)
    assert (outside / "someone-elses-file").exists()


def test_reopen_refuses_a_regular_file_at_a_workspace_name(opened_store, store_binding):
    with child_dir(store_binding.metadata_root_fd, "work") as parent_fd:
        os.close(os.open("tx1", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600, dir_fd=parent_fd))
    with pytest.raises(MetadataStoreInvalid):
        opened_store.reopen_workspace("tx1")


def test_close_closes_every_workspace_the_store_issued(opened_store):
    """Design §7.8: close() closes the connection A5a opened and any Workspace
    descriptors still outstanding. A store that tracks none leaves them open for the
    process's lifetime, holding directories the next lease owner may be reclaiming."""
    first = opened_store.create_workspace("tx1")
    second = opened_store.create_workspace("tx2")
    second.close()
    opened_store.close()
    # The ProtocolError alone would pass on a store that tracks nothing -- the anchor
    # property gates on the store being live. The descriptors are the claim.
    assert first._closed
    assert (first._staging_fd, first._work_fd) == (None, None)
    with pytest.raises(ProtocolError):
        first.staging_fd
    first.close()


def test_every_txid_list_workspaces_reports_is_one_reopen_accepts(opened_store, store_binding):
    opened_store.create_workspace("tx1").close()
    opened_store.create_workspace("tx2").close()
    os.rmdir("work/tx2", dir_fd=store_binding.metadata_root_fd)
    for txid in opened_store.list_workspaces():
        opened_store.reopen_workspace(txid).close()
    assert set(opened_store.list_workspaces()) == {"tx1", "tx2"}


def test_removal_empties_a_non_empty_staging_directory(opened_store, store_binding):
    """The ordinary pre-promotion crash: authority §7.3 step 3 streams every capture in
    and step 4 promotes only once the surface is complete, so rmdir alone reclaims a case
    that does not happen."""
    with opened_store.create_workspace("tx1") as workspace:
        for name in ("a", "b", "c"):
            stage(workspace, name, name.encode())
        opened_store.remove_workspace(workspace)
    assert opened_store.list_workspaces() == ()
    with child_dir(store_binding.metadata_root_fd, "staging") as parent_fd:
        assert "tx1" not in os.listdir(parent_fd)


def test_removal_refuses_a_non_empty_work_directory(opened_store):
    """Authority §9.5 classifies a work survivor by inode against the live filesystem,
    and that judgment is not a storage mechanism's."""
    with opened_store.create_workspace("tx1") as workspace:
        os.mkdir("partial", dir_fd=workspace.work_fd)
        with pytest.raises(ProtocolError):
            opened_store.remove_workspace(workspace)
    assert "tx1" in opened_store.list_workspaces()


def test_removal_is_not_idempotent(opened_store):
    workspace = opened_store.create_workspace("tx1")
    opened_store.remove_workspace(workspace)
    with pytest.raises(ProtocolError):
        opened_store.remove_workspace(workspace)


@pytest.mark.parametrize("disposition", ["both", "staging", "work"])
def test_removal_succeeds_on_every_legal_disposition(opened_store, store_binding, disposition):
    opened_store.create_workspace("tx1").close()
    root = store_binding.metadata_root_fd
    if disposition == "staging":
        os.rmdir("work/tx1", dir_fd=root)
    if disposition == "work":
        os.rmdir("staging/tx1", dir_fd=root)
    workspace = opened_store.reopen_workspace("tx1")
    opened_store.remove_workspace(workspace)
    assert opened_store.list_workspaces() == ()


def test_a_foreign_or_forged_workspace_is_refused(opened_store, store_on):
    with pytest.raises(TypeError):
        Workspace()
    workspace = opened_store.create_workspace("tx1")
    with pytest.raises(ProtocolError):
        copy.copy(workspace)
    with pytest.raises(ProtocolError):
        copy.deepcopy(workspace)
    with pytest.raises(ProtocolError):
        pickle.dumps(workspace)


def test_a_workspace_from_another_store_over_the_same_binding_is_refused(store_on):
    from atoms.store.connection import open_store

    with store_on() as binding:
        with open_store(binding) as first, open_store(binding) as second:
            workspace = first.create_workspace("tx1")
            with pytest.raises(ProtocolError):
                second.remove_workspace(workspace)
            workspace.close()


def test_removal_preserves_the_whole_directory_when_the_invalid_entry_is_last(opened_store):
    """Validating during the unlink loop satisfies the letter -- every entry is checked --
    while an invalid entry discovered last leaves the earlier captures destroyed and an
    almost-empty directory as its evidence (design §8.3, §8.5)."""
    with opened_store.create_workspace("tx1") as workspace:
        for name in ("a", "b", "c"):
            stage(workspace, name, name.encode())
        os.mkdir("zz-not-a-file", dir_fd=workspace.staging_fd)
        with pytest.raises(MetadataStoreInvalid):
            opened_store.remove_workspace(workspace)
        assert {"a", "b", "c", "zz-not-a-file"} <= set(os.listdir(workspace.staging_fd))


@pytest.mark.parametrize("parent", ["staging", "work"])
def test_enumeration_refuses_an_entry_no_permitted_producer_could_have_written(
    opened_store, store_binding, parent
):
    root = store_binding.metadata_root_fd
    parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY, dir_fd=root)
    try:
        fd = os.open("stray", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600, dir_fd=parent_fd)
        os.close(fd)
        with pytest.raises(MetadataStoreInvalid):
            opened_store.list_workspaces()
        assert "stray" in os.listdir(parent_fd)
    finally:
        os.close(parent_fd)


@pytest.mark.parametrize("parent", ["staging", "work"])
def test_enumeration_refuses_a_directory_whose_name_is_not_a_txid(
    opened_store, store_binding, parent
):
    root = store_binding.metadata_root_fd
    parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY, dir_fd=root)
    try:
        os.mkdir("not a txid", dir_fd=parent_fd)
        with pytest.raises(MetadataStoreInvalid):
            opened_store.list_workspaces()
        assert "not a txid" in os.listdir(parent_fd)
    finally:
        os.close(parent_fd)


@pytest.mark.parametrize("bad", [3, None, b"tx", "../escape", "", "x" * 65])
def test_the_txid_is_validated_before_any_mkdir(opened_store, bad):
    """Guarded traversal prevents the *parent* from being substituted; it does nothing
    about a name being '../x', which is still resolved relative to that descriptor."""
    with pytest.raises(ProtocolError):
        opened_store.create_workspace(bad)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_store_workspace.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'atoms.store.workspace'`.

- [ ] **Step 3: Write the module**

Create `src/atoms/store/workspace.py`:

```python
"""Per-txid scratch namespaces under the engine-owned metadata root (design §8.3)."""

from __future__ import annotations

import errno
import os
import stat
from typing import TYPE_CHECKING, Never

from atoms.core.errors import ProtocolError
from atoms.store.errors import MetadataStoreInvalid
from atoms.store.records import require_identifier

if TYPE_CHECKING:
    from atoms.store.connection import Store

STAGING_PARENT = "staging"
WORK_PARENT = "work"
_WORKSPACE_TOKEN = object()


class Workspace:
    """A live resource: two directory descriptors and their spent flags.

    Created only by Store.create_workspace or Store.reopen_workspace. Both descriptors it
    holds are OWNED by it and closed by close(); every descriptor it EXPOSES is borrowed --
    a consumer must never close one. The metadata_root descriptor it was opened from is
    borrowed in turn and stays A4a's.
    """

    __slots__ = ("_closed", "_staging_fd", "_store", "_txid", "_work_fd")

    def __init__(
        self,
        *,
        store: Store | None = None,
        txid: str = "",
        staging_fd: int | None = None,
        work_fd: int | None = None,
        _construction_token: object | None = None,
    ) -> None:
        if _construction_token is not _WORKSPACE_TOKEN:
            raise TypeError(
                "Workspace values are created only by Store.create_workspace or "
                "Store.reopen_workspace"
            )
        self._store = store
        self._txid = txid
        self._staging_fd = staging_fd
        self._work_fd = work_fd
        self._closed = False

    def __copy__(self) -> Never:
        raise ProtocolError("a copied Workspace would duplicate descriptor ownership")

    def __deepcopy__(self, memo: dict[int, object]) -> Never:
        raise ProtocolError("a copied Workspace would duplicate descriptor ownership")

    def __reduce__(self) -> Never:
        raise ProtocolError("a Workspace owns descriptors and cannot be pickled")

    def __reduce_ex__(self, protocol: int) -> Never:
        raise ProtocolError("a Workspace owns descriptors and cannot be pickled")

    @property
    def txid(self) -> str:
        return self._txid

    def _anchor(self, fd: int | None, label: str) -> int:
        """Both properties gate on liveness, and that is why they are properties.

        A directory descriptor is not an observation; it is standing authority to mutate
        an engine-owned namespace. Handing one out after the lock is released grants that
        authority outside the lease, and A5a never sees the syscalls made with it, so this
        retrieval is the only place the check can happen.
        """
        if self._closed:
            raise ProtocolError(f"this workspace is closed; {label} is gone")
        if fd is None:
            raise ProtocolError(
                f"{label} is spent for txid {self._txid!r}: that half of the workspace "
                "is not on disk, or promotion has already consumed it"
            )
        store = self._store
        if store is None:
            raise ProtocolError("this workspace has no store")
        store._require_live()
        return fd

    @property
    def staging_fd(self) -> int:
        return self._anchor(self._staging_fd, "staging_fd")

    @property
    def work_fd(self) -> int:
        return self._anchor(self._work_fd, "work_fd")

    def spend_staging(self) -> None:
        """Close and mark the staging half spent (design §8.1, §8.2).

        On success this is because step 4 removed the directory, and an open descriptor to
        an unlinked directory is the dangerous kind of still-valid: openat from it succeeds
        and produces files no path can ever name.
        """
        if self._staging_fd is not None:
            os.close(self._staging_fd)
            self._staging_fd = None

    def close(self) -> None:
        """Idempotent, and does not gate on liveness (design §7.8's reason)."""
        if self._closed:
            return
        self._closed = True
        for fd in (self._staging_fd, self._work_fd):
            if fd is not None:
                os.close(fd)
        self._staging_fd = None
        self._work_fd = None
        self._store._workspaces.discard(self)

    def __enter__(self) -> Workspace:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _issue(store: Store, workspace: Workspace) -> Workspace:
    """Register a workspace with the store that issued it (design §7.8).

    `close()` closes "the connection A5a opened and any Workspace descriptors still
    outstanding" -- which it can only do if it knows about them. `Workspace.close()`
    removes itself again, so a long-lived store that creates and closes many workspaces
    does not accumulate them.
    """
    store._workspaces.add(workspace)
    return workspace


def _parent_fd(store: Store, name: str) -> int:
    return os.open(
        name, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC,
        dir_fd=store._binding.metadata_root_fd,
    )


def _open_child(parent_fd: int, parent: str, txid: str) -> int | None:
    """Design §8.3 -- `O_NOFOLLOW`, and the refusal that goes with it.

    `list_workspaces` classifies each child by `fstatat` with `follow_symlinks=False`,
    but `reopen_workspace` is reachable without ever running it: A5b arrives holding a
    txid read out of a record. So the kind check belongs in the open. Without the flag
    this call hands back a descriptor to whatever a symlink names -- reproduced with the
    flags it would otherwise use, opening a directory outside metadata_root and listing
    it -- and that descriptor is a borrowed mutation anchor A6 stages captures through.

    On Linux both the symlink and the regular-file case fail ENOTDIR, measured;
    `O_DIRECTORY` pre-empts the ELOOP that `O_NOFOLLOW` alone documents, and ELOOP is
    named here because the flag's contract is what this depends on, not one kernel's
    choice between two errnos.
    """
    try:
        return os.open(
            txid,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=parent_fd,
        )
    except FileNotFoundError:
        return None
    except OSError as caught:
        if caught.errno not in (errno.ENOTDIR, errno.ELOOP):
            raise
        raise MetadataStoreInvalid(
            f"{parent}/{txid} is not a directory; create_workspace is the only "
            "permitted producer here and it makes directories"
        ) from caught


def _require_permitted_children(parent_fd: int, parent: str) -> tuple[str, ...]:
    """Design §8.5 for `staging/` and `work/`.

    Every child here is created by create_workspace, whose txid passed §5.5 and whose
    mkdirat made it a directory, so neither a non-directory nor an invalid name can be a
    survivor of A5a's own work. Returning one breaks the enumeration's guarantee that every
    txid it reports is one reopen_workspace accepts; skipping it leaves unaccounted debris.
    """
    names: list[str] = []
    for name in sorted(os.listdir(parent_fd)):
        info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if not stat.S_ISDIR(info.st_mode):
            raise MetadataStoreInvalid(
                f"{parent}/{name} is not a directory; no permitted producer could have "
                "written it"
            )
        if type(name) is not str or not _is_txid(name):
            raise MetadataStoreInvalid(
                f"{parent}/{name!r} is not a well-formed txid; no permitted producer "
                "could have written it"
            )
        names.append(name)
    return tuple(names)


def _is_txid(name: str) -> bool:
    from atoms.core.identifiers import is_valid_identifier

    return is_valid_identifier(name)


def create_workspace(store: Store, txid: str) -> Workspace:
    """Requires both directories absent and creates them, gating before each mkdirat.

    Two mkdirs are two mutations, and the lock can be released between them: the first
    would create staging/<txid>/ under a live lease and the second work/<txid>/ after it
    ended, while the next lease owner is already reclaiming the staging-only survivor.
    """
    store._require_live()
    require_identifier("txid", txid)
    staging_parent = _parent_fd(store, STAGING_PARENT)
    work_parent = _parent_fd(store, WORK_PARENT)
    try:
        for parent_fd, parent in ((staging_parent, STAGING_PARENT), (work_parent, WORK_PARENT)):
            if _stat_or_none(parent_fd, txid) is not None:
                raise ProtocolError(
                    f"{parent}/{txid} already exists; a survivor is evidence about a "
                    "previous attempt and adoption would bypass the occupancy question"
                )
        from atoms.store.connection import gate

        gate(store._binding)
        os.mkdir(txid, 0o700, dir_fd=staging_parent)
        gate(store._binding)
        os.mkdir(txid, 0o700, dir_fd=work_parent)
        store._binding.backend.flush_directory(staging_parent)
        store._binding.backend.flush_directory(work_parent)
        return _issue(store, Workspace(
            store=store,
            txid=txid,
            staging_fd=_open_child(staging_parent, STAGING_PARENT, txid),
            work_fd=_open_child(work_parent, WORK_PARENT, txid),
            _construction_token=_WORKSPACE_TOKEN,
        ))
    finally:
        os.close(staging_parent)
        os.close(work_parent)


def _stat_or_none(parent_fd: int, name: str) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None


def reopen_workspace(store: Store, txid: str) -> Workspace:
    """Opens whichever of the two directories exist. Adopts nothing that was not there."""
    store._require_live()
    require_identifier("txid", txid)
    staging_parent = _parent_fd(store, STAGING_PARENT)
    work_parent = _parent_fd(store, WORK_PARENT)
    try:
        staging_fd = _open_child(staging_parent, STAGING_PARENT, txid)
        work_fd = _open_child(work_parent, WORK_PARENT, txid)
        if staging_fd is None and work_fd is None:
            raise ProtocolError(f"no workspace on disk for txid {txid!r}")
        return _issue(store, Workspace(
            store=store, txid=txid, staging_fd=staging_fd, work_fd=work_fd,
            _construction_token=_WORKSPACE_TOKEN,
        ))
    finally:
        os.close(staging_parent)
        os.close(work_parent)


def list_workspaces(store: Store) -> tuple[str, ...]:
    """The txids having a staging/ or work/ directory. A5a reports what exists; which
    survivors are orphans is A5b's judgment."""
    store._require_live()
    found: set[str] = set()
    for parent in (STAGING_PARENT, WORK_PARENT):
        parent_fd = _parent_fd(store, parent)
        try:
            found.update(_require_permitted_children(parent_fd, parent))
        finally:
            os.close(parent_fd)
    return tuple(sorted(found))


def remove_workspace(store: Store, workspace: Workspace) -> None:
    """Preflight then act -- the shape §8.1 step 1 already uses, for the same reason."""
    store._require_live()
    if type(workspace) is not Workspace:
        raise ProtocolError(
            f"expected exactly Workspace, got {type(workspace).__name__}"
        )
    if workspace._closed:
        raise ProtocolError("this workspace is closed")
    if workspace._store is not store:
        raise ProtocolError(
            "this workspace belongs to a different Store over the same binding; its "
            "issuer may close its descriptors underneath"
        )

    from atoms.store.connection import gate

    txid = workspace._txid
    staging_parent = _parent_fd(store, STAGING_PARENT)
    work_parent = _parent_fd(store, WORK_PARENT)
    try:
        # 1. Preflight: every staging entry is a regular file, work/<txid>/ is empty.
        staging_names: tuple[str, ...] = ()
        if workspace._staging_fd is not None:
            staging_fd = workspace._staging_fd
            staging_names = tuple(sorted(os.listdir(staging_fd)))
            for name in staging_names:
                info = os.stat(name, dir_fd=staging_fd, follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode):
                    raise MetadataStoreInvalid(
                        f"staging/{txid}/{name} is not a regular file; A6's capture "
                        "writes regular files and nothing else"
                    )
        if workspace._work_fd is not None and os.listdir(workspace._work_fd):
            raise ProtocolError(
                f"work/{txid}/ is not empty; authority §9.5 classifies its contents by "
                "inode against the live filesystem, and that judgment is A7's"
            )

        # 2-3. Act, gating before each mutating syscall.
        if workspace._staging_fd is not None:
            for name in staging_names:
                gate(store._binding)
                os.unlink(name, dir_fd=workspace._staging_fd)
            store._binding.backend.flush_directory(workspace._staging_fd)
            gate(store._binding)
            os.rmdir(txid, dir_fd=staging_parent)
            store._binding.backend.flush_directory(staging_parent)
        if workspace._work_fd is not None:
            gate(store._binding)
            os.rmdir(txid, dir_fd=work_parent)
            store._binding.backend.flush_directory(work_parent)
    finally:
        os.close(staging_parent)
        os.close(work_parent)
    workspace.close()
```

Add the workspace registry to `Store` in `connection.py` — design §7.8's promise that `close()`
closes "any `Workspace` descriptors still outstanding". Extend `__slots__`:

```python
    __slots__ = (
        "_active_transaction", "_binding", "_closed", "_connection", "_workspaces",
    )
```

add the last line of `__init__`:

```python
        self._workspaces: set[Workspace] = set()
```

and close them in `close()`, between spending the transaction and closing the connection:

```python
        for workspace in tuple(self._workspaces):
            workspace.close()   # discards itself from the set; iterate a copy
        self._connection.close()
```

`Workspace` is unhashable-by-value and hashable by identity — it defines no `__eq__` — so a plain
`set` is the right container. The references are strong on purpose: a workspace the caller dropped
without closing is exactly the one whose descriptors `close()` has to reach.

Append the four delegating methods to `Store`:

```python
    def create_workspace(self, txid: str) -> Workspace:
        return create_workspace(self, txid)

    def reopen_workspace(self, txid: str) -> Workspace:
        return reopen_workspace(self, txid)

    def list_workspaces(self) -> tuple[str, ...]:
        return list_workspaces(self)

    def remove_workspace(self, workspace: Workspace) -> None:
        remove_workspace(self, workspace)
```

The `from atoms.store.connection import gate` imports inside functions are deliberate:
`connection.py` imports `workspace.py` for the delegating methods, so a module-level import
back would be a cycle. Task 13 asserts the direction; do not "clean this up" by moving the
delegating methods into `workspace.py`.

- [ ] **Step 4: Run the tests and the gates**

```bash
uv run pytest tests/test_store_workspace.py -v
uv run ruff check src/atoms/store tests/test_store_workspace.py
uv run pyright
```
Expected: PASS, `All checks passed!`, `0 errors`.

- [ ] **Step 5: Commit**

```bash
git add src/atoms/store tests/test_store_workspace.py
git commit -m "feat(store): model a workspace as a resource with three legal dispositions"
```

---

## Task 10: The digest mapping and blob reading

**Files:**
- Create: `src/atoms/store/blobs.py`
- Modify: `src/atoms/store/connection.py`
- Create: `tests/test_store_blobs.py`

**Interfaces:**
- Consumes: `require_identifier`'s sibling gates; `SELECT_BLOB`; `MetadataStoreInvalid`; `gate`.
- Produces: `StagedBlob` (frozen, ordinary); `DIGEST_PATTERN`, `require_digest(value)`,
  `require_component(label, value)`, `digest_to_leaf(digest)`, `leaf_to_digest(leaf)`,
  `verify_leaf(fd, digest, byte_len)`, `open_blob(store, digest) -> int`, `Store.open_blob`.

**The database key and the filesystem leaf are not the same string.** `blob.digest` and
`StagedBlob.digest` carry the full `sha256:<64 hex>` — self-describing, so a second algorithm later is
a new prefix rather than a schema change. The leaf under `blobs/sha256/` is the **extracted hex alone**,
because the directory already names the algorithm. The extraction is a single function, called nowhere
else, and may only be applied to a digest that already passed the grammar — which is what makes the
leaf fixed-width hex by construction.

**`open_blob` verifies before it returns.** Authority §11 lists "a blob whose bytes do not match its
digest" as a `MetadataStoreInvalid` condition, and the caller's own read cannot substitute: authority
§10's use is a **file prefix relation**, which stops at the first difference or at the shorter length
and never reaches EOF. A truncated blob whose surviving head matches the live file would pass a prefix
comparison and be treated as an authentic preimage.

**`StagedBlob` is deliberately unguarded.** A6 builds manifests — that is the whole point of the type —
so a construction token would block the intended caller and protect nothing: the values are validated
on arrival at `promote_staging`, which is where a forged one would have to do its damage.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_store_blobs.py`:

```python
"""Tier 4 -- blobs over a real ext4 metadata root (design §7.2, §7.3, §8.1, §8.2, §8.4)."""

from __future__ import annotations

import hashlib
import os

import pytest

from atoms.core.errors import ProtocolError
from atoms.store.blobs import StagedBlob, digest_to_leaf, leaf_to_digest
from atoms.store.errors import MetadataStoreInvalid
from tests.store_support import child_dir, digest_of, stage

HEX = "a" * 64


def test_the_digest_and_the_leaf_map_in_both_directions():
    assert digest_to_leaf(f"sha256:{HEX}") == HEX
    assert leaf_to_digest(HEX) == f"sha256:{HEX}"


@pytest.mark.parametrize(
    "bad", [3, None, b"x", HEX, f"sha256:{HEX.upper()}", f"sha256:{HEX[:63]}", f"sha1:{HEX}"]
)
def test_a_digest_failing_the_grammar_is_refused_before_any_lookup(opened_store, bad):
    with pytest.raises(ProtocolError):
        opened_store.open_blob(bad)


def test_an_unindexed_digest_raises_protocol_error_on_a_healthy_store(opened_store):
    """Membership, not existence: the caller asked for a blob this store does not have."""
    with pytest.raises(ProtocolError):
        opened_store.open_blob(f"sha256:{HEX}")


def test_open_blob_returns_a_verified_descriptor_at_offset_zero(promoted_blob):
    store, digest, content = promoted_blob
    fd = store.open_blob(digest)
    try:
        assert os.read(fd, len(content)) == content
    finally:
        os.close(fd)


def test_the_returned_descriptor_is_the_callers_to_close(promoted_blob):
    store, digest, _content = promoted_blob
    fd = store.open_blob(digest)
    store.close()
    os.close(fd)


def test_an_indexed_digest_whose_leaf_is_missing_refuses(promoted_blob, store_binding):
    store, digest, _content = promoted_blob
    with child_dir(store_binding.metadata_root_fd, "blobs/sha256") as blobs_fd:
        os.unlink(digest_to_leaf(digest), dir_fd=blobs_fd)
    with pytest.raises(MetadataStoreInvalid):
        store.open_blob(digest)


@pytest.mark.parametrize("kind", ["symlink", "directory"])
def test_an_indexed_digest_whose_leaf_is_not_a_regular_file_refuses(
    promoted_blob, store_binding, kind
):
    store, digest, _content = promoted_blob
    leaf = digest_to_leaf(digest)
    with child_dir(store_binding.metadata_root_fd, "blobs/sha256") as blobs_fd:
        os.unlink(leaf, dir_fd=blobs_fd)
        if kind == "symlink":
            os.symlink("/etc/passwd", leaf, dir_fd=blobs_fd)
        else:
            os.mkdir(leaf, dir_fd=blobs_fd)
    with pytest.raises(MetadataStoreInvalid):
        store.open_blob(digest)


def test_a_truncated_blob_whose_prefix_matches_still_refuses(promoted_blob, store_binding):
    """The case a prefix comparison accepts: authority §10's relation stops at the shorter
    length and never reaches EOF, so verification here answers a question the caller's own
    read structurally cannot."""
    store, digest, content = promoted_blob
    with child_dir(store_binding.metadata_root_fd, "blobs/sha256") as blobs_fd:
        fd = os.open(digest_to_leaf(digest), os.O_WRONLY, dir_fd=blobs_fd)
        try:
            os.ftruncate(fd, len(content) - 1)
        finally:
            os.close(fd)
    with pytest.raises(MetadataStoreInvalid):
        store.open_blob(digest)


def test_a_substituted_blob_of_the_same_length_refuses(promoted_blob, store_binding):
    store, digest, content = promoted_blob
    with child_dir(store_binding.metadata_root_fd, "blobs/sha256") as blobs_fd:
        fd = os.open(digest_to_leaf(digest), os.O_WRONLY, dir_fd=blobs_fd)
        try:
            os.write(fd, bytes(len(content)))
        finally:
            os.close(fd)
    with pytest.raises(MetadataStoreInvalid):
        store.open_blob(digest)


def test_staged_blob_is_an_ordinary_dataclass():
    """A6 builds manifests, so a construction token would block the intended caller."""
    assert StagedBlob(name="a", digest=f"sha256:{HEX}", byte_len=1).byte_len == 1
```

- [ ] **Step 2: Add the `promoted_blob` fixture placeholder**

Append to `tests/conftest.py`:

```python
@pytest.fixture
def promoted_blob(opened_store):
    """A store holding one promoted, indexed blob, with its digest and bytes.

    Task 11 replaces the direct INSERT with a real promotion; the tests above are written
    against the tuple, not against how it got there.
    """
    import os

    from atoms.store.blobs import digest_to_leaf
    from tests.store_support import child_dir, digest_of

    content = b"the blob's bytes"
    digest = digest_of(content)
    binding = opened_store._binding
    with child_dir(binding.metadata_root_fd, "blobs/sha256") as blobs_fd:
        fd = os.open(digest_to_leaf(digest), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600,
                     dir_fd=blobs_fd)
        try:
            os.write(fd, content)
        finally:
            os.close(fd)
    with opened_store.transaction() as txn:
        txn._store._connection.execute(
            "INSERT INTO blob (digest, byte_len) VALUES (?, ?)", (digest, len(content))
        )
    return opened_store, digest, content
```

That raw `INSERT` is a **fixture-only** statement and does not live in `atoms/store/`, so it
does not violate the sole-writer rule Task 13 asserts over the package. Task 11 rewrites the
fixture to promote instead; leave a `# Task 11: promote instead` comment on the INSERT.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_store_blobs.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'atoms.store.blobs'`.

- [ ] **Step 4: Write the module**

Create `src/atoms/store/blobs.py`:

```python
"""Content-addressed blobs under the engine-owned metadata root (design §7.2, §8)."""

from __future__ import annotations

import hashlib
import os
import re
import stat
from dataclasses import dataclass
from typing import TYPE_CHECKING

from atoms.core.errors import ProtocolError
from atoms.store.errors import MetadataStoreInvalid
from atoms.store.records import SELECT_BLOB

if TYPE_CHECKING:
    from atoms.store.connection import Store

BLOBS_PARENT = "blobs/sha256"
DIGEST_PATTERN = re.compile(r"\Asha256:[0-9a-f]{64}\Z")
_READ_CHUNK = 1 << 20


@dataclass(frozen=True, slots=True)
class StagedBlob:
    """One entry of a promotion manifest. Deliberately unguarded (design §7.1)."""

    name: str
    digest: str
    byte_len: int


def require_digest(value: object) -> str:
    """Design §5.5: exact str, then the grammar."""
    if type(value) is not str:
        raise ProtocolError(f"a digest must be exactly str, got {type(value).__name__}")
    if DIGEST_PATTERN.fullmatch(value) is None:
        raise ProtocolError(
            f"digest {value!r} is not sha256:<64 lowercase hex>"
        )
    return value


def require_component(label: str, value: object) -> str:
    """A single pathname component: non-empty, not '.' or '..', no '/', no NUL.

    Guarded traversal prevents the *parent* from being substituted; it does nothing about
    a name being '../x', which is still resolved relative to that descriptor.
    """
    if type(value) is not str:
        raise ProtocolError(f"{label} must be exactly str, got {type(value).__name__}")
    if not value or value in (".", "..") or "/" in value or "\x00" in value:
        raise ProtocolError(f"{label} {value!r} is not a single pathname component")
    return value


def digest_to_leaf(digest: str) -> str:
    """The only place the key becomes a filename. Applied only after the grammar passed,
    which is what makes the leaf fixed-width hex by construction."""
    return require_digest(digest).split(":", 1)[1]


def leaf_to_digest(leaf: str) -> str:
    return require_digest(f"sha256:{leaf}")


def blobs_fd(store: Store) -> int:
    return os.open(
        BLOBS_PARENT, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC,
        dir_fd=store._binding.metadata_root_fd,
    )


def verify_leaf(fd: int, digest: str, byte_len: int | None) -> int:
    """Kind, length, and streamed SHA-256, then rewind. Returns the observed length.

    `byte_len` is None for an orphan, which has no row to supply one; the length then
    follows from the hash.
    """
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode):
        raise MetadataStoreInvalid(
            f"the leaf for {digest} is not a regular file"
        )
    if byte_len is not None and info.st_size != byte_len:
        raise MetadataStoreInvalid(
            f"the leaf for {digest} is {info.st_size} bytes, the index says {byte_len}"
        )
    digester = hashlib.sha256()
    os.lseek(fd, 0, os.SEEK_SET)
    while chunk := os.read(fd, _READ_CHUNK):
        digester.update(chunk)
    observed = f"sha256:{digester.hexdigest()}"
    if observed != digest:
        raise MetadataStoreInvalid(
            f"the leaf named {digest} hashes to {observed}"
        )
    os.lseek(fd, 0, os.SEEK_SET)
    return info.st_size


def open_blob(store: Store, digest: str) -> int:
    """Design §7.2 -- membership, then content, then ownership transfer."""
    require_digest(digest)
    with store._read_transaction() as connection:
        row = connection.execute(SELECT_BLOB, (digest,)).fetchone()
        if row is None:
            raise ProtocolError(
                f"{digest} is not indexed by this store; a promoted-but-unindexed orphan "
                "is not openable, which is what distinguishes membership from existence"
            )
        byte_len = row[0]
    store._require_live()
    parent = blobs_fd(store)
    try:
        try:
            fd = os.open(
                digest_to_leaf(digest),
                os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=parent,
            )
        except FileNotFoundError as caught:
            raise MetadataStoreInvalid(
                f"{digest} has a blob row but no leaf under {BLOBS_PARENT}/"
            ) from caught
        except OSError as caught:
            if caught.errno == 40:  # ELOOP -- O_NOFOLLOW refused a symlink
                raise MetadataStoreInvalid(
                    f"the leaf for {digest} is a symlink"
                ) from caught
            raise
    finally:
        os.close(parent)
    try:
        verify_leaf(fd, digest, byte_len)
    except BaseException:
        os.close(fd)
        raise
    return fd
```

Append to `Store`:

```python
    def open_blob(self, digest: str) -> int:
        return open_blob(self, digest)
```

`errno == 40` is `ELOOP` on Linux; write `errno.ELOOP` with `import errno` rather than the
literal. Opening a *directory* with `O_RDONLY` succeeds, so that case is caught by
`verify_leaf`'s `S_ISREG` check rather than at the open — which is why both exist.

- [ ] **Step 5: Run the tests and the gates**

```bash
uv run pytest tests/test_store_blobs.py -v
uv run ruff check src/atoms/store tests/test_store_blobs.py tests/conftest.py
uv run pyright
```
Expected: PASS, `All checks passed!`, `0 errors`.

- [ ] **Step 6: Commit**

```bash
git add src/atoms/store tests/test_store_blobs.py tests/conftest.py
git commit -m "feat(store): verify a blob's bytes before handing out its descriptor"
```

---

## Task 11: Promotion

**Files:**
- Modify: `src/atoms/store/blobs.py`
- Modify: `src/atoms/store/connection.py`
- Modify: `tests/test_store_blobs.py`
- Modify: `tests/conftest.py`
- Modify: `tests/test_store_records.py`

**Interfaces:**
- Consumes: `Backend.transfer_noclobber`, `Backend.flush_directory`; Task 10's `verify_leaf`,
  `digest_to_leaf`, `require_digest`, `require_component`; `Workspace.spend_staging`.
- Produces: `_StoreTransaction.promote_staging(workspace, manifest)`; `INSERT_BLOB`; the
  record-reference half of `_run_barrier`.

**Promotion is a `_StoreTransaction` method and writes the `blob` rows itself.** Publishing a blob and
inserting its row are two substrates, and any interval between them is a window in which the blob is on
disk with nothing referencing it — indistinguishable, to a concurrent reclaimer, from an authority §7.3
crash orphan. Escalating the reclaimer to `BEGIN IMMEDIATE` only moves that window: the promoting
writer is *blocked*, not excluded, so the reclaimer still sees no row, unlinks, commits, and the writer
then commits the row — measured as `writer_committed=True`, row present, leaf absent.

**There is no `insert_blobs`.** A barrier that checked only presence admitted the disagreement it
looked like it prevented — promote `(digest, 10)`, insert `(digest, 11)` — and let a caller index a
digest that was never promoted. One operation owning the whole publication removes both.

**The six steps** (design §8.1):

1. Preflight the entire manifest: element types, `§5.5` names and digests, unique source names,
   duplicate digests agreeing on `byte_len`, the staging entry set equal to the manifest's names, each
   staged source's kind/length/streamed SHA-256, and each already-indexed digest's **leaf** verified in
   full. Then gate.
2. Per entry: gate, then `transfer_noclobber` into `blobs/sha256/<hex>`. `EEXIST` goes to §8.2.
3. `flush_directory` on `blobs/sha256/` **and** `staging/<txid>/`.
4. Require `staging/<txid>/` empty, gate, `rmdir`.
5. `flush_directory` on `staging/`.
6. Insert the `blob` rows, `ON CONFLICT(digest) DO NOTHING`.

- [ ] **Step 1: Write the failing tests**

First add the record builder every promotion test now needs, to `tests/store_support.py`. The
barrier requires the record for the workspace's txid to reference every digest the transaction
promoted, so `one_effect_spec` — which starts from `ABSENT` and references nothing — can no longer
follow a promotion:

```python
def spec_referencing(*contents: bytes):
    """A spec whose INITIAL surface declares one FileState per content.

    The promoted blobs are that record's rollback material, which is exactly what the
    §7.7 barrier checks for. Putting the hashes in the final surface would not work:
    referenced_digests walks the initial surface only (Task 8), because a final-surface
    hash names content the transaction has not produced yet.
    """
    names = [f"f{index}.txt" for index in range(len(contents))]
    pre = {n: file_state(c) for n, c in zip(names, contents, strict=True)}
    post = {n: file_state(c + b"!") for n, c in zip(names, contents, strict=True)}
    return build_spec(
        consumer_tag="test",
        intent_digest="sha256:" + "5" * 64,
        initial_surface=pre,
        final_surface=post,
        effects=[
            ReplaceFile(effect_id=f"e{index}", path=name, pre=pre[name], post=post[name])
            for index, name in enumerate(names)
        ],
    )
```

No leading underscore: it is imported across modules, and a private name would trip ruff's
private-member rules at every call site.

Then append to `tests/test_store_blobs.py`:

```python
from atoms.core.recovery.model import TransactionState
from tests.store_support import one_effect_spec, spec_referencing


def _manifest(*entries: tuple[str, bytes]) -> tuple[StagedBlob, ...]:
    return tuple(
        StagedBlob(name=name, digest=digest_of(content), byte_len=len(content))
        for name, content in entries
    )


def test_promotion_publishes_indexes_and_removes_the_staging_directory(
    opened_store, store_binding
):
    with opened_store.create_workspace("tx1") as workspace:
        stage(workspace, "one", b"first")
        stage(workspace, "two", b"second")
        manifest = _manifest(("one", b"first"), ("two", b"second"))
        with opened_store.transaction() as txn:
            txn.promote_staging(workspace, manifest)
            txn.insert_record("tx1", spec_referencing(b"first", b"second"))
    with child_dir(store_binding.metadata_root_fd, "blobs/sha256") as blobs_fd:
        assert set(os.listdir(blobs_fd)) == {digest_to_leaf(b.digest) for b in manifest}
    with child_dir(store_binding.metadata_root_fd, "staging") as staging_fd:
        assert os.listdir(staging_fd) == []
    # Only the staging half went; work/tx1 is what a successful promotion leaves behind.
    assert opened_store.list_workspaces() == ("tx1",)
    for entry in manifest:
        fd = opened_store.open_blob(entry.digest)
        os.close(fd)


def test_promotion_spends_the_staging_half(opened_store):
    with opened_store.create_workspace("tx1") as workspace:
        stage(workspace, "one", b"first")
        with opened_store.transaction() as txn:
            txn.promote_staging(workspace, _manifest(("one", b"first")))
            txn.insert_record("tx1", spec_referencing(b"first"))
        with pytest.raises(ProtocolError):
            workspace.staging_fd
        with pytest.raises(ProtocolError):
            with opened_store.transaction() as txn:
                txn.promote_staging(workspace, _manifest(("one", b"first")))


def test_an_empty_manifest_still_spends_the_staging_half(opened_store, store_binding):
    """A spec whose initial surface names no file has nothing to capture, so the
    directory is removed with no source removed. Spending on the first source removal
    would leave this workspace holding a descriptor to an unlinked directory -- openat
    from it still works and produces files no path can name (design §8.1 step 4)."""
    with opened_store.create_workspace("tx1") as workspace:
        with opened_store.transaction() as txn:
            txn.promote_staging(workspace, ())
            txn.insert_record("tx1", one_effect_spec())
        with pytest.raises(ProtocolError):
            workspace.staging_fd
    with child_dir(store_binding.metadata_root_fd, "staging") as staging_fd:
        assert os.listdir(staging_fd) == []


def test_a_promoted_digest_the_records_workspace_does_not_reference_refuses(opened_store):
    """Criterion 27 binds each promoted digest to the record for **that workspace's**
    txid. A union over every touched record would let tx2's record justify a blob
    promoted from tx1's workspace, leaving tx1's own record naming nothing."""
    with opened_store.create_workspace("tx1") as first:
        stage(first, "one", b"shared")
        with pytest.raises(ProtocolError) as caught:
            with opened_store.transaction() as txn:
                txn.promote_staging(first, _manifest(("one", b"shared")))
                txn.insert_record("tx1", one_effect_spec())
                txn.insert_record("tx2", spec_referencing(b"shared"))
    assert "tx1" in str(caught.value)


@pytest.mark.parametrize(
    "manifest",
    [
        [StagedBlob(name="one", digest="not-a-digest", byte_len=1)],
        [StagedBlob(name="../escape", digest=f"sha256:{HEX}", byte_len=1)],
        [StagedBlob(name="one", digest=f"sha256:{HEX}", byte_len=True)],
        [StagedBlob(name="one", digest=f"sha256:{HEX}", byte_len=-1)],
    ],
)
def test_a_malformed_manifest_moves_nothing(opened_store, manifest):
    with opened_store.create_workspace("tx1") as workspace:
        stage(workspace, "one", b"first")
        with pytest.raises(ProtocolError):
            with opened_store.transaction() as txn:
                txn.promote_staging(workspace, tuple(manifest))
        assert set(os.listdir(workspace.staging_fd)) == {"one"}


def test_two_entries_naming_one_source_are_refused_before_anything_moves(opened_store):
    with opened_store.create_workspace("tx1") as workspace:
        stage(workspace, "one", b"first")
        manifest = (
            StagedBlob(name="one", digest=digest_of(b"first"), byte_len=5),
            StagedBlob(name="one", digest=digest_of(b"first"), byte_len=5),
        )
        with pytest.raises(ProtocolError):
            with opened_store.transaction() as txn:
                txn.promote_staging(workspace, manifest)
        assert set(os.listdir(workspace.staging_fd)) == {"one"}


def test_one_digest_with_two_lengths_in_one_manifest_is_a_caller_error(opened_store):
    with opened_store.create_workspace("tx1") as workspace:
        stage(workspace, "one", b"first")
        stage(workspace, "two", b"first")
        manifest = (
            StagedBlob(name="one", digest=digest_of(b"first"), byte_len=5),
            StagedBlob(name="two", digest=digest_of(b"first"), byte_len=4),
        )
        with pytest.raises(ProtocolError):
            with opened_store.transaction() as txn:
                txn.promote_staging(workspace, manifest)


def test_an_omitted_file_fails_at_step_one_with_every_source_still_staged(opened_store):
    with opened_store.create_workspace("tx1") as workspace:
        stage(workspace, "one", b"first")
        stage(workspace, "two", b"second")
        with pytest.raises(ProtocolError) as caught:
            with opened_store.transaction() as txn:
                txn.promote_staging(workspace, _manifest(("one", b"first")))
        assert "two" in str(caught.value)
        assert set(os.listdir(workspace.staging_fd)) == {"one", "two"}


@pytest.mark.parametrize("corrupt", ["length", "content", "kind"])
def test_a_staged_source_is_verified_before_it_moves(opened_store, corrupt):
    with opened_store.create_workspace("tx1") as workspace:
        if corrupt == "kind":
            os.mkdir("one", dir_fd=workspace.staging_fd)
            manifest = _manifest(("one", b"first"))
        else:
            stage(workspace, "one", b"first")
            declared = b"first" if corrupt == "length" else b"other"
            manifest = (
                StagedBlob(
                    name="one",
                    digest=digest_of(declared),
                    byte_len=99 if corrupt == "length" else 5,
                ),
            )
        with pytest.raises(MetadataStoreInvalid):
            with opened_store.transaction() as txn:
                txn.promote_staging(workspace, manifest)
        assert "one" in set(os.listdir(workspace.staging_fd))


def test_a_matching_pre_existing_blob_unlinks_the_source_and_continues(
    opened_store, store_binding
):
    with opened_store.create_workspace("tx1") as first:
        stage(first, "one", b"shared")
        with opened_store.transaction() as txn:
            txn.promote_staging(first, _manifest(("one", b"shared")))
            txn.insert_record("tx1", spec_referencing(b"shared"))
    with opened_store.create_workspace("tx2") as second:
        stage(second, "again", b"shared")
        with opened_store.transaction() as txn:
            txn.promote_staging(second, (
                StagedBlob(name="again", digest=digest_of(b"shared"), byte_len=6),
            ))
            txn.insert_record("tx2", spec_referencing(b"shared"))
    with child_dir(store_binding.metadata_root_fd, "blobs/sha256") as blobs_fd:
        assert len(os.listdir(blobs_fd)) == 1


def test_a_mismatching_pre_existing_blob_preserves_the_staged_source(
    opened_store, store_binding
):
    """The staged bytes are the good copy; destroying them to tidy up after a corrupt
    blob would discard the only recovery material (design §8.2)."""
    with opened_store.create_workspace("tx1") as workspace:
        stage(workspace, "one", b"good")
        digest = digest_of(b"good")
        with child_dir(store_binding.metadata_root_fd, "blobs/sha256") as blobs_fd:
            fd = os.open(digest_to_leaf(digest), os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                         0o600, dir_fd=blobs_fd)
            try:
                os.write(fd, b"bad!")
            finally:
                os.close(fd)
        with pytest.raises(MetadataStoreInvalid):
            with opened_store.transaction() as txn:
                txn.promote_staging(workspace, _manifest(("one", b"good")))
        assert "one" in set(os.listdir(workspace.staging_fd))


def test_an_already_indexed_digest_has_its_leaf_verified_not_assumed(
    opened_store, store_binding, promoted_blob
):
    """Comparing only the row's byte_len left promotion silently repairing corruption it
    is not entitled to repair: step 2's rename would find no destination and re-create it,
    while open_blob calls that identical state MetadataStoreInvalid."""
    store, digest, content = promoted_blob
    with child_dir(store_binding.metadata_root_fd, "blobs/sha256") as blobs_fd:
        os.unlink(digest_to_leaf(digest), dir_fd=blobs_fd)
    with pytest.raises(MetadataStoreInvalid):
        store.open_blob(digest)
    with store.create_workspace("tx9") as workspace:
        stage(workspace, "again", content)
        with pytest.raises(MetadataStoreInvalid):
            with store.transaction() as txn:
                txn.promote_staging(workspace, (
                    StagedBlob(name="again", digest=digest, byte_len=len(content)),
                ))
        assert "again" in set(os.listdir(workspace.staging_fd))


def test_promoting_an_already_indexed_digest_with_a_different_length_refuses(
    opened_store, store_binding, promoted_blob
):
    store, digest, content = promoted_blob
    with store.create_workspace("tx9") as workspace:
        stage(workspace, "again", content)
        with pytest.raises(MetadataStoreInvalid):
            with store.transaction() as txn:
                txn.promote_staging(workspace, (
                    StagedBlob(name="again", digest=digest, byte_len=len(content) + 1),
                ))


def test_a_replay_of_a_promoted_entry_raises_enoent_not_eexist(opened_store, store_binding):
    """transfer_noclobber is renameat2(RENAME_NOREPLACE) (linux.py:54); replaying an entry
    that already moved fails ENOENT -- the source is gone -- which is why a batch that
    removed any source is not retryable."""
    backend = store_binding.backend
    with opened_store.create_workspace("tx1") as workspace:
        stage(workspace, "one", b"first")
        with child_dir(store_binding.metadata_root_fd, "blobs/sha256") as blobs_fd:
            backend.transfer_noclobber(
                workspace.staging_fd, "one", blobs_fd, digest_to_leaf(digest_of(b"first"))
            )
            with pytest.raises(FileNotFoundError):
                backend.transfer_noclobber(
                    workspace.staging_fd, "one", blobs_fd,
                    digest_to_leaf(digest_of(b"first")),
                )


def test_promoting_without_inserting_a_record_is_refused_at_the_barrier(
    opened_store, store_binding
):
    """blob has no foreign key to transaction_record and cannot have one -- the reference
    lives inside spec_json -- so a transaction that promotes and commits without a record
    leaves an indexed blob nothing names, invisible to both reclaimers (design §7.7)."""
    with opened_store.create_workspace("tx1") as workspace:
        stage(workspace, "one", b"first")
        with pytest.raises(ProtocolError) as caught:
            with opened_store.transaction() as txn:
                txn.promote_staging(workspace, _manifest(("one", b"first")))
        assert "reference" in str(caught.value)
    raw = raw_connect(store_binding)
    try:
        assert raw.execute("SELECT count(*) FROM blob").fetchone() == (0,)
        assert raw.execute("SELECT count(*) FROM transaction_record").fetchone() == (0,)
    finally:
        raw.close()
    assert opened_store.list_unindexed_blobs() != ()
```

Add `from tests.store_support import raw_connect` to the module's imports.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_store_blobs.py -k promot -v`
Expected: FAIL with `AttributeError: '_StoreTransaction' object has no attribute 'promote_staging'`.

- [ ] **Step 3: Add promotion**

Append to `src/atoms/store/blobs.py`:

```python
INSERT_BLOB = "INSERT INTO blob (digest, byte_len) VALUES (?, ?) ON CONFLICT(digest) DO NOTHING"


def _preflight(store: Store, workspace: Workspace, manifest: tuple[StagedBlob, ...]) -> None:
    """Design §8.1 step 1 -- every rejection happens while nothing has moved.

    Promotion is one batch and has no undo: once entry k has been renamed, discovering
    that entry k+1 is malformed leaves the workspace half-promoted, with some blobs
    published, some files still staged, and a staging/<txid>/ that step 3 can no longer
    remove.
    """
    if type(manifest) is not tuple:
        raise ProtocolError(f"the manifest must be exactly tuple, got {type(manifest).__name__}")
    lengths: dict[str, int] = {}
    names: set[str] = set()
    for entry in manifest:
        if type(entry) is not StagedBlob:
            raise ProtocolError(
                f"every manifest element must be exactly StagedBlob, got "
                f"{type(entry).__name__}"
            )
        require_component("a manifest name", entry.name)
        require_digest(entry.digest)
        if type(entry.byte_len) is not int:
            raise ProtocolError(
                f"byte_len must be exactly int, got {type(entry.byte_len).__name__}; "
                "bool is an int subclass and is refused"
            )
        if entry.byte_len < 0:
            raise ProtocolError(f"byte_len {entry.byte_len} is negative")
        if entry.name in names:
            raise ProtocolError(
                f"manifest name {entry.name!r} appears twice; the second rename would "
                "fail ENOENT mid-batch"
            )
        names.add(entry.name)
        previous = lengths.setdefault(entry.digest, entry.byte_len)
        if previous != entry.byte_len:
            raise ProtocolError(
                f"digest {entry.digest} carries byte_len {previous} and {entry.byte_len} "
                "in one manifest; both descriptions arrived in the same argument"
            )

    staging_fd = workspace.staging_fd
    present = set(os.listdir(staging_fd))
    if present != names:
        raise ProtocolError(
            f"the manifest does not describe staging/{workspace.txid}/ exactly: "
            f"missing {sorted(present - names)}, absent {sorted(names - present)}"
        )

    for entry in manifest:
        fd = os.open(entry.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=staging_fd)
        try:
            observed = verify_leaf(fd, entry.digest, entry.byte_len)
        finally:
            os.close(fd)
        del observed

    parent = blobs_fd(store)
    try:
        for digest in sorted(lengths):
            row = store._connection.execute(SELECT_BLOB, (digest,)).fetchone()
            if row is None:
                continue
            if row[0] != lengths[digest]:
                raise MetadataStoreInvalid(
                    f"the stored row for {digest} says byte_len {row[0]}, the verified "
                    f"content is {lengths[digest]}"
                )
            try:
                fd = os.open(
                    digest_to_leaf(digest),
                    os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                    dir_fd=parent,
                )
            except FileNotFoundError as caught:
                raise MetadataStoreInvalid(
                    f"{digest} is indexed but its leaf is gone; promotion does not "
                    "silently re-create a blob open_blob calls unreadable"
                ) from caught
            try:
                verify_leaf(fd, digest, row[0])
            finally:
                os.close(fd)
    finally:
        os.close(parent)


def promote_staging(
    store: Store, workspace: Workspace, manifest: tuple[StagedBlob, ...]
) -> None:
    """Design §8.1, six steps, inside the caller's write transaction."""
    from atoms.store.connection import gate

    if type(workspace) is not Workspace:
        raise ProtocolError(f"expected exactly Workspace, got {type(workspace).__name__}")
    if workspace._store is not store:
        raise ProtocolError("this workspace belongs to a different Store")
    _preflight(store, workspace, manifest)
    gate(store._binding)

    backend = store._binding.backend
    staging_fd = workspace.staging_fd
    parent = blobs_fd(store)
    spent = False
    try:
        for entry in manifest:
            gate(store._binding)
            leaf = digest_to_leaf(entry.digest)
            try:
                backend.transfer_noclobber(staging_fd, entry.name, parent, leaf)
                spent = True
            except FileExistsError:
                # Design §8.2 -- a destination with no row: a promoted orphan from an
                # earlier crash, the one case the index cannot preflight.
                existing = os.open(
                    leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent
                )
                try:
                    verify_leaf(existing, entry.digest, entry.byte_len)
                finally:
                    os.close(existing)
                gate(store._binding)
                os.unlink(entry.name, dir_fd=staging_fd)
                spent = True
        backend.flush_directory(parent)
        backend.flush_directory(staging_fd)
        remaining = os.listdir(staging_fd)
        if remaining:
            raise ProtocolError(
                f"staging/{workspace.txid}/ still holds {sorted(remaining)} after the "
                "rename loop; step 1's entry-set comparison and the loop disagree"
            )
    finally:
        if spent:
            workspace.spend_staging()
        os.close(parent)

    staging_parent = os.open(
        STAGING_PARENT, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC,
        dir_fd=store._binding.metadata_root_fd,
    )
    try:
        gate(store._binding)
        os.rmdir(workspace.txid, dir_fd=staging_parent)
        # The directory is gone, so the half is spent even if no source was removed --
        # an empty manifest is a real case (a spec whose initial surface names no file
        # has nothing to capture), and it reaches here with `spent` still False.
        workspace.spend_staging()
        backend.flush_directory(staging_parent)
    finally:
        os.close(staging_parent)

    for entry in manifest:
        store._connection.execute(INSERT_BLOB, (entry.digest, entry.byte_len))
```

Add `from atoms.store.workspace import STAGING_PARENT, Workspace` to the imports.

**Two thresholds, because the success and failure paths are answering different questions.**

- On the **failure** path, `workspace.spend_staging()` runs once *any source has been removed* — by a
  rename or by §8.2's `EEXIST` unlink. The threshold is the first source removal, not the first
  rename: `EEXIST` on a matching pre-existing blob unlinks the staged source and continues without any
  rename succeeding, so a batch can lose sources through that route alone. That is criterion 26 — the
  staging half is no longer a complete capture, so it is not retryable.
- On the **success** path, the half is spent when step 4's `rmdir` succeeds, which is strictly more
  cases: an **empty manifest removes the directory without removing any source**. Spending on source
  removal alone would leave that workspace holding an open descriptor to an unlinked directory —
  `openat` from it still succeeds and produces files no path can ever name, which is the exact state
  `spend_staging` exists to prevent (design §8.1 step 4).

Append to `_StoreTransaction`:

```python
    def promote_staging(
        self, workspace: Workspace, manifest: tuple[StagedBlob, ...]
    ) -> None:
        with self._mutating() as store:
            promote_staging(store, workspace, manifest)
            # Design §7.7: register both the txid -- so §7.6's predicate runs for it,
            # which promotion alone would not have caused -- and the digests, under
            # that txid.
            self._touched.add(workspace.txid)
            self._promoted.setdefault(workspace.txid, set()).update(
                entry.digest for entry in manifest
            )
```

Replace the second loop of `_run_barrier` with the record-reference check:

```python
        for txid, promoted in sorted(self._promoted.items()):
            row = store._connection.execute(SELECT_RECORD, (txid,)).fetchone()
            referenced = (
                set()
                if row is None
                else {
                    digest
                    for digest, _ in referenced_digests(from_canonical_json(row[0]))
                }
            )
            unreferenced = sorted(promoted - referenced)
            if unreferenced:
                raise ProtocolError(
                    f"this transaction promoted digests the record for txid {txid!r} "
                    "does not reference: " + ", ".join(unreferenced)
                    + ". A committed blob row with no reference is invisible to both "
                    "reclaimers and would be stranded permanently"
                )
```

**The lookup is per workspace txid, not a union over everything the transaction touched** (criterion
27). Unioning would let a blob promoted from `tx1`'s workspace be justified by a record for `tx2`
written in the same transaction — which satisfies "some record references it" while leaving `tx1`'s
own record naming nothing, and leaves the blob stranded the moment `tx2` is the one that gets
reclaimed.

- [ ] **Step 4: Rewrite the `promoted_blob` fixture to promote**

Replace the fixture body in `tests/conftest.py`:

```python
@pytest.fixture
def promoted_blob(opened_store):
    """A store holding one promoted, indexed blob, with its digest and bytes."""
    from atoms.store.blobs import StagedBlob
    from tests.store_support import digest_of, spec_referencing, stage

    content = b"the blob's bytes"
    digest = digest_of(content)
    with opened_store.create_workspace("fixture") as workspace:
        stage(workspace, "capture", content)
        with opened_store.transaction() as txn:
            txn.promote_staging(
                workspace,
                (StagedBlob(name="capture", digest=digest, byte_len=len(content)),),
            )
            txn.insert_record("fixture", spec_referencing(content))
    return opened_store, digest, content
```

`spec_referencing` is the helper Step 1 added to `tests/store_support.py`.

Then delete the direct `INSERT INTO blob` blocks from `tests/test_store_records.py` that
Task 8 marked `# Task 11: promote instead`, and route those tests through `promoted_blob` or a
promotion of their own.

- [ ] **Step 5: Run the tests and the gates**

```bash
uv run pytest tests/ -v
uv run ruff check src/atoms/store tests/
uv run pyright
```
Expected: PASS, `All checks passed!`, `0 errors`.

- [ ] **Step 6: Commit**

```bash
git add src/atoms/store tests/
git commit -m "feat(store): publish and index a blob in one write-lock interval"
```

---

## Task 12: Orphan-blob reclamation

**Files:**
- Modify: `src/atoms/store/blobs.py`
- Modify: `src/atoms/store/connection.py`
- Modify: `src/atoms/store/__init__.py`
- Modify: `tests/test_store_blobs.py`
- Modify: `tests/test_store_liveness.py`

**Interfaces:**
- Consumes: Task 10's `verify_leaf`, `leaf_to_digest`, `blobs_fd`; Task 11's `INSERT_BLOB`.
- Produces: `list_unindexed_blobs(store)`, `remove_unindexed_blob(store, digest)`, and the two `Store`
  methods.

**This is not the garbage collection §2.2 excludes.** That non-goal cites authority §7.5, which is
terminal cleanup *after* `COMMITTED` is durable and turns on whether a live record still references a
blob. What is needed here is authority §7.3's pre-COMMIT reclamation, whose predicate is strictly
simpler: *is there a row at all*.

**Removal takes `BEGIN IMMEDIATE`, and that is necessary without being sufficient.** A deferred
transaction fixes its snapshot at its first query and holds no write lock, so another connection can
insert and commit the blob row while the recheck still reads the older snapshot — measured. `IMMEDIATE`
blocks that writer, and Task 11's transaction binding is what completes it: no writer can be *between*
publishing a leaf and indexing it while another connection holds the write lock.

**A leaf whose name is not well-formed hex raises rather than being returned.** The return type is
digests and removal validates against the grammar, so no value satisfying it names a leaf like
`tmp.part` — the enumeration would hand back something its own removal refuses. It is also not a
survivor: every leaf is created by §8.1 step 2 from a digest that already passed §5.5, so leaf names are
fixed-width hex *by construction*.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_store_blobs.py`:

```python
def test_a_promoted_orphan_is_listed_and_removed(opened_store, store_binding):
    with opened_store.create_workspace("tx1") as workspace:
        stage(workspace, "one", b"orphaned")
        with pytest.raises(ProtocolError):
            with opened_store.transaction() as txn:
                txn.promote_staging(workspace, _manifest(("one", b"orphaned")))
    digest = digest_of(b"orphaned")
    assert opened_store.list_unindexed_blobs() == (digest,)
    opened_store.remove_unindexed_blob(digest)
    assert opened_store.list_unindexed_blobs() == ()
    with child_dir(store_binding.metadata_root_fd, "blobs/sha256") as blobs_fd:
        assert os.listdir(blobs_fd) == []


def test_an_indexed_digest_is_refused_and_still_exists(promoted_blob):
    """The fail-closed direction: the operation can only ever delete something no record
    names, so a caller passing a stale digest from an earlier enumeration destroys
    nothing."""
    store, digest, _content = promoted_blob
    assert digest not in store.list_unindexed_blobs()
    with pytest.raises(ProtocolError):
        store.remove_unindexed_blob(digest)
    os.close(store.open_blob(digest))


def test_removing_an_already_absent_leaf_raises_protocol_error(opened_store):
    with pytest.raises(ProtocolError):
        opened_store.remove_unindexed_blob(f"sha256:{HEX}")


@pytest.mark.parametrize("kind", ["symlink", "directory", "wrong_content"])
def test_an_unverifiable_orphan_is_preserved(opened_store, store_binding, kind):
    """A well-formed name is not enough: a symlink, a directory, or a regular file whose
    bytes do not hash to its name is as impossible a product of promotion as a name that
    is not hex, and unlinking it would destroy the only evidence of a foreign write."""
    digest = digest_of(b"pretend")
    leaf = digest_to_leaf(digest)
    with child_dir(store_binding.metadata_root_fd, "blobs/sha256") as blobs_fd:
        if kind == "symlink":
            os.symlink("/etc/passwd", leaf, dir_fd=blobs_fd)
        elif kind == "directory":
            os.mkdir(leaf, dir_fd=blobs_fd)
        else:
            fd = os.open(leaf, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600, dir_fd=blobs_fd)
            try:
                os.write(fd, b"different")
            finally:
                os.close(fd)
        with pytest.raises(MetadataStoreInvalid):
            opened_store.remove_unindexed_blob(digest)
        assert leaf in os.listdir(blobs_fd)


def test_a_leaf_that_is_not_well_formed_hex_refuses_the_enumeration(
    opened_store, store_binding
):
    with child_dir(store_binding.metadata_root_fd, "blobs/sha256") as blobs_fd:
        fd = os.open("tmp.part", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600, dir_fd=blobs_fd)
        os.close(fd)
        with pytest.raises(MetadataStoreInvalid):
            opened_store.list_unindexed_blobs()
        assert "tmp.part" in os.listdir(blobs_fd)


def test_removal_is_refused_inside_a_write_transaction(opened_store):
    with opened_store.transaction() as txn:
        txn.insert_record("tx1", one_effect_spec())
        with pytest.raises(ProtocolError):
            opened_store.remove_unindexed_blob(f"sha256:{HEX}")


def test_a_reclaimer_does_not_race_another_stores_promotion(store_on):
    """One store promotes and indexes inside a write transaction while the other reclaims,
    with the reclaimer's transaction opened after the renames and before the COMMIT. The
    assertion is the pair: no committed record names a missing leaf, and no leaf is
    unlinked while a row for it commits."""
    import threading

    from atoms.store.connection import open_store
    from tests.store_support import one_effect_spec

    content = b"contested"
    digest = digest_of(content)
    with store_on() as binding:
        with open_store(binding) as writer, open_store(binding) as reclaimer:
            with writer.create_workspace("tx1") as workspace:
                stage(workspace, "one", content)
                started = threading.Event()
                outcome: dict[str, object] = {}

                def reclaim():
                    started.wait(5)
                    try:
                        for candidate in reclaimer.list_unindexed_blobs():
                            reclaimer.remove_unindexed_blob(candidate)
                        outcome["error"] = None
                    except Exception as caught:  # noqa: BLE001 -- recorded, then asserted
                        outcome["error"] = caught

                thread = threading.Thread(target=reclaim)
                thread.start()
                with writer.transaction() as txn:
                    txn.promote_staging(
                        workspace,
                        (StagedBlob(name="one", digest=digest, byte_len=len(content)),),
                    )
                    txn.insert_record("tx1", spec_referencing(content))
                    started.set()
                thread.join(10)
        with open_store(binding) as store:
            record = store.read_record("tx1")
            assert record is not None
            os.close(store.open_blob(digest))
```

Add `from tests.store_support import spec_referencing` to the imports.

Append to `tests/test_store_liveness.py`:

```python
def test_the_gate_runs_before_the_orphan_unlink(store_on, monkeypatch):
    """Orphan removal hashes the leaf first, which is unbounded work."""
    from atoms.store import blobs as blobs_module
    from atoms.store.connection import open_store
    from tests.store_support import child_dir, digest_of, stage

    with store_on() as binding:
        store = open_store(binding)
        content = b"orphaned"
        digest = digest_of(content)
        with store.create_workspace("tx1") as workspace:
            stage(workspace, "one", content)
            with pytest.raises(ProtocolError):
                with store.transaction() as txn:
                    txn.promote_staging(
                        workspace,
                        (blobs_module.StagedBlob(
                            name="one", digest=digest, byte_len=len(content)
                        ),),
                    )

        real = blobs_module.verify_leaf

        def release_then_verify(fd, digest_, byte_len):
            result = real(fd, digest_, byte_len)
            binding.__exit__(None, None, None)
            return result

        monkeypatch.setattr(blobs_module, "verify_leaf", release_then_verify)
        with pytest.raises(ProtocolError):
            store.remove_unindexed_blob(digest)
        store.close()

    with child_dir(binding.metadata_root_fd, "blobs/sha256") as blobs_fd:
        assert os.listdir(blobs_fd) != [], "the leaf was unlinked after the lease ended"


def test_remove_workspace_is_not_exempt_from_the_gate(store_on):
    """It issues rmdir and directory flushes -- durable mutations of an engine-owned
    namespace, not a release. Running one after the lock is released is precisely the race
    authority §7.1's universal lease exists to prevent (design §5.4)."""
    from atoms.store.connection import open_store

    with store_on() as binding:
        store = open_store(binding)
        workspace = store.create_workspace("tx1")
    with pytest.raises(ProtocolError):
        store.remove_workspace(workspace)
    workspace.close()  # exempt, and must still succeed
    store.close()


def test_open_blob_is_refused_inside_this_stores_write_transaction(store_on):
    """Run as promote_staging followed by open_blob on a just-promoted digest -- the
    sequence that would otherwise expose a row and a blob that a rollback erases."""
    from atoms.store.blobs import StagedBlob
    from atoms.store.connection import open_store
    from tests.store_support import digest_of, spec_referencing, stage

    content = b"in flight"
    digest = digest_of(content)
    with store_on() as binding:
        with open_store(binding) as store:
            with store.create_workspace("tx1") as workspace:
                stage(workspace, "capture", content)
                with store.transaction() as txn:
                    txn.promote_staging(
                        workspace,
                        (StagedBlob(name="capture", digest=digest, byte_len=len(content)),),
                    )
                    txn.insert_record("tx1", spec_referencing(content))
                    with pytest.raises(ProtocolError):
                        store.open_blob(digest)


def _release_after(monkeypatch, module, name, binding, occurrence):
    """Release the project lock after the `occurrence`-th call to `module.name`.

    The gate is asserted by position rather than by counting gates: if a gate runs
    immediately before every one of these syscalls, then releasing the lock after
    occurrence k must make occurrence k+1 raise *before* it happens. A single gate at the
    front of the operation passes the ProtocolError half of every case below and fails the
    filesystem half of all of them.
    """
    real = getattr(module, name)
    calls = {"n": 0}

    def wrapper(*args, **kwargs):
        result = real(*args, **kwargs)
        calls["n"] += 1
        if calls["n"] == occurrence:
            binding.__exit__(None, None, None)
        return result

    monkeypatch.setattr(module, name, wrapper)


def test_the_gate_runs_between_create_workspaces_two_mkdirs(store_on, monkeypatch):
    """Lock loss after the first would create staging/<txid>/ under a live lease and
    work/<txid>/ after it ended, while the next owner reclaims the staging-only
    survivor."""
    from tests.store_support import child_dir

    from atoms.store.connection import open_store

    with store_on() as binding:
        store = open_store(binding)
        _release_after(monkeypatch, os, "mkdir", binding, 1)
        with pytest.raises(ProtocolError):
            store.create_workspace("tx1")
        store.close()
    with child_dir(binding.metadata_root_fd, "work") as work_fd:
        assert "tx1" not in os.listdir(work_fd)


def test_the_gate_runs_before_promotions_staging_rmdir(store_on, monkeypatch):
    """The last mutation of a successful batch, and the one an implementation that gates
    only inside loops leaves unauthorized."""
    from atoms.store.blobs import StagedBlob
    from atoms.store.connection import open_store
    from tests.store_support import child_dir, digest_of, spec_referencing, stage

    content = b"one"
    with store_on() as binding:
        store = open_store(binding)
        workspace = store.create_workspace("tx1")
        stage(workspace, "capture", content)
        # step 3 flushes blobs/sha256/ then staging/<txid>/; release after the second.
        _release_after(monkeypatch, binding.backend.__class__, "flush_directory", binding, 2)
        with pytest.raises(ProtocolError):
            with store.transaction() as txn:
                txn.promote_staging(
                    workspace,
                    (StagedBlob(
                        name="capture", digest=digest_of(content), byte_len=len(content)
                    ),),
                )
                txn.insert_record("tx1", spec_referencing(content))
        workspace.close()
        store.close()
    with child_dir(binding.metadata_root_fd, "staging") as staging_fd:
        assert "tx1" in os.listdir(staging_fd), "the rmdir ran after the lease ended"


def test_the_gate_runs_between_the_eexist_hash_and_the_source_unlink(store_on, monkeypatch):
    """§8.2 hashes the destination first, which is unbounded, and then unlinks the staged
    source -- a mutation like any other, sitting outside the rename loop."""
    from atoms.store import blobs as blobs_module
    from atoms.store.blobs import StagedBlob
    from atoms.store.connection import open_store
    from tests.store_support import child_dir, digest_of, spec_referencing, stage

    content = b"shared"
    digest = digest_of(content)
    with store_on() as binding:
        store = open_store(binding)
        with child_dir(binding.metadata_root_fd, "blobs/sha256") as blobs_fd:
            fd = os.open(
                blobs_module.digest_to_leaf(digest),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600, dir_fd=blobs_fd,
            )
            try:
                os.write(fd, content)
            finally:
                os.close(fd)
        workspace = store.create_workspace("tx1")
        stage(workspace, "capture", content)

        real = blobs_module.verify_leaf
        seen = {"n": 0}

        def release_on_the_destination_hash(fd_, digest_, byte_len):
            result = real(fd_, digest_, byte_len)
            seen["n"] += 1
            if seen["n"] == 2:  # 1 = the staged source in preflight, 2 = the destination
                binding.__exit__(None, None, None)
            return result

        monkeypatch.setattr(blobs_module, "verify_leaf", release_on_the_destination_hash)
        with pytest.raises(ProtocolError):
            with store.transaction() as txn:
                txn.promote_staging(
                    workspace,
                    (StagedBlob(name="capture", digest=digest, byte_len=len(content)),),
                )
                txn.insert_record("tx1", spec_referencing(content))
        assert "capture" in os.listdir(workspace._staging_fd)
        workspace.close()
        store.close()


@pytest.mark.parametrize("half", ["staging", "work"])
def test_the_gate_runs_before_each_of_remove_workspaces_rmdirs(store_on, monkeypatch, half):
    """Both gates were specified and neither was exercised; the tier named only
    promotion's rmdir, which is how a specified-but-unarmed gate stays that way."""
    from atoms.store.connection import open_store
    from tests.store_support import child_dir, stage

    with store_on() as binding:
        store = open_store(binding)
        workspace = store.create_workspace("tx1")
        if half == "staging":
            stage(workspace, "capture", b"x")
            # release after the single unlink, immediately before the staging rmdir
            _release_after(monkeypatch, os, "unlink", binding, 1)
        else:
            # staging is empty, so its rmdir is the first; release after it
            _release_after(monkeypatch, os, "rmdir", binding, 1)
        with pytest.raises(ProtocolError):
            store.remove_workspace(workspace)
        workspace.close()
        store.close()
    with child_dir(binding.metadata_root_fd, half) as parent_fd:
        assert "tx1" in os.listdir(parent_fd)


def test_the_gate_runs_before_creations_open_and_fchmod(store_on, monkeypatch):
    """open_store mutates before any Store exists: openat(O_CREAT) creates a name and
    fchmod changes a mode. An inventory that begins after initialization misses both."""
    from atoms.store import connection as connection_module
    from atoms.store.connection import open_store

    with store_on() as binding:
        binding.__exit__(None, None, None)
        with pytest.raises(ProtocolError):
            open_store(binding)
        assert "atoms.db" not in os.listdir(binding._metadata_root_fd_snapshot)


def test_the_gate_runs_before_the_wal_transition(store_on, monkeypatch):
    """PRAGMA journal_mode=WAL persistently converts the database file, so it is a
    mutation and is gated like any other."""
    from atoms.store import connection as connection_module
    from atoms.store.connection import create_store, open_store
    from tests.store_support import raw_connect

    with store_on() as binding:
        _release_after(monkeypatch, connection_module, "publish_entry", binding, 1)
        with pytest.raises(ProtocolError):
            create_store(binding)
    raw = raw_connect(binding)
    try:
        assert raw.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    finally:
        raw.close()


def test_the_gate_runs_before_the_repair_chmod(store_on, monkeypatch):
    """§5.2 step 2 repairs a mode the umask reduced; that chmod is a mutation too."""
    from atoms.store import connection as connection_module
    from atoms.store.connection import reopen_store

    previous = os.umask(0o277)
    try:
        with store_on() as binding:
            fd = os.open(
                "atoms.db", os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_RDWR, 0o600,
                dir_fd=binding.metadata_root_fd,
            )
            os.close(fd)
            binding.__exit__(None, None, None)
            with pytest.raises(ProtocolError):
                reopen_store(binding)
    finally:
        os.umask(previous)
    del connection_module
```

Two of these need a handle on the metadata-root descriptor after the binding is closed, which
`ProjectBinding` does not expose. **Capture it before releasing**: bind
`root_fd = binding.metadata_root_fd` at the top of the `with` block and assert against
`os.listdir(root_fd)`; the descriptor stays open until the fixture tears the volume down. Replace
`binding._metadata_root_fd_snapshot` in `test_the_gate_runs_before_creations_open_and_fchmod`
with that local, and do the same in any other case that asserts after the release.

The `binding.backend.__class__` patch in the promotion case reaches the `LinuxBackend` method, so
restore it through `monkeypatch` only — never assign it directly, or a later test in the same
session inherits the wrapper.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_store_blobs.py -k unindexed -v`
Expected: FAIL with `AttributeError: 'Store' object has no attribute 'list_unindexed_blobs'`.

- [ ] **Step 3: Add the two operations**

Append to `src/atoms/store/blobs.py`:

```python
def list_unindexed_blobs(store: Store) -> tuple[str, ...]:
    """The digests whose leaf exists with no blob row (design §7.3).

    A deferred read is enough: the result is advisory, since removal re-checks.
    """
    parent = blobs_fd(store)
    try:
        leaves = sorted(os.listdir(parent))
    finally:
        os.close(parent)
    orphans: list[str] = []
    with store._read_transaction() as connection:
        for leaf in leaves:
            digest = leaf_to_digest_or_refuse(leaf)
            if connection.execute(SELECT_BLOB, (digest,)).fetchone() is None:
                orphans.append(digest)
    return tuple(orphans)


def leaf_to_digest_or_refuse(leaf: str) -> str:
    """Design §8.5 for blobs/sha256/.

    Every leaf is created by §8.1 step 2 from a digest that already passed §5.5, so a name
    that is not fixed-width hex was not written by promotion -- it is a foreign write into
    an engine-owned directory. Returning it is impossible (removal would refuse the string
    the enumeration handed back) and skipping it leaves debris no later pass revisits.
    """
    try:
        return leaf_to_digest(leaf)
    except ProtocolError as caught:
        raise MetadataStoreInvalid(
            f"{BLOBS_PARENT}/{leaf!r} is not a name promotion could have written; "
            "leaf names are fixed-width hex by construction"
        ) from caught


def remove_unindexed_blob(store: Store, digest: str) -> None:
    """Re-check membership and unlink under one BEGIN IMMEDIATE (design §7.3).

    IMMEDIATE rather than the deferred read every other read takes, because this operation
    mutates the engine-owned namespace and is therefore a writer -- the same distinction
    §5.4 draws when it refuses to exempt remove_workspace from the liveness gate.
    """
    from atoms.store.connection import gate

    require_digest(digest)
    store._require_live()
    if store._active_transaction is not None:
        raise ProtocolError(
            "remove_unindexed_blob opens its own write transaction and cannot nest"
        )
    with translated("beginning reclamation"):
        store._connection.execute(_BEGIN_IMMEDIATE_SQL)
    try:
        row = store._connection.execute(SELECT_BLOB, (digest,)).fetchone()
        if row is not None:
            raise ProtocolError(
                f"{digest} is indexed; remove_unindexed_blob can only ever delete "
                "something no record names"
            )
        parent = blobs_fd(store)
        try:
            try:
                fd = os.open(
                    digest_to_leaf(digest),
                    os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                    dir_fd=parent,
                )
            except FileNotFoundError as caught:
                raise ProtocolError(
                    f"no leaf for {digest}; the argument is stale, re-enumerate"
                ) from caught
            except OSError as caught:
                if caught.errno == errno.ELOOP:
                    raise MetadataStoreInvalid(
                        f"the leaf for {digest} is a symlink; no permitted producer could "
                        "have written it"
                    ) from caught
                raise
            try:
                verify_leaf(fd, digest, None)
            finally:
                os.close(fd)
            gate(store._binding)
            os.unlink(digest_to_leaf(digest), dir_fd=parent)
            store._binding.backend.flush_directory(parent)
        finally:
            os.close(parent)
    except BaseException:
        store._connection.execute(_ROLLBACK_SQL)
        raise
    with translated("ending reclamation"):
        store._connection.execute(_COMMIT_SQL)
```

Add `_BEGIN_IMMEDIATE_SQL = "BEGIN IMMEDIATE"`, `_COMMIT_SQL = "COMMIT"`, and
`_ROLLBACK_SQL = "ROLLBACK"` as module-level constants in `blobs.py`, plus `import errno` and
`from atoms.store.errors import translated`. They are duplicated from `connection.py` rather
than imported because the inventory rule in Task 13 is stated over module-level constants and
a re-export would read as a second writer.

Append to `Store`:

```python
    def list_unindexed_blobs(self) -> tuple[str, ...]:
        return list_unindexed_blobs(self)

    def remove_unindexed_blob(self, digest: str) -> None:
        remove_unindexed_blob(self, digest)
```

- [ ] **Step 4: Restore `__init__.py`**

If Task 1 Step 3's fallback was used, restore the real body now — every name it imports exists.

- [ ] **Step 5: Run the tests and the gates**

```bash
uv run pytest tests/ -v
uv run ruff check src/atoms/store tests/
uv run pyright
```
Expected: PASS, `All checks passed!`, `0 errors`.

- [ ] **Step 6: Commit**

```bash
git add src/atoms/store tests/
git commit -m "feat(store): reclaim a promoted blob nothing references, fail-closed"
```

---

## Task 13: The architecture guard, the ledger, and the status

**Files:**
- Create: `tests/test_store_architecture.py`
- Create: `tests/test_store_process.py`
- Create: `tests/store_child.py`
- Modify: `tests/test_fs_architecture.py`
- Modify: `docs/deferred-obligation-ledger.md`
- Modify: `AGENTS.md`

**Interfaces:**
- Consumes: `tests/architecture_support.py`'s `fixture_names`, `decorator_name`, `parametrize_names`.
- Produces: no production code. This task is the whole-package guard.

**The SQL inventory guard is the static half of ledger #22.** §8.1's ordering — verify, publish, flush,
then index — is worth nothing if a second site can write a `blob` row without it. Counting occurrences
of `INSERT INTO blob` proves almost nothing: `REPLACE INTO`, `INSERT OR REPLACE`, `UPDATE blob`,
`DELETE FROM blob`, a trigger writing the table, and the same insert with different whitespace all pass
it. The rule is stated over *parsed statements*, and it rests on a property asserted first — that every
statement is a module-level constant, so the inventory is finite and enumerable.

- [ ] **Step 1: Write the architecture tests**

Create `tests/test_store_architecture.py`:

```python
"""Tier 6 -- properties of the package as a whole (design §11.6)."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

import atoms.store
from tests.architecture_support import decorator_name, fixture_names, parametrize_names

PACKAGE = Path(atoms.store.__file__).parent
SOURCES = sorted(p for p in PACKAGE.glob("*.py"))
TESTS = Path(__file__).parent


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _imported_modules(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


@pytest.mark.parametrize(
    "path",
    sorted(
        p for p in (Path(atoms.__file__).parent / "fs").glob("*.py")  # type: ignore[name-defined]
    )
    + sorted((Path(atoms.__file__).parent / "core").rglob("*.py")),  # type: ignore[name-defined]
    ids=lambda p: p.name,
)
def test_neither_fs_nor_core_imports_the_store(path):
    """The dependency runs store -> fs -> core (design §4.2). This extends the existing
    test_core_never_imports_the_filesystem_layer guard rather than inventing a second
    scheme."""
    assert not any(
        module.startswith("atoms.store") for module in _imported_modules(_tree(path))
    ), f"{path.name} imports atoms.store"


def test_the_public_surface_is_exactly_the_documented_names():
    assert atoms.store.__all__ == (
        "StagedBlob", "Store", "StoredRecord", "Workspace", "open_store"
    )
    exported = {
        name for name in dir(atoms.store)
        if not name.startswith("_") and name not in {"blobs", "connection", "records",
                                                     "schema", "workspace", "errors"}
    }
    assert exported == set(atoms.store.__all__)


def test_the_transaction_type_is_not_exported():
    assert "StoreTransaction" not in dir(atoms.store)
    assert "_StoreTransaction" not in atoms.store.__all__


def test_the_store_exports_no_sqlite_connection():
    import sqlite3

    for name in atoms.store.__all__:
        value = getattr(atoms.store, name)
        assert value is not sqlite3.Connection


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_no_raw_fsync_appears_in_the_package(path):
    """On Linux Backend.flush_file is os.fsync, so a behavioural test here would pass with
    the raw version in place and the defect would surface only on macOS, where F_FULLFSYNC
    is the difference between a flush and a durable one."""
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Attribute) and node.attr in ("fsync", "fdatasync"):
            pytest.fail(f"{path.name} calls os.{node.attr} directly")


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_no_blanket_oserror_handler(path):
    for node in ast.walk(_tree(path)):
        if not isinstance(node, ast.ExceptHandler):
            continue
        caught = node.type
        if caught is None:
            pytest.fail(f"{path.name} has a bare except")
        names = (
            [caught] if not isinstance(caught, ast.Tuple) else list(caught.elts)
        )
        for name in names:
            label = name.id if isinstance(name, ast.Name) else getattr(name, "attr", "")
            if label == "OSError":
                # Permitted only when the body discriminates on errno and re-raises.
                source = ast.unparse(node)
                assert "errno" in source and "raise" in source, (
                    f"{path.name} catches OSError without discriminating on errno"
                )


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_every_database_error_handler_contains_a_bare_raise(path):
    """§9.1's default is `raise`, bare -- not `raise X from caught` -- so an unrecognized
    code keeps its class and its traceback."""
    for node in ast.walk(_tree(path)):
        if not isinstance(node, ast.ExceptHandler):
            continue
        label = ast.unparse(node.type) if node.type else ""
        if "DatabaseError" not in label:
            continue
        assert any(
            isinstance(inner, ast.Raise) and inner.exc is None
            for inner in ast.walk(node)
        ), f"{path.name}'s DatabaseError handler has no bare raise"


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_executescript_is_never_called(path):
    """Over parsed calls, not over source text.

    `executescript` issues a COMMIT before running, which under `isolation_level=None`
    ends the explicit transaction initialization depends on -- so several docstrings in
    the package say exactly that, by name. A guard that greps the source would force
    those docstrings to lie about what they are explaining.
    """
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr != "executescript", f"{path.name} calls executescript"


def _module_bindings(tree: ast.Module) -> list[tuple[str, ast.expr]]:
    """Every module-level `NAME = value` and `NAME: T = value`, as (name, value node).

    AnnAssign matters: `SCHEMA_STATEMENTS: tuple[str, ...] = (...)` is one, and a helper
    that walked only `ast.Assign` would silently not see the package's largest constant.
    """
    bindings: list[tuple[str, ast.expr]] = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and node.value is not None:
            bindings.extend(
                (target.id, node.value)
                for target in node.targets
                if isinstance(target, ast.Name)
            )
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            if isinstance(node.target, ast.Name):
                bindings.append((node.target.id, node.value))
    return bindings


def _string_value(node: ast.expr) -> str | None:
    try:
        value = ast.literal_eval(node)
    except (ValueError, SyntaxError, TypeError):
        return None
    return value if isinstance(value, str) else None


def _sql_constants() -> dict[str, str]:
    """Every module-level name in the package bound to a string literal."""
    constants: dict[str, str] = {}
    for path in SOURCES:
        for name, value in _module_bindings(_tree(path)):
            text = _string_value(value)
            if text is not None:
                constants[f"{path.stem}.{name}"] = text
    return constants


def _is_literal(node: ast.expr) -> bool:
    try:
        ast.literal_eval(node)
    except (ValueError, SyntaxError, TypeError):
        return False
    return True


def _is_string(node: ast.expr) -> bool:
    return _string_value(node) is not None


def _is_nonstring_literal(node: ast.expr) -> bool:
    return _string_value(node) is None and _is_literal(node)


def _local_names(tree: ast.Module, accept) -> set[str]:
    return {name for name, value in _module_bindings(tree) if accept(value)}


def _package_names(path, accept) -> set[str]:
    """Names this module binds to a matching module-level constant, **including ones it
    imports from another `atoms.store` module**.

    The import route is not optional: `connection.py` issues `INSERT_RECORD`, which
    `records.py` owns, and a guard that walked only local assignments would either fail
    on the package's own code or be widened until it proved nothing.
    """
    tree = _tree(path)
    names = _local_names(tree, accept)
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom) or not (node.module or "").startswith(
            "atoms.store."
        ):
            continue
        source = PACKAGE / f"{node.module.rsplit('.', 1)[-1]}.py"
        if not source.exists():
            continue
        exported = _local_names(_tree(source), accept)
        names.update(
            alias.asname or alias.name for alias in node.names if alias.name in exported
        )
    return names


def _literal_iterated_names(tree: ast.Module, iterables: set[str]) -> set[str]:
    """Names bound by iterating a module-level LITERAL constant.

    Two loops in the package do this -- the DDL statements and the pinned pragmas, the
    second unpacking a tuple -- and in both the SQL comes out of a module-level constant
    structure, which is the property the rule is about. `for x in build_them()` and
    `for x in cursor` are not resolutions and are not admitted.
    """
    bound: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.For) or not isinstance(node.iter, ast.Name):
            continue
        if node.iter.id not in iterables:
            continue
        if isinstance(node.target, ast.Name):
            bound.add(node.target.id)
        elif isinstance(node.target, ast.Tuple):
            bound.update(e.id for e in node.target.elts if isinstance(e, ast.Name))
    return bound


def _parameters_fed_only_constants(tree: ast.Module, allowed: set[str]) -> set[str]:
    """Parameters that receive an allowed name at **every** call site in the module.

    `_set_column(UPDATE_STATE, txid, value)` is the shape: four public setters share one
    body and each passes its own constant. Resolving that means reading the call sites,
    which is a complete argument here because the helper is private to this module. A
    parameter fed a constant at one site and a runtime value at another resolves to
    neither -- verified against exactly that pair.
    """
    by_name: dict[str, list[ast.FunctionDef]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            by_name.setdefault(node.name, []).append(node)
    calls: dict[str, list[ast.Call]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = (
            node.func.attr
            if isinstance(node.func, ast.Attribute)
            else node.func.id if isinstance(node.func, ast.Name) else None
        )
        if name in by_name:
            calls.setdefault(name, []).append(node)
    resolved: set[str] = set()
    refused: set[str] = set()
    for name, definitions in by_name.items():
        sites = calls.get(name, [])
        for function in definitions:
            positional = [arg.arg for arg in function.args.args]
            offset = 1 if positional and positional[0] == "self" else 0
            for index, parameter in enumerate(positional[offset:]):
                values: list[ast.expr] = []
                for call in sites:
                    if index < len(call.args):
                        values.append(call.args[index])
                    else:
                        values.extend(
                            k.value for k in call.keywords if k.arg == parameter
                        )
                target = (
                    resolved
                    if values
                    and all(isinstance(v, ast.Name) and v.id in allowed for v in values)
                    else refused
                )
                target.add(parameter)
    return resolved - refused


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_every_execute_argument_resolves_to_a_module_level_constant(path):
    """A positive resolution, not a list of banned spellings: a local assigned from a
    helper, a dict lookup, an attribute off some holder, or a str subclass all walk past
    a ban on f-strings and `%`. Only a bare name this module can be shown to bind to a
    constant passes."""
    tree = _tree(path)
    allowed = _package_names(path, _is_string)
    allowed |= _literal_iterated_names(
        tree, _package_names(path, _is_nonstring_literal)
    )
    allowed |= _parameters_fed_only_constants(tree, allowed)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr not in ("execute", "executemany"):
            continue
        if not node.args:
            pytest.fail(f"{path.name}: execute() with no statement")
        first = node.args[0]
        if not isinstance(first, ast.Name):
            pytest.fail(
                f"{path.name}: execute({ast.unparse(first)}) is not a bare name; only a "
                "name resolving to a module-level SQL constant is permitted"
            )
        assert first.id in allowed, (
            f"{path.name}: execute({first.id}) does not resolve to a module-level SQL "
            "constant of the package"
        )


def test_no_statement_in_the_package_attaches_or_vacuums():
    """Over the statement inventory rather than the source text (criterion 12).

    The test above makes that inventory total, so this covers everything the package can
    execute -- and unlike a grep it does not fire on a docstring or on the authorizer's
    own `sqlite3.SQLITE_ATTACH` constant, which is the code that enforces the rule.
    """
    from atoms.store.schema import SCHEMA_STATEMENTS

    inventory = dict(_sql_constants())
    inventory.update(
        (f"schema.SCHEMA_STATEMENTS[{index}]", text)
        for index, text in enumerate(SCHEMA_STATEMENTS)
    )
    for label, text in inventory.items():
        for banned in ("ATTACH", "DETACH", "VACUUM"):
            assert not re.search(rf"\b{banned}\b", text, re.IGNORECASE), (
                f"{label} spells {banned}"
            )


def test_every_public_transaction_method_poisons_on_failure():
    """Design §7.7's poison rule, as a structural property rather than a convention.

    A method that talks to the connection without going through `_mutating` leaves a
    failure the caller can catch and commit around -- the defect the rule exists for --
    and it fails silently, since the happy path is identical.
    """
    tree = _tree(PACKAGE / "connection.py")
    body = next(
        node.body
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == "_StoreTransaction"
    )
    methods = {
        node.name: node
        for node in body
        if isinstance(node, ast.FunctionDef)
    }

    def opens_mutating(node: ast.FunctionDef) -> bool:
        first = next((s for s in node.body if not _is_docstring(s)), None)
        return (
            isinstance(first, ast.With)
            and any(
                isinstance(item.context_expr, ast.Call)
                and isinstance(item.context_expr.func, ast.Attribute)
                and item.context_expr.func.attr == "_mutating"
                for item in first.items
            )
        )

    def delegates_to_a_mutating_helper(node: ast.FunctionDef) -> bool:
        statements = [s for s in node.body if not _is_docstring(s)]
        if len(statements) != 1:
            return False
        call = statements[0].value if isinstance(statements[0], ast.Expr) else None
        if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute):
            return False
        target = methods.get(call.func.attr)
        return target is not None and opens_mutating(target)

    for name, node in sorted(methods.items()):
        if name.startswith("_"):
            continue
        assert opens_mutating(node) or delegates_to_a_mutating_helper(node), (
            f"_StoreTransaction.{name} does not run inside _mutating(), so a failure "
            "inside it would not poison the transaction"
        )


def _is_docstring(statement: ast.stmt) -> bool:
    return (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Constant)
        and isinstance(statement.value.value, str)
    )


def test_exactly_one_statement_in_the_package_writes_blob():
    """Stated over parsed statements, not substrings: REPLACE, UPDATE, DELETE, and a
    re-spelled INSERT all fail this and all pass a substring count."""
    writers = []
    for label, text in _sql_constants().items():
        head = text.strip().split()
        if not head:
            continue
        verb = head[0].upper()
        if verb not in ("INSERT", "REPLACE", "UPDATE", "DELETE"):
            continue
        target = re.search(
            r"\b(?:INTO|UPDATE|FROM)\s+([A-Za-z_][A-Za-z0-9_]*)", text, re.IGNORECASE
        )
        if target and target.group(1).lower() == "blob":
            writers.append((label, verb))
    assert writers == [("blobs.INSERT_BLOB", "INSERT")], writers


def test_no_trigger_body_writes_blob():
    """A trigger declared ON transaction_record whose body runs INSERT INTO blob writes
    the table on every record write while naming a different subject table."""
    from atoms.store.schema import SCHEMA_STATEMENTS

    for statement in SCHEMA_STATEMENTS:
        if not statement.strip().upper().startswith("CREATE TRIGGER"):
            continue
        body = statement.split("BEGIN", 1)[1].rsplit("END", 1)[0]
        for target in re.finditer(
            r"\b(?:INTO|UPDATE|FROM)\s+([A-Za-z_][A-Za-z0-9_]*)", body, re.IGNORECASE
        ):
            assert target.group(1).lower() != "blob", statement


def test_the_store_fixture_registry_covers_every_test_argument():
    registered = fixture_names(TESTS / "conftest.py")
    for path in sorted(TESTS.glob("test_store_*.py")):
        tree = _tree(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or not node.name.startswith("test_"):
                continue
            provided = parametrize_names(node) | {
                "monkeypatch", "tmp_path", "request", "capsys"
            }
            for argument in (a.arg for a in node.args.args):
                assert argument in registered or argument in provided, (
                    f"{path.name}::{node.name} takes unregistered fixture {argument!r}"
                )
            del decorator_name


def test_a5_status_is_synchronized_across_authority_documents():
    agents = (Path(__file__).parents[2] / "AGENTS.md").read_text(encoding="utf-8")
    assert "A5a" in agents
    assert "A5a designed and unimplemented" not in agents
```

- [ ] **Step 2: Write the fresh-process tier**

Design §11.5 and criterion 39 say **a fresh process**, and that is not a figure of speech: closing
and reopening in the same process shares the page cache, the loaded SQLite library, and — the part
that actually matters — the already-held project lock. A second process has to acquire that lock
itself, and everything A5a persists has to survive being read by code that watched none of it being
written. So the reader is a real subprocess.

Create `tests/store_child.py` — the other process:

```python
"""The reader half of design §11.5's tier-5 claim.

Run as `python -m tests.store_child <project_root> <metadata_root> <txid>`; prints one
JSON object describing what a fresh process reads back. A module rather than an inline
`-c` string because it re-runs the whole bind, including acquiring the project lock,
which is precisely the part a same-process reopen skips.
"""

from __future__ import annotations

import json
import os
import sys

from atoms.core.canonical import canonical_json
from atoms.fs.binding import bind_project_volume
from atoms.fs.linux import LinuxBackend
from atoms.fs.lock import acquire_project_lock
from atoms.fs.volume import StorageProfile
from atoms.store.connection import open_store
from atoms.store.records import referenced_digests
from tests.fs_support import build_test_allowlist

STORAGE = StorageProfile(profile_id="atoms-test-profile")


def main(project_root: str, metadata_root: str, txid: str) -> int:
    with acquire_project_lock(LinuxBackend(), metadata_root) as lock:
        allowlist = build_test_allowlist(lock, project_root, STORAGE)
        with bind_project_volume(
            project_root, lock, allowlist=allowlist, storage=STORAGE
        ) as binding:
            with open_store(binding) as store:
                record = store.read_record(txid)
                if record is None:
                    raise SystemExit(f"no record for txid {txid!r}")
                blobs = {}
                for digest, byte_len in referenced_digests(record.spec):
                    fd = store.open_blob(digest)
                    try:
                        blobs[digest] = len(os.read(fd, byte_len + 1))
                    finally:
                        os.close(fd)
                active = store.read_active()
    print(json.dumps({
        "spec": canonical_json(record.spec),
        "state": record.state.value,
        "journals": [[j.effect_id, j.state.value] for j in record.journals],
        "blobs": blobs,
        "active": None if active is None else active.txid,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:4]))
```

`STORAGE` must equal the `test_storage_profile` fixture's profile — the allowlist match is keyed on
it. If that fixture's `profile_id` differs in this checkout, take the value from
`grep -n "def test_storage_profile" -A 3 tests/conftest.py` and use it here.

Then create `tests/test_store_process.py`:

```python
"""Tier 5 -- the durability claim A5a actually makes (design §11.5)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from atoms.core.canonical import canonical_json
from atoms.store.blobs import StagedBlob
from atoms.store.connection import open_store
from atoms.store.records import referenced_digests
from tests.store_support import digest_of, spec_referencing, stage

ROOT = Path(__file__).resolve().parents[1]


def test_a_committed_record_reads_back_identically_in_a_fresh_process(store_on):
    content = b"durable bytes"
    digest = digest_of(content)
    with store_on() as binding:
        # Read the two roots back off the live descriptors: this works whatever the
        # underlying volume fixture is named in this checkout.
        project_root = os.readlink(f"/proc/self/fd/{binding.project_root_fd}")
        metadata_root = os.readlink(f"/proc/self/fd/{binding.metadata_root_fd}")
        with open_store(binding) as store:
            with store.create_workspace("tx1") as workspace:
                stage(workspace, "capture", content)
                with store.transaction() as txn:
                    txn.promote_staging(
                        workspace,
                        (StagedBlob(name="capture", digest=digest, byte_len=len(content)),),
                    )
                    txn.insert_record("tx1", spec_referencing(content))
                    txn.set_active("tx1")
            written = store.read_record("tx1")
        assert written is not None
        expected = {
            "spec": canonical_json(written.spec),
            "state": written.state.value,
            "journals": [[j.effect_id, j.state.value] for j in written.journals],
            "blobs": {digest: len(content)},
            "active": "tx1",
        }
    # The binding -- and with it the project lock -- is released here. A second process
    # cannot acquire it while this one holds it, which is exactly why the assertion
    # below means something that a reopen in this process would not.
    result = subprocess.run(
        [sys.executable, "-m", "tests.store_child", project_root, metadata_root, "tx1"],
        cwd=str(ROOT), capture_output=True, text=True, check=True,
    )
    assert json.loads(result.stdout) == expected, result.stderr


def test_a_committed_record_never_names_a_missing_blob(store_on):
    """Ledger #22's behavioural half: promote_staging is the sole writer of a blob row,
    it writes only verified digests over flushed leaves, and §7.6 requires every
    referenced digest to have one.

    Walk `referenced_digests`, not a surface. The final surface names content the
    transaction will produce, for which no blob exists or should -- iterating it either
    finds nothing (proving nothing) or asks open_blob for a digest that was never
    promoted.
    """
    content = b"referenced"
    digest = digest_of(content)
    with store_on() as binding:
        with open_store(binding) as store:
            with store.create_workspace("tx1") as workspace:
                stage(workspace, "capture", content)
                with store.transaction() as txn:
                    txn.promote_staging(
                        workspace,
                        (StagedBlob(name="capture", digest=digest, byte_len=len(content)),),
                    )
                    txn.insert_record("tx1", spec_referencing(content))
        with open_store(binding) as reopened:
            record = reopened.read_record("tx1")
            assert record is not None
            resolved = [d for d, _ in referenced_digests(record.spec)]
            assert resolved == [digest]
            for entry in resolved:
                os.close(reopened.open_blob(entry))
```

Cross-process WAL exclusion is **not** re-tested: A4a's `certify_sqlite_wal` proves it at bind
time, and re-asserting it here would duplicate a certified capability.

- [ ] **Step 3: Run the new tiers**

```bash
uv run pytest tests/test_store_architecture.py tests/test_store_process.py -v
```
Expected: PASS. `test_a5_status_is_synchronized_across_authority_documents` fails until Step 5.

- [ ] **Step 4: Record the ledger discharge**

Edit `docs/deferred-obligation-ledger.md`. Move row **#22** out of the open table and into the
discharged table, carrying every original column through "Verification" unchanged and appending
the three discharge columns the A4b-2 record uses. Its evidence is
`tests/test_store_architecture.py::test_exactly_one_statement_in_the_package_writes_blob`,
`::test_no_trigger_body_writes_blob`,
`::test_every_execute_argument_resolves_to_a_module_level_constant`,
`::test_executescript_is_never_called`, and, for the behavioural half,
`tests/test_store_process.py::test_a_committed_record_never_names_a_missing_blob` and
`::test_a_committed_record_reads_back_identically_in_a_fresh_process`.

**#23 stays open and unchanged.** A5a supplies `list_workspaces`, `reopen_workspace`,
`remove_workspace`, `list_unindexed_blobs`, and `remove_unindexed_blob` and invokes none of
them: it holds no lease and cannot know a crash occurred. Do not mark it discharged because the
mechanisms now exist — the entry is about the *invocation*, which is A5b's.

Every other open row is untouched.

- [ ] **Step 5: Synchronize the status**

Edit `AGENTS.md`, replacing the A5 bullet with:

```markdown
- **A5 — durable metadata store and recovery lease: A5a implemented on 2026-08-01, A5b not yet
  designed.** `python/src/atoms/store/` holds the SQLite-WAL store as a mechanism — creation and
  reopen under the verified `metadata_root`, the pinned connection profile, the schema, typed
  record read/write, blob promotion bound to the COMMIT that references it, and per-txid
  workspaces. It discharges ledger #22 and admits #23. A5b composes it into the recovery-resolve
  lease and owns entries #7, #9's enforcement half, #12, #17, #18, #19's part, #21, and #23.
```

- [ ] **Step 6: Run every gate**

```bash
uv run pytest
uv run ruff check
uv run pyright
```
Expected: the whole suite PASSES, `All checks passed!`, `0 errors`.

- [ ] **Step 7: Commit**

```bash
git add tests/ docs/deferred-obligation-ledger.md AGENTS.md
git commit -m "test(store): guard the package boundary and discharge ledger #22"
```

---

## Self-Review

**Spec coverage.** Every section of the design maps to a task:

| Design | Task |
| --- | --- |
| §4.1 module layout, §4.2 dependency direction | 1 (package), 13 (guard) |
| §5.1 creation, §5.3 pinned profile, authorizer, version floors | 3 |
| §5.2 reopen, the zero-length repair, every crash cut | 4 |
| §5.4 liveness | 3, 5, 9, 11, 12 — armed in `test_store_liveness.py` |
| §5.5 name validation | 7 (`require_identifier`), 10 (`require_digest`, `require_component`) |
| §6.1 tables, §6.2 constraints | 1, with the `STRICT` case in 7 |
| §6.3 `spec_json` | 7 (write), 8 (canonical re-encode on read) |
| §6.4 halt diagnostic | 6 |
| §7.1 surface, §7.8 lifecycle | 5, 13 |
| §7.2 blob reading | 10 |
| §7.3 orphan reclamation | 12 |
| §7.4 reads take one snapshot | 8 |
| §7.5 effect order | 8 (`_journal_vector`) |
| §7.6 predicate, §7.7 barrier | 8, with the record-reference half in 11 |
| §8.1 promotion, §8.2 pre-existing, §8.4 already-indexed | 11 |
| §8.3 workspaces | 9 |
| §8.5 the reclaimer rule | 9 (`staging/`, `work/`), 12 (`blobs/sha256/`) |
| §9 error contract, §9.1 SQLite hierarchy | 2, with the AST guard in 13 |
| §11.1–§11.6 verification tiers | 1, 3–13 |
| §12 obligations | 13 |

**One section is deliberately not a task: §10.** It states a limitation rather than a behaviour —
`TEMP_STORE=MEMORY` does not cover every SQLite transient, and the ownerless-directory threat model is
inherited from A4a. There is nothing to build; the ledger paragraph it belongs to is already written.

**One gap the first self-review found and this plan closes.** `referenced_digests` originally walked
both surfaces, which would have made every record refuse at the barrier: a final-surface hash names
content the transaction is about to *produce*, for which no blob exists or should. Blobs are rollback
material — authority §7.3 step 3 captures the *pre*-state — so the predicate walks the initial surface
only. This changed `spec_referencing`, the temporary blob rows in Task 8, and made `one_effect_spec`
(which starts from `ABSENT`) need no blob at all, which is why most record tests commit without one.

**What the fourteenth review found, and what changed.** Each was reproduced before it was closed.

| Finding | Closure |
| --- | --- |
| `reopen_workspace` followed symlinks — the plan's flags opened a symlink to an outside directory and listed it | `_open_child` opens `O_NOFOLLOW \| O_DIRECTORY` and maps `ENOTDIR`/`ELOOP` to `MetadataStoreInvalid`; tested through `reopen_workspace` directly, since the enumeration is not on that path (Task 9; design §8.3, criterion 32) |
| A caught mid-method failure could be committed — measured: statement rollback is not transaction rollback, and the surviving rows commit | `_StoreTransaction._mutating()` poisons on any escape; the exit refuses to COMMIT; Task 13 asserts every public method uses it (Tasks 5, 7, 11, 13; design §7.7, criterion 24) |
| The promotion barrier unioned references across every touched record | `_promoted` is keyed by workspace txid and checked against *that* record (Task 11; criterion 27) |
| `referenced_digests` collapsed a digest declared at two lengths — `compile_spec` accepts that spec, measured | it returns deduplicated `(digest, byte_len)` **pairs**, so both references are checked (Task 8; criterion 23) |
| A failed `COMMIT` left the transaction open — measured, and the next `BEGIN IMMEDIATE` then fails | one exit path with an `in_transaction` test, so every failure closes the transaction (Task 5; design §7.7) |
| `Store.close()` tracked no workspaces, contradicting §7.8; an empty manifest never spent the staging half | a `_workspaces` registry closed by `Store.close()`; the half is spent when step 4's `rmdir` succeeds (Tasks 9, 11; design §8.1 step 4, criterion 16) |
| A malformed halted record escaped as `KeyError` out of the loader | the journal-vector comparison is skipped once effect coverage fails, and reports a finding instead (Task 8; criterion 23) |
| Three claims were unarmed: the snapshot test never committed *between* queries, "fresh process" was a same-process reopen, and ledger #22's test walked the final surface | the snapshot test injects the commit from a trace callback (measured: `(prepared, pending)` inside the read transaction, `(prepared, **started**)` without it); tier 5 runs a real subprocess that acquires the lock itself; the #22 test walks `referenced_digests` (Tasks 8, 13; criteria 15, 37, 39) |
| The SQL inventory guard admitted any attribute and any loop variable — and would have *failed* on the package's own cross-module constants | positive resolution: a bare name bound to a module-level string here or imported from another `atoms.store` module, the target of a loop over a module-level literal, or a parameter fed an allowed name at every call site. Exercised against a hostile module — `holder.sql`, `for row in cursor`, and a helper fed a constant at one site and a runtime value at another are all refused (Task 13; criterion 37) |
| Task gates could not pass: Task 5 ran tests using methods from Task 7, promotion tests inserted a record referencing nothing, and the architecture guard banned the word `executescript` while docstrings use it | Task 5's tests use only Task 5's surface and the `-k` filter is gone; promotion tests use `spec_referencing`; `executescript` is checked over parsed calls, and `ATTACH`/`DETACH`/`VACUUM` over the statement inventory |

Three test bodies that were true but vacuous went with them: a `list_workspaces` assertion of the form
`... or True`, an `os.path.isdir(..., dir_fd=...)` call the stdlib does not accept, and two `os.open`
results never closed.

**Placeholder scan.** Clean. Every step that says "write this" carries the code. The nine-site gate
inventory in Task 12 was briefly a parametrized case with a `raise AssertionError` body; it is now six
named tests with real bodies and a stated technique — release the lock *after* occurrence k of the
target syscall, then assert occurrence k+1 did not happen, which is the shape that fails a
single-gate-at-entry implementation on its filesystem assertion rather than only on its exception.

Three places name work whose exact spelling depends on this checkout and say so rather than guessing:
the `store_on` fixture's underlying volume fixture (Task 3 Step 3), the lock-release seam for the
mid-operation liveness tests (Task 8 Step 1), and the `sqlite_autoindex_*` names if this SQLite build
differs (Task 1 Step 5). Each names the command that settles it.

**Type consistency.** Checked across tasks:

- `require_identifier(label: str, value: object) -> str` — `records.py`, used by `connection.py` and
  `workspace.py`. `require_digest(value: object) -> str` and
  `require_component(label: str, value: object) -> str` — `blobs.py`. All three take `object`, because
  their first job is to refuse a non-`str`.
- `verify_leaf(fd: int, digest: str, byte_len: int | None) -> int` — `None` only from
  `remove_unindexed_blob`, where an orphan has no row to supply one.
- `coherence_findings(connection, txid) -> tuple[str, ...]` returns findings; the two call sites raise
  `MetadataStoreInvalid` (load) and `ProtocolError` (barrier) from the same tuple.
- `referenced_digests(spec) -> tuple[tuple[str, int], ...]` is public in `records.py` because
  `connection.py`'s barrier and `tests/store_child.py` both read it. Pairs rather than a mapping, so a
  digest declared at two lengths keeps both references (§7.6).
- `_StoreTransaction._mutating() -> AbstractContextManager[Store]`, `_poison(cause: BaseException)`,
  `_require_not_poisoned() -> None`; `_touched: set[str]` and `_promoted: dict[str, set[str]]`.
- `_open_child(parent_fd: int, parent: str, txid: str) -> int | None` — `parent` is only for the
  refusal message, and the third argument is the name, which is the order `_parent_fd` reads in.
- `Workspace._store` is `Store | None`, and `_anchor` raises rather than dereferencing `None`.
- `Store.list_workspaces()`, `Store.list_unindexed_blobs()` both return `tuple[str, ...]`;
  `Store.open_blob()` returns `int` and transfers ownership.
- `promote_staging(store, workspace, manifest)` at module level, `promote_staging(workspace, manifest)`
  on the transaction. The module function lost an unused `transaction` parameter during this review.
- `StagedBlob.name`/`.digest` are `str`, `.byte_len` is `int` — and `bool` is refused explicitly,
  because it is an `int` subclass that would pass a naive check.
- `spec_referencing(*contents: bytes)`, `child_dir`, `stage`, `digest_of`, `file_state`,
  `one_effect_spec`, `replace_spec`, `duplicate_effect_spec`, `two_length_spec`, `raw_connect`,
  `matching_diagnostic`, and `every_diagnostic_shape` all live in `tests/store_support.py` and are
  imported by name; none carries a leading underscore, since all are cross-module. `spec_referencing`
  is variadic because promotion is a batch and a two-blob manifest needs a two-reference record.

**Import direction.** `connection.py` imports `workspace.py` and `blobs.py` for its delegating methods,
so those two import `gate` from `connection.py` *inside function bodies*. That is deliberate and Task 9
says so; moving the delegating methods would break the surface `__init__.py` and Task 13 assert.

---

## Execution Handoff

Plan complete and saved to `docs/plans/2026-08-01-plan-a5a-metadata-store.md`. Two execution options:

**1. Subagent-Driven (recommended)** — a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — execute tasks in this session using `superpowers:executing-plans`, batch
execution with checkpoints for review.
