# Plan A2 — Filesystem-independent compilation validation and repeated-path timelines

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `compile_spec(spec) -> CompiledSpec`, the engine's pure compilation trust boundary — thirteen fail-early validation phases over a `TransactionSpec`, plus the repeated-path timelines A3–A8 consume — with zero filesystem, SQLite, or platform dependency.

**Architecture:** Three new modules under `atoms.core`. `paths.py` holds the lexical project-relative path grammar and the Unicode name-equivalence key. `timeline.py` holds the `PathTimeline` value and its continuity rule. `compiler.py` holds `CompiledSpec` and the phase sequence, which is ordered deliberately — the order decides which violation surfaces first, so tests assert on it. Every refusal is `SpecValidationError`; nothing else escapes, including for a `TransactionSpec` constructed directly with ill-typed fields rather than through `build_spec`.

**Tech Stack:** Python ≥3.11, stdlib only (`dataclasses`, `re`, `unicodedata`, `functools`), managed with `uv`; `ruff` + `pyright` + `pytest`. All commands run from `python/`.

**Design authority:** [`2026-07-28-a2-compilation-validation-design.md`](2026-07-28-a2-compilation-validation-design.md), which refines [`2026-07-23-recoverable-fs-effect-engine-design.md`](2026-07-23-recoverable-fs-effect-engine-design.md) §5.3, §5.4, §13.3. Where they disagree, the authority design wins.

## Global Constraints

- **Python floor:** `requires-python = ">=3.11"`. Already set; do not change.
- **No third-party runtime dependency in `atoms.core`.** Stdlib only. Do not add dev dependencies either — `pytest`, `ruff`, `pyright` are the complete set.
- **Layout:** package work under `python/src/atoms/core/`, tests under `python/tests/`. All commands run via `uv run` from `python/`.
- **Tooling:** `ruff` line-length 120; `pyright` `typeCheckingMode = "basic"`; `pytest` `addopts = "-q"`.
- **Content hashes** are `sha256:<64 lowercase hex>`. **Modes** are permission bits only, `0 .. 0o7777`, never type bits.
- **A1 is frozen.** Do not modify any existing module under `atoms/core/`. A2 only adds files.
- **Every refusal is `SpecValidationError`.** No `TypeError`, `KeyError`, `AttributeError`, or assertion may escape `compile_spec` on any input.
- No AI-attribution trailers on commits/PRs/comments. Docs use `~/d/` (never `/home/keith/` or `/mnt/ssd/`).

## Phase-to-task map

The design's thirteen phases are implemented across Tasks 3–5. Use this to find the task owning a rule:

| Phase | Rule | Task |
| --- | --- | --- |
| 1 | Exhaustive structural typing (incl. non-empty effects) | 3 |
| 2 | Fingerprints | 3 |
| 3 | Path grammar | 1 (predicate), 4 (application) |
| 4 | Path alias distinctness | 1 (key), 4 (application) |
| 5 | Effect ID and exact variant shape | 4 |
| 6 | Duplicate effect IDs | 4 |
| 7 | Dependencies | 4 |
| 8 | Surface well-formedness | 4 |
| 9 | Timelines and continuity | 2 (builder), 5 (wiring) |
| 10 | Exact coverage | 5 |
| 11 | Timeline endpoints | 5 |
| 12 | Surface tree consistency | 5 |
| 13 | Created-directory ancestor ordering | 5 |

---

### Task 1: Project-relative path grammar and name-equivalence key

Implements design phase 3's predicate and phase 4's key as a standalone lexical module. Nothing here
touches a filesystem or a spec; it operates on strings.

Two subtleties the tests pin. First, **UTF-8 encodability is a durability requirement**: a Python `str`
may hold unpaired surrogates, which pass every other rule but make A1's `canonical_bytes` raise
`UnicodeEncodeError` — so a spec would compile and then be unserializable. Second, the scratch check uses
A1's **equivalence-aware** `aliases_scratch_sigil` (not the plain `is_scratch_leaf`) and applies to
**every** component, not just the leaf.

**Files:**
- Create: `python/src/atoms/core/paths.py`
- Test: `python/tests/test_paths.py`

**Interfaces:**
- Consumes: `atoms.core.errors` (`SpecValidationError`), `atoms.core.scratch` (`aliases_scratch_sigil`).
- Produces:
  - `require_rel_path(kind: str, value: str) -> str` — returns `value` or raises `SpecValidationError`
    naming `kind`. **Caller guarantees `value` is a `str`**; compiler phase 1 is the type gate.
  - `path_equivalence_key(path: str) -> str` — Unicode caseless-matching key, `NFC(casefold(NFC(p)))`.
  - `ancestors(path: str) -> tuple[str, ...]` — proper ancestors, outermost first. `"a/b/c"` →
    `("a", "a/b")`; `"a"` → `()`.

- [ ] **Step 1: Write the failing test**

`python/tests/test_paths.py`:

```python
import unicodedata

import pytest

from atoms.core.errors import SpecValidationError
from atoms.core.paths import ancestors, path_equivalence_key, require_rel_path


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
    assert path_equivalence_key("docs/A.md") == path_equivalence_key("docs/a.md")
    nfc = unicodedata.normalize("NFC", "café.txt")
    nfd = unicodedata.normalize("NFD", "café.txt")
    assert nfc != nfd
    assert path_equivalence_key(nfc) == path_equivalence_key(nfd)


def test_equivalence_key_separates_genuinely_distinct_paths():
    assert path_equivalence_key("a/b") != path_equivalence_key("a/c")
    assert path_equivalence_key("a/b") != path_equivalence_key("ab")


def test_ancestors_are_proper_and_outermost_first():
    assert ancestors("a/b/c") == ("a", "a/b")
    assert ancestors("a/b") == ("a",)
    assert ancestors("a") == ()
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `python/`): `uv run pytest tests/test_paths.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'atoms.core.paths'`.

- [ ] **Step 3: Write minimal implementation**

`python/src/atoms/core/paths.py`:

```python
"""Project-relative path grammar and Unicode name-equivalence (design §5.4, A2 phases 3-4).

Purely lexical: nothing here consults a filesystem. Resolution, containment, and
volume-specific name folding are A4's (design §6).
"""

from __future__ import annotations

import unicodedata

from atoms.core.errors import SpecValidationError
from atoms.core.scratch import aliases_scratch_sigil


def require_rel_path(kind: str, value: str) -> str:
    """Return ``value`` if it is a well-formed project-relative path, else raise.

    The caller guarantees ``value`` is a ``str``; compiler phase 1 is the type gate, so
    this function performs no defensive type check (design §6).
    """
    if not value:
        raise SpecValidationError(f"{kind} may not be empty")
    if "\x00" in value:
        raise SpecValidationError(f"{kind} {value!r} contains a NUL byte")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        # A lone surrogate passes every other rule here but makes canonical_bytes
        # raise, so a spec would compile and then be unserializable.
        raise SpecValidationError(f"{kind} {value!r} is not encodable as UTF-8: {exc}") from exc
    if value.startswith("/"):
        raise SpecValidationError(f"{kind} {value!r} must be project-relative, not absolute")
    if value.endswith("/"):
        raise SpecValidationError(f"{kind} {value!r} may not end with '/'")
    for component in value.split("/"):
        if not component:
            raise SpecValidationError(f"{kind} {value!r} contains an empty component")
        if component in (".", ".."):
            raise SpecValidationError(f"{kind} {value!r} contains a {component!r} component")
        if aliases_scratch_sigil(component):
            raise SpecValidationError(
                f"{kind} {value!r} contains component {component!r}, which aliases the reserved scratch sigil"
            )
    return value


def path_equivalence_key(path: str) -> str:
    """Unicode caseless-matching key for a path.

    Two paths sharing a key may name one entry on a case- or normalization-insensitive
    volume. This is a deliberately coarse, whole-path approximation: the authoritative
    question is per parent directory and belongs to A4 (design §5.4).
    """
    return unicodedata.normalize("NFC", unicodedata.normalize("NFC", path).casefold())


