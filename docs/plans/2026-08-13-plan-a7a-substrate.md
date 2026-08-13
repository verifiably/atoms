# A7a Substrate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land every A7 component that does not execute an effect: the extended `Backend` and the
audited facade, the engine-reserved prefix split, `TransactionSpec` v2, store schema v2 with its
structural triggers, the `AssemblyHalt` value, the chain package, the `register_root` and
`append_intent` commands, and A6's six carried gaps — leaving the `_resolve` trap in place for A7b.

**Architecture:** A7 splits at the substrate/executor seam because every A7b component consumes A7a
interfaces and nothing here consumes A7b. The facade (`fs/audit.py`) becomes the only mutation
surface production code can reach; the chain (`atoms/chain/`) is mechanism that never imports the
coordinator; the coordinator gains its first two public commands, both acquiring the lease
internally. A7b — the forward spine, five effect modules, plan executor, commit path, and trap
removal — gets its own plan, whose measured facts must be probed against the tree this plan
produces.

**Tech Stack:** Python 3.11+, stdlib only (`os`, `stat`, `hashlib`, `errno`, `enum`, `json`,
`sqlite3`, `dataclasses`, `contextlib`, `unicodedata`), `pytest`, `ruff`, `pyright`. Builds on A1–A6
as probed below.

**Spec:** [`2026-08-13-a7-effect-recovery-execution-design.md`](2026-08-13-a7-effect-recovery-execution-design.md).
**Authority:** [`2026-07-23-recoverable-fs-effect-engine-design.md`](2026-07-23-recoverable-fs-effect-engine-design.md).
Where this plan and either document disagree, the design wins over this plan and the authority wins
over both.

## Measured facts this plan is built on

Probed against the live tree on 2026-08-13 (worktree `.worktrees/a7-design`, `4a9b636`-series). **If
any turns out false during implementation, stop and report — do not adapt around it silently.**

| Fact | Where measured |
| --- | --- |
| `is_scratch_leaf(".#~chain")` is `True` today — the check is prefix-only. `aliases_scratch_sigil(".#~chain")` is `True`. `scratch_leaf(txid, effect_id, role)` returns `f".#~{txid}.{effect_id}.{role}"`. | probe 2026-08-13; `core/scratch.py:14-53` |
| The only `src/` caller of the sigil functions outside `core/scratch.py` is `core/paths.py:12,40` — A2's per-component `aliases_scratch_sigil` refusal. `is_scratch_leaf` has **no** `src/` caller outside its module. | grep 2026-08-13 |
| `Backend` has exactly 11 methods: `open_root`, `open_child_directory`, `exchange`, `transfer_noclobber`, `link_anchor`, `flush_file`, `flush_directory`, `open_regular_nofollow`, `symlink_fingerprint`, `lock_exclusive`, `try_lock_exclusive`. No unlink/rmdir/mkdir/create/write/fchmod/setxattr/symlink-create. | probe 2026-08-13; `fs/backend.py:67-102` |
| `TransactionSpec` fields: `schema_version`, `consumer_tag`, `intent_digest`, `initial_surface`, `final_surface`, `effects`, `dependencies`. `intent_digest` is the §5.1 frozen-intent digest — **distinct from the chain's `fulfills`**. | probe 2026-08-13 |
| `canonical_json(spec: TransactionSpec) -> str` and `from_canonical_json` live in `core/canonical.py` (`:142`). | grep 2026-08-13 |
| `SCHEMA_VERSION = 1` in `store/schema.py`, pinned via `PRAGMA user_version`. `active` is `CREATE TABLE active (singleton INTEGER PRIMARY KEY CHECK (singleton = 0), txid TEXT NOT NULL REFERENCES transaction_record(txid)) STRICT;`. | probe 2026-08-13 |
| `Store.set_active` **upserts** (`UPSERT_ACTIVE`) — an `UPDATE` path around any delete-side trigger exists today, exactly the third-review finding. | probe 2026-08-13; `store/connection.py` |
| Engine exceptions: `ProtocolError`, `PreconditionRefused`, `CapabilityUnavailable`, `TransactionHalted` in `core/errors.py` (base `AtomsError`); `MetadataStoreInvalid` in `store/errors.py`. | grep 2026-08-13 |
| Raw mutation sites to migrate: `fs/lock.py:107` (`os.mkdir`), `:214` (`os.setxattr`), `:217` (`os.open` lock create-or-open); `store/workspace.py:215,217` (`os.mkdir`), `:137` (`os.open`); `store/blobs.py:95` (`os.open`) plus its unlink/rmdir reclamation; `coordinator/capture.py:313` (`os.open` staging), `:340` (`os.write`); `fs/observe.py:291` (`os.write` sink streaming); `fs/probe.py:82-309` (open/write/mkdir/symlink probe writes). | grep 2026-08-13 |
| `_TRANSACTION_STAGE_ENTRY_POINTS` is `{"atoms/coordinator/prepare.py": ("open_workspace", "prepare_transaction"), "atoms/coordinator/capture.py": ("capture_initial_surface",), "atoms/coordinator/transitions.py": ("persist_plan_prefix",)}`; each must call `_require_admitted` first, and any new public coordinator function annotating `ProjectApprovedSpec` fails the suite unless registered. | `tests/test_fs_architecture.py:991-1080` |
| The A5b trap is `_resolve(store)` at `coordinator/lease.py:46-59`: `NotImplementedError` on any non-null `active`. Trap invariants pinned by `tests/test_coordinator_lease.py:120,135,154,174,325`. **A7a leaves all of this untouched.** | agent survey 2026-08-13 |
| `Lease` is a frozen slots dataclass holding `_binding`/`_store`; `ProjectBinding` exposes borrowed `backend`, `project_root_fd`, `metadata_root_fd`, and re-checks lock liveness per access (`fs/binding.py:80-137`). | agent survey 2026-08-13 |
| `prepare_transaction(lease, approved, workspace, manifest)` performs promote + `insert_record(txid, spec)` + `set_active(txid)` in **one** store transaction (`coordinator/prepare.py:36-39`). | agent survey 2026-08-13 |
| `Store.transaction()` refuses nesting (`_require_no_transaction`, "one barrier per lease step"); writer methods on `_StoreTransaction`: `promote_staging`, `insert_record`, `set_transaction_state`, `set_commit_decision`, `set_rollback_result`, `set_halt_diagnostic`, `set_journal_state`, `set_active`. | agent survey 2026-08-13; `store/connection.py:565-656` |
| `test_docs_status.py` holds `STAGES = (..., "A6", "A7", "A8", "A9")`, `FIRST_UNIMPLEMENTED = "A7"`; `_stages_of` expands a base label to every stage sharing its prefix, so `"A7"` expands to `A7a, A7b` once those are the tuple entries and existing `A7–A9` claims keep parsing. | read 2026-08-13 |
| `DirectoryConstraints` is `lookup_proof` + `name_max`; mount membership is `read_mount_id(fd)` vs `binding.evidence.mount_id` (`fs/lookup.py:45-47`, `fs/resolve.py:448-453`). `ProjectApprovedSpec` carries `compiled, binding, txid, topology, directories, paths, scratch, work_base`. | agent survey 2026-08-13 |
| `Observation.observe(parent_fd, leaf, *, sink_fd=None, modeled=None) -> ObservedEntry`; state classes `FileState(content_hash, mode, byte_len)`, `DirectoryState(mode)`, `SymlinkState(target, mode)`, `AbsentState()` in `core/fingerprint.py`. | agent survey 2026-08-13 |

