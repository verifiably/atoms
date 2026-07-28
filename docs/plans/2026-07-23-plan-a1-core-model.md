# Plan A1 — Core transaction model, vocabulary, and canonical form

**Status:** Implemented (2026-07-28). All eight tasks landed in `python/src/atoms/core/`; 82 tests pass with `ruff` and `pyright` clean. Successor: Plan A2 (compilation validation).

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the pure, in-memory core of the `atoms` engine — the `TransactionSpec` data model, the five effect variants, the semantic capability vocabulary, the reserved scratch grammar with a bounded safe-identifier grammar, and the durable canonical format (deterministic encode **and** strict decode) — with zero filesystem, SQLite, or platform dependency.

**Architecture:** Frozen stdlib dataclasses model spec/effect/state *shapes*; behavior (occurrence enumeration, capability derivation, canonical encode/decode) lives in separate dispatch functions, keeping "shapes vs. rules" cleanly split (design §4.3). No pydantic and no third-party runtime dependency in the core — the determinism guarantee (§5.1, §13.3) is met by explicit canonicalization (the serializer sorts set-like fields so canonical bytes are a pure function of content), not a validation library. The canonical format is round-trippable: a strict decoder reconstructs the exact spec from `spec_json` for fresh-process recovery (§7.2, §8.4), failing early on unknown discriminators, missing/extra fields, or duplicate keys. This sub-plan implements **only** the parts of design §5 expressible without touching a filesystem; the filesystem-identity checks of §5.4 (metadata-root containment, ancestor resolution) and all spec *validation* are deferred to A2/A4.

**Tech Stack:** Python ≥3.11, stdlib only (`dataclasses`, `enum`, `functools.singledispatch`, `json`, `unicodedata`, `hashlib`), managed with `uv`; hatchling build; `ruff` + `pyright` + `pytest`.

## Where this sits in the Plan A program

Design §14's "Plan A" is a program of eight sub-plans, sequenced so each produces working, testable software and no sub-plan prematurely locks a design decision the doc deferred:

| Sub-plan | Scope | Design refs | Deferred decisions settled |
| --- | --- | --- | --- |
| **A1 (this doc)** | Pure model, vocabulary, scratch grammar + safe identifiers, and the durable canonical format (encode **and** strict decode) | §5.1–§5.3, §5.5-as-data, §7.2/§13.3 format | none (dependency-free) |
| A2 | Compilation validation (fs-independent subset) + repeated-path timelines; enforces the safe-identifier grammar over every effect ID | §5.3, §5.4 | none |
| A3 | Executable recovery reference model (transaction + variant classifiers) | §8.4, §13.1 | none |
| A4 | Platform capability backend + per-volume probe + durability-allowlist tuples | §5.5, §14 | **durability-allowlist configuration tuples** |
| A5 | SQLite-WAL metadata store, project lock, recovery-resolve lease, preparation/commit ordering | §7 | **SQLite I/O-layer (stdlib vs. custom VFS)** |
| A6 | Coherent capture + restartable atomic materialization | §6, §10 | none |
| A7 | Five effect implementations + recovery executor | §8, §9 | none |
| A8 | Real-fs / subprocess / persistence-cut suites + synthetic exerciser + e2e recovery matrix | §12.1, §13 | none |

A1 depends on nothing. A2 and A3 depend only on A1's types. A4 onward introduce syscalls and are where the two deferred design decisions get made — each in its own reviewed sub-plan.

## Global Constraints

- **Python floor:** `requires-python = ">=3.11"` (matches `nodes`). Copy verbatim into `pyproject.toml`.
- **No third-party runtime dependency in `atoms.core`.** Stdlib only. Dev deps (`pytest`, `ruff`, `pyright`) are the only dependencies.
- **Distribution/import identity:** distribution name `atoms-core`; import namespace `atoms` (PEP 420 namespace — no `src/atoms/__init__.py`); import package `atoms.core`. Mirrors `nodes-core` / `nodes.core`.
- **Layout:** all package work lives under `python/` (`python/pyproject.toml`, `python/src/atoms/core/`, `python/tests/`), mirroring `~/d/nodes/python/`.
- **Tooling:** `ruff` line-length 120; `pyright` `typeCheckingMode = "basic"`, `pythonVersion = "3.11"`; `pytest` `addopts = "-q"`, `testpaths = ["tests"]`. All commands run via `uv run` from `python/`.
- **Content hashes** are strings of the form `sha256:<64 lowercase hex>`. **Modes** are integer permission bits only (e.g. `0o644`), never type bits.
- **Project-relative paths** (`RelPath`) are POSIX, `/`-separated, no leading slash, no `.`/`..` components. A1 stores them verbatim; resolution/containment is A2/A4's job.
- No AI-attribution trailers on commits/PRs/comments. Docs use `~/d/` (never `/home/keith/d/` or `/mnt/ssd/Dropbox/`).

---

### Task 1: Package scaffold and error hierarchy

Scaffolding for the whole `python/` subtree is folded here because every later task imports from it; the error hierarchy ships with it because it is trivial and needed by every module.

**Files:**
- Create: `python/pyproject.toml`
- Create: `python/README.md`
- Create: `python/LICENSE` (copy of `~/d/atoms/LICENSE`)
- Create: `python/src/atoms/core/__init__.py`
- Create: `python/src/atoms/core/py.typed` (empty PEP 561 marker)
- Create: `python/src/atoms/core/errors.py`
- Create: `python/tests/__init__.py`
- Test: `python/tests/test_errors.py`
- Test: `python/tests/test_packaging.py`

**Interfaces:**
- Consumes: nothing.
- Produces: exception classes `AtomsError`, `ProtocolError(AtomsError)`, `SpecValidationError(AtomsError)`, `PreconditionRefused(AtomsError)`, `CapabilityUnavailable(AtomsError)`, `TransactionHalted(AtomsError)`. Package import path `atoms.core`, shipped as an inline-typed (PEP 561) distribution mirroring `nodes-core`.

- [x] **Step 1: Write the failing test**

`python/tests/test_errors.py`:

```python
import pytest

from atoms.core.errors import (
    AtomsError,
    CapabilityUnavailable,
    PreconditionRefused,
    ProtocolError,
    SpecValidationError,
    TransactionHalted,
)


def test_all_engine_errors_subclass_atoms_error():
    for exc in (
        ProtocolError,
        SpecValidationError,
        PreconditionRefused,
        CapabilityUnavailable,
        TransactionHalted,
    ):
        assert issubclass(exc, AtomsError)


def test_errors_carry_a_message():
    with pytest.raises(CapabilityUnavailable, match="atomic_exchange"):
        raise CapabilityUnavailable("atomic_exchange missing on volume")
```

- [x] **Step 2: Run test to verify it fails**

Run (from `python/`): `uv run pytest tests/test_errors.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'atoms'` (package not yet built/installed).

- [x] **Step 3: Create the scaffold**

`python/pyproject.toml`:

```toml
[project]
name = "atoms-core"
import-names = ["atoms.core"]
import-namespaces = ["atoms"]
version = "0.1.0"
description = "Atoms core: a recoverable filesystem effect engine (pure model)"
readme = "README.md"
license = "MIT"
license-files = ["LICENSE"]
authors = [{ name = "Keith Hughitt", email = "keith.hughitt@gmail.com" }]
requires-python = ">=3.11"
classifiers = ["Typing :: Typed"]
dependencies = []

[project.urls]
Homepage = "https://github.com/atoms-dev/core"
Repository = "https://github.com/atoms-dev/core"
Issues = "https://github.com/atoms-dev/core/issues"

[build-system]
requires = ["hatchling>=1.30"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
core-metadata-version = "2.5"
packages = ["src/atoms"]

[tool.pytest.ini_options]
addopts = "-q"
testpaths = ["tests"]

[tool.ruff]
line-length = 120

[tool.pyright]
typeCheckingMode = "basic"
pythonVersion = "3.11"

[dependency-groups]
dev = [
    "pytest>=9.0",
    "ruff>=0.15.7",
    "pyright>=1.1.390",
]
```

`python/src/atoms/core/__init__.py`:

```python
"""Atoms core: the pure, in-memory transaction model and vocabulary."""
```

`python/tests/__init__.py`: empty file.

`python/src/atoms/core/errors.py`:

```python
"""Engine error hierarchy (design §11)."""


class AtomsError(Exception):
    """Base class for every error the engine raises."""


class ProtocolError(AtomsError):
    """An internal engine contract was violated."""


class SpecValidationError(AtomsError):
    """A TransactionSpec failed compilation validation (design §5.4)."""


class PreconditionRefused(AtomsError):
    """Concurrent drift was detected; the transaction refuses cleanly."""


class CapabilityUnavailable(AtomsError):
    """A required filesystem capability is not supplied by the active backend."""


class TransactionHalted(AtomsError):
    """State is unattributable; the engine preserves the record and evidence."""
```

`python/src/atoms/core/py.typed`: an **empty** file. PEP 561 requires this marker for a package to ship inline type information; the `Typing :: Typed` classifier is a promise the marker fulfills. Combined with `core-metadata-version = "2.5"` (which lets Hatchling emit the `Import-Name`/`Import-Namespace` metadata-2.5 fields) and `license-files = ["LICENSE"]`, this makes the distribution mirror `nodes-core` exactly (see `~/d/nodes/python/pyproject.toml` and `~/d/nodes/python/src/nodes/core/py.typed`).