def ancestors(path: str) -> tuple[str, ...]:
    """Return ``path``'s proper ancestors, outermost first."""
    parts = path.split("/")
    return tuple("/".join(parts[:i]) for i in range(1, len(parts)))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_paths.py -v`
Expected: PASS.

- [ ] **Step 5: Lint, type-check, commit**

```bash
uv run ruff check && uv run pyright
git add python/src/atoms/core/paths.py python/tests/test_paths.py
git commit -m "feat(core): project-relative path grammar and name-equivalence key"
```

---

### Task 2: Repeated-path timelines and continuity

Implements design §5.3 and A2 phase 9. `build_timelines` groups every effect occurrence by path in the
authoritative order — `(effect index, index within the effect)` — and enforces that each occurrence's
post-state equals the next one's pre-state.

**Precondition:** the caller has already validated that every element of `effects` is one of the five
variants. A1's `occurrences()` is a `singledispatch` that raises `TypeError` on anything else, so calling
this with unvalidated input would leak an exception the error contract forbids. Compiler phase 1 is that
gate; Task 5 wires them in the correct order.

**Files:**
- Create: `python/src/atoms/core/timeline.py`
- Test: `python/tests/test_timeline.py`

**Interfaces:**
- Consumes: `atoms.core.errors` (`SpecValidationError`), `atoms.core.effects` (`Effect`, `RelPath`,
  `occurrences`), `atoms.core.fingerprint` (`PathState`).
- Produces:
  - `TimelineOccurrence(effect_id: str, effect_index: int, role: str, pre: PathState, post: PathState)`
    frozen dataclass.
  - `PathTimeline(path: RelPath, occurrences: tuple[TimelineOccurrence, ...])` frozen dataclass.
  - `build_timelines(effects: Sequence[Effect]) -> tuple[PathTimeline, ...]` — path-sorted; raises
    `SpecValidationError` on a discontinuity.

- [ ] **Step 1: Write the failing test**

`python/tests/test_timeline.py`:

```python
import pytest

from atoms.core.effects import (
    CreateDirectory,
    CreateFileNoClobber,
    DeletePath,
    MoveNoClobber,
    ReplaceFile,
)
from atoms.core.errors import SpecValidationError
from atoms.core.fingerprint import ABSENT, DirectoryState, FileState, SymlinkState
from atoms.core.timeline import PathTimeline, TimelineOccurrence, build_timelines

F = FileState(content_hash="sha256:" + "1" * 64, mode=0o644, byte_len=3)
G = FileState(content_hash="sha256:" + "2" * 64, mode=0o644, byte_len=5)
D = DirectoryState(mode=0o755)
L = SymlinkState(target="../t", mode=0o777)


def test_single_effect_yields_a_one_occurrence_timeline():
    timelines = build_timelines([ReplaceFile(effect_id="e1", path="a", pre=F, post=G)])
    assert timelines == (
        PathTimeline(
            path="a",
            occurrences=(
                TimelineOccurrence(effect_id="e1", effect_index=0, role="target", pre=F, post=G),
            ),
        ),
    )


def test_timelines_are_sorted_by_path():
    timelines = build_timelines([
        CreateFileNoClobber(effect_id="e1", path="z", post=F),
        CreateFileNoClobber(effect_id="e2", path="a", post=F),
        CreateFileNoClobber(effect_id="e3", path="m", post=F),
    ])
    assert [t.path for t in timelines] == ["a", "m", "z"]


def test_repeated_path_absent_file_absent_file_is_continuous():
    # The design §5.3 case: one path through four states across four effects.
    effects = [
        CreateFileNoClobber(effect_id="e1", path="p", post=F),
        DeletePath(effect_id="e2", path="p", pre=F),
        CreateFileNoClobber(effect_id="e3", path="p", post=G),
        DeletePath(effect_id="e4", path="p", pre=G),
    ]
    (timeline,) = build_timelines(effects)
    assert timeline.path == "p"
    assert [(o.effect_id, o.effect_index) for o in timeline.occurrences] == [
        ("e1", 0), ("e2", 1), ("e3", 2), ("e4", 3),
    ]
    assert timeline.occurrences[0].pre is ABSENT
    assert timeline.occurrences[-1].post is ABSENT


def test_discontinuity_between_consecutive_occurrences_is_rejected():
    # e2 declares pre=G, but e1 left the path at F.
    effects = [
        CreateFileNoClobber(effect_id="e1", path="p", post=F),
        DeletePath(effect_id="e2", path="p", pre=G),
    ]
    with pytest.raises(SpecValidationError, match="p"):
        build_timelines(effects)


def test_discontinuity_across_kinds_is_rejected():
    effects = [
        CreateDirectory(effect_id="e1", path="p", post=D),
        DeletePath(effect_id="e2", path="p", pre=F),
    ]
    with pytest.raises(SpecValidationError, match="p"):
        build_timelines(effects)


def test_move_contributes_source_before_destination():
    (dst, src) = build_timelines([
        MoveNoClobber(effect_id="e1", source="s", destination="d", source_pre=F)
    ])
    assert dst.path == "d" and src.path == "s"
    assert src.occurrences[0].role == "source"
    assert dst.occurrences[0].role == "destination"
    assert src.occurrences[0].effect_index == dst.occurrences[0].effect_index == 0


def test_ancestor_type_change_timeline_is_continuous():
    # FILE -> ABSENT -> DIRECTORY on p, with a child created afterwards.
    effects = [
        DeletePath(effect_id="e1", path="p", pre=F),
        CreateDirectory(effect_id="e2", path="p", post=D),
        CreateFileNoClobber(effect_id="e3", path="p/q", post=F),
    ]
    timelines = {t.path: t for t in build_timelines(effects)}
    assert [o.pre for o in timelines["p"].occurrences] == [F, ABSENT]
    assert [o.post for o in timelines["p"].occurrences] == [ABSENT, D]
    assert len(timelines["p/q"].occurrences) == 1


def test_symlink_delete_forms_a_timeline():
    (timeline,) = build_timelines([DeletePath(effect_id="e1", path="lnk", pre=L)])
    assert timeline.occurrences[0].pre == L
    assert timeline.occurrences[0].post is ABSENT


def test_empty_effect_sequence_yields_no_timelines():
    # build_timelines is total on an empty sequence; refusing an empty spec is
    # compiler phase 1's job, not this module's.
    assert build_timelines([]) == ()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_timeline.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'atoms.core.timeline'`.

- [ ] **Step 3: Write minimal implementation**

`python/src/atoms/core/timeline.py`:

```python
"""Repeated-path timelines and the continuity rule (design §5.3)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise

from atoms.core.effects import Effect, RelPath, occurrences
from atoms.core.errors import SpecValidationError
from atoms.core.fingerprint import PathState


@dataclass(frozen=True, slots=True)
class TimelineOccurrence:
    """One effect's transition of one path, with its position in the effect order."""

    effect_id: str
    effect_index: int
    role: str
    pre: PathState
    post: PathState


@dataclass(frozen=True, slots=True)
class PathTimeline:
    """Every occurrence touching one path, in authoritative order."""

    path: RelPath
    occurrences: tuple[TimelineOccurrence, ...]