## Global Constraints

- Stdlib only; no new dependencies. Python 3.11+.
- Fail early, no silent fallbacks; every refusal is a typed exception from `core/errors.py`,
  `store/errors.py`, or the new `chain/errors.py`.
- **No capability-enum or probe-semantics change** (design §5.1). The probe implementation moves onto
  the facade; the probed eight capabilities and their meaning do not move.
- **No effect execution and no project mutation outside `.#~chain/`** in this half. The A5b trap, the
  A5b trap tests, and `CERTIFIED_ALLOWLIST = ()` stay exactly as they are.
- Every new public coordinator function acquires the lease internally and never exposes `Lease`
  (design §4); none accepts `ProjectApprovedSpec` (they are commands, not transaction-stage entry
  points).
- Conventional commits, no AI-attribution trailers.
- After every task: `uv run pytest`, `uv run ruff check`, `uv run pyright` from `python/`, all green.

## File Structure

```
python/src/atoms/
  core/
    scratch.py        # modify: engine-reserved prefix split (Task 1)
    spec.py           # modify: TransactionSpec v2 (Task 6)
    canonical.py      # modify: encode/decode the two new members (Task 6)
    compiler.py       # modify: validate the new members (Task 6)
    assembly.py       # create: AssemblyHalt + findings + canonical codec (Task 5)
  fs/
    backend.py        # modify: nine new protocol methods (Task 2)
    linux.py          # modify: their Linux implementations (Task 2)
    audit.py          # create: AuditedBackend facade + provenance registry (Task 3)
    lock.py, probe.py, observe.py   # modify: migrate onto the facade (Task 4)
  chain/
    __init__.py       # create: public names (Task 8)
    errors.py         # create: ChainStateInvalid (Task 8)
    model.py          # create: entry classes + canonical envelope + digests (Task 8)
    read.py           # create: walk, tip discovery, linearity validation, survivor classification (Task 8)
    append.py         # create: durable append + EEXIST proof + bootstrap (Task 8)
  store/
    schema.py         # modify: schema v2 DDL + triggers (Task 7)
    connection.py     # modify: new columns, writer methods, active insert/delete (Task 7)
    records.py        # modify: StoredRecord v2 fields, assembly-halt codec use (Task 7)
    workspace.py, blobs.py          # modify: migrate onto the facade (Task 4)
  coordinator/
    capture.py        # modify: migrate staging writes onto the facade (Task 4)
    descriptors.py    # modify: A6 gaps 1, 3, 5 (Task 10)
    commands.py       # create: register_root, append_intent, require_registered_root (Task 9)
python/tests/
  test_core_scratch.py, test_fs_audit.py, test_core_assembly.py, test_chain_model.py,
  test_chain_append.py, test_store_schema_v2.py, test_coordinator_commands.py  # create
  test_fs_architecture.py, test_docs_status.py, (existing suites)              # modify
```

---

## Task 1: The engine-reserved prefix split

**Files:**
- Modify: `python/src/atoms/core/scratch.py`
- Test: `python/tests/test_core_scratch.py` (create)

**Interfaces:**
- Consumes: existing `SCRATCH_SIGIL`, `scratch_leaf`, `aliases_scratch_sigil`, `require_valid_identifier`.
- Produces: `CHAIN_LEAF: str = ".#~chain"`; `is_engine_reserved_leaf(leaf: str) -> bool` (the old
  prefix check, verbatim semantics); `is_scratch_leaf(leaf: str) -> bool` **redefined** to the full
  `.#~<txid>.<effect_id>.<role>` grammar with `role ∈ {"staging", "tombstone", "anchor", "work"}`.
  Task 8 consumes `CHAIN_LEAF`; A2's refusal layer (`core/paths.py`) keeps calling
  `aliases_scratch_sigil` unchanged.

- [ ] **Step 1: Write the failing tests**

```python
"""The .#~ prefix is engine-reserved; scratch is its transaction-slot grammar (design §10.1)."""

from atoms.core.scratch import (
    CHAIN_LEAF,
    is_engine_reserved_leaf,
    is_scratch_leaf,
    scratch_leaf,
)


def test_chain_leaf_is_engine_reserved_but_not_scratch():
    assert CHAIN_LEAF == ".#~chain"
    assert is_engine_reserved_leaf(CHAIN_LEAF)
    assert not is_scratch_leaf(CHAIN_LEAF)


def test_every_scratch_leaf_is_both_scratch_and_engine_reserved():
    for role in ("staging", "tombstone", "anchor", "work"):
        leaf = scratch_leaf("ab12" * 8, "e1", role)
        assert is_scratch_leaf(leaf)
        assert is_engine_reserved_leaf(leaf)


def test_scratch_grammar_rejects_shape_violations():
    assert not is_scratch_leaf(".#~notthree")            # no dots
    assert not is_scratch_leaf(".#~a.b")                 # two parts
    assert not is_scratch_leaf(".#~a.b.badrole")         # unknown role
    assert not is_scratch_leaf(f".#~{'ab12' * 8}.e1.staging.extra")  # four parts


def test_a2_still_refuses_a_declared_chain_component():
    """The refusal layer is A2's grammar separation — no new approval refusal (design §10.1)."""
    import pytest
    from atoms.core.compiler import compile_spec
    from atoms.core.errors import SpecValidationError
    from tests.spec_builders import single_create_spec  # the existing test helper for a one-effect spec

    with pytest.raises(SpecValidationError):
        compile_spec(single_create_spec(path=".#~chain/f.txt"))
```

If the existing suites build specs through a different helper than `tests.spec_builders`, use that
helper — the assertion is about `compile_spec` refusing, not about the helper. Check how
`test_core_compiler.py` (or the nearest existing compiler suite) builds a minimal spec and reuse it.

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_core_scratch.py -v`; expected:
  `ImportError` on `CHAIN_LEAF` / `is_engine_reserved_leaf`.

- [ ] **Step 3: Implement**

In `core/scratch.py`: rename the current `is_scratch_leaf` body to `is_engine_reserved_leaf`
(unchanged semantics — it is the prefix check); add `CHAIN_LEAF = ".#~chain"`; reimplement
`is_scratch_leaf` as the full grammar:

```python
CHAIN_LEAF = ".#~chain"