`python/README.md` (required by `pyproject.toml`'s `readme` field, which resolves relative to `python/`):

```markdown
# atoms-core

Pure model for the atoms recoverable filesystem effect engine. See `~/d/atoms/README.md`.
```

`python/LICENSE`: copy the repository-root license so `license-files = ["LICENSE"]` resolves within `python/` (nodes keeps the same file in both places). Run from the repo root: `cp LICENSE python/LICENSE`.

- [x] **Step 3b: Add the source-marker sanity test**

`python/tests/test_packaging.py`:

```python
from pathlib import Path

import atoms.core


def test_py_typed_marker_present_in_source_package():
    # Necessary-but-not-sufficient: proves the marker sits next to the package
    # source. That it actually ships in the built wheel is proven separately, by
    # building and inspecting the wheel (see the packaging-verification step).
    marker = Path(atoms.core.__file__).with_name("py.typed")
    assert marker.is_file(), "PEP 561 py.typed marker must sit in atoms.core"
```

- [x] **Step 4: Run tests to verify they pass**

Run (from `python/`): `uv run pytest tests/test_errors.py tests/test_packaging.py -v`
Expected: PASS (all three tests). `uv run` builds/installs the editable package on first invocation. This only proves the marker exists in the source tree — the wheel-shipping proof is the next step.

- [x] **Step 4b: Build the wheel and prove the typed-distribution surface ships**

The editable install cannot show what a *published* wheel contains. Build one and inspect its ZIP and `METADATA` directly, so `py.typed` and the metadata-2.5 `Import-Name`/`Import-Namespace`/`License-File` fields are proven present in the actual artifact. Run from `python/`:

```bash
uv build --wheel
uv run python -c "
import glob, zipfile
whl = sorted(glob.glob('dist/atoms_core-*.whl'))[-1]
z = zipfile.ZipFile(whl)
names = z.namelist()
assert 'atoms/core/py.typed' in names, f'py.typed missing from wheel: {names}'
meta = next(n for n in names if n.endswith('.dist-info/METADATA'))
text = z.read(meta).decode()
for field in ('Import-Name: atoms.core', 'Import-Namespace: atoms', 'License-File:'):
    assert field in text, f'missing metadata field {field!r} in {meta}'
print('OK:', whl.rsplit('/', 1)[-1], '— py.typed + metadata-2.5 fields present')
"
```

Expected: prints `OK: atoms_core-0.1.0-…whl — py.typed + metadata-2.5 fields present`. If `Import-Name`/`Import-Namespace` are absent, `core-metadata-version = "2.5"` was not applied; if `License-File` is absent, `license-files` / `python/LICENSE` is missing. Add `dist/` to `python/.gitignore` in this step (create the file with a single `dist/` line) so build output is not committed.

- [x] **Step 5: Lint and type-check the new files**

Run (from `python/`): `uv run ruff check` then `uv run pyright`
Expected: no errors.

- [x] **Step 6: Commit**

```bash
git add python/
git commit -m "feat(core): scaffold atoms-core package and error hierarchy"
```

---

### Task 2: Path-state fingerprints

**Files:**
- Create: `python/src/atoms/core/fingerprint.py`
- Test: `python/tests/test_fingerprint.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `PathKind` enum (`ABSENT`, `FILE`, `DIRECTORY`, `SYMLINK`).
  - Frozen dataclasses `AbsentState`, `FileState(content_hash: str, mode: int, byte_len: int)`, `DirectoryState(mode: int)`, `SymlinkState(target: str, mode: int)`.
  - Type alias `PathState = AbsentState | FileState | DirectoryState | SymlinkState`.
  - `ABSENT: AbsentState` singleton.
  - `kind_of(state: PathState) -> PathKind`.

- [x] **Step 1: Write the failing test**

`python/tests/test_fingerprint.py`:

```python
import pytest

from atoms.core.fingerprint import (
    ABSENT,
    AbsentState,
    DirectoryState,
    FileState,
    PathKind,
    SymlinkState,
    kind_of,
)


def test_states_are_frozen_and_hashable():
    f = FileState(content_hash="sha256:" + "0" * 64, mode=0o644, byte_len=10)
    assert hash(f) == hash(FileState(content_hash="sha256:" + "0" * 64, mode=0o644, byte_len=10))
    with pytest.raises(Exception):
        f.mode = 0o600  # type: ignore[misc]


def test_absent_is_a_singleton_value():
    assert ABSENT is ABSENT
    assert AbsentState() == ABSENT


def test_kind_of_maps_every_variant():
    assert kind_of(ABSENT) is PathKind.ABSENT
    assert kind_of(FileState(content_hash="sha256:" + "a" * 64, mode=0o644, byte_len=1)) is PathKind.FILE
    assert kind_of(DirectoryState(mode=0o755)) is PathKind.DIRECTORY
    assert kind_of(SymlinkState(target="../x", mode=0o777)) is PathKind.SYMLINK
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_fingerprint.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'atoms.core.fingerprint'`.

- [x] **Step 3: Write minimal implementation**

`python/src/atoms/core/fingerprint.py`:

```python
"""Path-state fingerprints — the declared/observed state of a single path (design §6)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class PathKind(Enum):
    ABSENT = "absent"
    FILE = "file"
    DIRECTORY = "directory"
    SYMLINK = "symlink"


@dataclass(frozen=True, slots=True)
class AbsentState:
    """The path does not exist."""


@dataclass(frozen=True, slots=True)
class FileState:
    """A regular file: content hash, exact permission bits, and byte length."""

    content_hash: str
    mode: int
    byte_len: int


@dataclass(frozen=True, slots=True)
class DirectoryState:
    """A directory with exact permission bits."""

    mode: int


@dataclass(frozen=True, slots=True)
class SymlinkState:
    """A symlink: its target and exact permission bits (lstat-coherent)."""

    target: str
    mode: int


PathState = AbsentState | FileState | DirectoryState | SymlinkState

ABSENT = AbsentState()


def kind_of(state: PathState) -> PathKind:
    match state:
        case AbsentState():
            return PathKind.ABSENT
        case FileState():
            return PathKind.FILE
        case DirectoryState():
            return PathKind.DIRECTORY
        case SymlinkState():
            return PathKind.SYMLINK
```

- [x] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_fingerprint.py -v`
Expected: PASS.

- [x] **Step 5: Lint, type-check, commit**

```bash
uv run ruff check && uv run pyright
git add python/src/atoms/core/fingerprint.py python/tests/test_fingerprint.py
git commit -m "feat(core): path-state fingerprints"
```

---

### Task 3: Reserved scratch grammar and safe identifiers

Implements the letter-free `.#~` sigil (design §5.1) and the property that makes the letter-free choice sound: on any input, a plain `startswith` and an equivalence-aware (case + NFC/NFD) match must agree, so no persistent path can alias scratch through a normalization variant (§13.3).

It also fixes a distinct hazard: `scratch_leaf` interpolates a `txid`, a **consumer-controlled** `effect_id`, and a `role` directly into a single pathname component. An `effect_id` containing `/`, a NUL, a `.`, or excessive length would break the single-component grammar and only surface much later, when a `*at` syscall receives the malformed scratch name. A bounded **safe-identifier grammar** (`identifiers.py`) closes this: `scratch_leaf` validates every interpolated part at construction and raises `SpecValidationError` on a bad one, so the failure is a compile-time contract violation (the same predicate A2's compiler applies to every effect ID before any scratch is materialized), never a runtime syscall error. `.` is excluded from the grammar so the `.#~<txid>.<effect-id>.<role>` remainder stays unambiguously delimited.

**Files:**
- Create: `python/src/atoms/core/identifiers.py`
- Create: `python/src/atoms/core/scratch.py`
- Test: `python/tests/test_identifiers.py`
- Test: `python/tests/test_scratch.py`

**Interfaces:**
- Consumes: `atoms.core.errors` (`SpecValidationError`).
- Produces:
  - `SAFE_IDENTIFIER: re.Pattern` matching `^[A-Za-z0-9_-]{1,64}$`.
  - `is_valid_identifier(value: str) -> bool`.
  - `require_valid_identifier(kind: str, value: str) -> str` — returns `value` or raises `SpecValidationError` naming `kind` (e.g. `"effect_id"`).
  - `SCRATCH_SIGIL = ".#~"`.
  - `leaf_of(rel_path: str) -> str` — last POSIX component.
  - `is_scratch_leaf(leaf: str) -> bool` — plain prefix test.
  - `aliases_scratch_sigil(leaf: str) -> bool` — equivalence-aware test across `{leaf, NFC, NFD, casefold}`.
  - `scratch_leaf(txid: str, effect_id: str, role: str) -> str` — validates each part via `require_valid_identifier`, then builds `f"{SCRATCH_SIGIL}{txid}.{effect_id}.{role}"`.

- [x] **Step 1: Write the failing identifier test**

`python/tests/test_identifiers.py`:

```python
import pytest

from atoms.core.errors import SpecValidationError
from atoms.core.identifiers import (
    is_valid_identifier,
    require_valid_identifier,
)


def test_accepts_bounded_ascii_identifiers():
    for good in ("e07", "effect_1", "A-B-C", "x" * 64):
        assert is_valid_identifier(good)
        assert require_valid_identifier("effect_id", good) == good


@pytest.mark.parametrize(
    "bad",
    ["", "a/b", "a.b", "a b", "x" * 65, "e\x00", "café", "e#1"],
)
def test_rejects_unsafe_identifiers(bad):
    assert not is_valid_identifier(bad)
    with pytest.raises(SpecValidationError, match="effect_id"):
        require_valid_identifier("effect_id", bad)
```

- [x] **Step 2: Run it and verify it fails**

Run: `uv run pytest tests/test_identifiers.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'atoms.core.identifiers'`.

- [x] **Step 3: Implement `identifiers.py`**

`python/src/atoms/core/identifiers.py`:

```python
"""Bounded safe-identifier grammar for names interpolated into path components.

A stable effect ID (consumer-supplied) and the engine's txid/role are woven into a
single scratch-leaf component (design §5.1). Restricting them to a bounded ASCII
grammar makes that leaf a well-formed single component and turns a malformed ID into a
compile-time refusal (design §5.4) rather than a `*at` syscall failure at mutation time.
"""

from __future__ import annotations

import re

from atoms.core.errors import SpecValidationError

SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def is_valid_identifier(value: str) -> bool:
    return SAFE_IDENTIFIER.fullmatch(value) is not None


def require_valid_identifier(kind: str, value: str) -> str:
    if not is_valid_identifier(value):
        raise SpecValidationError(
            f"{kind} {value!r} is not a valid identifier: expected 1–64 chars of [A-Za-z0-9_-]"
        )
    return value
```

- [x] **Step 4: Run it and verify it passes**

Run: `uv run pytest tests/test_identifiers.py -v`
Expected: PASS.

- [x] **Step 5: Write the failing scratch test**

`python/tests/test_scratch.py`:

```python
import unicodedata

import pytest

from atoms.core.errors import SpecValidationError
from atoms.core.scratch import (
    SCRATCH_SIGIL,
    aliases_scratch_sigil,
    is_scratch_leaf,
    leaf_of,
    scratch_leaf,
)


def test_sigil_is_letter_free_punctuation():
    assert SCRATCH_SIGIL == ".#~"
    assert all(not c.isalpha() for c in SCRATCH_SIGIL)


def test_leaf_of_takes_last_component():
    assert leaf_of("a/b/c.txt") == "c.txt"
    assert leaf_of("solo") == "solo"


def test_scratch_leaf_builds_expected_shape():
    name = scratch_leaf("deadbeef", "e07", "stage")
    assert name == ".#~deadbeef.e07.stage"
    assert is_scratch_leaf(name)


def test_persistent_names_are_not_scratch():
    for name in ("index.md", ".hidden", "#notsigil", "~backup", ".#nottilde"):
        assert not is_scratch_leaf(name)
        assert not aliases_scratch_sigil(name)


def test_letter_free_sigil_has_no_case_or_normalization_alias():
    # The core invariant: because the sigil is letter-free ASCII punctuation with no
    # case or NFC/NFD variant, the plain prefix test and the equivalence-aware test
    # agree on EVERY input — so no persistent leaf can alias scratch through folding.
    samples = [
        "index.md",
        ".#~x",
        "A" * 3,
        "café",                                   # NFC vs NFD differ, but not at the prefix
        unicodedata.normalize("NFD", "café"),
        ".#~" + unicodedata.normalize("NFD", "é"),
        "K" + " elvin",                       # KELVIN SIGN casefolds to 'k'
    ]
    for s in samples:
        assert is_scratch_leaf(s) == aliases_scratch_sigil(s)


def test_scratch_leaf_rejects_unsafe_effect_id():
    # A malformed consumer effect_id must fail here, not when a *at syscall
    # later receives a multi-component or over-long scratch name.
    with pytest.raises(SpecValidationError, match="effect_id"):
        scratch_leaf("deadbeef", "bad/id", "stage")
    with pytest.raises(SpecValidationError):
        scratch_leaf("deadbeef", "e07", "x" * 65)
```

- [x] **Step 6: Run test to verify it fails**

Run: `uv run pytest tests/test_scratch.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'atoms.core.scratch'`.

- [x] **Step 7: Write minimal implementation**

`python/src/atoms/core/scratch.py`:

```python
"""Reserved scratch grammar (design §5.1).

The discriminating sigil is the exact leaf prefix ``.#~`` — three ASCII punctuation
bytes, none of which has a case or NFC/NFD variant. A leaf is a scratch name iff it
begins with the sigil; only the sigil participates in classification.
"""

from __future__ import annotations

import unicodedata

from atoms.core.identifiers import require_valid_identifier

SCRATCH_SIGIL = ".#~"


def leaf_of(rel_path: str) -> str:
    """Return the last ``/``-separated component of a project-relative path."""
    return rel_path.rsplit("/", 1)[-1]


def is_scratch_leaf(leaf: str) -> bool:
    """True iff ``leaf`` begins with the reserved sigil (plain prefix test)."""
    return leaf.startswith(SCRATCH_SIGIL)


def aliases_scratch_sigil(leaf: str) -> bool:
    """True iff any case- or NFC/NFD-normalized form of ``leaf`` begins with the sigil.

    Because the sigil is letter-free, this must agree with :func:`is_scratch_leaf` on
    every input; the agreement is the property that proves the letter-free choice sound
    (design §13.3).
    """
    forms = {
        leaf,
        unicodedata.normalize("NFC", leaf),
        unicodedata.normalize("NFD", leaf),
        leaf.casefold(),
    }
    return any(form.startswith(SCRATCH_SIGIL) for form in forms)


def scratch_leaf(txid: str, effect_id: str, role: str) -> str:
    """Build a scratch leaf name ``.#~<txid>.<effect-id>.<role>``.

    Every interpolated part is validated against the safe-identifier grammar first, so a
    malformed (multi-component, over-long, or NUL-bearing) part raises ``SpecValidationError``
    here rather than producing a leaf that fails a later ``*at`` syscall.
    """
    require_valid_identifier("txid", txid)
    require_valid_identifier("effect_id", effect_id)
    require_valid_identifier("role", role)
    return f"{SCRATCH_SIGIL}{txid}.{effect_id}.{role}"
```

- [x] **Step 8: Run test to verify it passes**

Run: `uv run pytest tests/test_scratch.py -v`
Expected: PASS.

- [x] **Step 9: Lint, type-check, commit**

```bash
uv run ruff check && uv run pyright
git add python/src/atoms/core/identifiers.py python/src/atoms/core/scratch.py \
        python/tests/test_identifiers.py python/tests/test_scratch.py
git commit -m "feat(core): scratch grammar with letter-free sigil + bounded safe identifiers"
```

---

### Task 4: Effect variants and uniform occurrence enumeration

**Files:**
- Create: `python/src/atoms/core/effects.py`
- Test: `python/tests/test_effects.py`

**Interfaces:**
- Consumes: `atoms.core.fingerprint` (`AbsentState`, `FileState`, `DirectoryState`, `SymlinkState`, `PathState`, `ABSENT`).
- Produces:
  - `RelPath = str` alias.
  - Frozen dataclasses `ReplaceFile(effect_id, path, pre: FileState, post: FileState)`, `CreateFileNoClobber(effect_id, path, post: FileState)`, `DeletePath(effect_id, path, pre: FileState | SymlinkState)`, `MoveNoClobber(effect_id, source, destination, source_pre: FileState)`, `CreateDirectory(effect_id, path, post: DirectoryState)`.
  - `Effect` union alias.
  - `Occurrence(path: RelPath, pre: PathState, post: PathState, role: str)` frozen dataclass.
  - `occurrences(effect: Effect) -> tuple[Occurrence, ...]` (`singledispatch`), enumerating every `(path, pre, post, role)` the variant touches, with roles `"target"`, `"source"`, `"destination"`.
  - `effect_id_of(effect: Effect) -> str`, `variant_name(effect: Effect) -> str`.

- [x] **Step 1: Write the failing test**

`python/tests/test_effects.py`:

```python
from atoms.core.effects import (
    CreateDirectory,
    CreateFileNoClobber,
    DeletePath,
    MoveNoClobber,
    Occurrence,
    ReplaceFile,
    effect_id_of,
    occurrences,
    variant_name,
)
from atoms.core.fingerprint import (
    ABSENT,
    DirectoryState,
    FileState,
    SymlinkState,
)

F1 = FileState(content_hash="sha256:" + "1" * 64, mode=0o644, byte_len=3)
F2 = FileState(content_hash="sha256:" + "2" * 64, mode=0o644, byte_len=5)


def test_replace_file_occurrence_is_pre_to_post_on_one_path():
    e = ReplaceFile(effect_id="e1", path="a.txt", pre=F1, post=F2)
    assert occurrences(e) == (Occurrence(path="a.txt", pre=F1, post=F2, role="target"),)
    assert effect_id_of(e) == "e1"
    assert variant_name(e) == "ReplaceFile"


def test_create_file_is_absent_to_post():
    e = CreateFileNoClobber(effect_id="e2", path="new.txt", post=F1)
    assert occurrences(e) == (Occurrence(path="new.txt", pre=ABSENT, post=F1, role="target"),)


def test_delete_is_pre_to_absent_for_file_and_symlink():
    ef = DeletePath(effect_id="e3", path="gone.txt", pre=F1)
    assert occurrences(ef) == (Occurrence(path="gone.txt", pre=F1, post=ABSENT, role="target"),)
    sl = SymlinkState(target="x", mode=0o777)
    es = DeletePath(effect_id="e4", path="link", pre=sl)
    assert occurrences(es) == (Occurrence(path="link", pre=sl, post=ABSENT, role="target"),)


def test_move_enumerates_source_and_destination():
    e = MoveNoClobber(effect_id="e5", source="s.txt", destination="d.txt", source_pre=F1)
    assert occurrences(e) == (
        Occurrence(path="s.txt", pre=F1, post=ABSENT, role="source"),
        Occurrence(path="d.txt", pre=ABSENT, post=F1, role="destination"),
    )


def test_create_directory_is_absent_to_dir():
    d = DirectoryState(mode=0o755)
    e = CreateDirectory(effect_id="e6", path="sub", post=d)
    assert occurrences(e) == (Occurrence(path="sub", pre=ABSENT, post=d, role="target"),)
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_effects.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'atoms.core.effects'`.

- [x] **Step 3: Write minimal implementation**

`python/src/atoms/core/effects.py`:

```python
"""The closed effect set and a uniform per-path occurrence view (design §5.2)."""

from __future__ import annotations

from dataclasses import dataclass
from functools import singledispatch

from atoms.core.fingerprint import (
    ABSENT,
    DirectoryState,
    FileState,
    PathState,
    SymlinkState,
)

RelPath = str


@dataclass(frozen=True, slots=True)
class ReplaceFile:
    effect_id: str
    path: RelPath
    pre: FileState
    post: FileState


@dataclass(frozen=True, slots=True)
class CreateFileNoClobber:
    effect_id: str
    path: RelPath
    post: FileState


@dataclass(frozen=True, slots=True)
class DeletePath:
    effect_id: str
    path: RelPath
    pre: FileState | SymlinkState


@dataclass(frozen=True, slots=True)
class MoveNoClobber:
    effect_id: str
    source: RelPath
    destination: RelPath
    source_pre: FileState


@dataclass(frozen=True, slots=True)
class CreateDirectory:
    effect_id: str
    path: RelPath
    post: DirectoryState


Effect = ReplaceFile | CreateFileNoClobber | DeletePath | MoveNoClobber | CreateDirectory


@dataclass(frozen=True, slots=True)
class Occurrence:
    """One path's (pre, post) transition within a single effect."""

    path: RelPath
    pre: PathState
    post: PathState
    role: str


@singledispatch
def occurrences(effect: Effect) -> tuple[Occurrence, ...]:
    raise TypeError(f"unknown effect variant: {type(effect).__name__}")


@occurrences.register
def _(effect: ReplaceFile) -> tuple[Occurrence, ...]:
    return (Occurrence(path=effect.path, pre=effect.pre, post=effect.post, role="target"),)


@occurrences.register
def _(effect: CreateFileNoClobber) -> tuple[Occurrence, ...]:
    return (Occurrence(path=effect.path, pre=ABSENT, post=effect.post, role="target"),)


@occurrences.register
def _(effect: DeletePath) -> tuple[Occurrence, ...]:
    return (Occurrence(path=effect.path, pre=effect.pre, post=ABSENT, role="target"),)


@occurrences.register
def _(effect: MoveNoClobber) -> tuple[Occurrence, ...]:
    return (
        Occurrence(path=effect.source, pre=effect.source_pre, post=ABSENT, role="source"),
        Occurrence(path=effect.destination, pre=ABSENT, post=effect.source_pre, role="destination"),
    )


@occurrences.register
def _(effect: CreateDirectory) -> tuple[Occurrence, ...]:
    return (Occurrence(path=effect.path, pre=ABSENT, post=effect.post, role="target"),)


def effect_id_of(effect: Effect) -> str:
    return effect.effect_id


def variant_name(effect: Effect) -> str:
    return type(effect).__name__
```

- [x] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_effects.py -v`
Expected: PASS.

- [x] **Step 5: Lint, type-check, commit**

```bash
uv run ruff check && uv run pyright
git add python/src/atoms/core/effects.py python/tests/test_effects.py
git commit -m "feat(core): five effect variants and uniform occurrence enumeration"
```

---

### Task 5: Capability vocabulary

Implements design §5.5's semantic capability set as data: the always-required trio, the per-variant additions (including `DeletePath`'s file-vs-symlink branch), and the spec-level union.