def build_timelines(effects: Sequence[Effect]) -> tuple[PathTimeline, ...]:
    """Group occurrences by path and require a continuous timeline for each.

    The caller guarantees every element of ``effects`` is one of the five variants;
    ``occurrences`` raises ``TypeError`` otherwise, which the compiler's error contract
    forbids escaping. Compiler phase 1 is that gate.
    """
    grouped: dict[RelPath, list[TimelineOccurrence]] = {}
    for index, effect in enumerate(effects):
        for occurrence in occurrences(effect):
            grouped.setdefault(occurrence.path, []).append(
                TimelineOccurrence(
                    effect_id=effect.effect_id,
                    effect_index=index,
                    role=occurrence.role,
                    pre=occurrence.pre,
                    post=occurrence.post,
                )
            )

    timelines: list[PathTimeline] = []
    for path in sorted(grouped):
        items = tuple(grouped[path])
        for earlier, later in pairwise(items):
            if earlier.post != later.pre:
                raise SpecValidationError(
                    f"path {path!r} has a discontinuous timeline: effect {earlier.effect_id!r} "
                    f"leaves it {earlier.post!r} but effect {later.effect_id!r} declares "
                    f"a precondition of {later.pre!r}"
                )
        timelines.append(PathTimeline(path=path, occurrences=items))
    return tuple(timelines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_timeline.py -v`
Expected: PASS. Note `pairwise` yields nothing for a one-occurrence timeline, which is why a
single-effect path is trivially continuous.

- [ ] **Step 5: Lint, type-check, commit**

```bash
uv run ruff check && uv run pyright
git add python/src/atoms/core/timeline.py python/tests/test_timeline.py
git commit -m "feat(core): repeated-path timelines with continuity enforcement"
```

---

### Task 3: `CompiledSpec`, structural typing, and fingerprints (phases 1–2)

Creates `compiler.py` with `CompiledSpec`, `compile_spec`, and the first two phases. After this task
`compile_spec` type-checks exhaustively and validates fingerprints, then returns a canonicalized
`CompiledSpec` with timelines. Phases 3–13 arrive in Tasks 4–5.

**Phase 1 must be exhaustive.** `TransactionSpec` and its members are plain frozen dataclasses that do no
runtime type checking, so a caller bypassing `build_spec` can put any object in any field. Every later
phase reads those fields directly with no defensive checks, so anything phase 1 misses becomes an
`AttributeError` or `TypeError` escaping `compile_spec` — which §6 of the design forbids.

**Every gate tests the exact runtime type, never `isinstance`.** The model is closed, so a subclass is
not a member. Under `isinstance` a subclass passes the gate and then breaks a later phase — by
overriding a method a phase calls, by yielding different members on a second iteration, or by missing
from the `type(...)`-keyed variant tables that phases 1, 3, and 5 index. Writing the gates as
`type(x) is C` and `type(x) in _TABLE` keeps those tables total by construction; it is also what refuses
`bool` where an integer is required, without a `bool` special case. The design's §5 phase 1 states this
contract.

**Files:**
- Create: `python/src/atoms/core/compiler.py`
- Create: `python/tests/support.py`
- Test: `python/tests/test_compiler_structure.py`

**Interfaces:**
- Consumes: `atoms.core.errors` (`SpecValidationError`), `atoms.core.spec` (`SCHEMA_VERSION`,
  `Dependency`, `SurfaceEntry`, `TransactionSpec`), `atoms.core.effects` (all five variants),
  `atoms.core.fingerprint` (all four states), `atoms.core.identifiers` (`require_valid_identifier`),
  `atoms.core.timeline` (`PathTimeline`, `build_timelines`).
- Produces:
  - `CompiledSpec(spec: TransactionSpec, timelines: tuple[PathTimeline, ...])` frozen dataclass.
  - `compile_spec(spec: TransactionSpec) -> CompiledSpec`.
  - `tests/support.py`: `F`, `G`, `D`, `L` state constants and
    `valid_spec(**overrides) -> TransactionSpec`, used by Tasks 3–6.

- [ ] **Step 1: Write the shared test support module**

`python/tests/support.py`:

```python
"""Shared fixtures for the A2 compiler tests."""

from __future__ import annotations

from atoms.core.effects import CreateFileNoClobber
from atoms.core.fingerprint import ABSENT, DirectoryState, FileState, SymlinkState
from atoms.core.spec import TransactionSpec, build_spec

F = FileState(content_hash="sha256:" + "1" * 64, mode=0o644, byte_len=3)
G = FileState(content_hash="sha256:" + "2" * 64, mode=0o644, byte_len=5)
D = DirectoryState(mode=0o755)
L = SymlinkState(target="../target", mode=0o777)
EMPTY = FileState(
    content_hash="sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    mode=0o644,
    byte_len=0,
)
DIGEST = "sha256:" + "0" * 64


def valid_spec(**overrides) -> TransactionSpec:
    """A minimal spec that passes every phase, with fields replaceable by keyword.

    Overrides are applied to the built spec by direct construction, so a test may
    install a deliberately malformed value that ``build_spec`` would not produce.
    """
    spec = build_spec(
        consumer_tag="cnsmr",
        intent_digest=DIGEST,
        initial_surface={"a.txt": ABSENT},
        final_surface={"a.txt": F},
        effects=(CreateFileNoClobber(effect_id="e1", path="a.txt", post=F),),
    )
    if not overrides:
        return spec
    fields = {
        "schema_version": spec.schema_version,
        "consumer_tag": spec.consumer_tag,
        "intent_digest": spec.intent_digest,
        "initial_surface": spec.initial_surface,
        "final_surface": spec.final_surface,
        "effects": spec.effects,
        "dependencies": spec.dependencies,
    }
    fields.update(overrides)
    return TransactionSpec(**fields)
```

- [ ] **Step 2: Write the failing test**

`python/tests/test_compiler_structure.py`:

```python
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
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_compiler_structure.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'atoms.core.compiler'`.

- [ ] **Step 4: Write minimal implementation**

`python/src/atoms/core/compiler.py`:

```python
"""The compilation trust boundary (design §5.4).

``compile_spec`` proves every filesystem-independent invariant a ``TransactionSpec`` must
satisfy, in a fixed phase order, and returns a frozen ``CompiledSpec`` that A3-A8 may trust.
Filesystem-dependent checks — root and metadata-root identity, ancestor traversal, volume
name folding, capability availability, live preconditions — remain A4's.

Every refusal is ``SpecValidationError``; no other exception escapes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from atoms.core.effects import (
    CreateDirectory,
    CreateFileNoClobber,
    DeletePath,
    MoveNoClobber,
    ReplaceFile,
)
from atoms.core.errors import SpecValidationError
from atoms.core.fingerprint import (
    AbsentState,
    DirectoryState,
    FileState,
    PathState,
    SymlinkState,
)
from atoms.core.identifiers import require_valid_identifier
from atoms.core.spec import SCHEMA_VERSION, Dependency, SurfaceEntry, TransactionSpec
from atoms.core.timeline import PathTimeline, build_timelines

SHA256_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")

# SHA-256 of the empty byte string. A zero-length file must carry exactly this hash.
EMPTY_CONTENT_HASH = "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

MAX_MODE = 0o7777

# Per variant: the path-valued field names and the state-valued field names.
# Membership in this table is also phase 1's variant gate: the effect set is closed, so
# being a key here is exactly what it means to be an effect.
_EFFECT_FIELDS: dict[type, tuple[tuple[str, ...], tuple[str, ...]]] = {
    ReplaceFile: (("path",), ("pre", "post")),
    CreateFileNoClobber: (("path",), ("post",)),
    DeletePath: (("path",), ("pre",)),
    MoveNoClobber: (("source", "destination"), ("source_pre",)),
    CreateDirectory: (("path",), ("post",)),
}

# Per state class: the scalar field names, by kind.
_STATE_STR_FIELDS: dict[type, tuple[str, ...]] = {
    AbsentState: (),
    FileState: ("content_hash",),
    DirectoryState: (),
    SymlinkState: ("target",),
}
_STATE_INT_FIELDS: dict[type, tuple[str, ...]] = {
    AbsentState: (),
    FileState: ("mode", "byte_len"),
    DirectoryState: ("mode",),
    SymlinkState: ("mode",),
}


@dataclass(frozen=True, slots=True)
class CompiledSpec:
    """A ``TransactionSpec`` proven well-formed, with its repeated-path timelines."""

    spec: TransactionSpec
    timelines: tuple[PathTimeline, ...]


def _require(condition: object, message: str) -> None:
    if not condition:
        raise SpecValidationError(message)


# Every gate below tests the exact runtime type rather than `isinstance`. The model is
# closed: the four states, the five variants, and the scalars they hold are the whole
# vocabulary, and nothing downstream is written to survive a member it has never seen.
# A subclass would pass an `isinstance` gate and then break a later phase in a way the
# error contract forbids — by overriding a method a phase calls (`str.startswith`,
# `Dependency.__lt__`), by returning different members on each pass (a `tuple`
# subclass), or by missing from the field tables that phases 1, 3, and 5 index by
# `type(...)`. Requiring the exact type keeps those tables total by construction.


def _require_str(value: Any, what: str) -> str:
    _require(type(value) is str, f"{what} must be a string, got {type(value).__name__}")
    return value


def _require_int(value: Any, what: str) -> int:
    # Exact type also refuses bool, which subclasses int and compares equal to 0 and 1.
    _require(type(value) is int, f"{what} must be an integer, got {type(value).__name__}")
    return value


def _require_tuple(value: Any, what: str) -> tuple[Any, ...]:
    _require(type(value) is tuple, f"{what} must be a tuple, got {type(value).__name__}")
    return value


def _require_state_structure(state: Any, what: str) -> None:
    # `type(...) in` is both the exact-type gate and the guard the two lookups need.
    _require(
        type(state) in _STATE_STR_FIELDS,
        f"{what} must be one of the four path states, got {type(state).__name__}",
    )
    for field in _STATE_STR_FIELDS[type(state)]:
        _require_str(getattr(state, field), f"{what}.{field}")
    for field in _STATE_INT_FIELDS[type(state)]:
        _require_int(getattr(state, field), f"{what}.{field}")


def _phase1_structure(spec: TransactionSpec) -> None:
    """Exhaustive structural typing. Every later phase reads fields with no defensive checks."""
    _require(type(spec) is TransactionSpec, f"spec must be a TransactionSpec, got {type(spec).__name__}")
    _require_int(spec.schema_version, "schema_version")
    _require(
        spec.schema_version == SCHEMA_VERSION,
        f"unsupported schema_version: {spec.schema_version!r} (expected {SCHEMA_VERSION})",
    )
    require_valid_identifier("consumer_tag", _require_str(spec.consumer_tag, "consumer_tag"))
    _require(
        SHA256_DIGEST.fullmatch(_require_str(spec.intent_digest, "intent_digest")) is not None,
        f"intent_digest {spec.intent_digest!r} must match sha256:<64 lowercase hex>",
    )

    for label in ("initial_surface", "final_surface"):
        for index, entry in enumerate(_require_tuple(getattr(spec, label), label)):
            what = f"{label}[{index}]"
            _require(
                type(entry) is SurfaceEntry,
                f"{what} must be a SurfaceEntry, got {type(entry).__name__}",
            )
            _require_str(entry.path, f"{what}.path")
            _require_state_structure(entry.state, f"{what}.state")

    effects = _require_tuple(spec.effects, "effects")
    _require(effects, "spec must declare at least one effect")
    for index, effect in enumerate(effects):
        what = f"effects[{index}]"
        _require(
            type(effect) in _EFFECT_FIELDS,
            f"{what} must be one of the five effect variants, got {type(effect).__name__}",
        )
        _require_str(effect.effect_id, f"{what}.effect_id")
        path_fields, state_fields = _EFFECT_FIELDS[type(effect)]
        for field in path_fields:
            _require_str(getattr(effect, field), f"{what}.{field}")
        for field in state_fields:
            _require_state_structure(getattr(effect, field), f"{what}.{field}")

    for index, dependency in enumerate(_require_tuple(spec.dependencies, "dependencies")):
        what = f"dependencies[{index}]"
        _require(
            type(dependency) is Dependency,
            f"{what} must be a Dependency, got {type(dependency).__name__}",
        )
        _require_str(dependency.before, f"{what}.before")
        _require_str(dependency.after, f"{what}.after")


def _every_state(spec: TransactionSpec) -> list[tuple[PathState, str]]:
    """Every path state reachable from the spec, each with a context label."""
    found: list[tuple[PathState, str]] = []
    for label in ("initial_surface", "final_surface"):
        for index, entry in enumerate(getattr(spec, label)):
            found.append((entry.state, f"{label}[{index}].state"))
    for index, effect in enumerate(spec.effects):
        _, state_fields = _EFFECT_FIELDS[type(effect)]
        for field in state_fields:
            found.append((getattr(effect, field), f"effects[{index}].{field}"))
    return found


def _phase2_fingerprints(spec: TransactionSpec) -> None:
    for state, what in _every_state(spec):
        if isinstance(state, FileState):
            _require(
                SHA256_DIGEST.fullmatch(state.content_hash) is not None,
                f"{what}.content_hash {state.content_hash!r} must match sha256:<64 lowercase hex>",
            )
            _require(state.byte_len >= 0, f"{what}.byte_len must be non-negative, got {state.byte_len}")
            is_empty_hash = state.content_hash == EMPTY_CONTENT_HASH
            _require(
                is_empty_hash == (state.byte_len == 0),
                f"{what} is inconsistent: a byte_len of 0 requires the empty-content hash "
                f"and vice versa (byte_len={state.byte_len}, content_hash={state.content_hash!r})",
            )
        if isinstance(state, SymlinkState):
            _require(state.target, f"{what}.target may not be empty")
            try:
                state.target.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise SpecValidationError(
                    f"{what}.target {state.target!r} is not encodable as UTF-8: {exc}"
                ) from exc
            _require("\x00" not in state.target, f"{what}.target contains a NUL byte")
        if isinstance(state, (FileState, DirectoryState, SymlinkState)):
            _require(
                0 <= state.mode <= MAX_MODE,
                f"{what}.mode must be permission bits in 0..0o7777, got {state.mode:#o}",
            )


def _canonicalize(spec: TransactionSpec) -> TransactionSpec:
    """Sort the set-like fields. Effect order is authoritative and never changed."""
    return TransactionSpec(
        schema_version=spec.schema_version,
        consumer_tag=spec.consumer_tag,
        intent_digest=spec.intent_digest,
        initial_surface=tuple(sorted(spec.initial_surface, key=lambda entry: entry.path)),
        final_surface=tuple(sorted(spec.final_surface, key=lambda entry: entry.path)),
        effects=spec.effects,
        dependencies=tuple(sorted(spec.dependencies)),
    )


def compile_spec(spec: TransactionSpec) -> CompiledSpec:
    """Validate ``spec`` and return a frozen, trusted ``CompiledSpec``.

    The phase order is part of the contract: it decides which violation surfaces first
    when a spec breaks several rules at once.
    """
    _phase1_structure(spec)
    _phase2_fingerprints(spec)
    timelines = build_timelines(spec.effects)
    return CompiledSpec(spec=_canonicalize(spec), timelines=timelines)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_compiler_structure.py -v`
Expected: PASS.

- [ ] **Step 6: Lint, type-check, commit**

`pyright` in `basic` mode may flag the deliberately ill-typed test values (e.g. `path=42`). Add
`# type: ignore[arg-type]` on those specific test lines only — never in `src/`.

```bash
uv run ruff check && uv run pyright
git add python/src/atoms/core/compiler.py python/tests/support.py python/tests/test_compiler_structure.py
git commit -m "feat(core): compilation boundary with exhaustive structural typing and fingerprint rules"
```

---

### Task 4: Path, identity, and dependency phases (phases 3–8)

Adds the six phases between fingerprints and timelines: path grammar, alias distinctness, variant shape,
duplicate IDs, dependencies, and surface well-formedness.

**Files:**
- Modify: `python/src/atoms/core/compiler.py` (add phase functions; extend `compile_spec`)
- Test: `python/tests/test_compiler_paths.py`

**Interfaces:**
- Consumes: everything from Task 3, plus `atoms.core.paths` (`require_rel_path`, `path_equivalence_key`)
  from Task 1.
- Produces: no new public names. `compile_spec` gains phases 3–8.

- [ ] **Step 1: Write the failing test**

`python/tests/test_compiler_paths.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_compiler_paths.py -v`
Expected: FAIL — the alias, variant-shape, duplicate-ID, dependency, and surface tests all fail, because
`compile_spec` currently stops after phase 2.

- [ ] **Step 3: Add phases 3–8 to `compiler.py`**

Add to the imports at the **top** of the module, on the line **after**
`from atoms.core.identifiers import require_valid_identifier` and **before**
`from atoms.core.spec import ...` — ruff's `I001` enforces alphabetical order within the block, so any
other position fails lint:

```python
from atoms.core.paths import path_equivalence_key, require_rel_path
```

Add this table beside the existing `_EFFECT_FIELDS`:

```python
# Per variant: which state classes each state-valued field may legally hold (phase 5).
_ALLOWED_STATES: dict[type, dict[str, tuple[type, ...]]] = {
    ReplaceFile: {"pre": (FileState,), "post": (FileState,)},
    CreateFileNoClobber: {"post": (FileState,)},
    DeletePath: {"pre": (FileState, SymlinkState)},
    MoveNoClobber: {"source_pre": (FileState,)},
    CreateDirectory: {"post": (DirectoryState,)},
}
```

Append the phase functions:

```python
def _declared_paths(spec: TransactionSpec) -> list[tuple[str, str]]:
    """Every declared path with a context label, in a deterministic order."""
    found: list[tuple[str, str]] = []
    for label in ("initial_surface", "final_surface"):
        for index, entry in enumerate(getattr(spec, label)):
            found.append((entry.path, f"{label}[{index}].path"))
    for index, effect in enumerate(spec.effects):
        path_fields, _ = _EFFECT_FIELDS[type(effect)]
        for field in path_fields:
            found.append((getattr(effect, field), f"effects[{index}].{field}"))
    return found


def _phase3_path_grammar(spec: TransactionSpec) -> None:
    for path, what in _declared_paths(spec):
        require_rel_path(what, path)


def _phase4_alias_distinctness(spec: TransactionSpec) -> None:
    first_seen: dict[str, str] = {}
    for path in sorted({path for path, _ in _declared_paths(spec)}):
        key = path_equivalence_key(path)
        previous = first_seen.setdefault(key, path)
        _require(
            previous == path,
            f"declared paths {previous!r} and {path!r} alias one another under Unicode "
            f"caseless matching; they may name a single entry on a case- or "
            f"normalization-insensitive volume",
        )


def _phase5_variant_shapes(spec: TransactionSpec) -> None:
    for index, effect in enumerate(spec.effects):
        what = f"effects[{index}]"
        require_valid_identifier(f"{what}.effect_id", effect.effect_id)
        for field, allowed in _ALLOWED_STATES[type(effect)].items():
            state = getattr(effect, field)
            _require(
                isinstance(state, allowed),
                f"{what} is a {type(effect).__name__}, whose {field!r} may not hold a "
                f"{type(state).__name__}",
            )
        if isinstance(effect, MoveNoClobber):
            _require(
                effect.source != effect.destination,
                f"{what} moves {effect.source!r} onto itself; source and destination must differ",
            )


def _phase6_unique_effect_ids(spec: TransactionSpec) -> None:
    seen: set[str] = set()
    for effect in spec.effects:
        _require(effect.effect_id not in seen, f"duplicate effect_id: {effect.effect_id!r}")
        seen.add(effect.effect_id)


def _phase7_dependencies(spec: TransactionSpec) -> None:
    order = {effect.effect_id: index for index, effect in enumerate(spec.effects)}
    seen: set[tuple[str, str]] = set()
    for index, dependency in enumerate(spec.dependencies):
        what = f"dependencies[{index}]"
        for endpoint in (dependency.before, dependency.after):
            _require(endpoint in order, f"{what} names unknown effect {endpoint!r}")
        _require(
            dependency.before != dependency.after,
            f"{what} makes effect {dependency.before!r} depend on itself",
        )
        edge = (dependency.before, dependency.after)
        _require(edge not in seen, f"duplicate dependency: {dependency.before!r} -> {dependency.after!r}")
        seen.add(edge)
        _require(
            order[dependency.before] < order[dependency.after],
            f"{what} requires {dependency.before!r} before {dependency.after!r}, but the "
            f"authoritative effect order places it after",
        )


def _phase8_surface_shape(spec: TransactionSpec) -> None:
    for label in ("initial_surface", "final_surface"):
        seen: set[str] = set()
        for entry in getattr(spec, label):
            _require(entry.path not in seen, f"{label} declares a duplicate path: {entry.path!r}")
            seen.add(entry.path)
```

Extend `compile_spec` to call them in order:

```python
def compile_spec(spec: TransactionSpec) -> CompiledSpec:
    _phase1_structure(spec)
    _phase2_fingerprints(spec)
    _phase3_path_grammar(spec)
    _phase4_alias_distinctness(spec)
    _phase5_variant_shapes(spec)
    _phase6_unique_effect_ids(spec)
    _phase7_dependencies(spec)
    _phase8_surface_shape(spec)
    timelines = build_timelines(spec.effects)
    return CompiledSpec(spec=_canonicalize(spec), timelines=timelines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_compiler_paths.py tests/test_compiler_structure.py -v`
Expected: PASS.

- [ ] **Step 5: Lint, type-check, commit**

```bash
uv run ruff check && uv run pyright
git add python/src/atoms/core/compiler.py python/tests/test_compiler_paths.py
git commit -m "feat(core): path grammar, alias distinctness, variant shape, and dependency phases"
```

---

### Task 5: Timeline, coverage, and tree phases (phases 9–13)

Completes `compile_spec`. Two ordering constraints from the design are load-bearing here: coverage
(phase 10) must run before endpoints (phase 11), or the endpoint lookup could miss a path no surface
declares; and phase 12 is about declared *states* while phase 13 is about the effect *sequence*, so
neither implies the other.

**Files:**
- Modify: `python/src/atoms/core/compiler.py` (add phase functions; extend `compile_spec`)
- Test: `python/tests/test_compiler_timelines.py`

**Interfaces:**
- Consumes: everything from Tasks 3–4, plus `atoms.core.paths` (`ancestors`) from Task 1.
- Produces: no new public names. `compile_spec` is complete after this task.

- [ ] **Step 1: Write the failing test**

`python/tests/test_compiler_timelines.py`:

```python
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
    compiled = compile_spec(_spec(
        {"p": ABSENT},
        {"p": G},
        (
            CreateFileNoClobber(effect_id="e1", path="p", post=F),
            DeletePath(effect_id="e2", path="p", pre=F),
            CreateFileNoClobber(effect_id="e3", path="p", post=G),
        ),
    ))
    (timeline,) = compiled.timelines
    assert [o.effect_id for o in timeline.occurrences] == ["e1", "e2", "e3"]


def test_discontinuous_timeline_is_rejected():
    with pytest.raises(SpecValidationError, match="discontinuous"):
        compile_spec(_spec(
            {"p": ABSENT},
            {"p": ABSENT},
            (
                CreateFileNoClobber(effect_id="e1", path="p", post=F),
                DeletePath(effect_id="e2", path="p", pre=G),
            ),
        ))


# --- phase 10: exact coverage ---

def test_surface_path_with_no_effect_is_rejected():
    with pytest.raises(SpecValidationError, match="no effect mutates"):
        compile_spec(_spec(
            {"a": ABSENT, "unused": F},
            {"a": F, "unused": F},
            (CreateFileNoClobber(effect_id="e1", path="a", post=F),),
        ))


def test_effect_path_missing_from_the_initial_surface_is_rejected():
    with pytest.raises(SpecValidationError, match="initial_surface"):
        compile_spec(_spec(
            {},
            {"a": F},
            (CreateFileNoClobber(effect_id="e1", path="a", post=F),),
        ))


def test_effect_path_missing_from_the_final_surface_is_rejected():
    with pytest.raises(SpecValidationError, match="final_surface"):
        compile_spec(_spec(
            {"a": ABSENT},
            {},
            (CreateFileNoClobber(effect_id="e1", path="a", post=F),),
        ))


def test_move_declares_both_endpoints_on_both_surfaces():
    compile_spec(_spec(
        {"s": F, "d": ABSENT},
        {"s": ABSENT, "d": F},
        (MoveNoClobber(effect_id="e1", source="s", destination="d", source_pre=F),),
    ))


# --- phase 11: timeline endpoints ---

def test_initial_surface_disagreeing_with_the_first_precondition_is_rejected():
    with pytest.raises(SpecValidationError, match="initial"):
        compile_spec(_spec(
            {"a": G},
            {"a": G},
            (ReplaceFile(effect_id="e1", path="a", pre=F, post=G),),
        ))


def test_final_surface_disagreeing_with_the_last_postcondition_is_rejected():
    with pytest.raises(SpecValidationError, match="final"):
        compile_spec(_spec(
            {"a": F},
            {"a": F},
            (ReplaceFile(effect_id="e1", path="a", pre=F, post=G),),
        ))


# --- phase 12: surface tree consistency ---

def test_descendant_of_an_absent_ancestor_must_be_absent():
    with pytest.raises(SpecValidationError, match="beneath"):
        compile_spec(_spec(
            {"d": ABSENT, "d/f": F},
            {"d": D, "d/f": ABSENT},
            (
                CreateDirectory(effect_id="e1", path="d", post=D),
                DeletePath(effect_id="e2", path="d/f", pre=F),
            ),
        ))


def test_descendant_of_a_file_ancestor_must_be_absent():
    with pytest.raises(SpecValidationError, match="beneath"):
        compile_spec(_spec(
            {"p": F, "p/q": F},
            {"p": ABSENT, "p/q": ABSENT},
            (
                DeletePath(effect_id="e1", path="p/q", pre=F),
                DeletePath(effect_id="e2", path="p", pre=F),
            ),
        ))


def test_ancestor_type_change_is_permitted():
    # The valid transition a stricter rule would wrongly reject: p/q is absent
    # initially precisely BECAUSE p is a file then.
    compiled = compile_spec(_spec(
        {"p": F, "p/q": ABSENT},
        {"p": D, "p/q": G},
        (
            DeletePath(effect_id="e1", path="p", pre=F),
            CreateDirectory(effect_id="e2", path="p", post=D),
            CreateFileNoClobber(effect_id="e3", path="p/q", post=G),
        ),
    ))
    assert [t.path for t in compiled.timelines] == ["p", "p/q"]


def test_symlink_ancestor_type_change_is_permitted():
    compile_spec(_spec(
        {"p": L, "p/q": ABSENT},
        {"p": D, "p/q": G},
        (
            DeletePath(effect_id="e1", path="p", pre=L),
            CreateDirectory(effect_id="e2", path="p", post=D),
            CreateFileNoClobber(effect_id="e3", path="p/q", post=G),
        ),
    ))


def test_undeclared_ancestor_carries_no_constraint():
    # Whether 'deep' exists and is a directory is a live filesystem question (A4).
    compile_spec(_spec(
        {"deep/nested/f": ABSENT},
        {"deep/nested/f": F},
        (CreateFileNoClobber(effect_id="e1", path="deep/nested/f", post=F),),
    ))


def test_the_rule_applies_to_the_final_surface_too():
    with pytest.raises(SpecValidationError, match="beneath"):
        compile_spec(_spec(
            {"p": ABSENT, "p/q": ABSENT},
            {"p": F, "p/q": G},
            (
                CreateFileNoClobber(effect_id="e1", path="p", post=F),
                CreateFileNoClobber(effect_id="e2", path="p/q", post=G),
            ),
        ))


# --- phase 13: created-directory ancestor ordering ---

def test_directory_creation_must_precede_its_descendants():
    with pytest.raises(SpecValidationError, match="before"):
        compile_spec(_spec(
            {"d": ABSENT, "d/f": ABSENT},
            {"d": D, "d/f": F},
            (
                CreateFileNoClobber(effect_id="e1", path="d/f", post=F),
                CreateDirectory(effect_id="e2", path="d", post=D),
            ),
        ))


def test_outer_to_inner_directory_creation_is_required():
    with pytest.raises(SpecValidationError, match="before"):
        compile_spec(_spec(
            {"a": ABSENT, "a/b": ABSENT},
            {"a": D, "a/b": D},
            (
                CreateDirectory(effect_id="e1", path="a/b", post=D),
                CreateDirectory(effect_id="e2", path="a", post=D),
            ),
        ))


def test_correctly_ordered_nested_creation_compiles():
    compile_spec(_spec(
        {"a": ABSENT, "a/b": ABSENT, "a/b/f": ABSENT},
        {"a": D, "a/b": D, "a/b/f": F},
        (
            CreateDirectory(effect_id="e1", path="a", post=D),
            CreateDirectory(effect_id="e2", path="a/b", post=D),
            CreateFileNoClobber(effect_id="e3", path="a/b/f", post=F),
        ),
    ))


def test_ordering_applies_to_a_move_destination_beneath_a_created_directory():
    with pytest.raises(SpecValidationError, match="before"):
        compile_spec(_spec(
            {"s": F, "d": ABSENT, "d/t": ABSENT},
            {"s": ABSENT, "d": D, "d/t": F},
            (
                MoveNoClobber(effect_id="e1", source="s", destination="d/t", source_pre=F),
                CreateDirectory(effect_id="e2", path="d", post=D),
            ),
        ))


# --- all five variants together ---

def test_a_spec_using_every_variant_compiles():
    compiled = compile_spec(_spec(
        {
            "dir": ABSENT, "repl.txt": F, "new.txt": ABSENT,
            "del.txt": F, "lnk": L, "src": F, "dst": ABSENT,
        },
        {
            "dir": D, "repl.txt": G, "new.txt": F,
            "del.txt": ABSENT, "lnk": ABSENT, "src": ABSENT, "dst": F,
        },
        (
            CreateDirectory(effect_id="e1", path="dir", post=D),
            ReplaceFile(effect_id="e2", path="repl.txt", pre=F, post=G),
            CreateFileNoClobber(effect_id="e3", path="new.txt", post=F),
            DeletePath(effect_id="e4", path="del.txt", pre=F),
            DeletePath(effect_id="e5", path="lnk", pre=L),
            MoveNoClobber(effect_id="e6", source="src", destination="dst", source_pre=F),
        ),
        [("e1", "e3")],
    ))
    assert [t.path for t in compiled.timelines] == [
        "del.txt", "dir", "dst", "lnk", "new.txt", "repl.txt", "src",
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_compiler_timelines.py -v`
Expected: FAIL — the coverage, endpoint, tree, and ordering tests fail; `compile_spec` stops after
phase 9.

- [ ] **Step 3: Add phases 10–13 to `compiler.py`**

Extend the `atoms.core.paths` import at the top of the module to include `ancestors` (keep it in the
same position, between `identifiers` and `spec`):

```python
from atoms.core.paths import ancestors, path_equivalence_key, require_rel_path
```

Append:

```python
def _surface_map(spec: TransactionSpec, label: str) -> dict[str, PathState]:
    return {entry.path: entry.state for entry in getattr(spec, label)}


def _phase10_coverage(
    timelines: tuple[PathTimeline, ...],
    initial: dict[str, PathState],
    final: dict[str, PathState],
) -> None:
    effect_paths = {timeline.path for timeline in timelines}
    for label, declared in (("initial_surface", initial), ("final_surface", final)):
        undeclared = sorted(effect_paths - set(declared))
        _require(
            not undeclared,
            f"{label} omits {len(undeclared)} path(s) that an effect mutates: {undeclared}",
        )
        untouched = sorted(set(declared) - effect_paths)
        _require(
            not untouched,
            f"{label} declares {len(untouched)} path(s) that no effect mutates: {untouched}",
        )


def _phase11_endpoints(
    timelines: tuple[PathTimeline, ...],
    initial: dict[str, PathState],
    final: dict[str, PathState],
) -> None:
    for timeline in timelines:
        first = timeline.occurrences[0]
        last = timeline.occurrences[-1]
        _require(
            first.pre == initial[timeline.path],
            f"path {timeline.path!r} declares an initial state of {initial[timeline.path]!r} "
            f"but effect {first.effect_id!r} expects {first.pre!r}",
        )
        _require(
            last.post == final[timeline.path],
            f"path {timeline.path!r} declares a final state of {final[timeline.path]!r} "
            f"but effect {last.effect_id!r} leaves it {last.post!r}",
        )


def _phase12_surface_tree(surface: dict[str, PathState], label: str) -> None:
    for path in sorted(surface):
        for ancestor in ancestors(path):
            ancestor_state = surface.get(ancestor)
            if ancestor_state is None or isinstance(ancestor_state, DirectoryState):
                continue
            _require(
                isinstance(surface[path], AbsentState),
                f"{label} declares {path!r} beneath {ancestor!r}, which is "
                f"{type(ancestor_state).__name__} and so cannot contain entries; "
                f"{path!r} must be declared absent",
            )


def _phase13_ancestor_ordering(spec: TransactionSpec) -> None:
    created = {
        effect.path: index
        for index, effect in enumerate(spec.effects)
        if isinstance(effect, CreateDirectory)
    }
    if not created:
        return
    for index, effect in enumerate(spec.effects):
        for occurrence in occurrences(effect):
            for ancestor in ancestors(occurrence.path):
                creator = created.get(ancestor)
                _require(
                    creator is None or creator < index,
                    f"effect {effect.effect_id!r} touches {occurrence.path!r} beneath {ancestor!r}, "
                    f"which this transaction creates later; outer directory creation must come before "
                    f"every affected descendant",
                )
```

Add `occurrences` to the `atoms.core.effects` import at the top of the module:

```python
from atoms.core.effects import (
    CreateDirectory,
    CreateFileNoClobber,
    DeletePath,
    MoveNoClobber,
    ReplaceFile,
    occurrences,
)
```

Complete `compile_spec`:

```python
def compile_spec(spec: TransactionSpec) -> CompiledSpec:
    _phase1_structure(spec)
    _phase2_fingerprints(spec)
    _phase3_path_grammar(spec)
    _phase4_alias_distinctness(spec)
    _phase5_variant_shapes(spec)
    _phase6_unique_effect_ids(spec)
    _phase7_dependencies(spec)
    _phase8_surface_shape(spec)

    timelines = build_timelines(spec.effects)
    initial = _surface_map(spec, "initial_surface")
    final = _surface_map(spec, "final_surface")

    _phase10_coverage(timelines, initial, final)
    _phase11_endpoints(timelines, initial, final)
    _phase12_surface_tree(initial, "initial_surface")
    _phase12_surface_tree(final, "final_surface")
    _phase13_ancestor_ordering(spec)

    return CompiledSpec(spec=_canonicalize(spec), timelines=timelines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/ -v`
Expected: PASS — the whole suite, including A1's 82 tests.

- [ ] **Step 5: Lint, type-check, commit**

```bash
uv run ruff check && uv run pyright
git add python/src/atoms/core/compiler.py python/tests/test_compiler_timelines.py
git commit -m "feat(core): timeline, coverage, endpoint, and tree phases complete compile_spec"
```

---

### Task 6: Cross-cutting guarantees and status sync

The per-rule tests prove each phase refuses what it should. This task proves the four properties the
boundary exists to provide, which no single-rule test can: totality of the error contract, durable
serializability of anything accepted, determinism, and A1 round-trip interoperability.

The totality test is the important one. It asserts the **property** — everything `compile_spec` accepts
survives `canonical_bytes` — rather than only the surrogate special case, so a future rule that admits
another unserializable value fails here. A handful of handcrafted specifications cannot carry that claim,
so the property is driven as a matrix: an adversarial string corpus crossed with every position where a
caller supplies a string freely — leaf path, interior path component, move source, move destination,
symlink target, effect ID, consumer tag. Refusal is an acceptable outcome at any cell; silent acceptance
of a value that will not encode is not. A companion test asserts each site still admits *something*, so
a future rule that refuses everything cannot make the matrix pass vacuously.

**Files:**
- Test: `python/tests/test_compiler_properties.py`
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `docs/plans/2026-07-28-a2-compilation-validation-design.md`

**Interfaces:**
- Consumes: everything from Tasks 1–5, plus `atoms.core.canonical` (`canonical_bytes`,
  `from_canonical_bytes`).
- Produces: nothing new.

- [ ] **Step 1: Write the failing test**

`python/tests/test_compiler_properties.py`:

```python
import pytest

from atoms.core.canonical import canonical_bytes, from_canonical_bytes
from atoms.core.compiler import compile_spec
from atoms.core.effects import (
    CreateDirectory,
    CreateFileNoClobber,
    DeletePath,
    MoveNoClobber,
    ReplaceFile,
)
from atoms.core.errors import SpecValidationError
from atoms.core.fingerprint import ABSENT, SymlinkState
from atoms.core.spec import TransactionSpec, build_spec
from tests.support import DIGEST, EMPTY, D, F, G, L


def _spec(initial, final, effects, dependencies=()):
    return build_spec(
        consumer_tag="cnsmr",
        intent_digest=DIGEST,
        initial_surface=initial,
        final_surface=final,
        effects=effects,
        dependencies=dependencies,
    )


def _all_variants_spec():
    return _spec(
        {
            "dir": ABSENT, "repl.txt": F, "new.txt": ABSENT, "empty.txt": ABSENT,
            "del.txt": F, "lnk": L, "src": F, "dst": ABSENT, "café.txt": ABSENT,
        },
        {
            "dir": D, "repl.txt": G, "new.txt": F, "empty.txt": EMPTY,
            "del.txt": ABSENT, "lnk": ABSENT, "src": ABSENT, "dst": F, "café.txt": F,
        },
        (
            CreateDirectory(effect_id="e1", path="dir", post=D),
            ReplaceFile(effect_id="e2", path="repl.txt", pre=F, post=G),
            CreateFileNoClobber(effect_id="e3", path="new.txt", post=F),
            CreateFileNoClobber(effect_id="e4", path="empty.txt", post=EMPTY),
            DeletePath(effect_id="e5", path="del.txt", pre=F),
            DeletePath(effect_id="e6", path="lnk", pre=L),
            MoveNoClobber(effect_id="e7", source="src", destination="dst", source_pre=F),
            CreateFileNoClobber(effect_id="e8", path="café.txt", post=F),
        ),
        [("e1", "e3")],
    )


def _repeated_path_spec():
    return _spec(
        {"p": ABSENT},
        {"p": G},
        (
            CreateFileNoClobber(effect_id="e1", path="p", post=F),
            DeletePath(effect_id="e2", path="p", pre=F),
            CreateFileNoClobber(effect_id="e3", path="p", post=G),
        ),
    )


def _ancestor_change_spec():
    return _spec(
        {"p": F, "p/q": ABSENT},
        {"p": D, "p/q": G},
        (
            DeletePath(effect_id="e1", path="p", pre=F),
            CreateDirectory(effect_id="e2", path="p", post=D),
            CreateFileNoClobber(effect_id="e3", path="p/q", post=G),
        ),
    )


CORPUS = [_all_variants_spec, _repeated_path_spec, _ancestor_change_spec]


# --- totality: nothing accepted can fail downstream ---
#
# The property the UTF-8 rule exists to protect: "compilation succeeded" must mean the
# specification is usable, not merely well-shaped. Three handcrafted specifications
# demonstrate that across the structural variety; they do not establish it. The matrix
# below does the establishing, by driving a corpus of adversarial strings through every
# position where a caller supplies one freely.

@pytest.mark.parametrize("make_spec", CORPUS)
def test_structurally_varied_specs_are_durably_serializable(make_spec):
    compiled = compile_spec(make_spec())
    assert from_canonical_bytes(canonical_bytes(compiled.spec)) == compiled.spec


# Written as escapes, not literal characters: several of these samples are visually
# identical to one another, and the difference between them is the whole point.
UNICODE_SAMPLES = [
    "plain",
    "caf\u00e9",                # precomposed (NFC)
    "cafe\u0301",               # decomposed (NFD) - same text, different code points
    "\u00df",                   # sharp s, whose casefold is longer than itself
    "\u0130",                   # dotted capital I, whose casefold crosses normal forms
    "\U0001f600",               # astral plane, correctly paired
    "\ufeff",                   # zero-width no-break space as an entire name
    "\ufffd",                   # the replacement character, arriving as real content
    "\ufffe",                   # a noncharacter
    "\u202e",                   # right-to-left override
    "\ud800",                   # lone high surrogate - not UTF-8 encodable
    "\udfff",                   # lone low surrogate - not UTF-8 encodable
    "\ud800\udc00",             # a surrogate pair spelled as two lone code points
    "a" * 300,                  # longer than any real NAME_MAX; admitted here (ledger #4)
    " leading and trailing ",
    "tab\tnewline\n",
    ".#nottilde",               # near the reserved scratch sigil without matching it
    "..dotdot",
]


def _site_leaf_path(sample):
    return _spec(
        {sample: ABSENT},
        {sample: F},
        (CreateFileNoClobber(effect_id="e1", path=sample, post=F),),
    )


def _site_directory_component(sample):
    leaf = f"{sample}/leaf.txt"
    return _spec(
        {sample: ABSENT, leaf: ABSENT},
        {sample: D, leaf: F},
        (
            CreateDirectory(effect_id="e1", path=sample, post=D),
            CreateFileNoClobber(effect_id="e2", path=leaf, post=F),
        ),
    )


def _site_move_source(sample):
    return _spec(
        {sample: F, "dst": ABSENT},
        {sample: ABSENT, "dst": F},
        (MoveNoClobber(effect_id="e1", source=sample, destination="dst", source_pre=F),),
    )


def _site_move_destination(sample):
    return _spec(
        {"src": F, sample: ABSENT},
        {"src": ABSENT, sample: F},
        (MoveNoClobber(effect_id="e1", source="src", destination=sample, source_pre=F),),
    )


def _site_symlink_target(sample):
    link = SymlinkState(target=sample, mode=0o777)
    return _spec(
        {"lnk": link},
        {"lnk": ABSENT},
        (DeletePath(effect_id="e1", path="lnk", pre=link),),
    )


def _site_effect_id(sample):
    return _spec(
        {"a.txt": ABSENT},
        {"a.txt": F},
        (CreateFileNoClobber(effect_id=sample, path="a.txt", post=F),),
    )


def _site_consumer_tag(sample):
    base = _all_variants_spec()
    return TransactionSpec(
        schema_version=base.schema_version,
        consumer_tag=sample,
        intent_digest=base.intent_digest,
        initial_surface=base.initial_surface,
        final_surface=base.final_surface,
        effects=base.effects,
        dependencies=base.dependencies,
    )


SITES = [
    _site_leaf_path,
    _site_directory_component,
    _site_move_source,
    _site_move_destination,
    _site_symlink_target,
    _site_effect_id,
    _site_consumer_tag,
]


def _compiles(site, sample) -> bool:
    try:
        compile_spec(site(sample))
    except SpecValidationError:
        return False
    return True


@pytest.mark.parametrize("sample", UNICODE_SAMPLES, ids=lambda s: ascii(s)[:28])
@pytest.mark.parametrize("site", SITES, ids=lambda f: f.__name__)
def test_every_accepted_string_survives_the_durable_format(site, sample):
    # Refusal is a valid outcome at every site. Acceptance is a promise: the value must
    # encode, and decode back to exactly the specification that was compiled.
    try:
        compiled = compile_spec(site(sample))
    except SpecValidationError:
        return
    assert from_canonical_bytes(canonical_bytes(compiled.spec)) == compiled.spec


def test_the_string_corpus_is_not_degenerate():
    # A rule that refused every sample would leave the matrix above passing vacuously.
    # Each site must still admit something, so each site is really exercising acceptance.
    for site in SITES:
        assert any(_compiles(site, sample) for sample in UNICODE_SAMPLES), (
            f"{site.__name__} accepted no sample; the matrix no longer proves anything there"
        )


# --- totality: nothing but SpecValidationError escapes ---

@pytest.mark.parametrize(
    "broken",
    [
        {"schema_version": "one"},
        {"consumer_tag": None},
        {"intent_digest": 12345},
        {"initial_surface": None},
        {"final_surface": 7},
        {"effects": None},
        {"effects": (None,)},
        {"dependencies": "e1->e2"},
    ],
)
def test_only_spec_validation_error_escapes(broken):
    base = _repeated_path_spec()
    fields = {
        "schema_version": base.schema_version,
        "consumer_tag": base.consumer_tag,
        "intent_digest": base.intent_digest,
        "initial_surface": base.initial_surface,
        "final_surface": base.final_surface,
        "effects": base.effects,
        "dependencies": base.dependencies,
    }
    fields.update(broken)
    spec = TransactionSpec(**fields)  # type: ignore[arg-type]
    with pytest.raises(SpecValidationError):
        compile_spec(spec)


def test_a_non_spec_argument_raises_spec_validation_error():
    for value in (None, 42, "spec", [], {}):
        with pytest.raises(SpecValidationError):
            compile_spec(value)  # type: ignore[arg-type]


# --- determinism and idempotence ---

@pytest.mark.parametrize("make_spec", CORPUS)
def test_compilation_is_deterministic(make_spec):
    first = compile_spec(make_spec())
    second = compile_spec(make_spec())
    assert first == second
    assert canonical_bytes(first.spec) == canonical_bytes(second.spec)


@pytest.mark.parametrize("make_spec", CORPUS)
def test_compilation_is_idempotent(make_spec):
    once = compile_spec(make_spec())
    twice = compile_spec(once.spec)
    assert once == twice


def test_canonicalization_sorts_set_like_fields_of_a_directly_built_spec():
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
    assert compile_spec(shuffled) == compile_spec(ordered)


def test_effect_order_is_never_canonicalized_away():
    compiled = compile_spec(_repeated_path_spec())
    assert [e.effect_id for e in compiled.spec.effects] == ["e1", "e2", "e3"]


# --- A1 interoperability ---

@pytest.mark.parametrize("make_spec", CORPUS)
def test_compiled_spec_survives_the_durable_round_trip(make_spec):
    # Fresh-process recovery reconstructs the spec from stored bytes (§8.4).
    compiled = compile_spec(make_spec())
    assert from_canonical_bytes(canonical_bytes(compiled.spec)) == compiled.spec


@pytest.mark.parametrize("make_spec", CORPUS)
def test_a_decoded_spec_recompiles_identically(make_spec):
    compiled = compile_spec(make_spec())
    decoded = from_canonical_bytes(canonical_bytes(compiled.spec))
    assert compile_spec(decoded) == compiled


# --- §13.3 alias conformance ---

def test_scratch_alias_is_refused_in_leaf_and_ancestor_position():
    for path in (".#~leaf", "dir/.#~leaf", ".#~anc/child", "a/.#~anc/child"):
        with pytest.raises(SpecValidationError, match="scratch"):
            compile_spec(_spec(
                {path: ABSENT}, {path: F},
                (CreateFileNoClobber(effect_id="e1", path=path, post=F),),
            ))
```

- [ ] **Step 2: Run test to verify it fails or passes**

Run: `uv run pytest tests/test_compiler_properties.py -v`
Expected: PASS if Tasks 1–5 are correct. These are regression locks on behavior already built, so
failures here mean an earlier task has a defect — fix the source, not the test.

If `test_a_decoded_spec_recompiles_identically` fails, the likely cause is `_canonicalize` producing an
ordering that differs from A1's `canonical_obj` sort. Both must sort surfaces by `path` and dependencies
by `(before, after)`.

- [ ] **Step 3: Update the project status documents**

In `README.md`, replace the A2 line in the Status section:

```markdown
- **A2 — compilation validation (implemented):** [`docs/plans/2026-07-28-plan-a2-compilation-validation.md`](docs/plans/2026-07-28-plan-a2-compilation-validation.md)
```

In `AGENTS.md`, replace the A2 and A3–A8 status lines with:

```markdown
- **A2 — compilation validation: implemented.** `compile_spec` in `atoms/core/compiler.py` is the
  engine's trust boundary; `paths.py` and `timeline.py` support it. A3–A8: not started.
```

In `docs/plans/2026-07-28-a2-compilation-validation-design.md`, change the header status line to:

```markdown
**Status:** Implemented (2026-07-28)
```

Leave `docs/deferred-obligation-ledger.md` unchanged. A2 owns none of the eight open entries — they are
owed by A4, A6, and A7 — so none discharge here.

- [ ] **Step 4: Full verification**

Run from `python/`:

```bash
uv run pytest && uv run ruff check && uv run pyright
```

Expected: all tests pass, no lint errors, no type errors. Confirm the reported test count exceeds A1's
82 before claiming completion.

- [ ] **Step 5: Commit**

```bash
git add python/tests/test_compiler_properties.py README.md AGENTS.md \
        docs/plans/2026-07-28-a2-compilation-validation-design.md
git commit -m "test(core): cross-cutting compilation guarantees and A2 status sync"
```

---

## Self-review

**Spec coverage.** Every phase in the design's §5 maps to a task via the phase-to-task table, and every
item in the design's §8 testing section maps to a test: per-rule coverage (Tasks 3–5), repeated-path
timelines (Tasks 2 and 5), ancestor type change (Tasks 2, 5, 6), path alias distinctness including the
create/delete/re-create counterexample (Task 4), UTF-8 encodability with its protecting property test
(Tasks 1, 3, 6), all five variants (Task 5), directly-constructed malformed dataclasses at every nesting
depth (Tasks 3 and 6), zero-effect refusal (Task 3), determinism and idempotence (Task 6), A1
interoperability (Task 6), and §13.3 alias conformance (Task 6). The design's §3 `CompiledSpec` shape,
§4 module layout, §6 error contract, and §7 recorded decisions are each realized in Tasks 1–6.

**Deliberately not covered.** The design's §2 deferral list is A4's, and the ledger's eight open entries
name A4, A6, and A7 as owners. A2 must leave those visibly unimplemented rather than partially done —
in particular, `paths.py` performs no resolution or containment, and no code here consults a filesystem.

**Placeholder scan.** Every step carries complete, runnable source. Two steps contain an explicit
correction to code shown immediately above them (Task 3 Step 2's `__import__` line, Task 4 Step 1's
`if False else None` placeholder); both give the exact replacement text rather than describing it. No
step says "add validation", "handle edge cases", or "similar to Task N".

**Type consistency.** `require_rel_path(kind, value)`, `path_equivalence_key(path)`, and
`ancestors(path)` are defined in Task 1 and used with those exact signatures in Tasks 4 and 5.
`TimelineOccurrence` and `PathTimeline` field names are fixed in Task 2 and read unchanged in Tasks 5
and 6. `CompiledSpec(spec, timelines)` is defined in Task 3 and used unchanged thereafter.
`_require`, `_require_str`, `_require_int`, `_require_tuple`, and `_EFFECT_FIELDS` are introduced in
Task 3 and reused by Tasks 4 and 5. `tests/support.py`'s `F`, `G`, `D`, `L`, `EMPTY`, `DIGEST`, and
`valid_spec` are defined in Task 3 and imported by Tasks 4–6. A1 names used here — `SCHEMA_VERSION`,
`SurfaceEntry`, `Dependency`, `TransactionSpec`, `build_spec`, `occurrences`, `require_valid_identifier`,
`aliases_scratch_sigil`, `canonical_bytes`, `from_canonical_bytes` — match the shipped source verbatim.

**Ordering hazards the plan encodes.** Each of the three forced orderings is stated where it is
implemented *and* locked by a test that fails if the two phases are swapped — a specification breaking
both rules, asserting which refusal surfaces.

- Phase 1 before `build_timelines`: A1's `occurrences` is a `singledispatch` raising `TypeError` on a
  non-variant (Tasks 2 and 3 both state this). Locked by `test_only_spec_validation_error_escapes`,
  whose `{"effects": (None,)}` case reaches `build_timelines` if phase 1 is skipped.
- Phase 6 before phase 7: phase 7 resolves endpoints through `{effect_id: index}`, which silently keeps
  only the last effect carrying a duplicated ID. Locked by
  `test_duplicate_ids_are_refused_before_dependencies_are_validated` (Task 4).
- Phase 10 before phase 11: phase 11 indexes the surface maps directly, so an undeclared path would
  raise `KeyError` rather than a refusal (Task 5 states this). Locked by the phase 10 "omits" tests,
  which produce exactly that specification.