_SCRATCH_ROLES = frozenset({"staging", "tombstone", "anchor", "work"})


def is_engine_reserved_leaf(leaf: str) -> bool:
    """True when the leaf spells the engine-reserved prefix (formerly is_scratch_leaf)."""
    return leaf.startswith(SCRATCH_SIGIL)


def is_scratch_leaf(leaf: str) -> bool:
    """True only for the full transaction-slot grammar .#~<txid>.<effect_id>.<role>."""
    if not leaf.startswith(SCRATCH_SIGIL):
        return False
    parts = leaf[len(SCRATCH_SIGIL) :].split(".")
    return len(parts) == 3 and all(parts[:2]) and parts[2] in _SCRATCH_ROLES
```

Keep `aliases_scratch_sigil` byte-identical: it is A2's refusal layer and the design requires its
behavior unchanged. Update `core/scratch.py`'s module docstring to say the sigil is engine-reserved
and scratch is one use of it.

- [ ] **Step 4: Run the new tests and the full suite** — both green. If any existing test called
  `is_scratch_leaf` expecting prefix semantics, repoint it to `is_engine_reserved_leaf` — the grep
  fact says no `src/` caller exists, so only tests can be affected.

- [ ] **Step 5: Commit** — `git commit -m "feat(core): split the engine-reserved prefix from the scratch grammar"`

---

## Task 2: The extended Backend protocol

**Files:**
- Modify: `python/src/atoms/fs/backend.py`, `python/src/atoms/fs/linux.py`
- Test: extend the existing backend suite (`python/tests/test_fs_backend.py` or the file that
  currently exercises `LinuxBackend` — locate it with `grep -rln "LinuxBackend" python/tests/`).

**Interfaces:**
- Produces (design §5.1) — all descriptor-relative, single-component leaves:

```python
def create_exclusive(self, parent_fd: int, name: str, mode: int) -> int: ...
    # os.open(name, O_CREAT|O_EXCL|O_NOFOLLOW|O_RDWR|O_CLOEXEC, mode, dir_fd=parent_fd)
    # O_RDWR, not O_WRONLY: postconditions are re-read through this same descriptor.
def write(self, fd: int, data: bytes) -> int: ...          # os.write
def set_mode(self, fd: int, mode: int) -> None: ...        # os.fchmod
def mkdir_child(self, parent_fd: int, name: str, mode: int) -> None: ...   # os.mkdir(dir_fd=)
def unlink_child(self, parent_fd: int, name: str) -> None: ...             # os.unlink(dir_fd=)
def rmdir_child(self, parent_fd: int, name: str) -> None: ...              # os.rmdir(dir_fd=)
def symlink_child(self, parent_fd: int, name: str, target: str) -> None: ...  # os.symlink(dir_fd=)
def create_or_open(self, parent_fd: int, name: str, mode: int) -> int: ...
    # os.open(name, O_CREAT|O_NOFOLLOW|O_RDWR|O_CLOEXEC, mode, dir_fd=parent_fd) — the lock file.
def set_marker_xattr(self, fd: int, name: str, value: bytes) -> None: ...  # os.setxattr on the fd
```

- **Not** produced: any new `Capability` member, any probe change, any `UNSUPPORTED_ERRNO` row —
  these primitives are POSIX-universal and carry no per-volume evidence (design §5.1).

- [ ] **Step 1: Write the failing tests** — one behavior each against a `tmp_path` root fd:
  `create_exclusive` creates, returns a descriptor whose `os.fstat` inode equals the leaf's, raises
  `FileExistsError` on the second call, and **supports read-back after write through the same fd**
  (write bytes, `os.lseek(fd, 0, SEEK_SET)`, `os.read` returns them — the `O_RDWR` proof);
  `write` returns the count and advances; `set_mode` is visible in `fstat`; `mkdir_child`/`rmdir_child`
  round-trip; `unlink_child` removes; `symlink_child` creates a link readable by
  `symlink_fingerprint`; `create_or_open` opens an existing file without truncating existing bytes;
  `set_marker_xattr` round-trips through `os.getxattr`.
- [ ] **Step 2: Run to verify failure** — protocol members missing.
- [ ] **Step 3: Implement** — add the nine members to the `Backend` protocol with docstrings naming
  their design role, and the Linux implementations exactly as the comments above; no wrapper logic,
  no errno translation (translation stays at call sites per §9.1 of the authority).
- [ ] **Step 4: Full suite green** — in particular the probe suite must pass **unchanged**: if any
  probe test fails, the implementation touched probe semantics, which this task forbids.
- [ ] **Step 5: Commit** — `git commit -m "feat(fs): extend Backend with the audited mutating primitives"`

---

## Task 3: The audited facade

**Files:**
- Create: `python/src/atoms/fs/audit.py`
- Test: `python/tests/test_fs_audit.py` (create)

**Interfaces:**
- Consumes: `Backend` (Task 2), `ProtocolError` from `core/errors.py`, `is_engine_reserved_leaf`,
  `is_scratch_leaf`, `CHAIN_LEAF` (Task 1).
- Produces (design §5.2):

```python
class TargetClass(enum.Enum):
    DECLARED_EFFECT = "declared-effect"
    ENGINE_SCRATCH = "engine-scratch"
    METADATA = "metadata"
    CHAIN_BOOKKEEPING = "chain-bookkeeping"

@dataclasses.dataclass(frozen=True, slots=True)
class Provenance:
    target_class: TargetClass
    path: str            # the engine-issued logical alias, for diagnostics and audit records

@dataclasses.dataclass(frozen=True, slots=True)
class AuditRecord:
    operation: str       # protocol method name
    target_class: TargetClass
    path: str            # alias of the parent provenance joined with the leaf, or the fd alias

class AuditedBackend:    # implements the full Backend protocol
    def __init__(self, inner: Backend) -> None: ...
    records: tuple[AuditRecord, ...]                     # successful mutations, in order
    def register(self, fd: int, provenance: Provenance) -> None: ...
    def rebind(self, fd: int, provenance: Provenance) -> None: ...
    def unregister(self, fd: int) -> None: ...
    def provenance_of(self, fd: int) -> Provenance: ...  # ProtocolError when unregistered