**Files:**
- Create: `python/src/atoms/core/capabilities.py`
- Test: `python/tests/test_capabilities.py`

**Interfaces:**
- Consumes: `atoms.core.effects` (`Effect` union + variants), `atoms.core.fingerprint` (`FileState`, `SymlinkState`).
- Produces:
  - `Capability` enum: `ATOMIC_EXCHANGE`, `NOCLOBBER_TRANSFER`, `IDENTITY_ANCHOR`, `ANCHORED_TRAVERSAL`, `DURABLE_PUBLISH`, `NOFOLLOW_COHERENT_READ`, `SYMLINK_FINGERPRINT`, `ADVISORY_PROJECT_LOCK`.
  - `ALWAYS_REQUIRED: frozenset[Capability]` = `{ANCHORED_TRAVERSAL, DURABLE_PUBLISH, ADVISORY_PROJECT_LOCK}`.
  - `variant_capabilities(effect: Effect) -> frozenset[Capability]`.
  - `required_capabilities(effects: Iterable[Effect]) -> frozenset[Capability]` = `ALWAYS_REQUIRED ∪ union(variant_capabilities)`.

- [x] **Step 1: Write the failing test**

`python/tests/test_capabilities.py`:

```python
from atoms.core.capabilities import (
    ALWAYS_REQUIRED,
    Capability,
    required_capabilities,
    variant_capabilities,
)
from atoms.core.effects import (
    CreateDirectory,
    CreateFileNoClobber,
    DeletePath,
    MoveNoClobber,
    ReplaceFile,
)
from atoms.core.fingerprint import DirectoryState, FileState, SymlinkState

F = FileState(content_hash="sha256:" + "3" * 64, mode=0o644, byte_len=2)
C = Capability


def test_always_required_trio():
    assert ALWAYS_REQUIRED == frozenset(
        {C.ANCHORED_TRAVERSAL, C.DURABLE_PUBLISH, C.ADVISORY_PROJECT_LOCK}
    )


def test_replace_file_needs_exchange_and_nofollow_read():
    e = ReplaceFile(effect_id="e", path="a", pre=F, post=F)
    assert variant_capabilities(e) == frozenset({C.ATOMIC_EXCHANGE, C.NOFOLLOW_COHERENT_READ})


def test_delete_capability_branches_on_precondition_kind():
    file_delete = DeletePath(effect_id="e", path="a", pre=F)
    assert variant_capabilities(file_delete) == frozenset(
        {C.NOCLOBBER_TRANSFER, C.NOFOLLOW_COHERENT_READ}
    )
    link_delete = DeletePath(effect_id="e", path="a", pre=SymlinkState(target="x", mode=0o777))
    assert variant_capabilities(link_delete) == frozenset(
        {C.NOCLOBBER_TRANSFER, C.SYMLINK_FINGERPRINT}
    )


def test_move_needs_identity_anchor():
    e = MoveNoClobber(effect_id="e", source="s", destination="d", source_pre=F)
    assert variant_capabilities(e) == frozenset(
        {C.IDENTITY_ANCHOR, C.NOCLOBBER_TRANSFER, C.NOFOLLOW_COHERENT_READ}
    )


def test_create_variants_need_noclobber_transfer():
    cf = CreateFileNoClobber(effect_id="e", path="a", post=F)
    cd = CreateDirectory(effect_id="e", path="a", post=DirectoryState(mode=0o755))
    assert variant_capabilities(cf) == frozenset({C.NOCLOBBER_TRANSFER})
    assert variant_capabilities(cd) == frozenset({C.NOCLOBBER_TRANSFER})


def test_replace_only_spec_excludes_identity_anchor_and_noclobber():
    caps = required_capabilities([ReplaceFile(effect_id="e", path="a", pre=F, post=F)])
    assert C.IDENTITY_ANCHOR not in caps
    assert C.NOCLOBBER_TRANSFER not in caps
    assert ALWAYS_REQUIRED <= caps
    assert {C.ATOMIC_EXCHANGE, C.NOFOLLOW_COHERENT_READ} <= caps


def test_required_is_union_over_effects():
    effects = [
        ReplaceFile(effect_id="e1", path="a", pre=F, post=F),
        MoveNoClobber(effect_id="e2", source="s", destination="d", source_pre=F),
    ]
    caps = required_capabilities(effects)
    assert caps == ALWAYS_REQUIRED | frozenset(
        {C.ATOMIC_EXCHANGE, C.NOFOLLOW_COHERENT_READ, C.IDENTITY_ANCHOR, C.NOCLOBBER_TRANSFER}
    )
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_capabilities.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'atoms.core.capabilities'`.

