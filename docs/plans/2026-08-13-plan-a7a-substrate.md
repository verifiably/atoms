# A7a Substrate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land every A7 component that does not execute an effect: the extended `Backend` and the
audited facade, the engine-reserved prefix split, `TransactionSpec` v2, store schema v2 with its
structural triggers, the `AssemblyHalt` value, the chain package, the `register_root` and
`append_intent` commands, and A6's six carried gaps — leaving the `_resolve` trap in place for A7b.

**Architecture:** A7 splits at the substrate/executor seam because every A7b component consumes A7a
interfaces and nothing here consumes A7b. The facade (`fs/audit.py`) becomes the only mutation
surface production code can reach, and it — not its callers — classifies every target against a
closed policy; the chain (`atoms/chain/`) is mechanism that never imports the coordinator; the
coordinator gains its first two public commands, both acquiring the lease internally. A7b — the
forward spine, five effect modules, plan executor, commit path, and trap removal — gets its own
plan, whose measured facts must be probed against the tree this plan produces.

**Tech Stack:** Python 3.11+, stdlib only (`os`, `stat`, `hashlib`, `errno`, `enum`, `json`,
`sqlite3`, `dataclasses`, `contextlib`, `unicodedata`), `pytest`, `ruff`, `pyright`. Builds on A1–A6
as probed below.

**Spec:** [`2026-08-13-a7-effect-recovery-execution-design.md`](2026-08-13-a7-effect-recovery-execution-design.md).
**Authority:** [`2026-07-23-recoverable-fs-effect-engine-design.md`](2026-07-23-recoverable-fs-effect-engine-design.md).
Where this plan and either document disagree, the design wins over this plan and the authority wins
over both.

## Measured facts this plan is built on

Probed against the live tree on 2026-08-13 (worktree `.worktrees/a7-design`, `310e60d`-series).
**If any turns out false during implementation, stop and report — do not adapt around it silently.**