```

Every **mutating** method (`exchange`, `transfer_noclobber`, `link_anchor`, `create_exclusive`,
`write`, `set_mode`, `mkdir_child`, `unlink_child`, `rmdir_child`, `symlink_child`,
`create_or_open`, `set_marker_xattr`) resolves the provenance of its descriptor argument(s) —
`ProtocolError` if any is unregistered — invokes `inner`, and appends an `AuditRecord` **only after
the syscall returns**. Opens (`open_root`, `open_child_directory`, `open_regular_nofollow`,
`create_exclusive`, `create_or_open`) **auto-register** the returned descriptor with the parent's
provenance class and joined path; `unregister` must be called on every close path **before** the fd
number can be reused. Read-only methods (`flush_*` included — they mutate nothing) pass through but
still require registered descriptors. Task 9's commands and A7b's executor choose each root
descriptor's initial `Provenance`; the leaf `CHAIN_LEAF` under a project-root provenance classifies
as `CHAIN_BOOKKEEPING`.

- [ ] **Step 1: Write the failing tests** — with a real `LinuxBackend` over `tmp_path`:
  - a mutation through an unregistered fd raises `ProtocolError` and appends no record;
  - `register` + `create_exclusive` + `write` appends records in order, each after success (a
    forced-failure `write` on a closed inner fd appends nothing);
  - the returned descriptor of `create_exclusive` is auto-registered (an immediate `set_mode`
    through it succeeds);
  - `rebind` changes `provenance_of` and subsequent records carry the new alias (the §9.5
    `CreateDirectory` rebind);
  - `unregister` then any mutation raises `ProtocolError`;
  - a leaf named `CHAIN_LEAF` under a `METADATA`-class parent still records — classification
    follows the parent's class except for `CHAIN_LEAF` under the **project-root** provenance,
    which records `CHAIN_BOOKKEEPING`.
- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement** exactly the surface above; the registry is a `dict[int, Provenance]`
  owned by the facade instance; no global state.
- [ ] **Step 4: Suite green.**
- [ ] **Step 5: Commit** — `git commit -m "feat(fs): the audited backend facade with descriptor provenance"`

---

## Task 4: Migrate every raw mutation onto the facade, and guard it

**Files:**
- Modify: `python/src/atoms/fs/lock.py`, `python/src/atoms/fs/probe.py`,
  `python/src/atoms/fs/observe.py`, `python/src/atoms/store/workspace.py`,
  `python/src/atoms/store/blobs.py`, `python/src/atoms/coordinator/capture.py`,
  `python/src/atoms/coordinator/root.py`
- Test: `python/tests/test_fs_architecture.py` (extend)

**Interfaces:**
- Consumes: `AuditedBackend` (Task 3). The composition root (`coordinator/root.py`) constructs the
  raw platform backend, **immediately** wraps it — `backend = AuditedBackend(LinuxBackend())` — and
  everything downstream types against `Backend`, so the migration is mostly threading the facade's
  registration calls through the sites in the measured-facts table and replacing their raw `os.*`
  calls with protocol calls.
- Produces: the facade-only property two architecture tests then pin:

- [ ] **Step 1: Write the two failing architecture tests**

```python
_MUTATING_OS = {
    "rename", "replace", "unlink", "mkdir", "rmdir", "symlink", "link",
    "chmod", "fchmod", "setxattr", "fsetxattr", "write", "open",
}
_FACADE_EXEMPT = {
    "atoms/fs/audit.py",        # the facade itself
    "atoms/fs/linux.py",        # the raw backend beneath it
    "atoms/fs/syscalls/",       # the vendored syscall wrappers beneath it
}


def test_no_direct_os_mutation_outside_the_facade():
    """Design §5.2: SQLite's VFS is the sole exclusion, and it never spells os.* in our source."""
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        rel = str(path.relative_to(SRC.parents[1]))
        if any(rel.startswith(prefix) or rel == prefix for prefix in _FACADE_EXEMPT):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "os"
                and node.attr in _MUTATING_OS
            ):
                offenders.append(f"{rel}:{node.lineno} os.{node.attr}")
    assert offenders == []


def test_the_production_backend_is_wrapped_at_the_composition_root():
    """The raw platform backend is unreachable from production commands (design §5.2)."""
    source = (SRC / "coordinator" / "root.py").read_text(encoding="utf-8")
    assert "AuditedBackend(" in source
    for path in sorted(SRC.rglob("*.py")):
        rel = str(path.relative_to(SRC.parents[1]))
        if rel in {"atoms/coordinator/root.py", "atoms/fs/audit.py"}:
            continue
        assert "LinuxBackend(" not in path.read_text(encoding="utf-8"), rel
```

(`SRC` follows the existing convention in `test_fs_architecture.py` — reuse its module-level root
constant.) Note `os.open` with `O_RDONLY` is also swept: read-only opens migrate to
`open_regular_nofollow`/`open_child_directory`/`create_or_open` so descriptor provenance stays
total. Where a site cannot express its open through the protocol (the probe's `O_RDONLY` payload
re-open, `fs/probe.py:95,261`), extend the *call site* to use `open_regular_nofollow` — the probe
semantics (what is created, what is checked) must not change.

- [ ] **Step 2: Run to verify failure** — the offender list must name exactly the measured-facts
  sites; if it names more, the measured facts were stale: stop and report.
- [ ] **Step 3: Migrate site by site**, one commit per file, keeping each file's tests green:
  lock (create-or-open through `create_or_open`, marker through `set_marker_xattr`, bootstrap mkdir
  through `mkdir_child`); workspace/blobs (mkdir/unlink/rmdir/open through the protocol; the
  workspace registers `staging_fd`/`work_fd` as `METADATA`); capture staging opens through
  `create_exclusive` and streaming through `backend.write`; observe's sink streaming through
  `backend.write` (the `Observation` already holds the backend); probe entirely through protocol
  calls under a `METADATA`-class probe-directory provenance.
- [ ] **Step 4: Run the two architecture tests and the full suite** — green; **the probe and
  capture suites in particular must pass unchanged**.
- [ ] **Step 5: Final commit** — `git commit -m "refactor(fs,store,coordinator): route every engine mutation through the audited facade"`

---

## Task 5: The `AssemblyHalt` value

**Files:**
- Create: `python/src/atoms/core/assembly.py`
- Test: `python/tests/test_core_assembly.py` (create)

**Interfaces:**
- Consumes: nothing new — stdlib plus `core/errors.ProtocolError` for constructor misuse.
- Produces (design §9.3) — deliberately **outside** `core/recovery/`; A3's model, reducer, and
  diagnostic encoding are untouched:

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

@dataclasses.dataclass(frozen=True, slots=True)
class AssemblyFinding:
    path: str
    kind: AssemblyFindingKind
    observed: tuple[tuple[str, str], ...]   # sorted (fact, value) pairs for exactly this kind

@dataclasses.dataclass(frozen=True, slots=True)
class AssemblyHalt:
    txid: str
    reason: AssemblyHaltReason
    expected: str                            # the persisted approval evidence, verbatim
    findings: tuple[AssemblyFinding, ...]    # ordered by (path, kind enum order)
    operator_action: AssemblyOperatorAction

def encode_assembly_halt(halt: AssemblyHalt) -> str: ...      # canonical JSON, sorted keys
def decode_assembly_halt(payload: str) -> AssemblyHalt: ...   # exact round-trip or ProtocolError
```