- [x] **Step 3: Write minimal implementation**

`python/src/atoms/core/capabilities.py`:

```python
"""Semantic filesystem capability vocabulary (design §5.5), expressed as data."""

from __future__ import annotations

from collections.abc import Iterable
from enum import Enum
from functools import singledispatch

from atoms.core.effects import (
    CreateDirectory,
    CreateFileNoClobber,
    DeletePath,
    Effect,
    MoveNoClobber,
    ReplaceFile,
)
from atoms.core.fingerprint import FileState


class Capability(Enum):
    ATOMIC_EXCHANGE = "atomic_exchange"
    NOCLOBBER_TRANSFER = "noclobber_transfer"
    IDENTITY_ANCHOR = "identity_anchor"
    ANCHORED_TRAVERSAL = "anchored_traversal"
    DURABLE_PUBLISH = "durable_publish"
    NOFOLLOW_COHERENT_READ = "nofollow_coherent_read"
    SYMLINK_FINGERPRINT = "symlink_fingerprint"
    ADVISORY_PROJECT_LOCK = "advisory_project_lock"


ALWAYS_REQUIRED: frozenset[Capability] = frozenset(
    {
        Capability.ANCHORED_TRAVERSAL,
        Capability.DURABLE_PUBLISH,
        Capability.ADVISORY_PROJECT_LOCK,
    }
)


@singledispatch
def variant_capabilities(effect: Effect) -> frozenset[Capability]:
    raise TypeError(f"unknown effect variant: {type(effect).__name__}")


@variant_capabilities.register
def _(effect: ReplaceFile) -> frozenset[Capability]:
    return frozenset({Capability.ATOMIC_EXCHANGE, Capability.NOFOLLOW_COHERENT_READ})


@variant_capabilities.register
def _(effect: CreateFileNoClobber) -> frozenset[Capability]:
    return frozenset({Capability.NOCLOBBER_TRANSFER})


@variant_capabilities.register
def _(effect: DeletePath) -> frozenset[Capability]:
    read = (
        Capability.NOFOLLOW_COHERENT_READ
        if isinstance(effect.pre, FileState)
        else Capability.SYMLINK_FINGERPRINT
    )
    return frozenset({Capability.NOCLOBBER_TRANSFER, read})


@variant_capabilities.register
def _(effect: MoveNoClobber) -> frozenset[Capability]:
    return frozenset(
        {
            Capability.IDENTITY_ANCHOR,
            Capability.NOCLOBBER_TRANSFER,
            Capability.NOFOLLOW_COHERENT_READ,
        }
    )


@variant_capabilities.register
def _(effect: CreateDirectory) -> frozenset[Capability]:
    return frozenset({Capability.NOCLOBBER_TRANSFER})


def required_capabilities(effects: Iterable[Effect]) -> frozenset[Capability]:
    caps = set(ALWAYS_REQUIRED)
    for effect in effects:
        caps |= variant_capabilities(effect)
    return frozenset(caps)
```