| Fact | Where measured |
| --- | --- |
| `is_scratch_leaf(".#~chain")` is `True` today — the check is prefix-only. `scratch_leaf(txid, effect_id, role)` accepts **any** identifier-safe role; `tests/test_scratch.py:26,58` builds leaves with role `"stage"`, which no closed vocabulary contains. | probe + grep 2026-08-13 |
| The only `src/` caller of the sigil functions outside `core/scratch.py` is `core/paths.py:12,40` (A2's `aliases_scratch_sigil` refusal). `is_scratch_leaf` has **no** `src/` caller outside its module. | grep 2026-08-13 |
| `Backend` has exactly 11 methods (`open_root`, `open_child_directory`, `exchange`, `transfer_noclobber`, `link_anchor`, `flush_file`, `flush_directory`, `open_regular_nofollow`, `symlink_fingerprint`, `lock_exclusive`, `try_lock_exclusive`). | probe 2026-08-13; `fs/backend.py:67-102` |
| `Capability` has the eight members; `ALWAYS_REQUIRED` is `{ANCHORED_TRAVERSAL, DURABLE_PUBLISH, ADVISORY_PROJECT_LOCK}` — **no** `NOCLOBBER_TRANSFER`. | read 2026-08-13; `core/capabilities.py:20-38` |
| `TransactionSpec` fields: `schema_version`, `consumer_tag`, `intent_digest`, `initial_surface`, `final_surface`, `effects`, `dependencies`; `intent_digest` is §5.1's frozen-intent digest, distinct from the chain's `fulfills`. Spec suites live in `tests/test_spec.py` and `tests/test_compiler_structure.py` / `test_compiler_paths.py` / `test_compiler_properties.py` / `test_compiler_timelines.py`. | probe + ls 2026-08-13 |
| `canonical_json` / `from_canonical_json` live in `core/canonical.py:142`. | grep 2026-08-13 |
| `SCHEMA_VERSION = 1` via `PRAGMA user_version`; `active` is a `STRICT` singleton table; `Store.set_active` **upserts** (`UPSERT_ACTIVE`). Transaction/journal state **literals in DDL must be derived from the state enums in `store/schema.py` at DDL-construction time** — this plan deliberately spells no state literal, because a hand-spelled literal was review finding 1. | probe 2026-08-13 |
| Engine exceptions: `ProtocolError`, `PreconditionRefused`, `CapabilityUnavailable`, `TransactionHalted` in `core/errors.py`; `MetadataStoreInvalid` in `store/errors.py`. | grep 2026-08-13 |
| Raw `os.*` mutation/close sites (the complete migration inventory): `fs/lock.py:107,214,217`; `fs/bootstrap.py:25-37` (`os.mkdir`, `os.close`); `fs/probe.py:82-309` (open/write/mkdir/symlink); `fs/observe.py:291` (`os.write`); `store/workspace.py:137,215,217`; `store/blobs.py:95` plus its unlink/rmdir reclamation; `store/connection.py:234` (`create_store`'s `os.open`/`os.close`) **and its mode-repair path using `O_PATH` + `/proc/self/fd` chmod**; `coordinator/capture.py:313,340`. `fs/resolve.py:436` opens with `O_PATH` **read-only** — an observation, not a mutation. `fs/platform.py:36` constructs `LinuxBackend()`. | grep + read 2026-08-13 |
| `test_fs_architecture.py` defines `SOURCE_ROOT = Path(__file__).parents[1] / "src" / "atoms"` at `:22`; `_TRANSACTION_STAGE_ENTRY_POINTS` covers `prepare.py`, `capture.py`, `transitions.py`; new public proof-accepting coordinator functions fail the suite unless registered, and each must call `_require_admitted` first. | read 2026-08-13 |
| The A5b trap is `_resolve(store)` at `coordinator/lease.py:46-59`; trap invariants pinned at `tests/test_coordinator_lease.py:120,135,154,174,325`. **A7a leaves all of it untouched.** | survey 2026-08-13 |
| `prepare_transaction(lease, approved, workspace, manifest)` = promote + `insert_record` + `set_active` in one store transaction (`coordinator/prepare.py:36-39`); `Store.transaction()` refuses nesting; `_StoreTransaction` writers: `promote_staging`, `insert_record`, `set_transaction_state`, `set_commit_decision`, `set_rollback_result`, `set_halt_diagnostic`, `set_journal_state`, `set_active`. | survey 2026-08-13 |
| `test_docs_status.py`: `STAGES = (..., "A6", "A7", "A8", "A9")`, `FIRST_UNIMPLEMENTED = "A7"`; `_stages_of` expands base labels, so `"A7"` claims cover `A7a`/`A7b` once the tuple splits. | read 2026-08-13 |
| `DirectoryConstraints` = `lookup_proof` + `name_max`; mount check = `read_mount_id(fd)` vs `binding.evidence.mount_id`; `ProjectApprovedSpec` = `compiled, binding, txid, topology, directories, paths, scratch, work_base`; the required ⊆ supplied adjudication site is A4b-2's (`fs/approval.py` — locate `variant_capabilities`/`ALWAYS_REQUIRED` consumption there before Task 9). | survey 2026-08-13 |
| `Observation.observe(parent_fd, leaf, *, sink_fd=None, modeled=None)`; states `FileState(content_hash, mode, byte_len)`, `DirectoryState(mode)`, `SymlinkState(target, mode)`, `AbsentState()` in `core/fingerprint.py`. | survey 2026-08-13 |

## Global Constraints

- Stdlib only; no new dependencies. Python 3.11+.
- Fail early, no silent fallbacks; every refusal is a typed exception from `core/errors.py`,
  `store/errors.py`, or the new `chain/errors.py`.
- **No capability-enum or probe-semantics change** (design §5.1). The probe implementation moves
  onto the facade; the probed eight capabilities and their meanings do not move. The
  **required-set derivation does change** — `NOCLOBBER_TRANSFER` joins `ALWAYS_REQUIRED` (Task 9)
  — exactly as design §5.1 rules.
- **No effect execution and no project mutation outside `.#~chain/`** in this half. The A5b trap,
  its tests, and `CERTIFIED_ALLOWLIST = ()` stay exactly as they are.
- Every new public coordinator function acquires the lease internally, never exposes `Lease`, and
  never accepts `ProjectApprovedSpec`.
- Conventional commits, no AI-attribution trailers.
- After every task: `uv run pytest`, `uv run ruff check`, `uv run pyright` from `python/`, all
  green.
- **Task 11 runs last.** It flips the roadmap boundary and status prose to "A7a implemented", which
  is true only after Tasks 1–10 are merged.

## File Structure

```
python/src/atoms/
  core/
    scratch.py        # modify: engine-reserved prefix split, closed role vocabulary (Task 1)
    spec.py           # modify: TransactionSpec v2 (Task 6)
    canonical.py      # modify: encode/decode the two new members (Task 6)
    compiler.py       # modify: validate the new members (Task 6)
    capabilities.py   # modify: NOCLOBBER_TRANSFER joins ALWAYS_REQUIRED (Task 9)
    assembly.py       # create: AssemblyHalt + typed findings + canonical codec (Task 5)
  fs/
    backend.py        # modify: ten new protocol methods (Task 2)
    linux.py          # modify: their Linux implementations (Task 2)
    audit.py          # create: AuditedBackend facade, TargetPolicy, close_fd (Task 3)
    platform.py       # modify: raw-backend reachability (Task 4)
    lock.py, bootstrap.py, probe.py, observe.py    # modify: migrate onto the facade (Task 4)
  chain/
    __init__.py, errors.py, model.py, read.py, append.py   # create (Task 8)
  store/
    schema.py         # modify: schema v2 DDL + enum-derived triggers (Task 7)
    connection.py     # modify: columns, writers, active insert/delete, migrate opens (Tasks 4, 7)
    records.py        # modify: StoredRecord v2 fields (Task 7)
    workspace.py, blobs.py                          # modify: migrate onto the facade (Task 4)
  coordinator/
    capture.py        # modify: migrate staging writes (Task 4)
    descriptors.py    # modify: A6 gaps 1, 3, 5 (Task 10)
    prepare.py        # modify: thread approval evidence (Task 7)
    commands.py       # create: register_root, append_intent, require_registered_root (Task 9)
    root.py           # modify: wrap the backend, register root descriptors (Task 4)
python/tests/
  test_scratch.py (extend), test_fs_audit.py, test_core_assembly.py, test_chain_model.py,
  test_chain_append.py, test_store_schema_v2.py, test_coordinator_commands.py   # create/extend
  test_fs_architecture.py, test_docs_status.py, existing suites                 # modify
```

---

## Task 1: The engine-reserved prefix split and the closed role vocabulary

**Files:**
- Modify: `python/src/atoms/core/scratch.py`
- Test: extend `python/tests/test_scratch.py`

**Interfaces:**
- Produces: `CHAIN_LEAF = ".#~chain"`; `SCRATCH_ROLES: frozenset[str] = frozenset({"staging",
  "tombstone", "anchor", "work"})` — the **one** role vocabulary, consumed by builder and
  classifier alike; `is_engine_reserved_leaf(leaf: str) -> bool` (the old prefix check, verbatim);
  `is_scratch_leaf(leaf: str) -> bool` redefined to the full grammar **including identifier
  validation** of the txid and effect-id parts; `scratch_leaf` now **refuses** a role outside
  `SCRATCH_ROLES` (`ProtocolError`).
- Consumers: Task 3 (`CHAIN_LEAF`, `is_scratch_leaf`, `is_engine_reserved_leaf`), Task 8
  (`CHAIN_LEAF`). The four role strings must equal `core/recovery/model.py`'s `ScratchRole` values
  — assert that equality in a test rather than importing across (scratch.py must stay
  recovery-free).

- [ ] **Step 1: Write the failing tests** (extend `tests/test_scratch.py`; its existing cases at
  `:26,58` use role `"stage"` — change them to `"staging"`, which is the closed vocabulary's
  member; the old value was only ever identifier-safe filler):

```python
def test_chain_leaf_is_engine_reserved_but_not_scratch():
    assert CHAIN_LEAF == ".#~chain"
    assert is_engine_reserved_leaf(CHAIN_LEAF)
    assert not is_scratch_leaf(CHAIN_LEAF)


def test_builder_and_classifier_share_one_closed_vocabulary():
    for role in sorted(SCRATCH_ROLES):
        assert is_scratch_leaf(scratch_leaf("deadbeef", "e07", role))
    with pytest.raises(ProtocolError):
        scratch_leaf("deadbeef", "e07", "stage")   # yesterday's filler role is refused


def test_roles_equal_the_recovery_enum():
    from atoms.core.recovery.model import ScratchRole
    assert SCRATCH_ROLES == frozenset(role.value for role in ScratchRole)


def test_scratch_grammar_validates_identifiers():
    assert not is_scratch_leaf(".#~notthree")
    assert not is_scratch_leaf(".#~a.b")
    assert not is_scratch_leaf(".#~a.b.badrole")
    assert not is_scratch_leaf(".#~bad/id.e07.staging")   # identifier grammar enforced
    assert not is_scratch_leaf(f".#~{'x' * 300}.e07.staging")  # length bound enforced


def test_a2_still_refuses_a_declared_chain_component():
    spec = replace(VALID_SPEC, effects=(CreateFileNoClobber("e1", ".#~chain/f.txt", POST),))
    with pytest.raises(SpecValidationError):
        compile_spec(spec)
```

Build the last test from whatever minimal-valid-spec constant `tests/test_compiler_structure.py`
already uses (copy its construction — do not invent a helper module).
`is_scratch_leaf` validates the txid/effect parts with the same rules `require_valid_identifier`
enforces — add a non-raising `is_valid_identifier(part: str) -> bool` beside it and implement
`require_valid_identifier` over it so the two cannot drift.

- [ ] **Step 2:** Run — expected failures: missing names, and the `"stage"` refusal.
- [ ] **Step 3:** Implement in `core/scratch.py`:

```python
CHAIN_LEAF = ".#~chain"
SCRATCH_ROLES: frozenset[str] = frozenset({"staging", "tombstone", "anchor", "work"})


def is_engine_reserved_leaf(leaf: str) -> bool:
    return leaf.startswith(SCRATCH_SIGIL)


def is_scratch_leaf(leaf: str) -> bool:
    if not leaf.startswith(SCRATCH_SIGIL):
        return False
    parts = leaf[len(SCRATCH_SIGIL) :].split(".")
    if len(parts) != 3:
        return False
    txid, effect_id, role = parts
    return is_valid_identifier(txid) and is_valid_identifier(effect_id) and role in SCRATCH_ROLES
```

and in `scratch_leaf`, before building: `if role not in SCRATCH_ROLES: raise ProtocolError(...)`.
Keep `aliases_scratch_sigil` byte-identical (A2's refusal layer).

- [ ] **Step 4:** Full suite green (the `"stage"` update is the only expected test edit).
- [ ] **Step 5:** `git commit -m "feat(core): engine-reserved prefix split with a closed scratch-role vocabulary"`

---

## Task 2: The extended Backend protocol

**Files:**
- Modify: `python/src/atoms/fs/backend.py`, `python/src/atoms/fs/linux.py`
- Test: extend the suite that exercises `LinuxBackend` (locate:
  `grep -rln "LinuxBackend" python/tests/`).

**Interfaces — ten methods, all descriptor-relative single-component leaves:**

```python
def create_exclusive(self, parent_fd: int, name: str, mode: int) -> int: ...
    # os.open(name, O_CREAT|O_EXCL|O_NOFOLLOW|O_RDWR|O_CLOEXEC, mode, dir_fd=parent_fd)
    # O_RDWR: postconditions are re-read through this same descriptor (design §5.1).
def write(self, fd: int, data: bytes) -> int: ...            # os.write
def set_mode(self, fd: int, mode: int) -> None: ...          # os.fchmod
def mkdir_child(self, parent_fd: int, name: str, mode: int) -> None: ...
def unlink_child(self, parent_fd: int, name: str) -> None: ...
def rmdir_child(self, parent_fd: int, name: str) -> None: ...
def symlink_child(self, parent_fd: int, name: str, target: str) -> None: ...
def create_or_open(self, parent_fd: int, name: str, mode: int) -> int: ...
    # os.open(name, O_CREAT|O_NOFOLLOW|O_RDWR|O_CLOEXEC, mode, dir_fd=parent_fd) — the lock file.
def set_marker_xattr(self, fd: int, name: str, value: bytes) -> None: ...
def repair_entry_mode(self, parent_fd: int, name: str, mode: int) -> None: ...
    # The store's mode-000 database repair seam: open with O_PATH|O_NOFOLLOW|O_CLOEXEC
    # (dir_fd=parent_fd), chmod through /proc/self/fd/<fd>, close. Copy the exact idiom from
    # store/connection.py's existing repair path — this primitive exists so that idiom can live
    # beneath the facade instead of beside it.
```

No new `Capability` member, no probe change, no `UNSUPPORTED_ERRNO` row.

- [ ] **Step 1:** Failing tests, one behavior each over a `tmp_path` root fd — including the
  `O_RDWR` read-back proof for `create_exclusive` (write, `os.lseek(fd, 0, 0)`, `os.read`
  returns the bytes), non-truncation for `create_or_open` on an existing file, and
  `repair_entry_mode` restoring `0o000 → 0o600` on an entry that cannot be opened `O_RDWR`.
- [ ] **Step 2:** Run — protocol members missing.
- [ ] **Step 3:** Implement protocol + Linux bodies exactly as the comments; no errno translation
  (translation stays at call sites, authority §9.1).
- [ ] **Step 4:** Full suite green; the probe suite must pass **unchanged**.
- [ ] **Step 5:** `git commit -m "feat(fs): extend Backend with the audited mutating primitives"`

---

## Task 3: The audited facade — policy-classified, never caller-classified

**Files:**
- Create: `python/src/atoms/fs/audit.py`
- Test: `python/tests/test_fs_audit.py` (create)

**Interfaces (design §5.2; review finding 2 is the reason for the shape):**

```python
class TargetClass(enum.Enum):
    DECLARED_EFFECT = "declared-effect"
    ENGINE_SCRATCH = "engine-scratch"
    METADATA = "metadata"
    CHAIN_BOOKKEEPING = "chain-bookkeeping"

class RootKind(enum.Enum):
    PROJECT = "project"
    METADATA = "metadata"

@dataclasses.dataclass(frozen=True, slots=True)
class Provenance:
    root: RootKind
    path: str                  # engine-issued logical alias relative to that root ("" for the root)

@dataclasses.dataclass(frozen=True, slots=True)
class AuditRecord:
    operation: str
    target_class: TargetClass
    targets: tuple[str, ...]   # one entry normally; two for exchange / transfer_noclobber

class AuditedBackend:          # implements the full (extended) Backend protocol
    def __init__(self, inner: Backend) -> None: ...
    records: tuple[AuditRecord, ...]
    def register(self, fd: int, provenance: Provenance) -> None: ...
    def rebind(self, fd: int, provenance: Provenance) -> None: ...
    def unregister(self, fd: int) -> None: ...
    def provenance_of(self, fd: int) -> Provenance: ...      # ProtocolError when unregistered
    def close_fd(self, fd: int) -> None: ...                 # unregister (exactly once), then os.close;
                                                             # unregister happens even when close raises
    def set_declared_paths(self, paths: frozenset[str]) -> None: ...  # A7b's per-transaction scope;
    def clear_declared_paths(self) -> None: ...                       # empty outside a transaction
```

**The facade classifies; callers only name descriptors.** For a mutation against
`(parent provenance P, leaf L)` the class is *derived*, never supplied:

- `P.root is METADATA` → `METADATA` (the whole metadata tree is engine-owned);
- `P.root is PROJECT` and the joined path's first component is `CHAIN_LEAF` → `CHAIN_BOOKKEEPING`;
- `P.root is PROJECT` and `is_scratch_leaf(L)` → `ENGINE_SCRATCH`;
- `P.root is PROJECT` and the joined path is in the declared-paths scope → `DECLARED_EFFECT`;
- anything else → `ProtocolError`, no syscall issued, no record appended.

A7a leaves the declared scope permanently empty (nothing here mutates a declared path); A7b's
executor sets it per transaction from `approved.paths`. Two-target operations classify **each**
target independently — both must classify — and record both in `targets`. Opens auto-register the
returned descriptor with the joined provenance; `flush_*` and reads require registration but
mutate nothing.

- [ ] **Step 1:** Failing tests with a real backend over `tmp_path`:
  - unregistered descriptor → `ProtocolError`, no record, no syscall (assert via a wrapped inner
    backend that counts calls);
  - a `PROJECT`-rooted mutation on an arbitrary leaf (`"data.txt"`, declared scope empty) →
    `ProtocolError` — **the caller cannot launder an arbitrary target**;
  - the same leaf after `set_declared_paths(frozenset({"data.txt"}))` classifies
    `DECLARED_EFFECT`; after `clear_declared_paths()` it refuses again;
  - `.#~chain` first component under `PROJECT` → `CHAIN_BOOKKEEPING`; a scratch-grammar leaf →
    `ENGINE_SCRATCH`; any leaf under `METADATA` → `METADATA`;
  - `exchange` records both targets, in argument order;
  - records append only after syscall success (inner forced to raise → no record);
  - `create_exclusive` auto-registers (immediate `set_mode` through the returned fd succeeds);
  - `rebind` changes classification for subsequent descendants (the §9.5 rebind);
  - `close_fd` unregisters exactly once, closes, and still unregisters when `os.close` raises
    (monkeypatch); a second `close_fd` on the same fd → `ProtocolError`.
- [ ] **Step 2:** Run — module missing.
- [ ] **Step 3:** Implement; the registry is instance state (`dict[int, Provenance]`); no globals.
- [ ] **Step 4:** Suite green.
- [ ] **Step 5:** `git commit -m "feat(fs): the audited facade with policy-derived target classes"`

---

## Task 4: Migrate every raw mutation and close onto the facade, and guard it

**Files:**
- Modify: `python/src/atoms/fs/platform.py`, `python/src/atoms/fs/lock.py`,
  `python/src/atoms/fs/bootstrap.py`, `python/src/atoms/fs/probe.py`,
  `python/src/atoms/fs/observe.py`, `python/src/atoms/store/workspace.py`,
  `python/src/atoms/store/blobs.py`, `python/src/atoms/store/connection.py`,
  `python/src/atoms/coordinator/capture.py`, `python/src/atoms/coordinator/root.py`
- Test: `python/tests/test_fs_architecture.py` (extend)

**Interfaces:**
- Consumes: the full Task 3 facade including `close_fd` and `repair_entry_mode` (Task 2).
- Produces: the facade-only property, pinned by three architecture tests.

- [ ] **Step 4.1: Write the three failing architecture tests** (in `test_fs_architecture.py`,
  reusing its `SOURCE_ROOT` constant from line 22):

```python
_MUTATING_OS = {
    "rename", "replace", "unlink", "mkdir", "rmdir", "symlink", "link",
    "chmod", "fchmod", "lchmod", "setxattr", "fsetxattr", "write", "close",
}
_MUTATING_OPEN_FLAGS = {"O_CREAT", "O_TRUNC", "O_APPEND", "O_WRONLY", "O_RDWR"}
_FACADE_EXEMPT = ("fs/audit.py", "fs/linux.py", "fs/syscalls/")


def _os_calls(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "os"
        ):
            yield node


def test_no_direct_os_mutation_or_close_outside_the_facade():
    """Design §5.2: the facade is the only mutation and close surface; SQLite's VFS never
    spells os.* in our source, so it needs no carve-out here."""
    offenders = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        rel = str(path.relative_to(SOURCE_ROOT))
        if rel.startswith(_FACADE_EXEMPT):
            continue
        for node in _os_calls(path):
            name = node.func.attr
            if name in _MUTATING_OS:
                offenders.append(f"{rel}:{node.lineno} os.{name}")
            elif name == "open":
                flags = {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}
                if flags & _MUTATING_OPEN_FLAGS:
                    offenders.append(f"{rel}:{node.lineno} os.open(mutating flags)")
    assert offenders == []


def test_the_raw_backend_is_constructed_only_by_the_platform_factory():
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        rel = str(path.relative_to(SOURCE_ROOT))
        if rel in {"fs/platform.py", "fs/linux.py"}:
            continue
        assert "LinuxBackend(" not in path.read_text(encoding="utf-8"), rel


def test_the_platform_factory_returns_the_audited_facade():
    source = (SOURCE_ROOT / "fs" / "platform.py").read_text(encoding="utf-8")
    assert "return AuditedBackend(LinuxBackend())" in source
```

Notes pinned by the measured facts: `os.open` with only read flags (`fs/resolve.py:436`'s
`O_PATH` observation) is deliberately **not** swept — it mutates nothing and provenance for
resolution descriptors is A4b-1's own lifecycle; `os.close` **is** swept, which forces every close
site onto `close_fd` and closes review finding 4 mechanically.

- [ ] **Step 4.2: Run** — the offender list must name exactly the measured-facts inventory
  (lock, bootstrap, probe, observe, workspace, blobs, connection, capture, plus every `os.close`
  site those modules own). More names than the inventory means stale facts: stop and report.
- [ ] **Step 4.3: Wrap the factory** — `fs/platform.py` returns `AuditedBackend(LinuxBackend())`;
  `coordinator/root.py` registers the four root descriptors it owns as they are opened
  (`Provenance(RootKind.PROJECT, "")`, `Provenance(RootKind.METADATA, "")`, and the lock/probe
  descriptors under `METADATA` aliases). Tests that need the raw backend construct
  `LinuxBackend()` directly in test code — test files are outside `SOURCE_ROOT` and unaffected by
  the guards.
- [ ] **Step 4.4: Migrate, one commit per module, suites green after each:**
  - `fs/lock.py` — `mkdir_child`, `create_or_open`, `set_marker_xattr`, `close_fd`;
  - `fs/bootstrap.py` — `mkdir_child` (keep the EEXIST-reopen-verify shape), `close_fd` on the
    error path;
  - `fs/probe.py` — every open/write/mkdir/symlink/close through the facade under `METADATA`
    provenance; probe semantics unchanged (its suite must not change);
  - `fs/observe.py` — sink streaming through `backend.write`; closes through `close_fd`;
  - `store/workspace.py`, `store/blobs.py` — mkdir/open/unlink/rmdir/close through the facade
    (`METADATA` provenance for `staging_fd`/`work_fd`/blob parents);
  - `store/connection.py` — `create_store`'s exclusive create via `create_exclusive`, the mode
    repair via `repair_entry_mode`, closes via `close_fd`;
  - `coordinator/capture.py` — staging create via `create_exclusive`, streaming via
    `backend.write`, closes via `close_fd`.
- [ ] **Step 4.5: Run the three guards and the full suite** — green; probe and capture suites
  unchanged.
- [ ] **Step 4.6:** `git commit -m "refactor: route every engine mutation and close through the audited facade"`

---

## Task 5: The `AssemblyHalt` value with typed findings

**Files:**
- Create: `python/src/atoms/core/assembly.py`
- Test: `python/tests/test_core_assembly.py` (create)

**Interfaces (design §9.3; review finding 7 closes the fact vocabulary):**

```python
class AssemblyHaltReason(enum.Enum):
    APPROVAL_EVIDENCE_MISMATCH = "approval-evidence-mismatch"

class AssemblyOperatorAction(enum.Enum):
    RESTORE_APPROVED_TOPOLOGY = "restore-approved-topology"

class AssemblyFindingKind(enum.Enum):     # enum order IS the same-path sort order
    NODE_MISSING = "node-missing"
    WRONG_ENTRY_KIND = "wrong-entry-kind"
    IDENTITY_CHANGED = "identity-changed"
    CONSTRAINTS_CHANGED = "constraints-changed"
    MOUNT_CHANGED = "mount-changed"
    WORK_ROOT_CHANGED = "work-root-changed"

# The closed fact vocabulary, per kind — validated in __post_init__, exact keys, no extras:
_FACT_KEYS: dict[AssemblyFindingKind, tuple[str, ...]] = {
    AssemblyFindingKind.NODE_MISSING: (),
    AssemblyFindingKind.WRONG_ENTRY_KIND: ("observed_kind",),          # "file"|"symlink"|"other"
    AssemblyFindingKind.IDENTITY_CHANGED: ("st_dev", "st_ino"),        # decimal strings
    AssemblyFindingKind.CONSTRAINTS_CHANGED: ("lookup_proof", "name_max"),
    AssemblyFindingKind.MOUNT_CHANGED: ("mount_id",),
    AssemblyFindingKind.WORK_ROOT_CHANGED: ("work_base",),             # "present"|"absent"
}

@dataclasses.dataclass(frozen=True, slots=True)
class AssemblyFinding:
    path: str
    kind: AssemblyFindingKind
    observed: tuple[tuple[str, str], ...]   # exactly _FACT_KEYS[kind], sorted, all values str

@dataclasses.dataclass(frozen=True, slots=True)
class AssemblyHalt:
    txid: str
    reason: AssemblyHaltReason
    expected: str
    findings: tuple[AssemblyFinding, ...]
    operator_action: AssemblyOperatorAction

def encode_assembly_halt(halt: AssemblyHalt) -> str: ...
def decode_assembly_halt(payload: str) -> AssemblyHalt: ...   # exact round-trip or ProtocolError
```

`AssemblyHalt.__post_init__` enforces: findings non-empty; ordered exactly by
`(path, kind enum order)`; a `NODE_MISSING` or `WRONG_ENTRY_KIND` finding is its path's sole
finding. `AssemblyFinding.__post_init__` enforces: `observed` keys equal `_FACT_KEYS[kind]`
exactly and in that order.

- [ ] **Step 1:** Failing tests — exact round-trip per kind; decode refuses unknown kind, missing
  member, wrong fact keys, extra fact keys, unsorted findings; construction refuses out-of-order
  findings, a `NODE_MISSING` sharing its path, an empty findings tuple, and an
  `IDENTITY_CHANGED` finding with a `mount_id` fact.
- [ ] **Step 2:** Run — module missing. **Step 3:** Implement. **Step 4:** Suite green.
- [ ] **Step 5:** `git commit -m "feat(core): AssemblyHalt with the closed per-kind fact vocabulary"`

---

## Task 6: `TransactionSpec` v2

**Files:**
- Modify: `python/src/atoms/core/spec.py`, `python/src/atoms/core/canonical.py`,
  `python/src/atoms/core/compiler.py`
- Test: extend `python/tests/test_spec.py` (construction/round-trip) and
  `python/tests/test_compiler_structure.py` (validation refusals).

**Interfaces:** `TransactionSpec` gains `fulfills: str | None = None` and
`registered_paths: tuple[str, ...] = ()`; accepted `schema_version` becomes **2 and only 2** —
`compile_spec` and `from_canonical_json` both refuse v1 (`SpecValidationError`; Plan A has no
production data). `compile_spec` gains one validation phase: `registered_paths` sorted,
duplicate-free, every entry a path of one of the two surfaces; `fulfills`, when present, a
64-lowercase-hex string. `canonical_json`/`from_canonical_json` carry both members.

- [ ] **Step 1:** Failing tests — round-trip with both members set and with defaults; refusals:
  `schema_version=1`, unsorted/duplicated `registered_paths`, an entry in neither surface,
  `fulfills="xyz"`, `fulfills` of 63 chars, uppercase hex.
- [ ] **Step 2:** Run. **Step 3:** Implement.
- [ ] **Step 4:** Full suite — move every spec construction to `schema_version=2` through the
  suites' central spec constants (`test_spec.py` and the compiler suites share them); let failures
  name inline stragglers.
- [ ] **Step 5:** `git commit -m "feat(core): TransactionSpec v2 with fulfills and the registered-path subset"`

---

## Task 7: Store schema v2 — columns, enum-derived triggers, writer methods

**Files:**
- Modify: `python/src/atoms/store/schema.py`, `python/src/atoms/store/connection.py`,
  `python/src/atoms/store/records.py`, `python/src/atoms/fs/approval.py`,
  `python/src/atoms/coordinator/prepare.py`
- Test: `python/tests/test_store_schema_v2.py` (create), existing store suites (extend fixtures).

**Interfaces:**
- `SCHEMA_VERSION = 2`; `transaction_record` gains `registration_digest TEXT UNIQUE`,
  `settlement_digest TEXT UNIQUE`, `approval_evidence TEXT NOT NULL`, `assembly_halt TEXT`.
- `encode_approval_evidence(approved: ProjectApprovedSpec) -> str` in `fs/approval.py`: canonical
  JSON (sorted keys) of, per approved directory node: node key, resolved identity
  (`st_dev`, `st_ino`) for existing directories, `lookup_proof`, `name_max`; plus the binding's
  `mount_id`; plus work-root presence and constraints. This string is what A7b's phase-5
  comparison reads and what Task 5's `expected` carries.
- `_StoreTransaction` gains `set_registration_digest(txid, digest)`,
  `set_settlement_digest(txid, digest)`, `set_assembly_halt(txid, halt: AssemblyHalt)`;
  `insert_record` gains required `approval_evidence: str`; `set_active(txid)` becomes
  **INSERT-only** (`set_active(None)` stays DELETE; `UPSERT_ACTIVE` is deleted).
- `StoredRecord` gains `registration_digest: str | None`, `settlement_digest: str | None`,
  `approval_evidence: str`, `assembly_halt: AssemblyHalt | None`.
- **Every state literal in trigger DDL is derived from the state enums** — build the trigger SQL
  as f-strings over the enum values `store/schema.py` already defines for transaction and journal
  states (locate their exact enum classes and `.value` spellings there first; review finding 1
  exists because this plan's first draft hand-spelled them). The trigger set, with `{prepared}`,
  `{applying}`, `{rolling_back}`, `{committed}`, `{rolled_back}`, `{pending}`, `{started}`
  standing for those derived values:

```sql
-- write-once, unconditional: any UPDATE of a non-null digest is refused, including to NULL
trg_registration_write_once:  BEFORE UPDATE OF registration_digest ON transaction_record
  WHEN OLD.registration_digest IS NOT NULL -> RAISE
trg_settlement_write_once:    BEFORE UPDATE OF settlement_digest ON transaction_record
  WHEN OLD.settlement_digest IS NOT NULL -> RAISE
trg_evidence_write_once:      BEFORE UPDATE OF approval_evidence ON transaction_record -> RAISE
trg_assembly_halt_write_once: BEFORE UPDATE OF assembly_halt ON transaction_record
  WHEN OLD.assembly_halt IS NOT NULL -> RAISE

-- the registration window (design §9.2's crash window, structurally)
trg_registration_window: BEFORE UPDATE OF registration_digest ON transaction_record
  WHEN NEW.registration_digest IS NOT NULL AND (
       OLD.state != '{prepared}'
       OR EXISTS (SELECT 1 FROM <effect table> e
                  WHERE e.<txid col> = OLD.txid AND e.journal_state != '{pending}')
  ) -> RAISE

-- departures and starts
trg_departure_needs_registration: BEFORE UPDATE OF state ON transaction_record
  WHEN OLD.state = '{prepared}' AND NEW.state IN ('{applying}', '{rolling_back}')
       AND OLD.registration_digest IS NULL -> RAISE
trg_journal_start_gate: BEFORE UPDATE OF journal_state ON <effect table>
  WHEN NEW.journal_state = '{started}' AND OLD.journal_state = '{pending}' AND EXISTS (
       SELECT 1 FROM transaction_record t WHERE t.txid = NEW.<txid col>
       AND (t.state != '{applying}' OR t.registration_digest IS NULL)
  ) -> RAISE

-- settlement gate
trg_settlement_gate: BEFORE UPDATE OF settlement_digest ON transaction_record
  WHEN NEW.settlement_digest IS NOT NULL AND (
       OLD.registration_digest IS NULL
       OR OLD.state NOT IN ('{committed}', '{rolled_back}')
  ) -> RAISE

-- active: never updated; delete-gated; insert-only publication
trg_active_no_update: BEFORE UPDATE ON active -> RAISE (unconditional)
trg_active_delete_gate: BEFORE DELETE ON active WHEN EXISTS (
       SELECT 1 FROM transaction_record t WHERE t.txid = OLD.txid AND (
            t.state NOT IN ('{committed}', '{rolled_back}')
            OR t.registration_digest IS NULL OR t.settlement_digest IS NULL
            OR t.assembly_halt IS NOT NULL
       )
  ) -> RAISE

-- terminal-record deletion
trg_record_delete_gate: BEFORE DELETE ON transaction_record
  WHEN OLD.state NOT IN ('{committed}', '{rolled_back}')
       OR OLD.registration_digest IS NULL OR OLD.settlement_digest IS NULL
       OR OLD.assembly_halt IS NOT NULL
       OR EXISTS (SELECT 1 FROM active WHERE active.txid = OLD.txid) -> RAISE

-- the assembly-halt freeze
trg_assembly_halt_freezes_record: BEFORE UPDATE ON transaction_record
  WHEN OLD.assembly_halt IS NOT NULL -> RAISE
trg_assembly_halt_freezes_journal: BEFORE UPDATE OF journal_state ON <effect table>
  WHEN EXISTS (SELECT 1 FROM transaction_record t
               WHERE t.txid = NEW.<txid col> AND t.assembly_halt IS NOT NULL) -> RAISE
```

`<effect table>`/`<txid col>` are copied from `store/schema.py`'s real DDL, not from this plan.
(`trg_assembly_halt_freezes_record` subsumes the halt column's own write-once for the
value→value path; keep both triggers anyway — the write-once documents the column rule and
covers it even if the freeze trigger is ever narrowed.)

- [ ] **Step 7.1:** Failing tests, one per trigger, proving **both sides** — the legal sequence
  commits; the illegal one raises through the store's standard trigger-failure surface (copy the
  exception-asserting convention from the nearest existing constraint test in the store suites).
  Include: clearing a non-null registration digest (UPDATE to NULL) is refused; clearing a
  non-null settlement digest is refused; `UPDATE active SET txid=...` is refused even when both
  records are terminal-and-bound; insert-over-existing `active` fails on the primary key; delete
  under a non-terminal record is refused.
- [ ] **Step 7.2:** Failing tests for the writer methods and `StoredRecord` round-trip
  (including `assembly_halt` through Task 5's codec), and an architecture-style assertion that
  `UPSERT_ACTIVE` no longer exists in `connection.py`.
- [ ] **Step 7.3:** Failing test for `encode_approval_evidence` — canonical, stable across two
  approvals of one unchanged tree, different when a directory identity changes (rename a real
  directory between approvals in `tmp_path`).
- [ ] **Step 7.4:** Implement schema v2 DDL + triggers (enum-derived f-strings), writer methods,
  decode, `encode_approval_evidence`.
- [ ] **Step 7.5:** Thread evidence through `prepare_transaction` (`insert_record(...,
  approval_evidence=encode_approval_evidence(approved))`). Check
  `transitions._require_projection_matches`: it compares spec/state/journals/terminal
  payloads/`active` — confirm the new columns are outside its projection and add a comment there
  saying A7b decides whether to widen it; do not widen it here.
- [ ] **Step 7.6:** Full suite green — store fixtures gain evidence via their central
  record-insertion helper.
- [ ] **Step 7.7:** `git commit -m "feat(store): schema v2 with enum-derived registration, settlement, evidence, and freeze triggers"`

---

## Task 8: The chain package

**Files:**
- Create: `python/src/atoms/chain/__init__.py`, `errors.py`, `model.py`, `read.py`, `append.py`
- Test: `python/tests/test_chain_model.py`, `python/tests/test_chain_append.py` (create)

**Interfaces:**
- Consumes: `AuditedBackend` (Task 3 — every signature below takes it, not raw `Backend`),
  `CHAIN_LEAF` (Task 1), the `core.fingerprint` state classes. Never imports `atoms.coordinator`
  or `atoms.store` (architecture-tested).
- Produces:

```python
# errors.py
class ChainStateInvalid(AtomsError): ...

# model.py
class ChainOutcome(enum.Enum):
    COMMITTED = "committed"
    ROLLED_BACK = "rolled-back"

@dataclasses.dataclass(frozen=True, slots=True)
class GenesisEntry:
    payload: bytes
    baseline: tuple[tuple[str, PathStateJSON], ...]
@dataclasses.dataclass(frozen=True, slots=True)
class RegisteredEntry:
    txid: str; intent_digest: str; consumer_tag: str
    initial: tuple[tuple[str, PathStateJSON], ...]; final: tuple[tuple[str, PathStateJSON], ...]
    fulfills: str | None
@dataclasses.dataclass(frozen=True, slots=True)
class SettledEntry:
    txid: str; registration: str; outcome: ChainOutcome
@dataclasses.dataclass(frozen=True, slots=True)
class IntentEntry:
    payload: bytes
Entry = GenesisEntry | RegisteredEntry | SettledEntry | IntentEntry

def state_to_json(state) -> PathStateJSON: ...      # all four fingerprint classes; absence explicit
def state_from_json(data: PathStateJSON): ...
def encode_entry(previous: str | None, entry: Entry) -> bytes: ...
def decode_entry(data: bytes) -> tuple[str | None, Entry]: ...   # ChainStateInvalid on any defect
def entry_digest(data: bytes) -> str: ...

# read.py
class SurvivorDisposition(enum.Enum):
    FINISH = "finish"
    REMOVE = "remove"

@dataclasses.dataclass(frozen=True, slots=True)
class SurvivorAction:
    name: str
    disposition: SurvivorDisposition
    envelope: bytes | None       # the planned envelope a FINISH completes; None for REMOVE

@dataclasses.dataclass(frozen=True, slots=True)
class ValidatedChain:
    entries: tuple[tuple[str, Entry], ...]   # genesis-first; () for the legitimate empty chain
    tip: str | None                          # None iff entries == () (pre-genesis, register_root only)
    survivors: tuple[SurvivorAction, ...]

def validate_chain(backend: AuditedBackend, chain_fd: int,
                   planned: tuple[bytes, ...] = ()) -> ValidatedChain: ...
    # read-only. Walks every digest-named file: digest-name == sha256(bytes), decode, linkage from
    # genesis, exactly one tip, no sibling/orphan — else ChainStateInvalid. Classifies every
    # staging-named survivor: byte-identical to a `planned` envelope -> FINISH (carrying it);
    # everything else -> REMOVE. An empty directory validates as ((), None, ()).

# append.py
STAGING_LEAF = ".#~stage"
def apply_survivors(backend: AuditedBackend, chain_fd: int,
                    validated: ValidatedChain) -> None: ...
    # FINISH: transfer_noclobber onto the envelope's digest name (EEXIST -> byte-proof, below),
    # flush_directory. REMOVE: unlink_child + flush_directory. Idempotent; must run before any
    # append so the fixed STAGING_LEAF is free — this is how a crashed append converges instead
    # of hitting EEXIST on its own debris (review finding 5).
def append_entry(backend: AuditedBackend, chain_fd: int, validated: ValidatedChain,
                 entry: Entry) -> str: ...
    # Requires survivors == () (apply_survivors ran): create_exclusive(STAGING_LEAF) -> write
    # envelope -> flush_file -> close_fd -> transfer_noclobber onto the digest name ->
    # flush_directory. EEXIST at the transfer: open the destination read-only, prove byte-equality
    # with the envelope, then unlink the staging survivor and flush — idempotent completion;
    # byte-inequality is ChainStateInvalid. Returns the digest.
def bootstrap_chain(backend: AuditedBackend, project_root_fd: int) -> int: ...
    # mkdir_child(CHAIN_LEAF) or open existing; flush_directory(project_root_fd); returns the
    # chain fd, registered CHAIN_BOOKKEEPING via the facade's open path.
```

- [ ] **Step 8.1:** Failing model tests — round-trip each entry class exactly (including
  `ChainOutcome` and both payload byte-embeddings); `decode_entry` refuses truncated bytes,
  unknown class, unsorted baseline, invalid base64, unknown outcome value; `state_to_json` covers
  all four state classes and absence explicitly.
- [ ] **Step 8.2:** Failing read tests over `tmp_path` — three-entry chain validates with the
  right tip; empty directory validates as `((), None, ())`; delete-middle / sibling-successor /
  orphan-file each `ChainStateInvalid`; survivor classification: byte-identical-to-planned →
  FINISH with the envelope, already-durable duplicate → REMOVE, partial write → REMOVE,
  decodable-but-underived → REMOVE.
- [ ] **Step 8.3:** Failing append tests — `apply_survivors` then `append_entry` converges after a
  cut at **each** barrier (inject by wrapping the facade to raise after N calls, then rerun the
  full validate → apply → append sequence): exactly one durable entry, staging gone; EEXIST
  destination byte-proof accepts our bytes and unlinks staging; a foreign digest-named file →
  `ChainStateInvalid`; `bootstrap_chain` idempotent, flushes the root, cut between mkdir and flush
  converges on rerun.
- [ ] **Step 8.4:** Implement `model.py` → green (8.1); implement `read.py` → green (8.2);
  implement `append.py` + `errors.py` → green (8.3).
- [ ] **Step 8.5:** Extend `test_fs_architecture.py`: no module under `atoms/chain/` imports
  `atoms.coordinator` or `atoms.store`. Full suite green.
- [ ] **Step 8.6:** `git commit -m "feat(chain): the tamper-evident chain mechanism"`

---

## Task 9: `register_root`, `append_intent`, the preflight, and the required capability

**Files:**
- Create: `python/src/atoms/coordinator/commands.py`
- Modify: `python/src/atoms/core/capabilities.py`, `python/src/atoms/coordinator/root.py`
- Test: `python/tests/test_coordinator_commands.py` (create); extend `test_fs_architecture.py`;
  extend the capability-adjudication suite (find the required ⊆ supplied refusal tests:
  `grep -rln "ALWAYS_REQUIRED" python/tests/`).

**Interfaces:**

```python
# core/capabilities.py — NOCLOBBER_TRANSFER joins ALWAYS_REQUIRED (design §5.1: once a root is
# registered every transaction appends to the chain, and chain publication is no-clobber).
ALWAYS_REQUIRED = frozenset({ANCHORED_TRAVERSAL, DURABLE_PUBLISH, ADVISORY_PROJECT_LOCK,
                             NOCLOBBER_TRANSFER})

# coordinator/commands.py — parameter row mirrors _recovery_lease's exactly
def register_root(backend, project_root, metadata_root, storage,
                  genesis_payload: bytes, registered_surface: tuple[str, ...]) -> str: ...
def append_intent(backend, project_root, metadata_root, storage, payload: bytes) -> str: ...
def require_registered_root(lease: Lease) -> int: ...
    # opens .#~chain/ under the project root; PreconditionRefused when absent with no live
    # record; ChainStateInvalid when absent with a live record; validates and returns the chain fd.
```

Both commands, under the internally acquired lease, first prove `NOCLOBBER_TRANSFER` is supplied
by the bound volume — reuse the exact adjudication the approval path uses over
`binding.evidence`; refusal is `CapabilityUnavailable` **before** any chain write. `register_root`:
`bootstrap_chain` → `validate_chain` → `apply_survivors` → if a genesis exists, prove the retry
(payload bytes byte-equal **and** supplied surface equals the baseline's path set → return the
digest; else `PreconditionRefused`); else capture the baseline (component-walk each path from
`project_root_fd` via `open_child_directory`, observe the leaf with the `core.fingerprint`
vocabulary; determinate `ENOENT` → `AbsentState`; any other errno propagates) and append the
genesis. `append_intent`: `require_registered_root` → `apply_survivors` →
`append_entry(IntentEntry(payload))`, durable before the digest returns.

- [ ] **Step 9.1:** Failing capability tests — `ALWAYS_REQUIRED` contains `NOCLOBBER_TRANSFER`;
  the existing adjudication suite's derivation cases update (an effectless spec now requires
  four); a command against a binding lacking it raises `CapabilityUnavailable` with no chain
  directory created.
- [ ] **Step 9.2:** Failing command tests (fixture pattern from `test_coordinator_lease.py`,
  test allowlist injection as there): fresh-root registration (chain dir + genesis + baseline
  states + tip); byte-identical retry returns the same digest, appends nothing; differing payload
  or path set → `PreconditionRefused`; `append_intent` pre-registration → `PreconditionRefused`
  and **no workspace/record/blob exists afterwards**; post-registration intent embeds bytes
  unchanged, links to genesis, survives a fresh process (subprocess pattern from
  `test_coordinator_process.py`); two intents chain linearly; a cut between staging and transfer
  leaves debris the next command's validate → apply classifies and clears, and the dead caller
  never received a digest.
- [ ] **Step 9.3:** Implement; register the chain fd provenance through the facade's open path.
- [ ] **Step 9.4:** Architecture extension: every public function in `commands.py` enters
  `_recovery_lease` and none annotates `ProjectApprovedSpec`. Full suite green.
- [ ] **Step 9.5:** `git commit -m "feat(coordinator): register_root and append_intent under the internal lease"`

---

## Task 10: A6's six carried gaps

**Files:**
- Modify: `python/src/atoms/coordinator/descriptors.py`, `python/src/atoms/coordinator/capture.py`,
  `python/src/atoms/fs/observe.py`
- Test: extend the suites that already cover each module.

One red-green cycle per gap (design §13 items 1–6; each lands with the test that was missing):

- [ ] **Gap 1** — `DescriptorTable.stops` becomes a property guarded like `fd_for`
  (`ProtocolError` after `close()`). Test: access after close raises.
- [ ] **Gap 2** — both `close()` methods route through the facade's `close_fd` per descriptor:
  unregister-then-close exactly once each, never retry an errored close, first failure re-raised
  after all attempts. Test: monkeypatch to fail the second of three closes; all three attempted
  once; error surfaces; registry empty.
- [ ] **Gap 3** — `fd_for` raises `ProtocolError` for an unknown node; delete `capture.py`'s
  translation wrapper. Test: unknown node → `ProtocolError` directly.
- [ ] **Gap 4** — `_verify_stops`' final `else`: create the blocking entry between admission and
  the walk; assert the declared-state refusal fires.
- [ ] **Gap 5** — the modeled-children derivation lives in `descriptors.py` (capture imports it
  already; the reverse is circular): delete `capture._modeled_under`, repoint capture downward.
  Test: `capture` has no `_modeled_under` attribute; the descriptor-table derivation is the one
  source.
- [ ] **Gap 6** — wrap the streaming loops (`observe.py:291`, `capture.py:340`) and
  `build_relation` in the same `translated_lookup` context the lookups use. Test: a closed-fd
  stream raises `ProtocolError`, not `OSError`.
- [ ] **Commit per gap** — `git commit -m "fix: close A6 gap N — <short name>"`

---

## Task 11: Roadmap split and status synchronization — runs last

**Files:**
- Modify: `python/tests/test_docs_status.py`, `AGENTS.md`, `README.md`,
  `docs/plans/2026-07-23-recoverable-fs-effect-engine-design.md` (§14),
  `docs/plans/2026-08-13-a7-effect-recovery-execution-design.md` (status line),
  `docs/deferred-obligation-ledger.md`

This task is **unambiguously last**: it declares A7a implemented, which is true only after Tasks
1–10 are merged and green.

- [ ] **Step 11.1:** `test_docs_status.py`: `STAGES` replaces `"A7"` with `"A7a", "A7b"`;
  `FIRST_UNIMPLEMENTED = "A7b"`.
- [ ] **Step 11.2:** Run the docs suite and fix exactly what the guard names, with these target
  spellings: authority status field — "A1–A7a are implemented (…; A7a adds the audited facade,
  spec/schema v2, the chain, and the root/intent commands); A7b–A9 (effect/recovery execution,
  synthetic exerciser, macOS backend) remain."; AGENTS.md — A7a joins the implemented bullets
  with its design link, and the A3 bullet's claim becomes "A7b–A9 remain unimplemented";
  README.md — same boundary move; the A7 design doc's status becomes "Design accepted 2026-08-13
  after three review rounds; **A7a implemented, A7b–A9 remain unimplemented.**" The top-level
  sub-plan count stays **nine** — the A5a/A5b precedent counts the family once (revert the
  "ten"/"nine sub-plans" phrasing only if an earlier task changed it; it must read nine).
- [ ] **Step 11.3:** Authority §14: after the A9 sentence, record the decomposition — "A7 is
  delivered as **A7a** (the substrate: audited facade, spec/schema v2, `AssemblyHalt`, the chain,
  the root and intent commands) and **A7b** (the executor: forward spine, five effects, plan
  executor, trap removal), split 2026-08-13 at plan time on the design's §1 layering."
- [ ] **Step 11.4:** Ledger annotations (no entry moves, no discharge — every A7 half stays open
  for A7b): #24, #25, #28 gain "A7a landed the mechanism (chain, schema gates); the executor path
  and verification land with A7b"; #26, #27 gain "A7a landed genesis/intent and their command
  tests; the `run_transaction` refusal and `fulfills` carriage land with A7b"; #29 gains "A7a
  landed the reserved leaf, facade class, and A2-refusal continuity". The A7 halves of #1, #3,
  #8, #12, #13, #14, #17, #19 are A7b's — annotate nothing there.
- [ ] **Step 11.5:** Full suite green. Commit —
  `git commit -m "docs(a7a): land the A7a/A7b roadmap split and move the status boundary"`

---

## Self-Review

Checked against the design and the plan-review findings, 2026-08-13:

1. **Spec coverage.** Design §5.1 (ten primitives — the mode-repair seam included) → Task 2;
   §5.2 (facade classifies, provenance lifecycle incl. close, migration, reachability, SQLite
   exclusion) → Tasks 3–4; §9.3's value shape with the closed fact vocabulary → Task 5; §10.1 →
   Task 1; §10.2 (bootstrap, append, survivors + their application, EEXIST proof) → Task 8;
   §10.3–§10.4 (genesis, retry proof, intent opacity, preflight, the always-required change) →
   Task 9; §11 (columns, evidence, enum-derived triggers, active insert-only, spec v2) →
   Tasks 6–7; §13 items 1–6 → Task 10. A7b owns: forward spine, effect modules, plan executor,
   commit path, reconciliation, trap removal, `run_transaction`, declared-paths population, every
   ledger-half discharge.
2. **Review findings closed.** (1) enum-derived literals + unconditional write-once triggers —
   Task 7; (2) policy-derived classification + two-target records — Task 3; (3) complete
   inventory incl. bootstrap/connection/platform, flag-based `os.open` sweep, `repair_entry_mode`
   seam — Tasks 2, 4; (4) `close_fd` + `os.close` sweep — Tasks 3, 4; (5) `apply_survivors`,
   `tip: str | None`, `AuditedBackend` signatures — Task 8; (6) `ALWAYS_REQUIRED` +
   command-level proof — Task 9; (7) `ChainOutcome`, `SurvivorDisposition`, `_FACT_KEYS` —
   Tasks 5, 8; (8) shared closed role vocabulary + identifier validation, `test_scratch.py`
   update — Task 1; (9) Task 11 last, `FIRST_UNIMPLEMENTED = "A7b"`, nine sub-plans, status
   prose — Task 11.
3. **Placeholder scan.** The remaining look-ups are copy-instructions with exact sources
   (`<effect table>` from `store/schema.py`; the minimal-spec constant from
   `test_compiler_structure.py`; the adjudication site via the `ALWAYS_REQUIRED` grep) — none is
   a design blank.
4. **Type consistency.** `AuditedBackend.register/rebind/unregister/provenance_of/close_fd/
   set_declared_paths` (Task 3) are what Tasks 4, 8, 9, 10 call; `CHAIN_LEAF`/`SCRATCH_ROLES`/
   `is_scratch_leaf` (Task 1) are what Tasks 3, 8 consume; `encode_assembly_halt`/`decode_assembly_halt`
   (Task 5) are what Task 7 uses; `validate_chain`/`apply_survivors`/`append_entry`/
   `bootstrap_chain`/`state_to_json` (Task 8) are what Task 9 calls; `encode_approval_evidence`
   (Task 7) is Task 5's `expected` string and A7b's comparison input.