The constructor (a `__post_init__`) enforces the closed rules: findings non-empty, ordered exactly
by `(path, kind enum order)`, and a `NODE_MISSING` or `WRONG_ENTRY_KIND` finding is its path's
**sole** finding.

- [ ] **Step 1: Failing tests** — round-trip through encode/decode is exact; decode of a mutated
  payload (unknown kind, missing member, unsorted findings) raises `ProtocolError`; constructing
  out-of-order findings raises; constructing `NODE_MISSING` plus `IDENTITY_CHANGED` for one path
  raises; constructing an empty findings tuple raises.
- [ ] **Step 2: Verify failure. Step 3: Implement. Step 4: Suite green.**
- [ ] **Step 5: Commit** — `git commit -m "feat(core): the AssemblyHalt value and its canonical codec"`

---

## Task 6: `TransactionSpec` v2

**Files:**
- Modify: `python/src/atoms/core/spec.py`, `python/src/atoms/core/canonical.py`,
  `python/src/atoms/core/compiler.py`
- Test: extend the existing spec/canonical/compiler suites where each behavior lives today
  (locate with `grep -rln "schema_version" python/tests/`).

**Interfaces:**
- Produces (design §11): `TransactionSpec` gains `fulfills: str | None = None` and
  `registered_paths: tuple[str, ...] = ()`; the accepted `schema_version` becomes **2** and only 2 —
  Plan A has no production data, so v1 is refused by `compile_spec` (`SpecValidationError`), and
  `from_canonical_json` of a v1 payload refuses the same way.
- `compile_spec` validation, added as a new phase beside the existing surface checks:
  `registered_paths` must be sorted, duplicate-free, and a subset of the union of both surfaces'
  paths; `fulfills`, when present, must be a 64-hex-char digest string. `canonical_json` /
  `from_canonical_json` carry both members; encoding a spec whose `fulfills` is `None` and whose
  `registered_paths` is empty must produce a *different* byte string from a v1 spec — the version
  member moves, so nothing can confuse the two.

- [ ] **Step 1: Failing tests** — construct a v2 spec with both members; `canonical_json` →
  `from_canonical_json` round-trips exactly; `compile_spec` refuses `schema_version=1`, unsorted or
  duplicated `registered_paths`, a `registered_paths` entry in neither surface, and a non-hex
  `fulfills`; accepts `fulfills=None` and `registered_paths=()`.
- [ ] **Step 2: Verify failure. Step 3: Implement.**
- [ ] **Step 4: Full suite** — every existing test that builds a spec with `schema_version=1` moves
  to 2 via the suite's central builder; if specs are built inline in many places, fix the builder
  first and let the failures name the stragglers.
- [ ] **Step 5: Commit** — `git commit -m "feat(core): TransactionSpec v2 with fulfills and the registered-path subset"`

---

## Task 7: Store schema v2 — columns, triggers, writer methods

**Files:**
- Modify: `python/src/atoms/store/schema.py`, `python/src/atoms/store/connection.py`,
  `python/src/atoms/store/records.py`, `python/src/atoms/fs/approval.py`,
  `python/src/atoms/coordinator/prepare.py`
- Test: `python/tests/test_store_schema_v2.py` (create), plus extending the existing store suites.

**Interfaces:**
- Consumes: `encode_assembly_halt`/`decode_assembly_halt` (Task 5).
- Produces:
  - `SCHEMA_VERSION = 2`. `transaction_record` gains
    `registration_digest TEXT UNIQUE`, `settlement_digest TEXT UNIQUE`,
    `approval_evidence TEXT NOT NULL`, `assembly_halt TEXT` (all `STRICT`-compatible).
  - `encode_approval_evidence(approved: ProjectApprovedSpec) -> str` in `fs/approval.py`: canonical
    JSON, sorted keys, of exactly — per approved directory node: node key, resolved identity
    (`st_dev`, `st_ino`) for existing directories, `lookup_proof`, `name_max`; plus the binding's
    `mount_id`; plus the work-root facts (`work_base` present/absent and its constraints). This is
    the byte string A7b's phase-5 comparison and Task 5's `expected` member carry.
  - `_StoreTransaction` gains `set_registration_digest(txid, digest)`,
    `set_settlement_digest(txid, digest)`, `set_assembly_halt(txid, halt: AssemblyHalt)`;
    `insert_record` gains a required `approval_evidence: str` parameter;
    `set_active` **loses its upsert**: `set_active(txid)` becomes `INSERT`, `set_active(None)`
    stays `DELETE`, and there is no UPDATE statement against `active` anywhere in the store.
  - `StoredRecord` gains `registration_digest: str | None`, `settlement_digest: str | None`,
    `approval_evidence: str`, `assembly_halt: AssemblyHalt | None`.
  - `prepare_transaction` passes `encode_approval_evidence(approved)` into `insert_record` — the
    evidence is durable in the `PREPARED` COMMIT (design §11).
- The triggers, verbatim DDL (design §11's exact predicates; journal states spell as the existing
  schema spells them — confirm the literal strings against `store/schema.py` before writing):