- [x] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_capabilities.py -v`
Expected: PASS.

- [x] **Step 5: Lint, type-check, commit**

```bash
uv run ruff check && uv run pyright
git add python/src/atoms/core/capabilities.py python/tests/test_capabilities.py
git commit -m "feat(core): semantic capability vocabulary and per-variant derivation"
```

---

### Task 6: TransactionSpec and canonicalizing builder

`build_spec` is the only sanctioned constructor: it canonicalizes surfaces (sorted by path, de-duplicated) and dependencies (sorted) so two builds from differently-ordered inputs produce an equal spec. It performs **no** validation — that is A2's `compile`/`validate` step; `build_spec` only imposes deterministic *form*.

**Files:**
- Create: `python/src/atoms/core/spec.py`
- Test: `python/tests/test_spec.py`

**Interfaces:**
- Consumes: `atoms.core.effects` (`Effect`), `atoms.core.fingerprint` (`PathState`), `atoms.core.capabilities` (`required_capabilities`, `Capability`).
- Produces:
  - `SCHEMA_VERSION = 1`.
  - `SurfaceEntry(path: str, state: PathState)` frozen dataclass.
  - `Dependency(before: str, after: str)` frozen dataclass.
  - `TransactionSpec(schema_version, consumer_tag, intent_digest, initial_surface: tuple[SurfaceEntry, ...], final_surface: tuple[SurfaceEntry, ...], effects: tuple[Effect, ...], dependencies: tuple[Dependency, ...])` frozen dataclass, with method `required_capabilities(self) -> frozenset[Capability]`.
  - `build_spec(*, consumer_tag, intent_digest, initial_surface: Mapping[str, PathState], final_surface: Mapping[str, PathState], effects: Sequence[Effect], dependencies: Iterable[tuple[str, str]] = ()) -> TransactionSpec`.

- [x] **Step 1: Write the failing test**

`python/tests/test_spec.py`:

```python
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
    a = _spec({"a": ABSENT, "b": ABSENT}, {"a": F, "b": F},
              [CreateFileNoClobber(effect_id="e1", path="a", post=F),
               CreateFileNoClobber(effect_id="e2", path="b", post=F)])
    b = _spec({"b": ABSENT, "a": ABSENT}, {"b": F, "a": F},
              [CreateFileNoClobber(effect_id="e1", path="a", post=F),
               CreateFileNoClobber(effect_id="e2", path="b", post=F)])
    assert a == b


def test_dependencies_are_sorted():
    spec = _spec(
        {"a": F, "b": F},
        {"a": F, "b": F},
        [ReplaceFile(effect_id="e1", path="a", pre=F, post=F),
         ReplaceFile(effect_id="e2", path="b", pre=F, post=F)],
        deps=[("e2", "e1"), ("e1", "e2")],
    )
    assert spec.dependencies == (Dependency(before="e1", after="e2"), Dependency(before="e2", after="e1"))