```sql
CREATE TRIGGER trg_registration_window BEFORE UPDATE OF registration_digest ON transaction_record
WHEN NEW.registration_digest IS NOT NULL AND (
    OLD.registration_digest IS NOT NULL
    OR OLD.state != 'PREPARED'
    OR EXISTS (SELECT 1 FROM effect WHERE effect.txid = OLD.txid AND effect.journal_state != 'PENDING')
) BEGIN SELECT RAISE(ABORT, 'registration binding outside the PREPARED/all-PENDING window'); END;

CREATE TRIGGER trg_departure_needs_registration BEFORE UPDATE OF state ON transaction_record
WHEN OLD.state = 'PREPARED' AND NEW.state IN ('APPLYING', 'ROLLING_BACK')
     AND OLD.registration_digest IS NULL
BEGIN SELECT RAISE(ABORT, 'transition away from PREPARED without a registration binding'); END;

CREATE TRIGGER trg_journal_start_gate BEFORE UPDATE OF journal_state ON effect
WHEN NEW.journal_state = 'STARTED' AND OLD.journal_state = 'PENDING' AND EXISTS (
    SELECT 1 FROM transaction_record t WHERE t.txid = NEW.txid
    AND (t.state != 'APPLYING' OR t.registration_digest IS NULL)
) BEGIN SELECT RAISE(ABORT, 'journal start outside APPLYING or without registration'); END;

CREATE TRIGGER trg_settlement_gate BEFORE UPDATE OF settlement_digest ON transaction_record
WHEN NEW.settlement_digest IS NOT NULL AND (
    OLD.settlement_digest IS NOT NULL
    OR OLD.registration_digest IS NULL
    OR OLD.state NOT IN ('COMMITTED', 'ROLLED_BACK')
) BEGIN SELECT RAISE(ABORT, 'settlement binding without registration or terminal state'); END;

CREATE TRIGGER trg_active_no_update BEFORE UPDATE ON active
BEGIN SELECT RAISE(ABORT, 'the active row is never updated; delete then insert'); END;

CREATE TRIGGER trg_active_delete_gate BEFORE DELETE ON active
WHEN EXISTS (
    SELECT 1 FROM transaction_record t WHERE t.txid = OLD.txid AND (
        t.state NOT IN ('COMMITTED', 'ROLLED_BACK')
        OR t.registration_digest IS NULL OR t.settlement_digest IS NULL
        OR t.assembly_halt IS NOT NULL
    )
) BEGIN SELECT RAISE(ABORT, 'active cleared before settlement binding'); END;

CREATE TRIGGER trg_record_delete_gate BEFORE DELETE ON transaction_record
WHEN OLD.state NOT IN ('COMMITTED', 'ROLLED_BACK')
     OR OLD.registration_digest IS NULL OR OLD.settlement_digest IS NULL
     OR OLD.assembly_halt IS NOT NULL
     OR EXISTS (SELECT 1 FROM active WHERE active.txid = OLD.txid)
BEGIN SELECT RAISE(ABORT, 'terminal-record deletion before settlement binding'); END;

CREATE TRIGGER trg_evidence_write_once BEFORE UPDATE OF approval_evidence ON transaction_record
BEGIN SELECT RAISE(ABORT, 'approval evidence is write-once'); END;

CREATE TRIGGER trg_assembly_halt_write_once BEFORE UPDATE OF assembly_halt ON transaction_record
WHEN OLD.assembly_halt IS NOT NULL
BEGIN SELECT RAISE(ABORT, 'assembly halt is write-once'); END;

CREATE TRIGGER trg_assembly_halt_freezes_record BEFORE UPDATE ON transaction_record
WHEN OLD.assembly_halt IS NOT NULL
BEGIN SELECT RAISE(ABORT, 'an assembly-halted record is frozen'); END;

CREATE TRIGGER trg_assembly_halt_freezes_journal BEFORE UPDATE OF journal_state ON effect
WHEN EXISTS (SELECT 1 FROM transaction_record t
             WHERE t.txid = NEW.txid AND t.assembly_halt IS NOT NULL)
BEGIN SELECT RAISE(ABORT, 'an assembly-halted record is frozen'); END;
```

(`trg_assembly_halt_freezes_record` fires before `trg_assembly_halt_write_once` can matter for any
non-halt column; keep both — the write-once trigger documents the halt column's own rule and covers
the `NULL → value → value` path.) Adjust table/column spellings to the real schema — the effect
table name and its txid column must be copied from `store/schema.py`, not from this plan.

- [ ] **Step 1: Failing tests** — one per trigger, each proving **both sides**: the legal sequence
  commits (e.g. bind registration in the window → `APPLYING` → journal start) and the illegal one
  raises `MetadataStoreInvalid`-wrapped-or-`sqlite3.IntegrityError`-surfaced-as-the-store's-standard
  refusal (match how existing store tests assert trigger failures; the store's `translated` wrapper
  decides the exception type — copy the convention from the nearest existing trigger/constraint
  test). The `set_active` insert-only change gets its own test: publish → publish again without
  delete raises; delete under a non-terminal record raises; the upsert statement is gone
  (architecture assertion: `UPSERT_ACTIVE` no longer exists in `connection.py`).
- [ ] **Step 2: Verify failure. Step 3: Implement** — schema DDL, writer methods, `StoredRecord`
  decode (including `decode_assembly_halt`), `encode_approval_evidence`, and the
  `prepare_transaction` threading. The A5b projection comparison in `transitions.py`
  (`_require_projection_matches`) compares spec, state, journals, terminal payloads, and `active` —
  confirm it ignores the new columns or extend its projection deliberately; if extended, the A7b
  plan inherits that decision, so record it in the task's commit message.
- [ ] **Step 4: Full suite green** — every store test that inserts records supplies evidence via the
  suite's central fixture.
- [ ] **Step 5: Commit** — `git commit -m "feat(store): schema v2 with registration, settlement, evidence, and freeze triggers"`

---

## Task 8: The chain package

**Files:**
- Create: `python/src/atoms/chain/__init__.py`, `errors.py`, `model.py`, `read.py`, `append.py`
- Test: `python/tests/test_chain_model.py`, `python/tests/test_chain_append.py` (create)

**Interfaces:**
- Consumes: `AuditedBackend` + `Provenance`/`TargetClass` (Task 3), `CHAIN_LEAF` (Task 1),
  `FileState`/`DirectoryState`/`SymlinkState`/`AbsentState` from `core/fingerprint.py`.
  **Never** imports `atoms.coordinator` or `atoms.store` (architecture-tested).
- Produces (design §10):

```python
# errors.py
class ChainStateInvalid(AtomsError): ...   # same response class as MetadataStoreInvalid

# model.py — entry classes; the envelope is canonical JSON with sorted keys
@dataclasses.dataclass(frozen=True, slots=True)
class GenesisEntry:
    payload: bytes                                   # consumer bytes, embedded unchanged (base64 in the envelope)
    baseline: tuple[tuple[str, PathStateJSON], ...]  # sorted typed path/state fingerprints
@dataclasses.dataclass(frozen=True, slots=True)
class RegisteredEntry:
    txid: str; intent_digest: str; consumer_tag: str
    initial: tuple[tuple[str, PathStateJSON], ...]; final: tuple[tuple[str, PathStateJSON], ...]
    fulfills: str | None
@dataclasses.dataclass(frozen=True, slots=True)
class SettledEntry:
    txid: str; registration: str; outcome: str       # "committed" | "rolled-back"
@dataclasses.dataclass(frozen=True, slots=True)
class IntentEntry:
    payload: bytes                                   # opaque consumer bytes, embedded unchanged
Entry = GenesisEntry | RegisteredEntry | SettledEntry | IntentEntry

def encode_entry(previous: str | None, entry: Entry) -> bytes: ...   # the canonical envelope
def decode_entry(data: bytes) -> tuple[str | None, Entry]: ...       # ChainStateInvalid on any defect
def entry_digest(data: bytes) -> str: ...                            # sha256 hex of the envelope bytes

# read.py
@dataclasses.dataclass(frozen=True, slots=True)
class ValidatedChain:
    entries: tuple[tuple[str, Entry], ...]   # (digest, entry) genesis-first
    tip: str
    survivors: tuple[SurvivorAction, ...]    # closed classification of staging debris (design §10.2)
@dataclasses.dataclass(frozen=True, slots=True)
class SurvivorAction:
    name: str
    action: str                              # "finish" | "remove"
def validate_chain(backend: Backend, chain_fd: int,
                   planned: tuple[bytes, ...] = ()) -> ValidatedChain: ...
    # read-only; walks every entry file, verifies digest-name == sha256(bytes), linkage from
    # genesis, exactly one tip, no sibling/orphan; classifies every staging-name survivor:
    # byte-identical to a `planned` envelope -> finish; anything else -> remove. Any digest-named
    # file that fails its own digest, decodes to no entry, or forks the sequence -> ChainStateInvalid.

# append.py
STAGING_LEAF = ".#~stage"                    # engine-reserved, inside .#~chain/
def append_entry(backend: Backend, chain_fd: int, previous: str | None, entry: Entry) -> str: ...
    # create_exclusive(STAGING_LEAF) -> write envelope -> flush_file -> transfer_noclobber onto the
    # digest name -> flush_directory(chain_fd). On EEXIST at the transfer: re-open the destination,
    # prove byte-equality with the envelope, then unlink the staging survivor and flush — the
    # idempotent completion; byte-inequality is ChainStateInvalid (design §10.2).
def bootstrap_chain(backend: Backend, project_root_fd: int) -> int: ...
    # mkdir_child(CHAIN_LEAF) or open existing; flush_directory(project_root_fd); returns chain fd.
```

`PathStateJSON` is the typed JSON projection of one `core.fingerprint` state — implement it in
`model.py` as a pair of functions (`state_to_json`, `state_from_json`) covering all four classes;
absence encodes explicitly (the design's "one state vocabulary" rule).

- [ ] **Step 1: Failing tests, model** — encode/decode round-trips each entry class exactly;
  `decode_entry` raises `ChainStateInvalid` on truncated bytes, unknown class, unsorted baseline,
  and a payload that is not valid base64; digests are stable across a round-trip.
- [ ] **Step 2: Failing tests, read/append** — over `tmp_path` with a real backend: a three-entry
  chain validates with the right tip; deleting the middle entry, adding a sibling second successor,
  or adding an orphan digest-named file each raise `ChainStateInvalid`; a staging survivor
  byte-identical to a planned envelope classifies `finish` and one that is not classifies `remove`;
  `append_entry` is cut at each of its four barriers (kill the append by raising from a wrapped
  backend after step N) and a re-run converges to exactly one durable entry; the `EEXIST` path
  proves bytes before accepting and raises on a swapped foreign file; `bootstrap_chain` is
  idempotent and flushes the project root.
- [ ] **Step 3: Implement. Step 4: Suite green, plus an architecture assertion in
  `test_fs_architecture.py` that no module under `atoms/chain/` imports `atoms.coordinator` or
  `atoms.store`.**
- [ ] **Step 5: Commit** — `git commit -m "feat(chain): the tamper-evident chain mechanism"`

---

## Task 9: `register_root`, `append_intent`, and the genesis preflight

**Files:**
- Create: `python/src/atoms/coordinator/commands.py`
- Modify: `python/src/atoms/coordinator/root.py` (facade wrap + root-descriptor provenance)
- Test: `python/tests/test_coordinator_commands.py` (create); extend `test_fs_architecture.py`.

**Interfaces:**
- Consumes: `_recovery_lease` (existing), `bootstrap_chain`/`append_entry`/`validate_chain`
  (Task 8), `GenesisEntry`/`IntentEntry`, `state_to_json`, the facade (Task 3).
- Produces (design §10.3–§10.4):

```python
def register_root(backend: Backend, project_root: str, metadata_root: str,
                  storage: StorageProfile, genesis_payload: bytes,
                  registered_surface: tuple[str, ...]) -> str: ...
def append_intent(backend: Backend, project_root: str, metadata_root: str,
                  storage: StorageProfile, payload: bytes) -> str: ...
def require_registered_root(lease: Lease) -> int: ...
    # opens .#~chain/ under the project root, validates genesis presence; returns the chain fd.
    # PreconditionRefused when absent and no live record; ChainStateInvalid when absent with a
    # live record (design §10.2). A7b's run_transaction calls this immediately after resolution.
```

`register_root`, under the lease: `bootstrap_chain`; if a genesis exists, prove the retry — supplied
payload bytes byte-equal the genesis payload **and** supplied `registered_surface` equals the
baseline's path set — return the existing digest, else `PreconditionRefused`; otherwise capture the
baseline (each path resolved component-by-component from `project_root_fd` via
`open_child_directory`, leaf observed as file/symlink/directory/absent with the `core.fingerprint`
vocabulary — a determinate `ENOENT` is `AbsentState`, any other errno propagates), append the
genesis, return its digest. `append_intent`, under the lease: `require_registered_root`, then
`append_entry(IntentEntry(payload))` against the validated tip, durable before the digest returns.
Signatures mirror `_recovery_lease`'s existing parameter row (match its exact parameter names and
`StorageProfile` import); neither function accepts or exposes `Lease` or `ProjectApprovedSpec`.

- [ ] **Step 1: Failing tests** — with a bound tmp project (reuse the fixture pattern the
  coordinator suites already use for `_recovery_lease`; the `CERTIFIED_ALLOWLIST` is empty, so the
  tests inject the test allowlist exactly as `test_coordinator_lease.py` does):
  - `register_root` on a fresh root creates `.#~chain/`, appends a genesis whose baseline carries
    the supplied paths' typed states, and returns a digest that `validate_chain` confirms as tip;
  - a byte-identical retry returns the same digest and appends nothing; a differing payload or
    path set raises `PreconditionRefused`;
  - `append_intent` before any `register_root` raises `PreconditionRefused` **and the metadata root
    shows no workspace, record, or blob from the attempt**;
  - `append_intent` after registration appends an `IntentEntry` embedding the payload bytes
    unchanged, linked to the genesis, durable (validate in a fresh process via the existing
    subprocess-test pattern in `test_coordinator_process.py`);
  - two sequential `append_intent` calls produce a linear two-intent chain, tip advancing;
  - kill-injection between the intent's staging write and its transfer leaves a survivor that the
    next command's validation classifies `remove`, and the caller that died never received a digest.