def test_required_capabilities_derives_from_effects():
    spec = _spec({"a": F}, {"a": F}, [ReplaceFile(effect_id="e1", path="a", pre=F, post=F)])
    caps = spec.required_capabilities()
    assert ALWAYS_REQUIRED <= caps
    assert Capability.ATOMIC_EXCHANGE in caps
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_spec.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'atoms.core.spec'`.

- [x] **Step 3: Write minimal implementation**

`python/src/atoms/core/spec.py`:

```python
"""The internal TransactionSpec and its canonicalizing builder (design §5.1)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from atoms.core.capabilities import Capability, required_capabilities
from atoms.core.effects import Effect
from atoms.core.fingerprint import PathState

SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class SurfaceEntry:
    path: str
    state: PathState


@dataclass(frozen=True, slots=True)
class Dependency:
    before: str
    after: str


@dataclass(frozen=True, slots=True)
class TransactionSpec:
    schema_version: int
    consumer_tag: str
    intent_digest: str
    initial_surface: tuple[SurfaceEntry, ...]
    final_surface: tuple[SurfaceEntry, ...]
    effects: tuple[Effect, ...]
    dependencies: tuple[Dependency, ...]

    def required_capabilities(self) -> frozenset[Capability]:
        return required_capabilities(self.effects)


def _canonical_surface(surface: Mapping[str, PathState]) -> tuple[SurfaceEntry, ...]:
    return tuple(
        SurfaceEntry(path=path, state=surface[path]) for path in sorted(surface)
    )


def build_spec(
    *,
    consumer_tag: str,
    intent_digest: str,
    initial_surface: Mapping[str, PathState],
    final_surface: Mapping[str, PathState],
    effects: Sequence[Effect],
    dependencies: Iterable[tuple[str, str]] = (),
) -> TransactionSpec:
    """Construct a spec in canonical form. Imposes deterministic ordering only; it does
    not validate (that is compilation validation, design §5.4)."""
    deps = tuple(sorted(Dependency(before=b, after=a) for b, a in dependencies))
    return TransactionSpec(
        schema_version=SCHEMA_VERSION,
        consumer_tag=consumer_tag,
        intent_digest=intent_digest,
        initial_surface=_canonical_surface(initial_surface),
        final_surface=_canonical_surface(final_surface),
        effects=tuple(effects),
        dependencies=deps,
    )
```

Note: `sorted(Dependency(...))` requires `Dependency` to be order-comparable. `@dataclass(frozen=True, slots=True)` is not ordered by default; add `order=True` to the `Dependency` decorator (`@dataclass(frozen=True, slots=True, order=True)`) so the sort is well-defined. Apply that change to `Dependency` before running the test.

- [x] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_spec.py -v`
Expected: PASS.

- [x] **Step 5: Lint, type-check, commit**

```bash
uv run ruff check && uv run pyright
git add python/src/atoms/core/spec.py python/tests/test_spec.py
git commit -m "feat(core): TransactionSpec and canonicalizing builder"
```

---

### Task 7: Deterministic canonical serialization

Implements the byte-stable encoding the design relies on twice: §13.3 (validate a spec twice → identical canonical output) and §7.2 (`spec_json` stored immutably). Encoding is a tagged-union `dict` tree emitted via `json.dumps` with sorted keys and no whitespace variance.

`TransactionSpec` stays a public frozen dataclass, so a caller *could* construct one with unsorted set-like fields and bypass `build_spec`'s canonicalization. To make canonical bytes a pure function of the spec's *content* rather than its tuple order, the serializer **re-sorts the set-like fields itself**: `initial_surface` and `final_surface` (sets keyed by path) and `dependencies` (a set) are sorted at emit time. `effects` is an ordered sequence — its order is semantically meaningful (design §5.2) — so it is **not** re-sorted. (De-duplication and well-formedness remain compilation-validation's job in A2; A1's guarantee is order-invariance of equivalent content, not rejection of malformed content.)

**Files:**
- Create: `python/src/atoms/core/canonical.py`
- Create: `python/tests/fixtures/spec_all_variants.canonical.json` (generated golden fixture)
- Test: `python/tests/test_canonical.py`

**Interfaces:**
- Consumes: all of `atoms.core.fingerprint`, `atoms.core.effects`, `atoms.core.spec`.
- Produces:
  - `canonical_obj(spec: TransactionSpec) -> dict` — a JSON-ready tree with `"type"` discriminators for each state and effect; `initial_surface`, `final_surface`, and `dependencies` are emitted in sorted order regardless of the spec's tuple order.
  - `canonical_json(spec: TransactionSpec) -> str` — `json.dumps(canonical_obj(spec), sort_keys=True, separators=(",", ":"), ensure_ascii=False)`.
  - `canonical_bytes(spec: TransactionSpec) -> bytes` — UTF-8 of `canonical_json`.

- [x] **Step 1: Write the failing test**

`python/tests/test_canonical.py`:

```python
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
        effects=(CreateFileNoClobber(effect_id="e1", path="a", post=F),
                 CreateFileNoClobber(effect_id="e2", path="b", post=F)),
    )
    reverse = build_spec(
        consumer_tag="c",
        intent_digest="sha256:" + "0" * 64,
        initial_surface={"b": ABSENT, "a": ABSENT},
        final_surface={"b": F, "a": F},
        effects=(CreateFileNoClobber(effect_id="e1", path="a", post=F),
                 CreateFileNoClobber(effect_id="e2", path="b", post=F)),
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
        effects=(MoveNoClobber(effect_id="m", source="s", destination="d", source_pre=F),),
    )
    obj = canonical_obj(spec)
    assert obj["effects"][0]["type"] == "MoveNoClobber"
    assert obj["initial_surface"][0]["state"]["type"] in {"absent", "file"}


def test_two_replace_specs_differing_only_in_hash_differ():
    g = FileState(content_hash="sha256:" + "6" * 64, mode=0o644, byte_len=7)
    a = build_spec(consumer_tag="c", intent_digest="sha256:" + "0" * 64,
                   initial_surface={"x": F}, final_surface={"x": g},
                   effects=(ReplaceFile(effect_id="e", path="x", pre=F, post=g),))
    b = build_spec(consumer_tag="c", intent_digest="sha256:" + "0" * 64,
                   initial_surface={"x": F}, final_surface={"x": F},
                   effects=(ReplaceFile(effect_id="e", path="x", pre=F, post=F),))
    assert canonical_json(a) != canonical_json(b)


def _all_variants_spec():
    """One spec exercising every effect variant, every path-state, dependencies,
    and non-ASCII data — the fixture the durable-format tests lock."""
    return build_spec(
        consumer_tag="cnsmr",
        intent_digest="sha256:" + "0" * 64,
        initial_surface={
            "café.txt": F, "new.txt": ABSENT, "del.txt": F, "lnk": L,
            "src": F, "dst": ABSENT, "dir": ABSENT,
        },
        final_surface={
            "café.txt": F, "new.txt": F, "del.txt": ABSENT, "lnk": ABSENT,
            "src": ABSENT, "dst": F, "dir": G,
        },
        effects=(
            ReplaceFile(effect_id="e1", path="café.txt", pre=F, post=F),
            CreateFileNoClobber(effect_id="e2", path="new.txt", post=F),
            DeletePath(effect_id="e3", path="del.txt", pre=F),
            DeletePath(effect_id="e4", path="lnk", pre=L),
            MoveNoClobber(effect_id="e5", source="src", destination="dst", source_pre=F),
            CreateDirectory(effect_id="e6", path="dir", post=G),
        ),
        dependencies=[("e2", "e5"), ("e1", "e3")],
    )


def test_every_effect_and_state_variant_encodes_with_exact_discriminators():
    obj = canonical_obj(_all_variants_spec())
    by_id = {e["effect_id"]: e for e in obj["effects"]}
    file_obj = {"type": "file", "content_hash": F.content_hash, "mode": 0o644, "byte_len": 7}
    assert by_id["e1"] == {"type": "ReplaceFile", "effect_id": "e1", "path": "café.txt",
                           "pre": file_obj, "post": file_obj}
    assert by_id["e2"] == {"type": "CreateFileNoClobber", "effect_id": "e2", "path": "new.txt",
                           "post": file_obj}
    assert by_id["e3"] == {"type": "DeletePath", "effect_id": "e3", "path": "del.txt", "pre": file_obj}
    assert by_id["e4"]["pre"] == {"type": "symlink", "target": "café/target", "mode": 0o777}
    assert by_id["e5"] == {"type": "MoveNoClobber", "effect_id": "e5", "source": "src",
                           "destination": "dst", "source_pre": file_obj}
    assert by_id["e6"]["post"] == {"type": "directory", "mode": 0o755}
    assert "absent" in {e["state"]["type"] for e in obj["initial_surface"]}
    # dependencies emitted in sorted order regardless of input order
    assert obj["dependencies"] == [{"before": "e1", "after": "e3"}, {"before": "e2", "after": "e5"}]


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
    fixture = Path(__file__).with_name("fixtures") / "spec_all_variants.canonical.json"
    assert canonical_bytes(_all_variants_spec()) == fixture.read_bytes()
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_canonical.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'atoms.core.canonical'`.

- [x] **Step 3: Write minimal implementation**

`python/src/atoms/core/canonical.py`:

```python
"""Deterministic canonical serialization of a TransactionSpec (design §13.3, §7.2)."""

from __future__ import annotations

import json
from functools import singledispatch
from typing import Any

from atoms.core.effects import (
    CreateDirectory,
    CreateFileNoClobber,
    DeletePath,
    Effect,
    MoveNoClobber,
    ReplaceFile,
)
from atoms.core.fingerprint import (
    AbsentState,
    DirectoryState,
    FileState,
    PathState,
    SymlinkState,
)
from atoms.core.spec import Dependency, SurfaceEntry, TransactionSpec


@singledispatch
def _state_obj(state: PathState) -> dict[str, Any]:
    raise TypeError(f"unknown path state: {type(state).__name__}")


@_state_obj.register
def _(state: AbsentState) -> dict[str, Any]:
    return {"type": "absent"}


@_state_obj.register
def _(state: FileState) -> dict[str, Any]:
    return {"type": "file", "content_hash": state.content_hash, "mode": state.mode, "byte_len": state.byte_len}


@_state_obj.register
def _(state: DirectoryState) -> dict[str, Any]:
    return {"type": "directory", "mode": state.mode}


@_state_obj.register
def _(state: SymlinkState) -> dict[str, Any]:
    return {"type": "symlink", "target": state.target, "mode": state.mode}


@singledispatch
def _effect_obj(effect: Effect) -> dict[str, Any]:
    raise TypeError(f"unknown effect variant: {type(effect).__name__}")


@_effect_obj.register
def _(effect: ReplaceFile) -> dict[str, Any]:
    return {"type": "ReplaceFile", "effect_id": effect.effect_id, "path": effect.path,
            "pre": _state_obj(effect.pre), "post": _state_obj(effect.post)}


@_effect_obj.register
def _(effect: CreateFileNoClobber) -> dict[str, Any]:
    return {"type": "CreateFileNoClobber", "effect_id": effect.effect_id, "path": effect.path,
            "post": _state_obj(effect.post)}


@_effect_obj.register
def _(effect: DeletePath) -> dict[str, Any]:
    return {"type": "DeletePath", "effect_id": effect.effect_id, "path": effect.path,
            "pre": _state_obj(effect.pre)}


@_effect_obj.register
def _(effect: MoveNoClobber) -> dict[str, Any]:
    return {"type": "MoveNoClobber", "effect_id": effect.effect_id, "source": effect.source,
            "destination": effect.destination, "source_pre": _state_obj(effect.source_pre)}


@_effect_obj.register
def _(effect: CreateDirectory) -> dict[str, Any]:
    return {"type": "CreateDirectory", "effect_id": effect.effect_id, "path": effect.path,
            "post": _state_obj(effect.post)}


def _surface_obj(entry: SurfaceEntry) -> dict[str, Any]:
    return {"path": entry.path, "state": _state_obj(entry.state)}


def _dependency_obj(dep: Dependency) -> dict[str, Any]:
    return {"before": dep.before, "after": dep.after}


def canonical_obj(spec: TransactionSpec) -> dict[str, Any]:
    # Set-like fields (surfaces keyed by path; dependencies) are sorted here so canonical
    # bytes are a pure function of content, independent of a spec's tuple order — even a
    # spec built directly, bypassing build_spec. `effects` is an ordered sequence (its
    # order is semantic, design §5.2) and is emitted as-is.
    initial = sorted(spec.initial_surface, key=lambda e: e.path)
    final = sorted(spec.final_surface, key=lambda e: e.path)
    deps = sorted(spec.dependencies, key=lambda d: (d.before, d.after))
    return {
        "schema_version": spec.schema_version,
        "consumer_tag": spec.consumer_tag,
        "intent_digest": spec.intent_digest,
        "initial_surface": [_surface_obj(e) for e in initial],
        "final_surface": [_surface_obj(e) for e in final],
        "effects": [_effect_obj(e) for e in spec.effects],
        "dependencies": [_dependency_obj(d) for d in deps],
    }


def canonical_json(spec: TransactionSpec) -> str:
    return json.dumps(canonical_obj(spec), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_bytes(spec: TransactionSpec) -> bytes:
    return canonical_json(spec).encode("utf-8")
```

- [x] **Step 4: Generate the golden fixture**

`test_golden_bytes_lock_the_durable_contract` needs the fixture to exist. Generate it once from the now-implemented encoder and commit it (this is deliberate golden-file bootstrap, not a placeholder). Run from `python/`:

```bash
mkdir -p tests/fixtures
uv run python -c "
from pathlib import Path
from atoms.core.canonical import canonical_bytes
from tests.test_canonical import _all_variants_spec
Path('tests/fixtures/spec_all_variants.canonical.json').write_bytes(canonical_bytes(_all_variants_spec()))
"
```

Then eyeball the fixture (`cat tests/fixtures/spec_all_variants.canonical.json`) and confirm it contains each discriminator (`ReplaceFile`, `CreateFileNoClobber`, `DeletePath`, `MoveNoClobber`, `CreateDirectory`, `file`, `directory`, `symlink`, `absent`), the literal `café`, and sorted dependencies — i.e. that the *reviewed content*, not just self-generated bytes, is what gets locked.

- [x] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_canonical.py -v`
Expected: PASS (all tests, including the golden and non-ASCII locks).

- [x] **Step 6: Full suite, lint, type-check**

Run (from `python/`): `uv run pytest && uv run ruff check && uv run pyright`
Expected: all tests pass, no lint/type errors.

- [x] **Step 7: Commit**

```bash
git add python/src/atoms/core/canonical.py python/tests/test_canonical.py \
        python/tests/fixtures/spec_all_variants.canonical.json
git commit -m "feat(core): deterministic canonical serialization + golden durable-format lock"
```

---

### Task 8: Strict canonical decoder and round-trip

The durable format (design §7.2 `spec_json`) is only half-specified by an encoder: **fresh-process recovery must reconstruct the exact `TransactionSpec` from stored bytes** (design §8.4). This task adds the inverse of Task 7 as a **complete, type-safe structural boundary** — not a happy-path parser. It validates every object, array, and scalar with an exact type (refusing `bool` where an integer is required, since `bool` subclasses `int`), dispatches on schema version and effect/state discriminators, rejects unknown discriminators / missing / extra fields / duplicate JSON keys, enforces effect↔state compatibility (e.g. `ReplaceFile.pre` must be a file, never absent), and converts malformed JSON and non-UTF-8 input into `SpecValidationError`. The guarantee is that `from_canonical_*` raises **only** `SpecValidationError` on any malformed input — never a leaked `KeyError`/`AttributeError`/`TypeError`/`UnicodeDecodeError` — and round-trips every well-formed spec, including the golden fixture. This is durable-*format* validation, distinct from A2's semantic/path/timeline validation.

**Files:**
- Modify: `python/src/atoms/core/canonical.py` (add decoder functions)
- Test: `python/tests/test_canonical_decode.py`

**Interfaces:**
- Consumes: `atoms.core.errors` (`SpecValidationError`), all of `atoms.core.fingerprint`, `atoms.core.effects`, `atoms.core.spec`.
- Produces:
  - `from_canonical_obj(obj: object) -> TransactionSpec` — accepts an arbitrary parsed value and refuses anything that is not a well-typed spec.
  - `from_canonical_json(text: str) -> TransactionSpec` — parses with a duplicate-key-rejecting hook (and JSON-error trapping), then delegates.
  - `from_canonical_bytes(data: bytes) -> TransactionSpec` — decodes UTF-8 (trapping decode errors), then delegates.
  - Round-trip guarantee: for any `build_spec`-produced spec, `from_canonical_bytes(canonical_bytes(spec)) == spec`.
  - Failure guarantee: on any malformed input, exactly `SpecValidationError` is raised.

- [x] **Step 1: Write the failing test**

`python/tests/test_canonical_decode.py`:

```python
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


def _minimal_obj(**overrides):
    obj = {
        "schema_version": 1, "consumer_tag": "c", "intent_digest": "sha256:" + "0" * 64,
        "initial_surface": [], "final_surface": [], "effects": [], "dependencies": [],
    }
    obj.update(overrides)
    return obj


def test_round_trip_of_every_variant_is_identity():
    spec = _all_variants_spec()
    assert from_canonical_bytes(canonical_bytes(spec)) == spec


def test_round_trip_through_the_committed_golden_fixture():
    fixture = Path(__file__).with_name("fixtures") / "spec_all_variants.canonical.json"
    assert from_canonical_bytes(fixture.read_bytes()) == _all_variants_spec()


def test_unknown_schema_version_is_rejected():
    spec = build_spec(consumer_tag="c", intent_digest="sha256:" + "0" * 64,
                      initial_surface={"a": F}, final_surface={"a": F}, effects=())
    obj = canonical_obj(spec)
    obj["schema_version"] = 999
    with pytest.raises(SpecValidationError, match="schema_version"):
        from_canonical_obj(obj)


def test_unknown_effect_discriminator_is_rejected():
    obj = canonical_obj(
        build_spec(consumer_tag="c", intent_digest="sha256:" + "0" * 64,
                   initial_surface={"a": ABSENT}, final_surface={"a": F}, effects=())
    )
    obj["effects"] = [{"type": "Frobnicate", "effect_id": "e", "path": "a"}]
    with pytest.raises(SpecValidationError, match="Frobnicate"):
        from_canonical_obj(obj)


def test_missing_state_field_is_rejected():
    with pytest.raises(SpecValidationError, match="content_hash"):
        from_canonical_obj(_minimal_obj(
            initial_surface=[{"path": "a", "state": {"type": "file", "mode": 420, "byte_len": 1}}],
        ))


def test_extra_field_is_rejected():
    with pytest.raises(SpecValidationError, match="unexpected"):
        from_canonical_obj(_minimal_obj(surprise=True))


def test_duplicate_json_key_is_rejected():
    dup = '{"schema_version":1,"schema_version":1,"consumer_tag":"c","intent_digest":"x",' \
          '"initial_surface":[],"final_surface":[],"effects":[],"dependencies":[]}'
    with pytest.raises(SpecValidationError, match="duplicate"):
        from_canonical_json(dup)


# --- adversarial coverage at each nesting level (F1) ---

def test_non_object_top_level_is_rejected():
    with pytest.raises(SpecValidationError, match="spec must be an object"):
        from_canonical_obj([1, 2, 3])


def test_bool_schema_version_is_rejected():
    # bool is a subclass of int; True == 1 must NOT pass as schema_version.
    with pytest.raises(SpecValidationError, match="schema_version must be an integer"):
        from_canonical_obj(_minimal_obj(schema_version=True))


def test_float_schema_version_is_rejected():
    with pytest.raises(SpecValidationError, match="schema_version must be an integer"):
        from_canonical_obj(_minimal_obj(schema_version=1.0))


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
        from_canonical_obj(_minimal_obj(
            initial_surface=[{"state": {"type": "absent"}}],
        ))


def test_mode_bool_is_rejected():
    with pytest.raises(SpecValidationError, match="mode must be an integer"):
        from_canonical_obj(_minimal_obj(
            initial_surface=[{"path": "a", "state": {"type": "directory", "mode": True}}],
        ))


def test_effect_field_rejects_incompatible_state_kind():
    # ReplaceFile.pre must be a file; an absent state must be refused, not accepted
    # into a field whose annotation is FileState.
    obj = canonical_obj(build_spec(
        consumer_tag="c", intent_digest="sha256:" + "0" * 64,
        initial_surface={"a": F}, final_surface={"a": F},
        effects=(ReplaceFile(effect_id="e", path="a", pre=F, post=F),),
    ))
    obj["effects"][0]["pre"] = {"type": "absent"}
    with pytest.raises(SpecValidationError, match="pre"):
        from_canonical_obj(obj)


def test_malformed_json_is_rejected():
    with pytest.raises(SpecValidationError, match="malformed canonical JSON"):
        from_canonical_json("{not json")


def test_malformed_utf8_is_rejected():
    with pytest.raises(SpecValidationError, match="not valid UTF-8"):
        from_canonical_bytes(b"\xff\xfe")
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_canonical_decode.py -v`
Expected: FAIL — `ImportError: cannot import name 'from_canonical_bytes'`.

- [x] **Step 3: Add the decoder to `canonical.py`**

First, add two imports at the **top** of `python/src/atoms/core/canonical.py` (alongside the existing imports, so ruff's E402 does not fire): `from atoms.core.errors import SpecValidationError` and extend the `atoms.core.spec` import to include `SCHEMA_VERSION` (i.e. `from atoms.core.spec import SCHEMA_VERSION, Dependency, SurfaceEntry, TransactionSpec`). The module already imports `json`. Then append the decoder below the existing encoder:

```python
# Scalar-type sentinels: distinct objects compared with `is`.
_STR = object()
_INT = object()

# Each state variant: (class, ((field, scalar-sentinel), ...)).
_STATE_SPEC: dict[str, tuple[Any, tuple[tuple[str, Any], ...]]] = {
    "absent": (AbsentState, ()),
    "file": (FileState, (("content_hash", _STR), ("mode", _INT), ("byte_len", _INT))),
    "directory": (DirectoryState, (("mode", _INT),)),
    "symlink": (SymlinkState, (("target", _STR), ("mode", _INT))),
}

# Each effect variant: (class, ((field, spec), ...)) where spec is _STR for a string
# field or a tuple of the state classes that field is permitted to hold.
_EFFECT_SPEC: dict[str, tuple[Any, tuple[tuple[str, Any], ...]]] = {
    "ReplaceFile": (ReplaceFile, (("effect_id", _STR), ("path", _STR),
                                  ("pre", (FileState,)), ("post", (FileState,)))),
    "CreateFileNoClobber": (CreateFileNoClobber, (("effect_id", _STR), ("path", _STR),
                                                  ("post", (FileState,)))),
    "DeletePath": (DeletePath, (("effect_id", _STR), ("path", _STR),
                                ("pre", (FileState, SymlinkState)))),
    "MoveNoClobber": (MoveNoClobber, (("effect_id", _STR), ("source", _STR),
                                      ("destination", _STR), ("source_pre", (FileState,)))),
    "CreateDirectory": (CreateDirectory, (("effect_id", _STR), ("path", _STR),
                                          ("post", (DirectoryState,)))),
}


def _ensure(condition: object, message: str) -> None:
    if not condition:
        raise SpecValidationError(message)


def _as_dict(value: Any, ctx: str) -> dict[str, Any]:
    _ensure(isinstance(value, dict), f"{ctx} must be an object")
    return value


def _as_list(value: Any, ctx: str) -> list[Any]:
    _ensure(isinstance(value, list), f"{ctx} must be an array")
    return value


def _as_str(value: Any, ctx: str) -> str:
    _ensure(isinstance(value, str), f"{ctx} must be a string")
    return value


def _as_int(value: Any, ctx: str) -> int:
    # bool is a subclass of int; reject it where a true integer is required.
    _ensure(isinstance(value, int) and not isinstance(value, bool), f"{ctx} must be an integer")
    return value


def _require_only(what: str, obj: dict[str, Any], allowed: tuple[str, ...]) -> None:
    for field in allowed:
        _ensure(field in obj, f"{what} is missing field {field!r}")
    extra = set(obj) - set(allowed)
    _ensure(not extra, f"{what} has unexpected field(s): {sorted(extra)}")


def _decode_scalar(sentinel: Any, value: Any, ctx: str) -> Any:
    return _as_str(value, ctx) if sentinel is _STR else _as_int(value, ctx)


def _decode_state(value: Any, ctx: str) -> PathState:
    obj = _as_dict(value, ctx)
    tag = obj.get("type")
    _ensure(tag in _STATE_SPEC, f"{ctx}: unknown path-state discriminator: {tag!r}")
    cls, fields = _STATE_SPEC[tag]
    _require_only(f"{ctx} state {tag}", obj, ("type", *(name for name, _ in fields)))
    kwargs = {name: _decode_scalar(sentinel, obj[name], f"{ctx}.{name}") for name, sentinel in fields}
    return cls(**kwargs)


def _decode_effect(value: Any, ctx: str) -> Effect:
    obj = _as_dict(value, ctx)
    tag = obj.get("type")
    _ensure(tag in _EFFECT_SPEC, f"{ctx}: unknown effect discriminator: {tag!r}")
    cls, fields = _EFFECT_SPEC[tag]
    _require_only(f"{ctx} effect {tag}", obj, ("type", *(name for name, _ in fields)))
    kwargs: dict[str, Any] = {}
    for name, spec in fields:
        if spec is _STR:
            kwargs[name] = _as_str(obj[name], f"{ctx}.{name}")
        else:
            state = _decode_state(obj[name], f"{ctx}.{name}")
            _ensure(isinstance(state, spec),
                    f"{ctx} effect {tag} field {name!r} may not hold a {type(state).__name__}")
            kwargs[name] = state
    return cls(**kwargs)


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    seen: dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            raise SpecValidationError(f"duplicate JSON key: {key!r}")
        seen[key] = value
    return seen


def from_canonical_obj(obj: Any) -> TransactionSpec:
    spec = _as_dict(obj, "spec")
    _require_only("spec", spec, ("schema_version", "consumer_tag", "intent_digest",
                                 "initial_surface", "final_surface", "effects", "dependencies"))
    version = _as_int(spec["schema_version"], "schema_version")
    _ensure(version == SCHEMA_VERSION, f"unsupported schema_version: {version!r}")
    surfaces: dict[str, tuple[SurfaceEntry, ...]] = {}
    for key in ("initial_surface", "final_surface"):
        entries = []
        for i, raw in enumerate(_as_list(spec[key], key)):
            item = _as_dict(raw, f"{key}[{i}]")
            _require_only(f"{key}[{i}]", item, ("path", "state"))
            entries.append(SurfaceEntry(
                path=_as_str(item["path"], f"{key}[{i}].path"),
                state=_decode_state(item["state"], f"{key}[{i}].state"),
            ))
        surfaces[key] = tuple(entries)
    deps = []
    for i, raw in enumerate(_as_list(spec["dependencies"], "dependencies")):
        item = _as_dict(raw, f"dependencies[{i}]")
        _require_only(f"dependencies[{i}]", item, ("before", "after"))
        deps.append(Dependency(
            before=_as_str(item["before"], f"dependencies[{i}].before"),
            after=_as_str(item["after"], f"dependencies[{i}].after"),
        ))
    effects = tuple(
        _decode_effect(raw, f"effects[{i}]")
        for i, raw in enumerate(_as_list(spec["effects"], "effects"))
    )
    return TransactionSpec(
        schema_version=version,
        consumer_tag=_as_str(spec["consumer_tag"], "consumer_tag"),
        intent_digest=_as_str(spec["intent_digest"], "intent_digest"),
        initial_surface=surfaces["initial_surface"],
        final_surface=surfaces["final_surface"],
        effects=effects,
        dependencies=tuple(deps),
    )


def from_canonical_json(text: str) -> TransactionSpec:
    try:
        parsed = json.loads(text, object_pairs_hook=_no_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise SpecValidationError(f"malformed canonical JSON: {exc}") from exc
    return from_canonical_obj(parsed)


def from_canonical_bytes(data: bytes) -> TransactionSpec:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SpecValidationError(f"canonical bytes are not valid UTF-8: {exc}") from exc
    return from_canonical_json(text)
```

Note: every JSON scalar, object, and array is type-checked with an exact type (`bool` refused where an integer is required), each state is checked for compatibility with the effect field that holds it (so `ReplaceFile(pre=AbsentState())` is refused, not silently reconstructed), and malformed JSON / non-UTF-8 input is converted to `SpecValidationError` — so `from_canonical_*` raises **only** `SpecValidationError`, never a leaked `KeyError`/`AttributeError`/`TypeError`/`UnicodeDecodeError`. This is durable-format validation, distinct from A2's semantic/path/timeline validation. Decoding rebuilds `TransactionSpec` directly (not via `build_spec`), preserving stored tuple order; round-trip identity holds because a `build_spec`-produced spec is already canonical and `==` on the frozen dataclasses is structural.

- [x] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_canonical_decode.py -v`
Expected: PASS.

- [x] **Step 5: Full suite, lint, type-check, commit**

```bash
uv run pytest && uv run ruff check && uv run pyright
git add python/src/atoms/core/canonical.py python/tests/test_canonical_decode.py
git commit -m "feat(core): strict canonical decoder with round-trip and fail-early rejection"
```

---

## Self-review

**Spec coverage (against design §5 + the durable-format contract, the A1 slice):**
- §5.1 `TransactionSpec` fields — Task 6 (schema version, consumer tag, intent digest, initial/final surfaces, ordered effects, dependencies). Required-capability set is derived (Task 5/6), not stored, per §5.1. ✔
- §5.1 reserved scratch grammar + letter-free sigil — Task 3, including the case/NFC/NFD-agreement property (§13.3). ✔ Compiler *rejection* of persistent paths that alias scratch is A2 (needs the validation pass); A1 ships the predicates it will use.
- §5.1 scratch-leaf identifier safety — Task 3's bounded safe-identifier grammar makes a malformed consumer `effect_id` fail at construction, not at a later `*at` syscall. ✔ A2's compiler applies the same predicate to every effect ID.
- §5.2 closed effect set + "move is one effect over source and destination" — Task 4. ✔
- §5.3 repeated-path timelines — the `Occurrence` view (Task 4) is the data A2's continuity check consumes; the *check itself* is A2. Flagged, not silently dropped.
- §5.5 capability vocabulary as data, always-required trio, per-variant additions incl. Delete file/symlink branch — Task 5. ✔ Empirical probing is A4.
- §7.2 / §13.3 durable canonical format — Task 7 (deterministic encode; set-like fields sorted in the serializer so equal content yields equal bytes even for a directly-constructed spec; golden-byte fixture over every variant/state + non-ASCII) and **Task 8** (a complete type-safe decode boundary reconstructing the spec from `spec_json` for fresh-process recovery: exact scalar/container typing incl. bool-vs-int, effect↔state compatibility, malformed-JSON/UTF-8 trapping, round-trip identity, and the guarantee that only `SpecValidationError` escapes — with adversarial tests at each nesting level). ✔
- PEP 561 typed-distribution parity with `nodes-core` — Task 1: the `py.typed` marker, `core-metadata-version = "2.5"`, and `license-files` are proven to ship by **building the wheel and inspecting its ZIP + METADATA** (Step 4b), not merely by the editable-install source check; all verified against `~/d/nodes/python`. ✔
- **Deferred by design, not omitted:** filesystem-identity metadata-root containment and ancestor-resolution checks (§5.4), all compilation validation (§5.4), and the recovery classifier (§8.4/§13.1) belong to A2/A3/A4 and are listed in the program table above.

**Placeholder scan:** none — every code step carries complete source. The one golden fixture is generated by an explicit committed command (Task 7 Step 4), not left as a blank. ✔

**Type consistency:** `occurrences`, `variant_capabilities`, `required_capabilities`, `build_spec`, `canonical_json`/`canonical_bytes`, and `from_canonical_obj`/`from_canonical_json`/`from_canonical_bytes` names and signatures are used identically across tasks and the interface blocks. The decoder's per-variant descriptors (`_STATE_SPEC`, `_EFFECT_SPEC`) mirror the encoder's `_state_obj`/`_effect_obj` field spellings exactly, so a round-trip is structurally closed, and their per-field state-class constraints match the effect dataclass annotations from Task 4. `Dependency` is declared `order=True` (Task 6 note) precisely because `build_spec` sorts it. `FileState`/`DirectoryState`/`SymlinkState`/`AbsentState` field names are stable from Task 2 through Task 8. `SpecValidationError` (Task 1) is the single failure type for identifier violations (Task 3) and every decode violation (Task 8). ✔

**Scope:** A1 is one dependency-free subsystem, correctly sized for a single plan; the remaining Plan A work is decomposed into A2–A8 above. ✔