- [ ] **Step 2: Verify failure. Step 3: Implement**, wiring `AuditedBackend` registration for the
  project-root and chain descriptors (`Provenance(TargetClass.CHAIN_BOOKKEEPING, ...)` for the
  chain fd, per Task 3's classification rule).
- [ ] **Step 4: Architecture extension** — add to `test_fs_architecture.py`: every public function
  in `coordinator/commands.py` calls `_recovery_lease` (the lease guard for public commands,
  design §13 item 10) and none annotates `ProjectApprovedSpec`; run everything green.
- [ ] **Step 5: Commit** — `git commit -m "feat(coordinator): register_root and append_intent under the internal lease"`

---

## Task 10: A6's six carried gaps

**Files:**
- Modify: `python/src/atoms/coordinator/descriptors.py`, `python/src/atoms/coordinator/capture.py`,
  `python/src/atoms/fs/observe.py`
- Test: extend the suites that already cover each module.

Each gap is one red-green cycle; the fix list is design §13 items 1–6, and each lands with the test
that was missing (design §14):

- [ ] **Gap 1** — `DescriptorTable.stops` raises `ProtocolError` after `close()`: convert the bare
  slot to a property guarded like `fd_for`. Test: access after close raises.
- [ ] **Gap 2** — both `close()` methods unregister ownership (facade `unregister`) before each
  `os.close` attempt, attempt every fd exactly once, never retry an errored close, and raise the
  first failure after all attempts. Test: monkeypatch `os.close` to fail on the second of three
  fds; assert all three were attempted exactly once and the error surfaced.
- [ ] **Gap 3** — `fd_for` raises `ProtocolError` for an unknown node; delete `capture.py`'s
  translation wrapper at its call site. Test: unknown node raises `ProtocolError` directly.
- [ ] **Gap 4** — `_verify_stops`' final `else` gets its test: build a table over an approved spec,
  then create the blocking entry between admission and the walk (the test controls both), assert
  the declared-state-mismatch refusal fires.
- [ ] **Gap 5** — the modeled-children derivation stays in `descriptors.py` (capture already
  imports it; the reverse is circular): delete `capture._modeled_under`, export the shared
  derivation from `descriptors.py` under its existing name, repoint capture. Test: the two former
  derivations agree by construction — assert `capture` has no `_modeled_under` attribute and the
  descriptor-table path is the one source (grep-style architecture assertion).
- [ ] **Gap 6** — wrap the streaming loops (`observe.py:291` region, `capture.py:340` region) and
  `build_relation` in the same `translated_lookup` context the lookups use, so `EBADF` maps to
  `ProtocolError` per authority §9.1. Test: a closed-fd stream raises `ProtocolError`, not
  `OSError`.
- [ ] **Commit per gap** — `git commit -m "fix(coordinator): close A6 gap N — <short name>"`

---

## Task 11: Roadmap split, ledger annotations, status synchronization

**Files:**
- Modify: `python/tests/test_docs_status.py`, `AGENTS.md`, `README.md`,
  `docs/plans/2026-07-23-recoverable-fs-effect-engine-design.md` (§14),
  `docs/deferred-obligation-ledger.md`,
  `docs/plans/2026-08-13-a7-effect-recovery-execution-design.md` (status only, if needed — see below)

This is the banking-commit task for the A7a/A7b split itself; it lands **first** in execution order
if the executor prefers docs-green throughout, or last as one commit — either is fine because
nothing in Tasks 1–10 changes a status claim.

- [ ] **Step 1:** `test_docs_status.py`: `STAGES` replaces `"A7"` with `"A7a", "A7b"`;
  `FIRST_UNIMPLEMENTED = "A7a"`. The measured fact says `_stages_of` expands `"A7"` claims to both
  halves, so the design doc's "A7–A9 remain unimplemented" keeps parsing to exactly
  `{A7a, A7b, A8, A9}` — run the suite to confirm no other document needs wording changes; fix any
  the guard names.
- [ ] **Step 2:** Authority §14: after the A9 sentence, record the decomposition — "A7 is delivered
  as **A7a** (the substrate: audited facade, spec/schema v2, `AssemblyHalt`, the chain, the root
  and intent commands) and **A7b** (the executor: forward spine, the five effects, the plan
  executor, trap removal), split 2026-08-13 at plan time on the design's §1 layering." AGENTS.md
  and README.md: "nine sub-plans" → "ten sub-plans"; extend README's A7 expectations line only if
  the guard requires it.
- [ ] **Step 3:** Ledger annotations (no entry moves, no discharge): #24, #25, #28 gain "A7a lands
  the mechanism (chain, schema gates); A7b completes the executor path and the verification";
  #26, #27 gain "A7a lands genesis/intent and their command tests; the `run_transaction` refusal
  and `fulfills` carriage land with A7b"; #29 gains "A7a lands the reserved leaf, facade class, and
  A2-refusal continuity". The A7 halves of #1, #3, #8, #12, #13, #14, #17, #19 are all A7b's —
  annotate nothing there.
- [ ] **Step 4:** Full suite green. Commit —
  `git commit -m "docs(a7a): split A7 into A7a/A7b in the roadmap, guard, and ledger"`

---

## Self-Review

Checked against the design 2026-08-13, after drafting:

1. **Spec coverage.** Design §5.1 (nine primitives — the plan's Task 2 lists nine including
   `create_or_open` and `set_marker_xattr`; `symlink_child` included since materialization requires
   it) → Task 2. §5.2 (facade, provenance lifecycle, migration, reachability, SQLite exclusion) →
   Tasks 3–4. §9.3's value shape → Task 5; its *resolver* behavior (phases, narrow persistence
   path) is A7b's, consuming Task 5's codec and Task 7's column. §10.1 → Task 1; §10.2 (bootstrap,
   append, survivors, EEXIST proof) → Task 8; §10.3–§10.4 (genesis, retry proof, intent opacity,
   preflight) → Task 9 — `registered`/`settled` *construction in the transaction path* is A7b's,
   their entry classes and codecs land here in Task 8. §11 (columns, evidence, triggers, active
   insert-only, spec v2) → Tasks 6–7. §13 items 1–6 → Task 10; items 7–10 → Tasks 1, 4, 9; items
   11–13 were landed with the design's banking commit (authority amendments, guard, ledger
   entries) — Task 11 carries only the *split's* increment. Deliberately absent, owned by A7b:
   the forward spine, effect modules, plan executor, commit path, reconciliation, trap removal,
   `run_transaction`, and every ledger-half discharge.
2. **Placeholder scan.** No TBDs. Two deliberate look-ups remain ("locate the spec-builder
   helper", "copy the journal-state spellings from `store/schema.py`") — each is an instruction to
   copy a measured value at implementation time with the exact place to copy from, not a design
   blank.
3. **Type consistency.** `AuditedBackend.register/rebind/unregister/provenance_of` (Task 3) are
   what Tasks 4, 8, 9 call; `CHAIN_LEAF` (Task 1) is what Tasks 3, 8 reference;
   `encode_assembly_halt`/`decode_assembly_halt` (Task 5) are what Task 7's `set_assembly_halt`/
   `StoredRecord` use; `validate_chain`/`append_entry`/`bootstrap_chain` (Task 8) are what Task 9
   calls; `state_to_json` (Task 8) is what Task 9's baseline uses; `encode_approval_evidence`
   (Task 7) is the string Task 5's `expected` carries. Trigger DDL names (`transaction_record`,
   `effect`, journal-state literals) are flagged for copy-from-schema rather than trusted.
