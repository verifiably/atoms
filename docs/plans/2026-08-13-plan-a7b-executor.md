# Plan A7b — the effect and recovery executor

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the executor half of the accepted A7 design — the five effect modules,
the one plan executor, the widened resolver with chain reconciliation and the assembly
halt, and the `run_transaction` forward spine — on top of the A7a substrate, moving the
roadmap boundary to A8.

**Architecture:** Three new coordinator modules (`effects/`, `recover.py`,
`execute.py`/`commit.py`) layered strictly on A7a's seams: every filesystem mutation
goes through the `AuditedBackend` facade, every chain write through `atoms/chain/`,
every store transition through `_StoreTransaction`, and every recovery mutation through
`classify_recovery` → `persist_plan_prefix` → `authorize_recovery_step`. The A5b
`NotImplementedError` trap is replaced by §9's pinned resolution phases.

**Tech stack:** Python 3.12, uv, pytest, ruff, pyright — unchanged. All commands run
from `python/`.

**Spec:** `docs/plans/2026-08-13-a7-effect-recovery-execution-design.md` (§6–§9,
§12–§15), which argues from
`docs/plans/2026-07-23-recoverable-fs-effect-engine-design.md` (authority §8–§11).
**The design wins over this plan.** A conflict between a step here and a design clause
is a blocker to raise, not a choice to make silently.

## Measured facts (probed 2026-08-13 against `c186670`)

Every fact below was read from the tree this plan builds on, not remembered.

| Fact | Where |
|---|---|
| The trap: `_resolve(store)` raises `NotImplementedError` when `store.read_active() is not None`; called last inside `_recovery_lease` before `Lease` is yielded | `coordinator/lease.py:46-59`, `coordinator/root.py:27-57` |
| `_recovery_lease(backend, project_root, metadata_root, storage)` wraps `AuditedBackend(backend, project_root=..., metadata_root=...)`, then lock → `reclaim_probe_survivors` → `bind_project_volume` → `open_store` → `_reclaim_orphans` → `_resolve` | `coordinator/root.py:27-57` |
| `commands.__all__ == ("append_intent", "register_root")`; `_registered_root(lease)` yields `(chain_fd, ValidatedChain)`, maps ENOENT+active-record to `ChainStateInvalid` and unregistered to `PreconditionRefused` | `coordinator/commands.py:35-73` |
| `validate_chain(backend, chain_fd, planned=()) -> ValidatedChain(entries: tuple[tuple[str, Entry], ...], tip: str \| None, survivors)`; `apply_survivors(backend, chain_fd, validated)` returns a **fresh** proof; `append_entry(backend, chain_fd, validated, entry) -> str` re-proves the directory then stages/transfers; `bootstrap_chain(backend, project_root_fd) -> int` | `chain/read.py:35-160`, `chain/append.py:133-300` |
| Entry shapes: `GenesisEntry(payload, baseline)`, `RegisteredEntry(txid, intent_digest, consumer_tag, initial, final, fulfills)`, `SettledEntry(txid, registration, outcome)`, `IntentEntry(payload)`; `ChainOutcome = {COMMITTED: "committed", ROLLED_BACK: "rolled-back"}`; `state_to_json`/`state_from_json` | `chain/model.py:30-72, 226-241` |
| `CHAIN_LEAF = ".#~chain"`, `STAGING_LEAF = ".#~stage"`, `SCRATCH_ROLES = {"staging", "tombstone", "anchor", "work"}` | `core/scratch.py:15-17`, `chain/read.py:17` |
| Store mutations: `_StoreTransaction.set_transaction_state/set_commit_decision/set_rollback_result/set_halt_diagnostic/set_registration_digest/set_settlement_digest/set_assembly_halt/set_journal_state/set_active(txid \| None)`; reads: `Store.read_record/read_active`; `Store.open_blob(digest) -> int`; `Store.transaction()` context manager | `store/connection.py:621-856` |
| `StoredRecord(txid, spec, state, committed, rollback_result, halt_diagnostic, registration_digest, settlement_digest, approval_evidence, assembly_halt, journals)` | `store/records.py:420-431` |
| `persist_plan_prefix(lease, approved, plan, start) -> int` persists metadata-only steps, stops before `_MUTATING = (TransformEffectTuple, RemoveScratch)`; its `_require_projection_matches` carries the open comment "A7b decides whether this comparison must grow" | `coordinator/transitions.py:23-64` |
| `authorize_recovery_step(plan, step_index, observed: JointObservation) -> AuthorizedStep \| HaltPlan` — validates exact coverage against `step.expected_before`, compares authorization projections, returns `HaltPlan` on mismatch | `core/recovery/authorization.py:37-68` |
| `classify_recovery(snapshot) -> RecoveryPlan`; `RecoveryPlan = ActionPlan \| HaltPlan \| NoRecoveryPlan`; dispositions `ROLL_BACK, ROLL_BACK_REFUSED, COMMITTED_CLEANUP, DETACH_TERMINAL, HALT, NO_RECOVERY`; `apply_recovery_plan` is A3's fixed point | `core/recovery/classifier.py:124`, `plan.py:20-27,162`, `reducer.py:124` |
| Step shapes: `TransformEffectTuple(effect_id, variant, settlement, expected_before, result_after, identity_relations)`, `RemoveScratch(effect_id, role, expected_before, result_after)`, `TransitionTransactionState`, `TransitionEffectState`, `DetachActive()`; `SettlementKind = {RESTORE_PRE, REMOVE_ATTRIBUTABLE_CREATION, REPAIR_INTERMEDIATE, FINISH_LANDED_UNDO, REMOVE_COMMITTED_SCRATCH}`; `EffectVariant` five members | `core/recovery/plan.py:29-115` |
| `build_recovery_snapshot(*, compiled, topology, transaction_state, commit_decision, rollback_result, halt_diagnostic, active, journals, persistent_observations, scratch_observations)` validates complete coverage | `core/recovery/snapshot.py:175-215` |
| `Observation.observe(parent_fd, leaf, *, sink_fd=None, modeled=None)`, `pinned_descriptor(identity) -> int`, `build_relation(staged_fd, planned_fd)`; `DescriptorTable.stops/fd_for/is_unreachable/close`; `_build_descriptor_table(lease, approved, workspace, observation)` | `fs/observe.py:70-178`, `coordinator/descriptors.py:63-163` |
| `prepare_transaction` persists `approval_evidence=encode_approval_evidence(approved)` in the PREPARED COMMIT; the encoding is canonical JSON `{"directories": [...], "mount_id": int, "work_root": {...} \| null}` with sorted keys | `coordinator/prepare.py:21-44`, `fs/approval.py:166-207` |
| `approve_for_project(compiled, ProjectContext(binding, txid)) -> ProjectApprovedSpec` — callable with an **existing** txid; `admit` wraps it with fresh-txid generation and scratch-occupancy checks | `fs/approval.py:220`, `coordinator/admission.py:61-86` |
| `ProjectApprovedSpec(compiled, binding, txid, topology, directories, paths, scratch, work_base)`; `ApprovedPath(path, parent_node, leaf)`; `ApprovedScratch(effect_id, role, parent_node, leaf)` | `fs/approval.py:62-87`, `fs/topology.py:63-75` |
| Effect shapes: `ReplaceFile(effect_id, path, pre, post)`, `CreateFileNoClobber(effect_id, path, post)`, `DeletePath(effect_id, path, pre: FileState \| SymlinkState)`, `MoveNoClobber(effect_id, source, destination, source_pre)`, `CreateDirectory(effect_id, path, post)` | `core/effects.py:20-55` |
| Facade: `AuditedBackend.rebind(fd, provenance)`, `set_declared_paths(frozenset[str])`/`clear_declared_paths()` exist and have **no callers** in `src/` — they are the A7b hook | `fs/audit.py:109-150`; `grep set_declared_paths` finds only `audit.py` |
| `AssemblyHalt`/`AssemblyFinding` with closed `_FACT_KEYS` per kind, `encode_assembly_halt`/`decode_assembly_halt`; `require_assembly_halt_binding(txid, approval_evidence, halt)` | `core/assembly.py:30-206`, `store/records.py:434-446` |
| `TransactionHalted` exists in `core/errors.py:38` and is raised **nowhere** in `src/` yet | `grep TransactionHalted` |
| Subprocess harness: `tests/coordinator_child.py` runs lease/durable phases in a fresh process and reports JSON; `tests/fs_support.py` has `build_test_allowlist`, `make_bound_volume`; `tests/coordinator_support.py` has `prepared()`, `prepared_with_transform()`, `prepared_with_remove_scratch()`, `prepared_with_halt()`, `spec_digest(spec)` (sha256 of `canonical_json`) | `tests/coordinator_child.py:1-60`, `tests/coordinator_support.py:55-376` |
| Architecture guards already global: the mutating-`os` attribute sweep with `_FACADE_EXEMPT = ("fs/audit.py", "fs/linux.py", "fs/syscalls/")` and `_OS_OPEN_ALLOWED = ("fs/resolve.py",)`; facade constructed only at the composition root; `chain` imports no coordinator/store | `tests/test_fs_architecture.py:24-82,182` |
| The docs-status guard: `STAGES` ends `..."A7a", "A7b", "A8", "A9"`, `FIRST_UNIMPLEMENTED = "A7b"` | `tests/test_docs_status.py:31-37` |
| A3 emits exactly six `(variant, settlement)` transform pairs: Replace×RESTORE_PRE, CreateFile×REMOVE_ATTRIBUTABLE_CREATION, Delete×RESTORE_PRE, Move×RESTORE_PRE, Move×REPAIR_INTERMEDIATE, CreateDirectory×REMOVE_ATTRIBUTABLE_CREATION; `FINISH_LANDED_UNDO`/`REMOVE_COMMITTED_SCRATCH` never appear as transforms | `core/recovery/variants.py:546,683,801,973,1001,1033,1246` |
| `AuthorizedStep(plan, step_index, step)` is token-guarded — only `authorize_recovery_step` constructs it | `core/recovery/plan.py:184-203` |
| `promote_staging` requires the manifest to describe `staging/<txid>` exactly (leftover leaves → `ProtocolError`), spends the staging fd, and removes the directory before PREPARED completes | `store/blobs.py:345-395` |
| A staging survivor classifies `FINISH` only when its exact planned envelope is passed to `validate_chain`, else `REMOVE`; a planned envelope must already be durable or derive from the validated tip | `chain/read.py:160-181` |
| `_build_descriptor_table` records a `WalkStop` for **every** planned directory node, whatever it observes — both outcomes stop the walk; the table's fd mapping is fixed at construction | `coordinator/descriptors.py:63-125,215-249` |
| Approval refusals are unstructured: a missing required ancestor raises `ProjectApprovalRefused` with no errno cause; resolution converts absence into topology data before approval | `fs/judgment.py:110-137` |
| `TransactionSpec` carries `consumer_tag: str` and `intent_digest: str` as required v1 members, compiler-validated (`require_valid_identifier`; `sha256:<64 lowercase hex>`), durable in `spec_json`; authority §13.4 names them "persisted in the durable record" for recovery attributability | `core/spec.py:31-40`, `core/compiler.py:202-214`, authority `:1690-1692` |
| Evidence directory keys carry no path: `_node_key(TopologyDirectory)` is `topology_directory:<id>`; the (path, node) pairs live in `ResolvedTopology.directory_nodes` at approval time; `load_record` stores `approval_evidence` as an unchecked string | `fs/approval.py:123-139`, `fs/topology.py:95`, `store/records.py:573` |
| Reverse-move durability is the forward move's mirror: fsync the restored-source parent before the old-destination parent | authority `:1150-1153` |
| The proof-gate registry `_TRANSACTION_STAGE_ENTRY_POINTS` maps file → function names and requires each listed function's **first statement** to be a `_require_admitted` call | `tests/test_fs_architecture.py:1126-1143` |
| A planned directory's evidence entry carries `identity = null`; only approved-existing directories carry identity facts | `fs/approval.py:170-176` |
| Move × `RESTORE_PRE` is destination→source only with the anchor kept (a `RemoveScratch` follows via `_remove_after_transform`); the dual-name and anchor-only shapes are `REPAIR_INTERMEDIATE`; CreateDirectory removal targets the live directory only | `core/recovery/variants.py:962-1035,1235-1250` |

## Global constraints

- Run everything from `python/`: `uv run pytest`, `uv run ruff check .`,
  `uv run pyright`. All three green before every commit.
- Conventional commits. No AI-attribution trailer or footer.
- **Facade-only mutation**: no new module touches `os.*` mutators or `os.open`; the
  existing attribute-reference sweep enforces this automatically — do not add
  exemptions.
- **No proof forgery**: `ProjectApprovedSpec`, `RecoveryPlan`, `RecoverySnapshot`,
  `ValidatedChain` are factory-issued; tests obtain them through the real factories
  (`tests/coordinator_support.py`, `tests/recovery_support.py`), never by token
  smuggling.
- New coordinator modules export nothing (`test_the_coordinator_exports_nothing`
  already enforces `__all__` discipline; only `commands.py` carries public names).
- Explicit > defensive: refuse with a named error at the seam that owns the fact;
  never catch-and-continue. `KeyboardInterrupt`/`SystemExit` handling is exactly the
  design's §6 catch — nowhere else.
- Every task ends with the full suite green, not just the task's file.

## Task order

Tasks 1–4 build the effect modules bottom-up (no imports from `recover.py`/
`execute.py`, so they land first). Task 5 is the plan executor; Tasks 6–7 the
resolver; Task 8 the forward spine that ties them; Tasks 9–10 the kill matrix and
conformance/architecture suites; Task 11 (docs/roadmap/ledger) runs **last** because
the status guard refuses "implemented" claims until the tree makes them true.

---
## Task 1: The effects package — sites, shared helpers, and `ReplaceFile`

**Files:**
- Create: `python/src/atoms/coordinator/effects/__init__.py` (docstring only, no exports)
- Create: `python/src/atoms/coordinator/effects/common.py`
- Create: `python/src/atoms/coordinator/effects/sites.py`
- Create: `python/src/atoms/coordinator/effects/replace_file.py`
- Modify: `python/tests/coordinator_support.py` (spec builders per variant)
- Test: `python/tests/test_effects_common.py`, `python/tests/test_effects_replace.py`

**Interfaces:**
- Consumes: `AuditedBackend` primitives (`create_exclusive`, `write`, `set_mode`,
  `flush_file`, `flush_directory`, `exchange`, `close_fd`), `Store.open_blob`,
  `DescriptorTable.fd_for`, `ApprovedPath`/`ApprovedScratch` (measured facts above).
- Produces, for Tasks 2–5 and 8:
  - `sites.py`: per-variant frozen site values and one builder —
    `ReplaceSite(effect_id, parent_fd, live_leaf, staging_leaf)`,
    `CreateFileSite(effect_id, parent_fd, live_leaf, staging_leaf)`,
    `DeleteSite(effect_id, parent_fd, live_leaf, tombstone_leaf)`,
    `MoveSite(effect_id, source_fd, source_leaf, anchor_leaf, destination_fd, destination_leaf)`,
    `MkdirSite(effect_id, work_fd, work_leaf, parent_fd, live_leaf)`;
    `_site_for(approved: ProjectApprovedSpec, table: DescriptorTable, effect: Effect) -> Site`
    — parent fds from `table.fd_for(ApprovedPath.parent_node)` (borrowed, never
    closed), leaves from `ApprovedPath.leaf`, scratch leaves from `approved.scratch`
    matched on `(effect_id, role)`; a missing approved path or scratch row is
    `ProtocolError` (the proof was issued for this spec, so absence is engine misuse).
  - `common.py`:
    - `class EffectMismatch(AtomsError)` — the one signal a variant raises when
      in-process verification fails. **Never exported, never surfaced to consumers**:
      the §6 catch feeds it to the plan loop, and the loop's outcome — clean rollback
      or halt — decides what the consumer sees (design §6: `PreconditionRefused`
      only after restoration is proved).
    - `stream_blob(backend, store, state: FileState, dest_fd: int) -> None` — read
      the blob fd from `store.open_blob(state.content_hash)` in 64 KiB `os.read`
      chunks (reads are unaudited; the facade audits mutation), write each through
      `backend.write(dest_fd, chunk)`, hash **as written**; a final digest or length
      disagreeing with `state` raises `MetadataStoreInvalid` (the store's own blob
      barrier was supposed to make this impossible — disagreement is substrate
      evidence, not a precondition).
    - `build_staged_file(backend, store, parent_fd, leaf, state: FileState) -> int`
      — `create_exclusive(parent_fd, leaf, 0o600)` (through `run_determinate`,
      so a raced-in staging occupant converts instead of leaking raw `EEXIST`
      after `STARTED`) → `stream_blob` → `set_mode(fd,
      state.mode)` → `flush_file(fd)` → return the retained fd. Ownership is
      failure-complete: on **any** exception after the create — stream, mode,
      flush — the helper closes the fd via `close_fd` before re-raising, so the
      retained descriptor is the caller's to close only on the success return
      (the staged leaf itself stays behind as attributable debris for recovery).
    - `verify_live_file(backend, retained_fd, parent_fd, leaf, state: FileState) -> None`
      — identity: `os.lstat(leaf, dir_fd=parent_fd)` (through
      `run_determinate("lstat", ...)`) names the same `(st_dev,
      st_ino)` as `os.fstat(retained_fd)`; postcondition: re-read the bytes through
      `retained_fd` (`os.lseek(fd, 0, SEEK_SET)` + `os.read` loop), sha256 equals
      `state.content_hash`, size equals `state.byte_len`, `stat.S_IMODE` equals
      `state.mode`. Any disagreement raises `EffectMismatch` naming the axis.
    - `run_determinate(operation: str, slot: str, call: Callable[[], _T], *,
      passthrough: tuple[int, ...] = ()) -> _T` — the **one** `OSError`-conversion
      seam every mutation call in Tasks 1–4 routes through, forward and recovery
      alike. The world can move between any observation and its mutation syscall;
      design §11 requires that interference to end as a clean refusal after
      restoration (`2026-07-23-...-design.md:1275-1283`), and the spine's catch
      only translates `EffectMismatch`/`PreconditionRefused` — a raw `OSError`
      would roll back and then escape raw. The wrapper **validates before it
      invokes**: it resolves `_DETERMINATE[operation]` first — an unknown
      operation is `ProtocolError` raised with the callable never invoked,
      since a misuse whose callable happens to succeed would otherwise mutate
      the world under an unvalidated operation — and requires every
      `passthrough` member to be an `int`. Only then does it invoke `call`,
      catch `OSError`, re-raise it unchanged when its errno is in
      `passthrough` (the caller owns that branch), raise `EffectMismatch`
      (naming the operation, the slot, and the errno) when it is in the
      resolved determinate set, and otherwise — `EIO` and kin — re-raise the
      `OSError` it is (design §9.3: never encoded as drift). The table:
      - `unlink_child`: `ENOENT`, `EISDIR`, `EBUSY`;
      - `rmdir_child`: `ENOENT`, `ENOTDIR`, `EBUSY`, and `ENOTEMPTY`/`EEXIST`
        (POSIX permits either for a nonempty directory; the authority explicitly
        requires the concurrent-child refusal to end safely, measured
        `2026-07-23-recoverable-fs-effect-engine-design.md:1227`);
      - `transfer_noclobber`: `ENOENT`, `EEXIST`, `ENOTDIR`, `EXDEV`, `EBUSY`;
      - `exchange`: `ENOENT`, `ENOTDIR`, `EXDEV`, `EBUSY`;
      - `link_anchor`: `ENOENT`, `EEXIST`, `ENOTDIR`, `EXDEV`;
      - `create_exclusive`: `ENOENT`, `EEXIST`, `ENOTDIR`;
      - `mkdir_child`: `ENOENT`, `EEXIST`, `ENOTDIR`;
      - the verification lookups — `lstat`: `ENOENT`, `ENOTDIR`;
        `open_regular_nofollow`: `ENOENT`, `ENOTDIR`, `ELOOP`, `EISDIR`;
        `symlink_fingerprint`: `ENOENT`, `ENOTDIR`, `EINVAL`.
      The lookup entries exist because post-mutation verification reads race
      exactly the way the mutations do: an entry vanishing between the
      exchange and its verifying `lstat` is drift evidence and must surface
      as `EffectMismatch`, never a raw `FileNotFoundError` — while `EIO`
      from the same lookups still propagates.
      The boundary errnos are determinate for the same reason the diff walk's
      `EXDEV` is: a mount planted mid-flight makes rename/link refuse `EXDEV`,
      and a mount point pinned under a name makes unlink/rmdir/rename refuse
      `EBUSY` — both are world-drift evidence, not substrate failure. The two
      creation entries exist because a scratch or work occupant racing in
      after admission — or a vanished parent — is the same post-`STARTED`
      world drift: design §11 classifies scratch-leaf occupancy as a refusal
      case, and this conversion is what routes it there through the spine.
- `replace_file.apply(backend: AuditedBackend, store: Store, site: ReplaceSite,
  effect: ReplaceFile) -> None` — design §7's sequence verbatim, spelled in Step 1.3.
  **Routing rule for Tasks 1–4:** every forward-sequence and recovery-cell
  mutation call (`create_exclusive`, `mkdir_child`, `exchange`,
  `transfer_noclobber`, `link_anchor`, `unlink_child`, `rmdir_child`) goes
  through `run_determinate` — `build_staged_file`'s exclusive create and
  mkdir's work-slot `mkdir_child` included; create-file's and mkdir's
  publication transfers pass `passthrough=(errno.EEXIST,)` so their
  compensating occupancy branch still sees the raw `EEXIST`. **Compensation
  mutations inside `except` blocks route through the same seam**: an `ENOENT`
  during staging cleanup, an `EEXIST` during rename-back, an `ENOTEMPTY`
  during work-slot removal are determinate drift and convert to
  `EffectMismatch` like any other (delete's transfer-back hitting `EEXIST` —
  the reappeared-live case — is exactly this conversion), while an
  indeterminate error propagates raw. **Post-mutation verification lookups
  route the same way**: `verify_live_file`'s identity `lstat`,
  `_verify_displaced_pre`'s `open_regular_nofollow`, delete's tombstone
  `symlink_fingerprint`, move's anchor-validation lookups, and mkdir's
  identity lookup all go through the seam under their lookup operation keys.
  Either way §11's refusal-or-halt contract owns the outcome — the plan loop
  must still prove restoration before any refusal is surfaced.

- [ ] **Step 1.1: Spec builders.** In `tests/coordinator_support.py` add
  `replace_spec()`, `delete_spec()`, `move_spec()`, `create_file_spec()` returning
  `TransactionSpec` values shaped like the existing `file_spec()`/`directory_spec()`
  (measured at `tests/coordinator_support.py:68-101`), one per missing variant, each
  with coherent surfaces. Run `uv run pytest tests/test_coordinator_prepare.py -x`
  to prove the support module still imports.
- [ ] **Step 1.2: Failing tests.** `test_effects_common.py`: `stream_blob` writes
  exact bytes and refuses a truncated blob with `MetadataStoreInvalid` (write the
  blob file short by hand through the store's staging seam);
  `verify_live_file` accepts the built state and raises `EffectMismatch` on each
  axis (foreign inode swapped in via a raw `LinuxBackend` beneath the facade, mode
  drift via raw `fchmod`, byte drift via raw write); `run_determinate` — first
  an equality assertion that `_DETERMINATE` matches the expected mapping
  **spelled literally in the test** (a missing operation fails the equality;
  parametrizing from the module's own table could never detect one), then,
  parametrized over that literal's `(operation, errno)` pairs, a stub raising
  that `OSError` converts into an `EffectMismatch` naming operation, slot, and
  errno — while `OSError(EIO)` from every operation propagates unchanged, a
  `passthrough` errno re-raises the raw `OSError`, and an unknown operation
  raises `ProtocolError` with a sentinel callable left **uninvoked** (assert
  via a flag the sentinel would set). `test_effects_replace.py`:
  forward success publishes the postimage at the live path with the preimage
  displaced onto the staging leaf; verification failure with the live path still
  ours exchanges back (preimage restored) and raises `EffectMismatch`; a foreign
  live entry AND altered staging raises `EffectMismatch` without further mutation
  (the both-changed case mutates nothing more — the plan loop owns what happens
  next); `build_staged_file` leaks no descriptor when the stream, mode, or flush
  step fails (inject each beneath the facade; assert with
  `tests/fs_support.descriptor_count` before/after). Call-site routing is
  tested where it matters, with the same injecting backend: a determinate
  failure injected into the exchange-back compensation, the verifying
  `lstat` (entry removed beneath the facade after the exchange), and
  `build_staged_file`'s `create_exclusive` (staging occupant raced in) each
  surfaces as `EffectMismatch` — a raw `OSError` escaping any `apply` fails
  the test. Build state with
  `prepared()`-style helpers over `make_bound_volume`.
  Run: `uv run pytest tests/test_effects_common.py tests/test_effects_replace.py -x`
  — expected: import errors (modules do not exist).
- [ ] **Step 1.3: Implement.** `replace_file.apply`, exactly:

  ```python
  def apply(backend, store, site, effect):
      staged_fd = build_staged_file(
          backend, store, site.parent_fd, site.staging_leaf, effect.post
      )
      try:
          run_determinate(
              "exchange",
              str(effect.path),
              lambda: backend.exchange(
                  site.parent_fd, site.live_leaf, site.staging_leaf
              ),
          )
          verify_live_file(
              backend, staged_fd, site.parent_fd, site.live_leaf, effect.post
          )
          _verify_displaced_pre(backend, site, effect.pre)   # staging now holds pre
          backend.flush_directory(site.parent_fd)
      except EffectMismatch:
          _exchange_back_if_ours(backend, staged_fd, site, effect.post)
          raise
      finally:
          backend.close_fd(staged_fd)
  ```

  `_exchange_back_if_ours` re-checks that the live leaf still names the retained
  identity before exchanging back, and after the exchange **flushes the parent
  before re-raising** — authority §9.1 is explicit: "exchange it back … fsync,
  and refuse". An unflushed compensation is a time bomb: rollback observes the
  restored pre-world, detaches durably, and a power cut then loses the
  exchange, leaving a detached store claiming a world the disk does not hold.
  **Every compensation mutation in Tasks 1–3 carries the same rule — mutate,
  then `flush_directory` the mutated parent(s), then raise.** When the live
  leaf does not match (both changed), it mutates nothing. `_verify_displaced_pre`
  validates the displaced staging entry against `effect.pre` through a fresh
  `open_regular_nofollow` descriptor, closed via `close_fd`.
- [ ] **Step 1.4:** `uv run pytest tests/test_effects_common.py
  tests/test_effects_replace.py -x` — expected: PASS. Then the full gate:
  `uv run pytest && uv run ruff check . && uv run pyright`.
- [ ] **Step 1.5:** `git commit -m "feat(effects): effect sites, staging helpers, and ReplaceFile forward execution"`

---

## Task 2: `CreateFileNoClobber` and `DeletePath` forward execution

**Files:**
- Create: `python/src/atoms/coordinator/effects/create_file.py`
- Create: `python/src/atoms/coordinator/effects/delete_path.py`
- Test: `python/tests/test_effects_create_file.py`, `python/tests/test_effects_delete.py`

**Interfaces:**
- Consumes: Task 1's `common.py`/`sites.py`; `transfer_noclobber`,
  `symlink_fingerprint`, `unlink_child`.
- Produces: `create_file.apply(backend, store, site: CreateFileSite, effect) -> None`,
  `delete_path.apply(backend, store, site: DeleteSite, effect) -> None`.

- [ ] **Step 2.1: Failing tests.** Create-file: forward success; `EEXIST` on the
  live transfer removes **only** the attributable staging object (identity-checked
  through the retained fd before `unlink_child`) and raises `PreconditionRefused`
  (design §7: this refusal is a real precondition, not a verification mismatch —
  the world already held the path); byte-equivalence of the occupant is never
  adopted (test: occupant with identical bytes still refuses); an injected
  `OSError(EIO)` from the publication transfer propagates raw — no
  compensation runs, the staging entry survives (the apply-level counterpart
  of the seam's own `EIO` test: the `except OSError` branch must not treat an
  indeterminate error as occupancy); call-site routing: an `ENOENT` injected
  into the staging-cleanup `unlink_child`, an `EEXIST` into delete's
  tombstone-return transfer, and a determinate failure into either variant's
  verification lookup each surfaces as `EffectMismatch`, never a raw
  `OSError`. Delete: forward
  success moves the live entry to the tombstone slot and validates it against
  `effect.pre` (file: descriptor-coherent via `verify_live_file` against the
  tombstone leaf; symlink: `symlink_fingerprint(parent_fd, tombstone_leaf)`
  target + identity comparison); a tombstone that fails validation transfers back
  no-clobber and raises `EffectMismatch`; a live path that reappeared while the
  mismatched tombstone was being returned raises `EffectMismatch` with no further
  mutation. Run: expect import failures.
- [ ] **Step 2.2: Implement.** Create-file:

  ```python
  def apply(backend, store, site, effect):
      staged_fd = build_staged_file(
          backend, store, site.parent_fd, site.staging_leaf, effect.post
      )
      try:
          try:
              run_determinate(
                  "transfer_noclobber",
                  str(effect.path),
                  lambda: backend.transfer_noclobber(
                      site.parent_fd, site.staging_leaf, site.parent_fd, site.live_leaf
                  ),
                  passthrough=(errno.EEXIST,),
              )
          except OSError as caught:
              if caught.errno != errno.EEXIST:
                  raise      # indeterminate (EIO and kin) re-raised raw by the seam
              _remove_attributable_staging(backend, staged_fd, site)
              backend.flush_directory(site.parent_fd)   # compensation barrier
              raise PreconditionRefused(
                  f"{effect.path!r} already exists; CreateFileNoClobber refuses"
              ) from caught
          verify_live_file(
              backend, staged_fd, site.parent_fd, site.live_leaf, effect.post
          )
          backend.flush_directory(site.parent_fd)
      finally:
          backend.close_fd(staged_fd)
  ```

  Delete: `transfer_noclobber(live → tombstone)` → validate per preimage type →
  on mismatch `transfer_noclobber(tombstone → live)` back **then
  `flush_directory(site.parent_fd)` before raising** (the compensation-barrier
  rule; the seam converts an `EEXIST` there — the reappeared-live case — into
  the `EffectMismatch` that stops further mutation).
- [ ] **Step 2.3:** Task tests pass, then the full gate (pytest, ruff, pyright).
- [ ] **Step 2.4:** `git commit -m "feat(effects): CreateFileNoClobber and DeletePath forward execution"`

---

## Task 3: `MoveNoClobber` and `CreateDirectory` forward execution

**Files:**
- Create: `python/src/atoms/coordinator/effects/move.py`
- Create: `python/src/atoms/coordinator/effects/create_directory.py`
- Modify: `python/src/atoms/coordinator/descriptors.py` (`DescriptorTable.adopt`)
- Test: `python/tests/test_effects_move.py`, `python/tests/test_effects_mkdir.py`,
  additions to `python/tests/test_coordinator_descriptors.py`

**Interfaces:**
- Consumes: `link_anchor`, `mkdir_child`, `open_child_directory`, facade `rebind`
  (measured `fs/audit.py:109`), `Provenance`/`RootKind` for the rebound declared
  path.
- Produces:
  - `move.apply(backend, store, site: MoveSite, effect) -> None`.
  - `create_directory.apply(backend, store, site: MkdirSite, effect) -> int` —
    returns the **retained directory descriptor**, provenance already rebound to
    the live declared path (ledger #3). Ownership is transfer-or-close: the
    caller must either adopt the fd into the `DescriptorTable` or close it; the
    module itself closes the fd on **every** exception path before re-raising.
  - `DescriptorTable.adopt(node: TopologyNode, fd: int) -> None` — the adoption
    seam both the forward spine (Task 8) and recovery descent (Task 7) need.
    The table currently owns a fixed fd mapping built once and has no way to
    accept a descriptor after construction (measured `descriptors.py:63-125,
    215-249`: a planned node always records a `WalkStop`). `adopt` requires the
    table open, requires `node` to be currently stopped (adopting over a live fd
    or an unknown node is `ProtocolError`), removes the node from the stop set,
    records the fd so `fd_for(node)` serves it and `is_unreachable(node)` turns
    false, and takes ownership — `close()` closes adopted fds with the rest.

- [ ] **Step 3.1: Failing tests.** Move: forward success (anchor durable before the
  transfer — assert the anchor exists and `flush_directory(source_fd)` was reached
  by cutting with a fault-injecting backend beneath the facade between
  `link_anchor` and `transfer_noclobber`, then checking the anchor survives);
  an anchor validation failure — which happens **before** the source has moved —
  removes the attributable anchor (`unlink_child` + `flush_directory(source_fd)`)
  and raises `EffectMismatch` (rename-back only applies after destination
  publication); post-transfer, destination and anchor must name one inode at the
  expected fingerprint — a non-identity renames the destination back no-clobber
  and flushes both parents (restored-source parent first, the reverse-move
  mirror) before raising `EffectMismatch`; a reappeared source after that failed validation
  raises `EffectMismatch` without further mutation. Mkdir: forward success
  publishes an empty directory with the approved mode at the live path, identity
  verified through the retained descriptor, and the descriptor's facade
  provenance now names the live path (assert via `provenance_of`); occupancy
  (`EEXIST` on the cross-directory transfer) raises `PreconditionRefused` after
  removing only the attributable work-slot directory and flushing `work_fd`
  (the compensation-barrier rule); an injected `OSError(EIO)` from that same
  transfer propagates raw with no compensation and the work slot intact;
  call-site routing: an `ENOENT` injected into the anchor-removal
  `unlink_child`, an `EEXIST` into move's rename-back transfer, an
  `ENOTEMPTY` into the work-slot `rmdir_child`, and an `EEXIST` from a
  raced-in work-slot occupant at `mkdir_child` each surfaces as
  `EffectMismatch`, never a raw `OSError`; an injected failure at
  each of `set_mode`, `flush_file`, the transfer, and verification closes the
  retained fd before the exception escapes (descriptor-count assertion via
  `tests/fs_support.descriptor_count`). `DescriptorTable.adopt`: adopting a
  stopped planned node makes `fd_for` serve the fd and `close()` close it;
  adopting an open node, an unknown node, or after `close()` refuses with
  `ProtocolError`. Run: expect import failures.
- [ ] **Step 3.2: Implement.** Move, design §7 order exactly (each mutation
  through `common.run_determinate`, per Task 1's routing rule):
  `link_anchor(source_fd, source_leaf, source_fd, anchor_leaf)` →
  `flush_directory(source_fd)` → validate
  the anchor against `effect.source_pre` → `transfer_noclobber(source_fd,
  source_leaf, destination_fd, destination_leaf)` → require destination and anchor
  to name one inode still at the expected fingerprint → `flush_directory
  (destination_fd)` **then** `flush_directory(source_fd)`. Mkdir:
  `mkdir_child(work_fd, work_leaf, mode)` → `open_child_directory(work_fd,
  work_leaf)` (retain) → `set_mode` → `flush_file(dir_fd)` →
  `transfer_noclobber(work_fd, work_leaf, parent_fd, live_leaf)` →
  `flush_directory(parent_fd)` → verify identity, mode, and emptiness through the
  retained descriptor → `flush_directory(work_fd)` → `backend.rebind(dir_fd,
  Provenance(RootKind.PROJECT, str(effect.path)))` → return `dir_fd`.
- [ ] **Step 3.3:** Task tests pass, then the full gate.
- [ ] **Step 3.4:** `git commit -m "feat(effects): MoveNoClobber and CreateDirectory forward execution with provenance rebinding"`

---

## Task 4: The recovery-mutation mapping

**Files:**
- Create: `python/src/atoms/coordinator/effects/settle.py`
- Test: `python/tests/test_effects_settle.py`

**Interfaces:**
- Consumes: Tasks 1–3 helpers; `AuthorizedStep(plan, step_index, step)` — the
  token-guarded proof `authorize_recovery_step` issues (measured
  `core/recovery/plan.py:184-203`); `state_from_json` is NOT involved —
  expected/result states arrive as `JointObservation` values inside the step.
- Produces, for Task 5's loop:
  - `settle.apply_transform(lease, approved, table, authorized: AuthorizedStep)
    -> None` — a transaction-stage entry point: its **first statement** is
    `_require_admitted(lease, approved)` (the proof-gate registry demands
    exactly that shape — measured `test_fs_architecture.py:1126-1143`), it
    derives the backend and store from the lease, and it is enumerated in
    `_TRANSACTION_STAGE_ENTRY_POINTS` (Task 10). It is also **typed against the
    authorization proof, not the raw step**: the only capability proving a
    fresh observation authorized this mutation is the `AuthorizedStep`, so it
    demands it (exact-type check) and reads `authorized.step` itself.
    Dispatches on `(step.variant, step.settlement)`, executes the recovery
    mutation, verifies the world now matches `step.result_after` for the step's
    covered slots (fresh observation through the same parent descriptors), then
    makes the mutation durable — `flush_directory` on every parent it mutated;
    where two are involved the order is the forward move's mirror (authority
    §9.4): the parent that **received** the restored entry before the parent it
    was removed from, so a reverse move flushes the restored-source parent
    before the old-destination parent. A post-mutation mismatch raises
    `EffectMismatch`.
  - `settle.apply_remove_scratch(lease, approved, table, authorized:
    AuthorizedStep) -> None` — same gate, same proof discipline;
    `unlink_child` (file/symlink) or `rmdir_child` (directory) of exactly the
    named slot, per the observed kind in `step.expected_before`, then
    `flush_directory` of the slot's parent.
- **The dispatch table is the A3-authorized table, not design §7's full
  cross-product.** Measured: the classifier's variant factories emit exactly six
  `(variant, settlement)` pairs — `REPLACE_FILE × RESTORE_PRE`
  (`variants.py:546`), `CREATE_FILE_NO_CLOBBER × REMOVE_ATTRIBUTABLE_CREATION`
  (`:683`), `DELETE_PATH × RESTORE_PRE` (`:801`), `MOVE_NO_CLOBBER × RESTORE_PRE`
  (`:973`), `MOVE_NO_CLOBBER × REPAIR_INTERMEDIATE` (`:1001,:1033`),
  `CREATE_DIRECTORY × REMOVE_ATTRIBUTABLE_CREATION` (`:1246`).
  `FINISH_LANDED_UNDO` and `REMOVE_COMMITTED_SCRATCH` are **never** emitted as
  transforms — committed cleanup arrives as `RemoveScratch` steps. Every pair
  outside the six is `ProtocolError`, including the pairs design §7's closing
  paragraph muses about (staged re-creation of missing preimages, symlink
  restoration): a world state with no restorable survivor classifies as a
  `HaltPlan` in A3, so an executor fallback for it would be unreachable invention.
  Task 11's design-amendment note records this narrowing against §7.
- Each implemented cell executes the **slot delta** `expected_before →
  result_after` using only the variant's own primitives, with its sub-cases
  enumerated from A3's fixture families (`tests/recovery_support.py:379-744` —
  `make_replace_transform_case`, `make_create_file_case`, `make_delete_case`,
  `make_move_case` and their parametrizations are the authoritative census of
  world shapes each cell must handle). A delta the census does not contain is
  `EffectMismatch` territory only if it appears at runtime *after*
  authorization — at dispatch time an unrecognized shape is `ProtocolError`.
  The deltas per cell, measured from the variant factories:
  - **Replace × restore** is exactly the exchange back — `live=POST,
    staging=PRE → live=PRE, staging=POST` (`variants.py:533-546`). A
    not-yet-landed staging artifact is **not** this transform's business: its
    cleanup arrives as a separate `RemoveScratch` step, as does the displaced
    postimage after the exchange.
  - **Create-file × remove** transfers the creation back off the live path —
    `live=POST → staging=POST` via `transfer_noclobber(live → staging)`
    (`variants.py:665-683`: the transform's `result_after` has the bytes on the
    staging slot); the following `RemoveScratch` removes the staging. Directly
    unlinking the live path would skip `result_after` and is wrong.
  - **Delete × restore** transfers the validated tombstone back no-clobber.
  - **Move × restore** is exactly the destination-back-to-source transfer —
    `(source=ABSENT, destination=PRE) → (source=PRE, destination=ABSENT)` with
    the anchor untouched (`variants.py:962-988`: the transform's scratch result
    keeps the anchor; `_remove_after_transform` emits the `RemoveScratch` that
    removes it). **Move × repair** covers the other two mid-move shapes
    (`variants.py:990-1035`): the dual-name tuple removes the anchor-owned
    destination, and the anchor-only tuple restores the source from the anchor
    via `link_anchor` (a hard link from the retained anchor to the source
    leaf — the anchor stays) — neither transform removes the anchor; the
    following `RemoveScratch` does.
  - **Mkdir × remove** `rmdir_child`s the **live** directory only
    (`variants.py:1235-1250`); work-slot removal arrives as its own
    `RemoveScratch`.
- **Post-authorization syscall races are determinate drift, not crashes.** The
  authorization proof is a fresh observation, but the world can move between
  it and the mutation syscall, and a raw `OSError` from the syscall would
  bypass Task 5's halt path entirely. Every cell's mutation calls (in both
  `apply_transform` and `apply_remove_scratch`) therefore route through Task
  1's shared `common.run_determinate` seam and its closed `_DETERMINATE`
  table — the same seam the forward modules use, covering `unlink_child`,
  `rmdir_child`, `transfer_noclobber`, `exchange`, **and `link_anchor`** (the
  anchor-only repair restores the source by hard-linking from the retained
  anchor, so its `ENOENT`/`EEXIST`/`EXDEV` races must convert too). The
  resulting `EffectMismatch` rides Task 5's existing post-mutation path:
  reauthorize once solely to obtain the factory `HaltPlan`.

- [ ] **Step 4.1: Failing tests.** Drive each of the six cells through a real
  prepared state: build the mid-flight filesystem shape by running the forward
  module up to a cut (fault-injecting backend beneath the facade), then construct
  the plan with `tests/coordinator_support.prepared_with_transform`-style helpers
  and assert `apply_transform` lands the world on `result_after` — byte-for-byte
  where the cell restores content — and that the mutated parents were flushed
  (call-recording backend asserts `flush_directory` after the last mutation,
  before return). Assert a raw `TransformEffectTuple` (not an `AuthorizedStep`)
  is refused with `ProtocolError`. Assert every out-of-census pair refuses.
  Assert `apply_remove_scratch` removes exactly the named slot and nothing else
  (plant a sibling scratch leaf; it must survive). Assert the
  post-authorization race maps per operation: a wrapper backend that mutates
  the world immediately before invoking the inner call — adds a child to the
  directory before `rmdir_child`, removes the slot before `unlink_child`,
  occupies the destination before `transfer_noclobber`, removes the anchor
  before the anchor-only repair's `link_anchor` and occupies its source leaf
  in a second case — makes the cell raise `EffectMismatch`, never a raw
  `OSError`; a bind mount planted over the covered slot's parent (via
  `find_distinct_mount`, skip-with-reason when unavailable) makes the
  boundary errno convert the same way; an injected `OSError(EIO)` from the
  same sites propagates unchanged. Run: expect import failure.
- [ ] **Step 4.2: Implement** `settle.py` as a dict-of-dispatch keyed by the six
  `(EffectVariant, SettlementKind)` pairs; every cell reuses Task 1–3 helpers; no
  cell re-derives scratch names (they come from `approved.scratch` via
  `_site_for`, ledger #12).
- [ ] **Step 4.3:** Task tests pass, then the full gate.
- [ ] **Step 4.4:** `git commit -m "feat(effects): the variant-by-settlement recovery-mutation mapping"`

---
## Task 5: The plan executor

**Files:**
- Create: `python/src/atoms/coordinator/recover.py`
- Modify: `python/src/atoms/coordinator/transitions.py` (the `DetachActive` stop,
  `persist_detach`, and the projection-comment decision)
- Modify: `python/src/atoms/coordinator/commands.py` (`_registered_root` moves out)
- Modify: `python/src/atoms/core/recovery/model.py`,
  `python/src/atoms/core/recovery/variants.py`, `python/src/atoms/fs/observe.py`,
  `python/src/atoms/coordinator/capture.py` (the total-observation substrate
  change below)
- Test: `python/tests/test_coordinator_recover.py`; update
  `python/tests/test_coordinator_transitions.py` for the new stop; update
  `python/tests/test_fs_observe.py`, `python/tests/test_recovery_variants_files.py`,
  `python/tests/test_recovery_variants_paths.py`,
  `python/tests/test_coordinator_capture.py` for the new observed arm

**Interfaces:**
- Consumes: `classify_recovery`, `persist_plan_prefix`, `authorize_recovery_step`,
  Task 4's `settle.apply_transform`/`apply_remove_scratch`, `append_entry` +
  `SettledEntry`, `_StoreTransaction.set_settlement_digest`/`set_active`.
- Produces, for Tasks 7–8:
  - `recover._registered_root(lease)` — **moved verbatim** from
    `commands.py:39-73` (body byte-identical in this task; only the module
    changes — Task 7 then strips its `_apply_survivors` call when reconciliation
    starts running at lease entry). `commands.py` imports it from `recover` —
    `commands → recover` is the dependency direction, so no cycle forms when
    Task 8 adds `run_transaction`.
  - `recover.run_plan(lease, approved, table, plan: RecoveryPlan) -> RecoveryPlan`
    — the §8 loop, opening with `_require_admitted(lease, approved)` and
    enumerated in the transaction-stage entry-point registry (Task 10). Returns
    the plan it finished (an `ActionPlan` driven to completion, or the
    `HaltPlan` it persisted — the caller decides whether that raises).
  - `transitions.persist_detach(lease, approved, plan, cursor) -> int` — opens
    with `_require_admitted(lease, approved)` (it is a registered
    transaction-stage entry point, Task 10) and persists exactly one
    `DetachActive` step after asserting the active record carries **both**
    `registration_digest` and `settlement_digest`; any other step type at
    `cursor`, or a missing binding, is `ProtocolError`.

**Recovery observation is total (substrate change this task lands first):**
`Observation.observe` today raises `PreconditionRefused` for a FIFO, socket,
or device entry (measured `fs/observe.py:123-126`), and A3's closed
`ObservedEntry` union has no arm for one (measured `model.py:105`). Once a
durable record exists, the authority requires unattributable world state to
**halt and preserve evidence** — but with the refusal baked into `observe`,
a FIFO at a covered slot makes phase 6 raise `PreconditionRefused` into the
moved-world rule, which re-diffs a *clean* topology (the diff walks
directories only) and raises `ProtocolError` claiming an engine defect;
makes `_roll_back`'s re-observation escape before restoration is proved; and
leaves `_observe_for_step` unable to build the `JointObservation` a factory
`HaltPlan` needs. Four coordinated changes:

- `model.py`: `ObservedUnrecognized(st_mode: int)` — frozen, carrying the
  canonical observed mode — joins the closed `ObservedEntry` union. No
  identity member: these kinds are never opened (opening a FIFO can block),
  so like `ObservedSymlink` there is no descriptor to pin.
- `observe.py`: `observe` returns `ObservedUnrecognized(st_mode=info.st_mode)`
  where it today raises — observation states facts, it does not judge
  (ledger #13's rule, already the module's charter). `fs/observe.py` is the
  **only production constructor**, pinned by a source scan in the
  architecture tests (the `_approve_for_recovery` sole-caller pattern).
- `variants.py`: `_classify_entry` returns `EntryClass.EXTERNAL` for the new
  arm **before** `_entry_state` is consulted (`_entry_state` stays closed
  over the kinds that carry a declared `PathState`, measured
  `variants.py:149-159`). `EXTERNAL` is already every family's
  no-restorable-survivor route, so classification lands on the existing
  `HaltPlan` arms and persists through the untouched `HaltDiagnostic`
  encoding — no new decision table.
- `capture.py`: capture translates an `ObservedUnrecognized` into
  `PreconditionRefused` naming the path and `st_mode` — capture runs before
  `PREPARED`, where refusal is §11's correct outcome. The judgment moves
  from the observation layer to the one consumer entitled to make it;
  `verify_committed_surface`'s comparison likewise treats the arm as the
  mismatch it is (dataclass inequality — no special case).

Tests this task owns: plant a FIFO at a covered slot between prepare and
`run_plan` — authorization mismatch yields the factory `HaltPlan`
(`PLAN_PRECONDITION_CHANGED`), record `HALTED`, evidence preserved, and
neither `PreconditionRefused` nor `ProtocolError` escapes; the observe unit
tests convert from asserting the refusal to asserting the returned arm; the
variants tests pin `EXTERNAL` classification for the arm; the capture tests
keep the refusal at capture. (Task 7 and Task 8 pin the fresh-recovery and
caught-rollback FIFO paths; Task 11 records the design amendment — §9.1's
observation phase becomes total over entry kinds.)

**Decisions this task encodes (both were left open in the tree):**

1. `transitions.py`'s stop set grows: `_STOP = (TransformEffectTuple, RemoveScratch,
   DetachActive)` — `persist_plan_prefix` now stops before detach, making design
   §8's "never walk from a terminal transition through detach in one call" a
   mechanical property. Existing A5b transitions tests that walked through detach
   update to call `persist_detach` explicitly.
2. The open comment at `transitions.py:63-64` resolves to: **the A3 projection
   comparison does not grow.** Registration and settlement are chain-side facts
   enforced at three places that own them — the resolver's reconciliation (Task 6)
   before any plan runs, `persist_detach`'s explicit binding assertions, and schema
   v2's triggers. Assembly-halted records never reach `persist_plan_prefix` (the
   phase-2 short-circuit returns first, and the freeze trigger refuses the write if
   one somehow did). Replace the comment with this decision; add nothing defensive.

- [ ] **Step 5.1: Failing tests.** The loop drives a `prepared_with_transform` plan
  to completion: world lands on `result_after`, journal rows advance, terminal
  state persisted, settled entry appended exactly once, binding written, `active`
  detached. An authorization mismatch (mutate the covered slot between prepare and
  `run_plan`) persists the returned `HaltPlan` — record `HALTED`, diagnostic
  stored, world untouched after the halt — and `run_plan` returns that `HaltPlan`
  (never reclassifies, ledger #14). The settlement stop is idempotent: pre-append
  a matching `settled` entry without a binding (simulate the crash window with
  direct `append_entry`), rerun — the binding is backfilled and the chain gains
  **no** second settlement (assert entry count). `persist_detach` refuses a cursor
  not naming `DetachActive` and refuses when the settlement binding is absent.
  Run: expect import failure on `recover`.
- [ ] **Step 5.2: Implement.** The loop, exactly:

  ```python
  def run_plan(lease, approved, table, plan):
      _require_admitted(lease, approved)   # first statement: the registry's proof gate
      if type(plan) is NoRecoveryPlan:
          return plan
      cursor = 0
      while cursor < len(plan.steps):
          cursor = persist_plan_prefix(lease, approved, plan, cursor)
          if cursor == len(plan.steps):
              break
          step = plan.steps[cursor]
          if type(step) is DetachActive:
              _reconcile_settlement(lease, approved)
              cursor = persist_detach(lease, approved, plan, cursor)
              continue
          observed = _observe_for_step(lease, approved, table, step)
          authorized = authorize_recovery_step(plan, cursor, observed)
          if type(authorized) is HaltPlan:
              _persist_halt(lease, approved, authorized)
              return authorized
          _execute_mutating(lease, approved, table, authorized)
          cursor += 1
      return plan
  ```

  `_observe_for_step` opens **one fresh `Observation`** and observes exactly the
  slots `step.expected_before` covers — its persistent paths, its scratch slots,
  its parent-occupancy parents (with the modeled-children set from
  `descriptors._modeled_children`) — nothing more (design §8 step 3: for
  committed-cleanup `RemoveScratch`, that means the named slot alone).
  `_execute_mutating` receives the **`AuthorizedStep`** — never the raw step —
  and dispatches on `type(authorized.step)`: `TransformEffectTuple` to
  `settle.apply_transform`, `RemoveScratch` to `settle.apply_remove_scratch`,
  passing the proof through (Task 4's functions are typed against it; the
  authorization capability is consumed, not discarded). It persists nothing: a mutating step has no store projection of its own —
  `transitions._persist_one` refuses both types by design (measured
  `transitions.py:100-127`; the journal transitions around a mutation are separate
  plan steps), and `_require_projection_matches` holds across the advance because
  the reducer projects no record change for filesystem-mutating steps. The loop
  just continues at `cursor + 1`.

  **A post-mutation verification failure never escapes the loop, and is never
  retried.** `EffectMismatch` is effects-private; if `run_plan` let it
  propagate, resolver recovery would leak the signal without persisting A3's
  prefix-bound halt. So `_execute_mutating`'s caller catches it and re-enters
  the authorization seam **once**, at the same cursor, solely to obtain the
  factory-issued halt: take a fresh observation with the step's coverage and
  call `authorize_recovery_step(plan, cursor, observed)` — a `HaltPlan` (the
  world no longer matches `expected_before`) is persisted via `_persist_halt`
  and returned, exactly as an up-front mismatch would be. If it instead returns
  another `AuthorizedStep`, raise `ProtocolError` immediately — **do not
  execute again**: ledger #14 requires halting "instead of reclassifying,
  retrying, or inventing a second decision table", and a primitive that
  reported success while verification failed against an unmoved world is an
  engine defect, not drift. Step 5.1 gains the test: a settle-level
  verification failure injected beneath the facade ends in a persisted
  `HaltPlan`, and `EffectMismatch` is never observable from `run_plan`'s
  callers. `_reconcile_settlement`: under
  `_registered_root(lease)`, read the active record; if `settlement_digest` is
  set, verify it resolves in the validated chain and return; otherwise find a
  `SettledEntry` with this txid (exactly one → backfill its digest; more than one
  → `ChainStateInvalid`) or append
  `SettledEntry(txid, registration=record.registration_digest, outcome=...)` with
  the outcome derived from the durable terminal decision, then bind the digest in
  one store transaction. `_persist_halt` persists the `HaltPlan`'s transition
  steps through `persist_plan_prefix` (they are metadata-only).
- [ ] **Step 5.3:** Task tests pass; update `test_coordinator_transitions.py`
  expectations for the detach stop; full gate.
- [ ] **Step 5.4:** `git commit -m "feat(recover): the plan executor loop with the settlement-reconciling detach stop"`

---

## Task 6: Chain reconciliation — §9.2 as code

**Files:**
- Modify: `python/src/atoms/coordinator/recover.py`
- Test: `python/tests/test_coordinator_reconcile.py`

**Interfaces:**
- Consumes: `validate_chain`, `apply_survivors`, `append_entry`, `RegisteredEntry`/
  `SettledEntry`, `StoredRecord`, `ChainStateInvalid`.
- Produces, for Task 7's resolver:
  - `recover._derive_reconciliation(record: StoredRecord | None, validated:
    ValidatedChain) -> Reconciliation` — **pure**; `Reconciliation` is a private
    frozen value holding at most one registration action and one settlement action,
    each `BACKFILL(digest)` or `APPEND(entry)`, plus nothing when bindings resolve
    cleanly. Raises `ChainStateInvalid` for every contradictory case.
  - `recover._perform_reconciliation(backend: AuditedBackend, store: Store,
    chain_fd, validated, actions: Reconciliation) -> ValidatedChain` — performs
    exactly the derived actions (chain appends via `append_entry`, bindings via
    one store transaction each) and returns the fresh proof. It takes its
    actual dependencies — **not a `Lease`**: the resolver calls it in phase 3,
    before any `Lease` exists (the `Lease` is constructed in phase 6), so a
    lease parameter would force manufacturing one early.

The derivation is design §9.2 verbatim; every branch below gets a test:

- Registration: digest present → must resolve to a `RegisteredEntry` with matching
  txid (dangling → `ChainStateInvalid`; txid mismatch → `ChainStateInvalid`;
  duplicate txid entries in the chain → `ChainStateInvalid`). Digest missing with
  record `PREPARED` and **every** journal `PENDING` → unique matching entry:
  `BACKFILL`; none: `APPEND`. Digest missing in any other state → `ChainStateInvalid`
  **even when a matching entry exists**.
- Settlement: binding present → must resolve to a `SettledEntry` whose kind matches
  the durable terminal decision, whose `registration` is the record's bound
  registration, and whose txid matches — anything else `ChainStateInvalid`. Binding
  missing with a durable terminal decision → exactly one matching settlement:
  `BACKFILL`; none: `APPEND`; duplicates, wrong kind, wrong registration reference
  → `ChainStateInvalid`. A settlement on a record neither terminal nor a committed
  halt → `ChainStateInvalid`; the settled-but-`HALTED` history is accepted **iff**
  the frozen diagnostic proves `pre_halt_state = COMMITTED`; an uncommitted halt
  with a settlement → `ChainStateInvalid`.

- [ ] **Step 6.1: Failing tests.** One test per reconciliation branch above
  (sixteen minimum), plus: a reconciliation `APPEND` produces an entry
  byte-identical to the one the forward path would have written for the same
  record (build both through `_registration_entry` and compare digests).
  Fabricate engine-unreachable store states the honest way: a direct `sqlite3`
  connection that `DROP TRIGGER`s exactly the guard in the way, applies the raw
  alteration, and recreates nothing — the test **is** the raw-tamper scenario
  §9.2 classifies. Fabricate chain shapes with direct `append_entry` calls and
  raw byte writes beneath the facade for the malformed cases. Also the phase-1
  discipline pair: a `HALTED` record with a malformed chain raises
  `ChainStateInvalid` from derivation (the stored diagnostic is **not** returned);
  derivation performs zero writes (assert chain directory and store bytes
  unchanged after a `ChainStateInvalid`). Run: expect `AttributeError` on the new
  names.
- [ ] **Step 6.2: Implement.** Every field a reconciliation `APPEND` needs is
  already durable in `spec_json`: `TransactionSpec` carries `consumer_tag` and
  `intent_digest` as required v1 members, compiler-validated (`intent_digest`
  must match `sha256:<64 lowercase hex>`) — measured `core/spec.py:31-40`,
  `core/compiler.py:202-214` — and authority §13.4 names them "persisted in the
  durable record" precisely so recovery-completed publication stays
  attributable. So the rebuild is deterministic with **no new carrier**:
  `_registration_entry(spec, txid)` returns
  `RegisteredEntry(txid, spec.intent_digest, spec.consumer_tag, initial/final =
  state_to_json` of the surface states restricted to `spec.registered_paths` in
  sorted order, `fulfills=spec.fulfills)` — defined here in `recover.py`, unit
  tested, imported by Task 8's forward path so both produce byte-identical
  envelopes for the same record and tip.

  Then implement `_derive_reconciliation` (pure pattern-match over the §9.2
  table) and `_perform_reconciliation`. **Reconciliation over the chain is
  two-pass, and `_perform_reconciliation` exclusively owns survivor
  application** (measured `chain/read.py:160-181`: a staging survivor classifies
  `FINISH` only when its exact planned envelope is passed to `validate_chain`,
  else `REMOVE`; and `apply_survivors`' `FINISH` arm publishes the entry and
  advances the tip, `chain/append.py:107-133`): phase 1 validates the durable
  history read-only with `planned=()` and derives the actions **without
  applying survivors** — no other caller applies them either. Inside
  `_perform_reconciliation`: re-validate with `planned=(envelope,)` for a
  derived `APPEND` (proving the history and tip unchanged, and letting an
  interrupted append's survivor classify `FINISH` instead of being removed and
  re-staged), then `apply_survivors`; **if the finished survivor's digest now
  satisfies the `APPEND` action, the append is done** — append via
  `append_entry` only when the derived envelope's digest is still absent from
  the fresh proof, so the entry is never duplicated. Then the bindings — each
  in its own `store.transaction()`; a `BACKFILL` never appends.

  **Design-amendment candidate:** §9.2 pins when reconciliation appends but not
  that the appended entry is rebuilt from the durable `spec_json` (nor that a
  finished staging survivor satisfies the append). One dated sentence in §9.2
  records both at landing (the design wins, so the design must say it).
- [ ] **Step 6.3:** Task tests pass; full gate.
- [ ] **Step 6.4:** `git commit -m "feat(recover): two-pass chain reconciliation with survivor-aware appends"`

---

## Task 7: The widened resolver, the assembly halt, and full assembly

**Files:**
- Modify: `python/src/atoms/coordinator/recover.py` (the `resolve` entry, phases 1–7)
- Modify: `python/src/atoms/coordinator/root.py` (call `recover.resolve`; `Lease`
  constructed after resolution)
- Modify: `python/src/atoms/coordinator/lease.py` (delete `_resolve`; the trap is gone)
- Modify: `python/src/atoms/coordinator/descriptors.py` (recovery descent through
  created planned directories — see phase 6)
- Modify: `python/src/atoms/core/assembly.py` (the fact-free `MOUNT_BOUNDARY`
  finding kind — see phase 5)
- Modify: `python/src/atoms/fs/approval.py` (`directory_paths` on the proof, the
  evidence `"path"` member, `decode_approval_evidence`, the `_approve_for_recovery`
  factory — see phase 5; the `ProjectApprovedSpec` constructor's token error,
  which today claims "created only by `approve_for_project`" (measured
  `approval.py:92-95`), updates to name both factories)
- Modify: `python/src/atoms/store/records.py` (`load_record` validates the stored
  evidence through the closed decoder)
- Test: `python/tests/test_coordinator_resolve.py`,
  `python/tests/test_coordinator_assembly_halt.py`; update
  `python/tests/test_coordinator_lease.py` (trap tests convert to recovery tests
  preserving record/`active`/lock/descriptor discipline, per design §9.1)

**Interfaces:**
- Consumes: Tasks 5–6, `ProjectContext` with the existing txid and the new
  `_approve_for_recovery` factory (this task adds it beside `approve_for_project`), `encode_approval_evidence`, `compile_spec`, `build_recovery_snapshot`,
  `_build_descriptor_table`, `AssemblyHalt`/`AssemblyFinding`/`encode_assembly_halt`,
  `require_assembly_halt_binding`, `set_assembly_halt`, `TransactionHalted`.
- Produces: `recover.resolve(binding, store) -> None` — design §9.1's seven phases;
  on return either no active record remains, the record is detached-terminal, or
  a `TransactionHalted` was raised. `root._recovery_lease` calls it where
  `lease._resolve` sat (measured `root.py:56`), and constructs `Lease` only after
  it returns.

Phase mapping, exactly §9.1:

1. If no active record and no chain directory → return (nothing to resolve;
   registration is `register_root`'s job). Otherwise open the chain (ENOENT with an
   active record → `ChainStateInvalid`, as `_registered_root` already encodes),
   `validate_chain` (read-only), and `_derive_reconciliation`. Nothing persisted.
2. Short-circuits, after phase 1 passes: `record.state is HALTED` → raise
   `TransactionHalted` carrying the stored diagnostic; `record.assembly_halt` non-
   null → raise `TransactionHalted` carrying it (verify with
   `require_assembly_halt_binding` first). No mutation, no silent discharge.
3. `_perform_reconciliation` — which exclusively owns survivor application
   (Task 6); `resolve` never calls `apply_survivors` itself. This task makes
   that exclusivity tree-wide: `_registered_root` and `register_root` lose
   their own `_apply_survivors` calls (measured `commands.py:60,156`) and
   validate only — after lease-entry reconciliation a nonempty survivor set
   means post-resolution external mutation, which `_registered_root` refuses as
   `ChainStateInvalid`; their crash-retry tests move to lease-entry fixtures.
   **Then, if no active record exists → return.** A registered root with no
   transaction in flight is the normal state before `append_intent` or a new
   transaction — survivor cleanup was the only work, and every later phase
   reads `record`.
4. `compiled = compile_spec(record.spec)` — pure.
5. **Diff first, proof after.** The exception surface cannot carry the split —
   resolution converts absence into topology data during approval, and a missing
   required ancestor raises an unstructured `ProjectApprovalRefused` with no
   errno cause (measured `fs/judgment.py:110-137`) — so the diff never consults
   `approve_for_project` exceptions. Two substrate changes make the diff
   implementable and its input trustworthy:
   - **The evidence carries a route.** As encoded today a directory is keyed
     `topology_directory:<id>` with no path, parent edge, or component spelling
     (measured `fs/approval.py:123-139`), so nothing could walk to it.
     `ProjectApprovedSpec` gains `directory_paths: tuple[tuple[str,
     TopologyNode], ...]` — factory-set in `approve_for_project` from
     `ResolvedTopology.directory_nodes`, the (path, node) pairs resolution
     already builds (measured `fs/topology.py:95`) — and each directory object
     in `encode_approval_evidence` gains a `"path"` member (project-relative,
     `""` for the project root; `work_root` stays its own member). No production
     data exists; A7a's encoder tests update. Design §11's evidence clause is
     amended with the member (Task 11).
   - **The stored document is decoded closed, at load.** `load_record` today
     passes `approval_evidence` through as an unchecked string (measured
     `store/records.py:573`), so raw SQLite tampering could reach phase 5 as
     malformed JSON and escape as incidental exceptions.
     `fs/approval.decode_approval_evidence(text) -> dict` validates the exact
     canonical shape — the closed key set, exact types, no duplicate keys,
     canonical integers, unique sorted node keys, well-formed paths — and
     `load_record` calls it, translating any refusal to `MetadataStoreInvalid`;
     phase 5 consumes the decoded value, never raw `json.loads`.

   The seam itself: `recover._diff_approved_topology(binding, expected: dict) ->
   tuple[AssemblyFinding, ...]` walks the decoded evidence's directory entries
   by their `"path"`, shallowest-first, with descriptor-relative
   `open_child_directory`/`lstat` lookups from the project root, comparing
   **conditionally by entry class**:
   - **Existing entries** (non-null `identity`): a determinate `ENOENT` →
     `NODE_MISSING`; a determinate non-directory kind → `WRONG_ENTRY_KIND`; a
     determinate `EXDEV` → **`MOUNT_BOUNDARY`**, a new fact-free finding kind
     this task adds to `core/assembly.py`'s closed vocabulary
     (`_FACT_KEYS[MOUNT_BOUNDARY] = ()`): the walk's `open_child_directory`
     carries `RESOLVE_NO_XDEV` (measured `fs/linux.py:111-118`), so a
     bind-mounted child refuses **before** any descriptor exists to read a
     mount id from — and `MOUNT_CHANGED` cannot carry the refusal, because its
     closed fact set requires a canonical `("mount_id", …)` (measured
     `core/assembly.py:35`) that an errno does not supply. The boundary's
     existence is the determinate evidence; probing the foreign mount for an
     id would cross exactly the boundary the resolver refuses to cross.
     `MOUNT_CHANGED` remains the finding for the project root's own mount
     comparison, where the bound descriptor's id is readable. Each of these
     is the node's **sole** finding — the facts the other kinds would carry
     are unreadable behind it. Otherwise compare identity, constraints, mount
     membership, and work-root facts against the expected document and emit
     every applicable changed-kind finding. Design §9.3's vocabulary gains
     `MOUNT_BOUNDARY` as a dated amendment (Task 11).
   - **Planned entries** (`identity = null`, measured `fs/approval.py:170-176`):
     **absent or non-directory emits nothing** — those states are legitimately
     variable at recovery (not yet created, or a foreign blocker) and are
     **A3's** to classify through phase 6's stops and observations. But a
     planned node **present as a directory** is compared on what the evidence
     does pin: constraints and mount membership, identity ignored (none was
     ever persisted) — its constraints can drift independently of every
     ancestor, and letting phase 6's descriptor validation discover that would
     surface as `PreconditionRefused` (measured `descriptors.py:163-175`)
     after the durable `PREPARED`, violating the halt-not-refuse rule.
     Constraint drift there emits `CONSTRAINTS_CHANGED`; a determinate `EXDEV`
     opening the planned child emits `MOUNT_BOUNDARY` under exactly the
     existing-entry rule — the boundary rule is uniform wherever the walk's
     `RESOLVE_NO_XDEV` open refuses, including the `metadata_root/work` open
     for the work-root comparison. `MOUNT_CHANGED` appears only where a bound
     descriptor's mount id is actually readable (the project root's own
     comparison).

   An indeterminate errno — `EIO` and kin — propagates as the `OSError` it is
   (design §9.3: never encoded as drift). Any findings → build the
   `AssemblyHalt` (below), persist it through the narrow path, raise
   `TransactionHalted`.

   **Zero findings → the recovery approval path.** Ordinary
   `approve_for_project` cannot issue this proof: its planned-path judgment
   rejects exactly the A3-variable worlds recovery exists for — a foreign file
   at a planned node fails `_require_removed_before_creation` (measured
   `fs/judgment.py:86-91`) because the frozen spec does not remove an occupant
   that appeared after approval. So `fs/approval.py` gains a second
   factory-owned entry, `_approve_for_recovery(compiled, context: ProjectContext,
   *, evidence: dict) -> ProjectApprovedSpec`: the same resolution, evidence
   construction, and token-guarded proof assembly, but it **skips the
   forward-planning viability judgment of planned paths** (those rules judge a
   plan not yet executed; this plan already ran, and A3 classifies what it
   left) and instead asserts the resolved existing facts match `evidence` —
   raising **`ProjectApprovalRefused`** on a mismatch, the same class its
   resolution raises, so the moved-world rule below has exactly one factory
   signal to catch. The construction token stays module-private, the function itself is
   underscore-private (it deliberately issues a weaker proof from a plain
   `dict`, and `_require_admitted` checks only exact type and binding — it
   cannot tell recovery-issued from fabricated), and Task 10 adds the guard:
   a source scan asserting `_approve_for_recovery`'s **only** production
   caller is `recover.resolve`'s phase 5. `CapabilityUnavailable` propagates.
   Task 11's §9.1 amendment records that recovery's "fresh factory-controlled
   `ProjectApprovedSpec`" is issued by this path.

   **A moved world after the diff halts; it never refuses.** Phases 5–6 read
   the world more than once (the diff walk, the factory's resolution, the
   descriptor build), and #19's rule holds at every one of them: after durable
   `PREPARED`, a deterministic mismatch is an assembly halt, not a refusal. So
   the resolver wraps phases 5–6 in one recovery rule whose catch is **exactly
   `(ProjectApprovalRefused, PreconditionRefused)`** — the factory's
   resolution and evidence comparison both raise the former, the descriptor
   builder's identity/constraint/mount validation raises the latter, and
   `ProtocolError` (or any broader class) is never caught. On catching one, it
   re-runs `_diff_approved_topology`
   **once**: findings now present → the world moved between passes — persist
   the `AssemblyHalt` built from them and raise `TransactionHalted`; still
   zero findings → the two seams deterministically disagree about an unmoved
   world, which is an engine defect — raise `ProtocolError` chained to the
   caught signal. Indeterminate errors propagate untouched, and the
   substrate-invalid classes are never caught. Step 7.1 tests both TOCTOU
   windows with an injected mutation hook: between the diff and the factory's
   resolution, and between the proof and the table build — each ends in a
   persisted halt, never a surfaced refusal.
6. `lease = Lease(_binding=binding, _store=store)` (internal — resolution is past);
   reopen the workspace, build the `DescriptorTable`, observe **every** persistent
   path and **every** effect's required scratch slot in one `Observation`
   universe, `build_recovery_snapshot` from the `StoredRecord` + observations
   (its validators enforce complete coverage — trust them, add none).
   **Recovery descent rule:** `_build_descriptor_table` stops at every
   `ApprovedPlannedDirectory` whatever it observes, records a `WalkStop` only
   for the **first** planned ancestor, and merely marks the subtree unreachable
   (measured `descriptors.py:212-249`) — so a restarted mid-flight transaction
   could not otherwise observe descendants of a directory it already created,
   and `adopt` alone cannot resume the walk. Task 7 therefore adds the concrete
   resume algorithm as `descriptors._resume_descent(table, backend, observation,
   approved, node) -> None` — private, since it is a one-use helper inside the
   resolver's phase 6, not a transaction-stage entry point; its `observation`
   parameter is phase 6's **single** universe, shared with every persistent and
   scratch observation, so descent stops and snapshot observations come from one
   closed token universe — called for each planned-node `WalkStop` whose
   observed entry is a directory:
   1. Open the stop's directory (`open_child_directory` from the stop's
      `parent_fd`/`component`), validate it with the same constraint checks the
      builder applies, and `table.adopt(node, fd)` — ownership passes to the
      table (transfer-or-close on the way in).
   2. Walk the node's descendant directory nodes shallowest-first (the builder's
      `_walk_order`/`_component` machinery over the approved paths mapping,
      restricted to the adopted subtree — every such node is itself planned, so
      each is observed via its now-open parent with its modeled-children set).
      A descendant observed as a directory is opened, validated, adopted, and
      recursed into; observed absent or as a file/symlink it gets a **fresh
      `WalkStop` recorded** (so snapshot coverage exists) and its own subtree
      stays unreachable.
   3. Recompute reachability: nodes adopted in this descent leave
      `_unreachable`; everything below a fresh stop remains.
   A planned node whose original stop observed a file or symlink is never
   descended: the observation feeds the snapshot and A3's classification rules
   on it.
7. `plan = classify_recovery(snapshot)`; then, because a fresh process has never
   installed audit authority, `backend.set_declared_paths(frozenset(p.path for
   p in approved.paths))` — the facade type-checks for an **exact** `frozenset`
   and refuses every declared-effect target while none is installed (measured
   `fs/audit.py:143-148, 387-389`), so without this scope every persistent
   recovery mutation dies as unauthorized — then `run_plan(lease, approved,
   table, plan)` with `clear_declared_paths()` in a `finally`; a returned
   `HaltPlan` raises `TransactionHalted` with its diagnostic. (The
   reconciliation appends of phase 3 correctly run before authority is
   installed: scratch and chain targets classify as
   `ENGINE_SCRATCH`/`CHAIN_BOOKKEEPING` and need no declared scope.)

The `AssemblyHalt` diff: over the closed-decoded expected document and the
observed facts the diff walk gathered; for each directory node in
the union, emit findings under the closed vocabulary with `NODE_MISSING`/
`WRONG_ENTRY_KIND` as a node's **sole** finding when applicable, else every
applicable changed-kind finding; `MOUNT_CHANGED`/`WORK_ROOT_CHANGED` from the top-
level members (`MOUNT_BOUNDARY` instead when the work-root open itself refuses
with a determinate `EXDEV`); order by `(path, finding-kind enum order)`; `expected` is
`record.approval_evidence` verbatim. The narrow persistence path is one function,
`recover._persist_assembly_halt(store, halt)` — it takes **no proof** (that is the
point: the proof is exactly what could not be issued) and is the architecture
registry's one recorded exception (Task 10 pins it).

- [ ] **Step 7.1: Failing tests.** Resolution: a clean store with no active record
  resolves to a no-op (lease enters, no `NotImplementedError` anywhere — grep the
  tree in the test); a **registered idle root** — genesis present, no active
  record — resolves to a return after survivor cleanup (append a genesis, crash
  no transaction, re-enter: no error, chain intact, nothing persisted; this is
  the state every `append_intent` call passes through); a mid-flight `PREPARED`
  record with untouched world rolls
  back to `ROLLED_BACK` + settled + detached on fresh lease entry; a `HALTED`
  record short-circuits with its stored diagnostic (and phase order: the same
  record with a corrupted chain entry raises `ChainStateInvalid` instead);
  an assembly-halted record short-circuits unchanged. Assembly halt: move the
  approved parent directory aside between prepare and lease re-entry → lease
  entry raises `TransactionHalted`; the persisted `assembly_halt` decodes to
  findings `[IDENTITY_CHANGED]` (or `NODE_MISSING` for removal) at the right
  path; transaction state, journals, and `active` are byte-unchanged; a second
  lease entry returns the same halt without touching the world (no silent
  discharge, freeze trigger holds). The `EIO` split is tested with an injecting
  backend raising `OSError(EIO)` during re-resolution → the `OSError` propagates
  and **nothing** is persisted. Run: expect failures on the missing `resolve`.
  The descent rule gets its own tests: a mid-flight `CreateDirectory`
  transaction killed after publication recovers with the created directory's
  descendants observed (a nested effect's slot appears in the snapshot); a
  planned node occupied by a foreign file at recovery **reaches phase 6** — the
  diff emits nothing for it and `_approve_for_recovery` still issues the proof —
  stays a stop, and the classification rules on it (this is the test ordinary
  `approve_for_project` would make unpassable). The conditional planned
  comparison gets its own pair: a planned node present as a directory with
  drifted constraints (or behind a bind mount) persists an `AssemblyHalt` with
  `CONSTRAINTS_CHANGED`/`MOUNT_BOUNDARY` at the planned path — never a
  `PreconditionRefused` from the table builder — while the same directory with
  intact constraints resolves and recovers normally. The mount findings are
  exercised for real where the harness allows: a bind mount over an approved
  directory via `tests/fs_support.find_distinct_mount` (skip with the named
  reason when unavailable), asserting the `EXDEV` route produces
  `MOUNT_BOUNDARY` — not a mocked `read_mount_id`. The unrecognized-kind
  path is pinned here for fresh recovery: a FIFO planted at a covered
  persistent slot before lease re-entry resolves to a persisted factory
  `HaltPlan` (record `HALTED`, evidence preserved) — never a `ProtocolError`
  from the moved-world rule, because the observation returns
  `ObservedUnrecognized` instead of refusing and the halt comes from
  classification, not assembly.

  Two more test families this step owns:
  - **Hostile stored evidence**, parametrized: duplicate JSON keys, noncanonical
    bytes (unsorted keys, whitespace), duplicate paths or node keys, a node/path
    inconsistency, wrong member types, and invalid enum/fact values — each read
    back through `load_record` raises `MetadataStoreInvalid`, never an
    incidental `KeyError`/`TypeError` (fabricate via direct `sqlite3` writes).
  - **Resource lifetimes on raising branches.** The resolver owns a chain fd, a
    workspace, a `DescriptorTable`, and `Observation`s across many raising
    paths; each owned resource is context-managed (or closed in `finally`), and
    a `descriptor_count` before/after assertion wraps a lease entry that exits
    through each failure class — `ChainStateInvalid` in phase 1, the phase-2
    short-circuits, an `AssemblyHalt` in phase 5, and a `HaltPlan` in phase 7 —
    proving no descriptor leaks on any of them. The phase-7 declared-path
    scope is a lifetime too: after a lease entry exits through the `HaltPlan`
    raise, the facade's declared set is empty again (a declared-effect
    mutation through the same facade refuses).
- [ ] **Step 7.2: Implement** phases 1–7 as one `resolve` function calling private
  per-phase helpers in order, with an early return per short-circuit; wire
  `root.py`; delete `lease._resolve`; convert the A5b trap tests.
- [ ] **Step 7.3:** Task tests pass; full gate — the A5b lease/process tests must
  still pass with the trap gone (`tests/coordinator_child.py`'s `"trapped"` arm
  now reports `None`; update its assertions).
- [ ] **Step 7.4:** `git commit -m "feat(recover): pinned-phase resolution with chain reconciliation and the assembly halt"`

---

## Task 8: The forward spine, the commit half, and `run_transaction`

**Files:**
- Create: `python/src/atoms/coordinator/execute.py`
- Create: `python/src/atoms/coordinator/commit.py`
- Modify: `python/src/atoms/coordinator/commands.py` (`run_transaction`,
  `TransactionOutcome`, `__all__`)
- Test: `python/tests/test_coordinator_execute.py`,
  `python/tests/test_coordinator_commit.py`, `python/tests/test_coordinator_run.py`

**Interfaces:**
- Consumes: everything above — including Task 6's `_registration_entry` — plus
  `admit`, `capture_initial_surface`, `prepare_transaction`, `open_workspace`,
  `set_declared_paths`/`clear_declared_paths`, `set_registration_digest`,
  journal/state setters. The consumer tag and frozen-intent digest travel
  **inside the spec** (`spec.consumer_tag`/`spec.intent_digest`, validated by
  `compile_spec` — measured facts above); `run_transaction` takes no separate
  tag argument.
- Produces (the public seam):
  - `commands.TransactionOutcome(txid: str, outcome: ChainOutcome,
    registration: str, settlement: str)` — frozen dataclass, **public**: it is
    the return type consumers hold, so `__all__` becomes
    `("TransactionOutcome", "append_intent", "register_root",
    "run_transaction")` (Task 10 asserts the same tuple).
  - `commands.run_transaction(backend, project_root, metadata_root, storage,
    spec: TransactionSpec, payloads: PayloadSource) -> TransactionOutcome`.

The spine (design §6, numbered as there):

```python
def run_transaction(backend, project_root, metadata_root, storage,
                    spec, payloads):
    compiled = compile_spec(spec)
    with _recovery_lease(backend, project_root, metadata_root, storage) as lease:  # 1
        _require_chain_publication(lease._binding.evidence)
        with _registered_root(lease) as (chain_fd, validated):                     # 1: preflight
            result = _run_under_lease(
                lease, chain_fd, validated, compiled, payloads
            )
    return TransactionOutcome(                             # the public type is built HERE,
        txid=result.txid,                                  # from commit._CommitResult —
        outcome=ChainOutcome.COMMITTED,                    # never below commands.py
        registration=result.registration,
        settlement=result.settlement,
    )
```

`execute._run_under_lease`, steps 2–9: `admit` (2) → `open_workspace` +
`capture_initial_surface` (3) → `prepare_transaction` (4, PREPARED COMMIT — the
spec, tag and intent digest included, is durable in `spec_json` from here) →
`set_declared_paths(frozenset(p.path for p in approved.paths))` — the facade
refuses anything but an exact `frozenset` (measured `fs/audit.py:143-145`) →
`append_entry` of
`_registration_entry(spec, approved.txid)` (5 — the builder is Task 6's,
imported from `recover`: txid, `spec.intent_digest`, `spec.consumer_tag`,
initial/final = `state_to_json` of the surface states restricted to
`spec.registered_paths` in sorted order, `fulfills=spec.fulfills`) →
`set_registration_digest` in one store transaction (6) → `APPLYING` transition
(7) → per effect in `compiled` order: `STARTED` journal COMMIT → `_site_for` →
the variant module's `apply` (a `CreateDirectory` return descriptor is adopted
via `table.adopt(node, fd)` under transfer-or-close: the spine closes it if
adoption raises) → `DONE` journal COMMIT (8) → `APPLIED` transition (9).
`clear_declared_paths()` in a `finally` whose `try` spans through the catch,
so `_roll_back`'s `RESTORE_PRE` transforms mutate declared paths under the
same installed authority.

`commit.py`, split across the catch boundary (see the catch below) — note
**no `validated` parameter on either half**: the proof taken at entry is stale
the moment the registration append lands (`append_entry` refuses a proof whose
history no longer matches, measured `chain/append.py:224-226`), so every later
append works from a fresh proof taken under the still-held `chain_fd`
immediately before it.
- `commit.verify_committed_surface(lease, approved, table)` — step 10: one
  fresh `Observation` universe observing the **complete compiled final surface
  first, then the complete scratch vector**; any disagreement raises
  `EffectMismatch` (a caught failure; the plan loop rolls back). Runs as the
  `try` block's last statement.
- `commit.finalize_commit(lease, approved, table, chain_fd) -> _CommitResult`
  — steps 11–14, **outside** the catch. `_CommitResult` is a commit-internal
  frozen value `(txid, registration, settlement)`: the public
  `TransactionOutcome` belongs to `commands.py`, and the import direction is
  `commands → execute → commit`, so the lower layer returns internal digests
  and `commands.run_transaction` constructs the public dataclass — never a
  reverse import. The steps: `COMMITTED` state +
  commit decision in **one** store transaction (11) → fresh
  `validate_chain(backend, chain_fd)` → `settled(committed)` append against
  that fresh proof, referencing the bound registration digest (12) →
  settlement binding COMMIT (13) → committed cleanup and detach **through the
  plan loop** (14): assemble a fresh snapshot, `classify_recovery`
  (disposition `COMMITTED_CLEANUP`), `run_plan` — the detach stop finds the
  settlement already bound and detaches. (The rollback arm's settlement goes
  through `run_plan`'s `_reconcile_settlement`, which already opens and
  validates its own chain view.)

The catch (design §6, last paragraph), in `execute.py` — **the `try` covers
steps 5–10 only**; the commit decision and everything after it (11–14) sit
outside, because a failure past `COMMITTED` must preserve the committed arm for
recovery, never enter rollback:

```python
try:
    ...steps 5-10...                     # registration through the two proofs
except (ChainStateInvalid, MetadataStoreInvalid):
    raise                                # substrate-invalid: no rollback, evidence preserved
except BaseException as caught:          # KeyboardInterrupt and SystemExit included
    _roll_back(lease, approved, table, chain_fd, caught)   # returns after restoring;
                                                           # raises only on halt/failure
    if isinstance(caught, (EffectMismatch, PreconditionRefused)):
        raise PreconditionRefused(str(caught)) from caught
    raise
return finalize_commit(lease, approved, table, chain_fd)   # steps 11-14, outside the
                                                           # catch; _CommitResult —
                                                           # commands builds the outcome
```

This is why `commit.py` splits in two: `verify_committed_surface(lease,
approved, table)` is step 10 — the two proofs — called as the `try`'s last
statement; `finalize_commit(lease, approved, table, chain_fd)` owns steps
11–14. A single function owning 10–14 cannot be placed on either side of the
catch correctly. An exception inside `finalize_commit` propagates as-is; a
fresh lease entry finds the durable `COMMITTED` decision and finishes
settlement, binding, cleanup, and detach through resolution (Step 8.1 tests
exactly this: a post-`COMMITTED`, pre-settlement exception leaves the record
committed, and re-entry converges on the commit arm).

`_roll_back` first **reconciles registration under the held `chain_fd`** —
Task 6's derive-then-perform pair, whose backfill window (`PREPARED`, every
journal `PENDING`) is exactly this state — because a caught failure at step 5
or 6 leaves `registration_digest` NULL (possibly with a staging survivor or a
published unbound entry), and the schema refuses **every** transition away
from `PREPARED` while it is NULL, so the `ROLLING_BACK` transition would be
structurally impossible. Then everything observational happens **inside one
`with Observation(backend) as observation:` block** — re-observing the
planned-node stops, running `_resume_descent` for any stop whose entry is now
a directory (a `CreateDirectory` that published before the failure; without
this the stale stopped table cannot cover what the transaction already
created), observing every persistent path and scratch slot, and
`build_recovery_snapshot` — because `_resume_descent`'s fresh `WalkStop`
identities and the snapshot's observations must come from one closed token
universe, not two. Step 8.1 asserts exactly one `Observation` is constructed
across a caught rollback (count constructions via a monkeypatched counter).
Only then: classify (`ROLL_BACK`), run the plan loop to `ROLLED_BACK` +
`settled(rolled-back)` + binding + detach, and let the refusal or original
exception surface (authority §11: `PreconditionRefused` only after restoration
is proved). A `HaltPlan` from the loop raises `TransactionHalted` instead.

- [ ] **Step 8.1: Failing tests.** `test_coordinator_run.py`: a clean two-effect
  transaction returns `TransactionOutcome` with `outcome is ChainOutcome.COMMITTED`,
  the world holds the final surface, the chain holds genesis + registered + settled
  in that order, both digests bound, `active` empty, scratch slots gone;
  `run_transaction` on an unregistered root refuses **before** any metadata write
  (assert store byte-identical); `spec.consumer_tag`, `spec.intent_digest`,
  `fulfills`, and the `registered_paths` projection land in the
  `RegisteredEntry` verbatim; a mid-apply verification failure (foreign swap
  beneath the facade between two effects) rolls back — world restored
  byte-for-byte, record `ROLLED_BACK`, `settled(rolled-back)` appended and bound,
  `PreconditionRefused` raised; `KeyboardInterrupt` injected beneath the facade
  rolls back then re-raises `KeyboardInterrupt`; a `ChainStateInvalid` planted
  mid-apply propagates with **no** rollback mutation. The registration crash
  window, caught in-process (not killed): an exception raised (i) before the
  registration append, (ii) during append publication (leaving a staging
  survivor), and (iii) between the append and the binding — each rolls back
  cleanly because `_roll_back` reconciled registration first; without that the
  `ROLLING_BACK` transition is trigger-refused (assert the world restored and
  both digests bound in the final record). The mkdir descent case: a foreign
  entry inserted (beneath the facade) into a published `CreateDirectory`
  between publication and verification rolls back with the created directory's
  descendants observed — `_roll_back`'s `_resume_descent` pass, not the stale
  stopped table. `test_coordinator_commit.py`:
  the proof order is observable — inject a fault that makes the scratch proof fail
  and assert the final-surface proof already ran (call recording beneath the
  facade); commit decision and state land in one transaction (kill between them
  is impossible — assert via the store's single-COMMIT counter, measured
  `_run_barrier`); the catch boundary holds — an exception injected inside
  `finalize_commit` after the `COMMITTED` transaction (pre-settlement)
  propagates without any rollback mutation, the record stays committed, and a
  fresh lease entry converges on the commit arm (settlement appended, bound,
  cleaned, detached). The clean-refusal contract holds against forward races:
  a beneath-facade hook that removes the live entry immediately before
  replace's `exchange` makes `run_transaction` roll back fully and raise
  `PreconditionRefused` chained to the converted mismatch — never a raw
  `OSError` (design §11: once mutation may have begun, the refusal comes only
  after restoration, `2026-07-23-...-design.md:1275-1283`). The
  unrecognized-kind path is pinned here for caught rollback: a FIFO planted
  beneath the facade at a covered slot mid-flight drives `_roll_back`'s
  re-observation into a classified halt — `TransactionHalted` with the
  record `HALTED` and evidence preserved, never an escaping
  `PreconditionRefused` before restoration is proved. Lifetimes: the
  spine's owned resources — the `Workspace`,
  `Captured`'s descriptor table, adopted `CreateDirectory` fds, the chain fd —
  are context-managed or `finally`-closed, and a `descriptor_count`
  before/after assertion wraps a run exiting through each class (clean commit,
  caught rollback, substrate-invalid propagation): no descriptor leaks on any
  of them. Run: expect import failures.
- [ ] **Step 8.2: Implement** as specified. The APPLYING transition must be refused
  by the schema while the registration digest is null — do not pre-check it; let
  the trigger own the fact (a test drops the binding write and asserts the
  trigger's `IntegrityError` surfaces as `MetadataStoreInvalid` through the
  store's translation).
- [ ] **Step 8.3:** Task tests pass; full gate.
- [ ] **Step 8.4:** `git commit -m "feat(coordinator): the run_transaction spine with commit, rollback, and settlement"`

---
## Task 9: The SIGKILL recovery matrix

**Files:**
- Create: `python/tests/execute_child.py` (the killable child)
- Create: `python/tests/test_coordinator_kill_matrix.py`
- Modify: `python/tests/fs_support.py` (`KillingBackend`)

**Interfaces:**
- Consumes: `run_transaction`, the `coordinator_child.py` JSON-over-stdout pattern
  (measured), `run_in_subprocess`-style plumbing from `tests/test_coordinator_process.py`.
- Produces: `KillingBackend(inner, *, method: str, countdown: int)` — delegates
  every `Backend` member; when the named method's call count reaches `countdown`,
  it sends `SIGKILL` to its own process **before** invoking the inner call
  (cut-before) or after it returns (cut-after, `countdown < 0` convention:
  `abs(countdown)`-th call, kill after). Injected beneath the facade by passing it
  as `run_transaction`'s `backend` argument — the composition root wraps it in
  `AuditedBackend`, so the injection point is exactly design §14's "beneath the
  facade". **Counts are anchored, not global**: lease entry, binding, capture,
  and chain bootstrap all call the same methods, so a raw call count silently
  shifts when setup changes. Each kill site is therefore derived from a recorded
  rehearsal: the fixture first runs the identical transaction un-killed on
  separate roots with a call-recording backend, the test asserts the recorded
  per-method event sequence matches a constant embedded in the test (a shift
  fails loudly, naming the drift), and the countdown for the named cut is read
  off that recorded sequence.

**The matrix.** Kill sites are named, not sampled — one parametrized test id per
cut:

- Backend barriers, per effect variant: before/after each `flush_file`,
  `flush_directory`, `exchange`, `transfer_noclobber`, `link_anchor` the forward
  sequences issue (the counts come from Tasks 1–3's pinned sequences).
- Store COMMIT barriers, **both sides of every one** (the design demands
  before/after each journal COMMIT, and `_run_barrier` runs *before* the lease
  gate and the SQLite `COMMIT` — measured `store/connection.py:795-806` — so
  one hook cannot cut both sides). Two child wrappers: the **pre-commit** cut
  monkeypatches `atoms.store.connection._StoreTransaction._run_barrier` to
  count invocations and `SIGKILL` before the nth; the **post-commit** cut
  wraps the store's `_connection` (the repository's existing proxy pattern) to
  detect the `_COMMIT` statement and `SIGKILL` immediately after it returns,
  before control reaches the caller.
  Both cuts are enumerated per barrier: PREPARED, registration binding,
  APPLYING, each STARTED/DONE, APPLIED, COMMITTED, settlement binding, detach.
- Chain barriers: registration and settlement appends killed between staging
  create/fsync/transfer/directory-fsync (drive `KillingBackend` counts through the
  append sequence, measured `chain/append.py:211-254`).
- Both terminal arms: including between settlement append and binding, and between
  binding and detach; and mid-rollback (kill inside a `RESTORE_PRE` transform).
- Compensation barriers: for each in-process compensation (replace's
  exchange-back, create-file's `EEXIST` staging removal, delete's tombstone
  return, move's rename-back and anchor removal, mkdir's work-slot removal),
  a kill **between the compensation mutation and its `flush_directory`** —
  recovery must converge from the unflushed state, which is exactly why the
  forward modules flush before raising. A clean run never executes a
  compensation, so these cuts cannot be counted off the clean rehearsal: each
  cut's child configures the **same adverse scenario its Task 1–3 unit test
  defines**, injected beneath the facade **mid-run**: for create-file's and
  mkdir's `EEXIST`, a hook that plants the live occupant immediately before
  the publication `transfer_noclobber` — planting it before `run_transaction`
  would refuse at capture (step 3, before `PREPARED`), and the compensation
  would never execute; for replace's verification failure, delete's
  tombstone-validation failure, and move's anchor-validation and
  post-transfer identity failures, the corresponding tamper hook — with
  `execute_child`'s env JSON naming the scenario alongside the kill site. The rehearsal for such a cut runs that
  identical adverse scenario un-killed, asserts the recorded sequence actually
  contains the compensation's own events (the exchange back, the staging
  unlink, the tombstone-return transfer, the rename back, the anchor unlink,
  the work-slot rmdir), and reads the countdown off that adverse rehearsal.

Each case: (1) child runs `run_transaction` with the kill configuration from an
env-passed JSON and dies; (2) parent asserts the child was killed (exit signal 9);
(3) parent runs a fresh lease entry in a second subprocess (the existing
`coordinator_child._lease_phase`) and asserts convergence — `active` is `None`
with the world at the committed final surface or fully restored preimage, or a
`TransactionHalted` with an explained diagnostic; (4) a third pass produces
byte-identical durable state and chain digests (idempotence). Which terminal arm a
cut converges to is asserted per site from the design's barrier semantics: cuts
strictly before the COMMITTED COMMIT roll back; cuts at or after it finish the
commit arm.

- [ ] **Step 9.1:** Write `KillingBackend` + one smoke case (kill after PREPARED;
  recovery rolls back; idempotent second pass) and run it: expect failure only
  until the plumbing works — the engine code is already landed.
- [ ] **Step 9.2:** Fill the matrix as parametrized cases; every site listed above
  appears in `pytest --collect-only` output by name.
- [ ] **Step 9.3:** Full gate (this suite is the slow one; it still runs in the
  default gate — no skip markers).
- [ ] **Step 9.4:** `git commit -m "test(coordinator): SIGKILL recovery matrix across every forward and terminal barrier"`

---

## Task 10: Executor–A3 conformance and the architecture guards

**Files:**
- Create: `python/tests/test_coordinator_conformance.py`
- Modify: `python/tests/test_coordinator_architecture.py`,
  `python/tests/test_fs_architecture.py` (only if the public-surface expectation
  lists `commands.__all__`)

**Interfaces:** consumes `apply_recovery_plan` (A3's fixed point, measured
`reducer.py:124`) and the executor.

- [ ] **Step 10.1: Conformance.** For each family — clean commit, caught-failure
  rollback, and every `tests/recovery_support.py` fixture family that Tasks 5–7
  exercised (`make_replace_transform_case`, `make_delete_case`, `make_move_case`,
  `make_create_file_case`, the halt cases) — run the executor against a real
  prepared state, then rebuild the A3 snapshot from the **durable** result and
  assert: the executor's terminal `StoredRecord` projection (state, decision,
  rollback result, journal vector, active flag) equals
  `apply_recovery_plan(snapshot_before, classify_recovery(snapshot_before))` (the reducer takes both the snapshot and the plan — measured `reducer.py:123-129`). The full
  model/real/persistence-cut agreement matrix stays A8's (ledger #15) — this test
  pins the fixed points only.
- [ ] **Step 10.2: Architecture.** Add to the existing guard files:
  - `commands.__all__` is exactly `("TransactionOutcome", "append_intent",
    "register_root", "run_transaction")`, every public command's signature
    accepts neither `Lease` nor `ProjectApprovedSpec` (source scan of
    `commands.py`), and each acquires `_recovery_lease` in its body.
  - `set_assembly_halt` is called from exactly one production site,
    `recover._persist_assembly_halt`, and that function's parameters include no
    proof type — recorded as the registry's one exception with a comment naming
    design §9.3.
  - `_TRANSACTION_STAGE_ENTRY_POINTS` (measured
    `test_fs_architecture.py:1126-1143`) gains **every** new proof consumer
    the source scan will find — `settle.apply_transform`,
    `settle.apply_remove_scratch`, `recover.run_plan`,
    `transitions.persist_detach`, `commit.verify_committed_surface`, and
    `commit.finalize_commit` — each opening with `_require_admitted` as its
    first statement; a source scan asserts no production call site constructs
    or forwards a raw `TransformEffectTuple`/`RemoveScratch` into the `settle`
    pair (they demand `AuthorizedStep`). One-use helpers below these entry
    points (`_resume_descent`, `_diff_approved_topology`, `_site_for`) stay
    genuinely private — underscore-prefixed and unregistered.
  - The proof-consumer scan goes recursive: `test_no_unregistered_public_function_accepts_the_proof`
    walks `coordinator` with `glob("*.py")` today (measured
    `test_fs_architecture.py:1153`), which never reaches
    `coordinator/effects/settle.py` — change it to `rglob("*.py")` so the
    registered set and the found set can actually match, and nested consumers
    cannot evade the guard.
  - `_approve_for_recovery` has exactly one production caller —
    `recover.resolve`'s phase 5 — asserted by source scan (it issues a weaker
    proof from a plain `dict`; the scan is what keeps that power scoped).
  - Effects modules import no `atoms.chain`, no `atoms.coordinator.execute`/
    `commit`/`recover`/`commands` (syscall execution only, design §4); `execute`/
    `commit`/`recover` never read `TransactionSpec.dependencies` (attribute scan —
    ledger's "dependencies unread by execution modules").
  - The facade-only sweep needs **no** change — assert (in the test file, as a
    comment-free check) that the sweep's module walk already covers
    `coordinator/effects/`.
- [ ] **Step 10.3:** Full gate.
- [ ] **Step 10.4:** `git commit -m "test(coordinator): executor-A3 conformance and the A7b architecture guards"`

---

## Task 11: Roadmap, docs, and the ledger — runs LAST

**Files:**
- Modify: `python/tests/test_docs_status.py` (`FIRST_UNIMPLEMENTED = "A8"`)
- Modify: `docs/plans/2026-08-13-a7-effect-recovery-execution-design.md` (status)
- Modify: `docs/plans/2026-07-23-recoverable-fs-effect-engine-design.md` (status)
- Modify: `docs/deferred-obligation-ledger.md`, `README.md`, `AGENTS.md`

- [ ] **Step 11.1:** Flip `FIRST_UNIMPLEMENTED` to `"A8"`. Run
  `uv run pytest tests/test_docs_status.py -x`: expected **FAIL** — the guard now
  demands the docs claim A7b implemented; the failures list exactly the claims to
  update.
- [ ] **Step 11.2:** Update each failing claim: the A7 design's status header
  (implemented, with the landing date), the authority design's status field
  ("A8–A9 … remain"), README and AGENTS status sections. Grep both docs trees for
  "A7b" and "A7–A9" to catch propagation beyond what the guard names (per the
  design-doc drift rule). In the same commit, land the six design amendments
  this plan carries as candidates: **§9.1** — the phase-5 proof is issued by the
  factory-owned `_approve_for_recovery` path, which admits A3-variable planned
  states and skips forward-planning viability judgment (Task 7); **§9.3** — the
  finding vocabulary gains the fact-free `MOUNT_BOUNDARY` kind for determinate
  `EXDEV` at a child of an approved directory (Task 7); **§9.2** — a
  reconciliation append is rebuilt
  deterministically from the durable `spec_json` (the spec's own
  `consumer_tag`/`intent_digest`), and a finished staging survivor satisfies the
  append (Task 6); **§11** — the canonical recovery-approval evidence carries a
  `"path"` member per directory node, routed from `directory_paths` on the
  proof (Task 7); **§7** — the recovery-mutation surface is the A3-authorized
  six pairs, and the closing paragraph's staged re-creation/symlink-restore
  cases are unreachable through A3's classifier today, so the executor does not
  implement them (Task 4); **§9.1 (observation phase)** — recovery observation
  is total over entry kinds: an entry that is neither file, symlink, nor
  directory observes as `ObservedUnrecognized` and classifies to a `HaltPlan`,
  never a refusal, once a durable record exists (Task 5). Each amendment is one
  dated note in the design, same
  shape as the 2026-08-13 §5.1 amendments.
- [ ] **Step 11.3:** Ledger: mark the A7 halves of #1, #3, #8, #12, #13, #14, #17,
  #19 and all of #24–#29 discharged, each naming the suite that covers it (the
  design §14 bullet names map: per-variant tests → #1/#3/#8/#12, kill matrix →
  #17, authorization loop → #13/#14, assembly halt → #19, chain suites →
  #24–#29). #15 stays open, owned by A8. #18's architecture assertion is
  unchanged.
- [ ] **Step 11.4:** Walk design §15's six acceptance criteria against the tree and
  record each as met in the design's status note (criterion → suite name).
- [ ] **Step 11.5:** Full gate: `uv run pytest && uv run ruff check . && uv run pyright`.
- [ ] **Step 11.6:** `git commit -m "docs(a7b): move the roadmap boundary to A8 and discharge the A7 ledger halves"`

---

## Self-review

**Spec coverage.** Design §6 → Task 8 (spine steps 1–15, the catch, the
substrate-invalid exclusion, genesis preflight). §7 → Tasks 1–4 (five forward
sequences verbatim; the recovery-mutation surface narrowed to the A3-authorized
six pairs, recorded as a §7 amendment in Task 11). §8 → Task 5 (loop, fresh
per-step observation, halt-never-reclassified, detach prefix stop with settlement
reconciliation). §9.1 → Task 7 (seven phases, order pinned). §9.2 → Task 6 (every
case a test; two-pass validation so an interrupted append's survivor finishes).
§9.3 → Task 7 (halt build/persist/surface; the determinacy split carried by the
dedicated diff seam, not exceptions). §10.4's registered/settled payloads →
Tasks 6/8. §11's triggers and schema are A7a's, untouched; Task 8 deliberately
leans on them (the APPLYING gate test); Task 7 amends only the evidence
encoding (the `"path"` member). §12 → `EffectMismatch` is internal;
`ChainStateInvalid`/`TransactionHalted`/`PreconditionRefused` surfaced exactly
as specified. §13 item 8 → Task 7; items 1–7 and 9–13 landed in A7a (verified
against the tree — the trap at `lease.py:59` was the one remainder). §14 →
Tasks 9 (kill matrix with rehearsal-anchored cuts), 10 (conformance,
architecture), 1–4 (per-variant fault injection). §15 criteria → Task 11 step
11.4. §16's gaps stay open and named for A8/A9.

**Placeholders.** None found on the closing scan: every step names its files,
signatures, refusal types, and expected run outcomes.

**Type consistency.** `run_plan(lease, approved, table, plan)` is consumed with
that shape in Tasks 7 and 8; `_site_for(approved, table, effect)` in Tasks 4, 5
and 8; `_registered_root` keeps its measured `(chain_fd, ValidatedChain)` yield
across Tasks 5–8; `AuthorizedStep` flows `authorize_recovery_step` → `run_plan`
→ `_execute_mutating` → `settle.*` with no raw-step bypass; `TransactionOutcome`
fields match Task 8's construction and Task 10's `__all__` assertion;
`_registration_entry(spec, txid)` has one definition (Task 6) and two consumers
(Task 6's reconciliation appends, Task 8's forward append), both reading the
tag and digest from the spec itself; `DescriptorTable.adopt` and
`_resume_descent` are defined once (Tasks 3 and 7) with named consumers.

**Known open decisions surfaced to the executor-of-this-plan:** the two the tree
left open (`transitions.py`'s stop set and the projection-comparison comment) are
decided in Task 5's "Decisions" block; the six design amendments (§9.1 recovery approval; §9.3 MOUNT_BOUNDARY; §9.2 rebuild
and survivor-satisfies-append; §11 evidence path member; §7 narrowing to the
A3-authorized pairs; §9.1's observation phase total over entry kinds) are
decided in Tasks 4–7 and land dated in Task 11
step 11.2.

## First-round findings closed (2026-08-13)

1. The Task 4 table is now the measured A3-authorized six pairs with slot-delta
   semantics, fixture-census sub-cases, per-cell durability flushes, and
   `ProtocolError` everywhere else; the invented fallbacks are gone and the §7
   narrowing is a named design amendment.
2. The staged `registration.entry` carrier is gone — `promote_staging`'s
   exact-manifest discipline made it impossible. (This round's schema-v3
   replacement was itself superseded in the second round: the spec already
   carries the tag and digest durably.)
3. Reconciliation is explicitly two-pass: read-only history validation with
   `planned=()`, then revalidation with the derived envelope so an interrupted
   append's survivor classifies `FINISH`.
4. `DescriptorTable.adopt` is a specified seam (Task 3) consumed by the forward
   spine under transfer-or-close and by Task 7's recovery descent rule through
   created planned directories.
5. Phase 5 diffs through a dedicated `_diff_approved_topology` walk of the
   decoded evidence — determinate absence/kind drift becomes findings,
   indeterminate errnos propagate — and never parses approval exceptions.
6. `AuthorizedStep` is threaded end-to-end; `settle.*` are proof-accepting and
   registry-listed.
7. `build_staged_file` and `create_directory.apply` carry failure-complete
   descriptor ownership with injected-failure descriptor-count tests.
8. Minors: the pre-transfer anchor failure removes the attributable anchor
   (rename-back only after publication); `TransactionOutcome` is public and in
   both `__all__` assertions; kill counts are rehearsal-anchored.

## Second-round findings closed (2026-08-13)

1. Schema v3 is deleted: `consumer_tag` and `intent_digest` are required v1
   `TransactionSpec` members, compiler-validated and durable in `spec_json`
   (authority §13.4 says exactly this). `_registration_entry(spec, txid)` reads
   them from the spec; `run_transaction` takes no tag argument; no store or
   prepare changes; the never-needed `intent_digest()` helper (which would have
   produced bare hex where the chain requires `sha256:<hex>`) is gone.
2. The canonical evidence gains a `"path"` member per directory node, routed
   from a new `ProjectApprovedSpec.directory_paths` field fed by
   `ResolvedTopology.directory_nodes` — without it `_diff_approved_topology`
   had nothing to walk. Design §11 amendment added to Task 11.
3. `decode_approval_evidence` is a closed canonical decoder; `load_record`
   validates the stored document through it and translates malformed evidence
   to `MetadataStoreInvalid`; phase 5 never touches raw `json.loads`.
4. `_perform_reconciliation` exclusively owns survivor application: phase 1 and
   `resolve` never call `apply_survivors`; a survivor finished by the planned
   revalidation satisfies the `APPEND` action, and `append_entry` runs only
   when the derived digest is still absent — no removal of interrupted appends,
   no duplicates.
5. Task 4's deltas are the variant factories' own: replace-restore is exactly
   the exchange back (staging cleanup is `RemoveScratch`), create-file-remove
   transfers the creation back onto the staging slot (never a live unlink), and
   two-parent durability follows the reverse-move mirror — restored-source
   parent before old-destination parent.
6. `resume_descent` is the concrete recovery-resume algorithm in
   `descriptors.py`: open-validate-adopt at the stop, shallowest-first descent
   over the adopted subtree with fresh `WalkStop`s for non-directory
   descendants, and reachability recomputed — `adopt` alone was not a walk.
7. `run_plan` catches `EffectMismatch` from a recovery mutation and re-enters
   `authorize_recovery_step` at the same cursor: a `HaltPlan` is persisted and
   returned; a still-at-`expected_before` world permits one re-execution, and a
   second failure there is `ProtocolError`. The private signal is never
   observable from `run_plan`'s callers.

## Third-round findings closed (2026-08-14)

1. `_perform_reconciliation` takes `(backend, store, chain_fd, validated,
   actions)` — its actual dependencies — because the resolver calls it in
   phase 3, before the internal `Lease` exists (phase 6).
2. Phase 3 ends with an explicit return when no active record exists: a
   registered idle root — the normal state before `append_intent` or a new
   transaction — stops after survivor cleanup instead of falling through to
   `record.spec` on `None`. Step 7.1 gains the registered-idle-root test.
3. Survivor-application exclusivity is tree-wide: Task 7 strips
   `_apply_survivors` from both `_registered_root` and `register_root`
   (validate-only; post-resolution survivors are `ChainStateInvalid`), with
   their crash-retry tests moved to lease-entry fixtures. Task 5's move stays
   byte-identical so the intermediate tree remains green.
4. `_diff_approved_topology` walks only evidence entries with non-null
   `identity`: planned directories (identity = null) are legitimately absent,
   transaction-created, or foreign-blocked — A3's classification territory via
   phase 6, never assembly drift — which keeps the planned-node-stays-a-stop
   case reachable.
5. `commit_prepared` drops the stale `validated` parameter; the settlement
   append works from a fresh `validate_chain` under the held `chain_fd`
   immediately before appending, since `append_entry` refuses a proof whose
   history moved at the registration append.
6. The move and mkdir cells now match the factories exactly: restore is the
   destination→source transfer alone, both dual-name and anchor-only shapes
   are repair, no transform removes the anchor (the following `RemoveScratch`
   does), and mkdir-remove targets the live directory only.
7. The one-retry rule is gone: after a post-mutation `EffectMismatch`, the loop
   reauthorizes once solely to obtain the factory `HaltPlan`; a returned
   `AuthorizedStep` is `ProtocolError` immediately, per ledger #14's
   no-retry/no-reclassify clause.
8. `settle.apply_*` accept `Lease`, open with `_require_admitted`, and are
   enumerated in `_TRANSACTION_STAGE_ENTRY_POINTS` along with `run_plan`;
   `resume_descent` became the private `_resume_descent`.
9. Lifetimes and hostile input are tested: descriptor-count assertions wrap
   every raising exit class of both the resolver and the spine, and a
   parametrized hostile-evidence family (duplicate keys, noncanonical bytes,
   duplicate/inconsistent nodes, wrong types) must surface as
   `MetadataStoreInvalid` from `load_record`.

## Fourth-round findings closed (2026-08-14)

1. Phase 5's planned entries are compared conditionally: absent/non-directory
   stays A3-variable (no finding), but a planned node present as a directory is
   compared on constraints and mount (identity ignored) so drift halts durably
   instead of surfacing as the table builder's `PreconditionRefused`. Proof
   issuance moves to the factory-owned `_approve_for_recovery`, which skips the
   forward-planning viability judgment that made ordinary approval reject
   foreign blockers at planned nodes — the promised foreign-blocker-reaches-A3
   test is now passable, and §9.1's amendment records the path.
2. `_roll_back` reconciles registration under the held chain fd before any
   snapshot or classification — the §9.2 backfill window is exactly the caught
   pre-binding state, and without the binding the `ROLLING_BACK` transition is
   trigger-refused. Step 8.1 tests all three caught windows (before append,
   during publication, between append and binding).
3. `commit.py` splits at the catch boundary: `verify_committed_surface`
   (step 10) is the `try`'s last statement; `finalize_commit` (steps 11–14)
   runs outside it, so a post-`COMMITTED` pre-settlement exception preserves
   the committed arm — tested via injected failure plus re-entry convergence.
4. The registry is complete: `persist_detach`, `verify_committed_surface`, and
   `finalize_commit` join the six previously listed entry points, `run_plan`'s
   pseudocode opens with the gate, and `site_for` became `_site_for`.
5. `_roll_back` runs `_resume_descent` for planned stops now observed as
   directories before assembling its snapshot, with the foreign-insertion
   mkdir test covering the published-then-failed-verification window.
6. Task 10's conformance assertion calls
   `apply_recovery_plan(snapshot_before, classify_recovery(snapshot_before))`
   — the reducer's real two-argument shape.

## Fifth-round findings closed (2026-08-14)

1. Phases 5–6 carry one moved-world rule: any deterministic mismatch signal
   caught after the diff — the factory's resolution refusing, the descriptor
   builder's validation, the evidence comparison — triggers exactly one
   re-diff; findings persist an `AssemblyHalt` (the world moved between
   passes), zero findings raise `ProtocolError` (two seams disagreeing about
   an unmoved world is an engine defect). Both TOCTOU windows are tested with
   injected mutation hooks. No deterministic mismatch after durable `PREPARED`
   surfaces as a refusal.
2. A determinate `EXDEV` from the diff walk's `RESOLVE_NO_XDEV` open is
   classified as `MOUNT_CHANGED` (the errno is the mount evidence — no
   descriptor exists to read an id from), and the test exercises a real bind
   mount via `find_distinct_mount` where available instead of mocking
   `read_mount_id`. (The kind is superseded by the sixth round: an errno
   cannot fill `MOUNT_CHANGED`'s closed fact set, so the finding is the
   fact-free `MOUNT_BOUNDARY`.)
3. The kill matrix cuts both sides of every store COMMIT: `_run_barrier` fires
   only pre-commit, so a second child wrapper kills immediately after the
   `_COMMIT` statement returns; both cuts are enumerated per barrier.
4. `_approve_for_recovery` is private with a sole-production-caller guard
   naming `recover.resolve` — the weaker proof factory's power is scoped by
   scan, and the constructor's token error names both factories.
5. Task 10 changes the proof-consumer scan from `glob` to `rglob` so
   `coordinator/effects/settle.py` (and any future nested consumer) is
   actually scanned against the registry.
6. `finalize_commit` returns the commit-internal `_CommitResult`; the public
   `TransactionOutcome` is constructed only in `commands.run_transaction`,
   preserving the `commands → execute → commit` import direction.
7. Caught rollback pins one `Observation` universe across stop re-observation,
   `_resume_descent`, all persistent/scratch observations, and
   `build_recovery_snapshot` — asserted by counting constructions — and
   phase 6's descent is explicitly the same single universe as its
   observations.

## Sixth-round findings closed (2026-08-14)

1. Determinate `EXDEV` maps to a new fact-free `MOUNT_BOUNDARY` finding kind
   (added to `core/assembly.py`'s closed vocabulary, design §9.3 amendment) —
   `MOUNT_CHANGED`'s closed fact set demands a canonical `mount_id` an errno
   cannot supply, and probing the foreign mount for one would cross exactly
   the boundary the resolver refuses. `MOUNT_CHANGED` stays for the root's
   own readable-id comparison.
2. Every in-process compensation mutation — replace's exchange-back (the
   authority's own "exchange it back … fsync, and refuse"), create-file's
   staging removal, delete's tombstone return, move's rename-back and anchor
   removal, mkdir's work-slot removal — flushes its mutated parent(s) before
   raising, and the kill matrix gains a cut between each compensation and its
   flush.
3. The moved-world catch is exactly `(ProjectApprovalRefused,
   PreconditionRefused)`: `_approve_for_recovery`'s evidence comparison raises
   `ProjectApprovalRefused` like its resolution does, and `ProtocolError` is
   never caught.

## Seventh-round findings closed (2026-08-14)

1. Phase 7 installs audit authority before `run_plan` —
   `set_declared_paths(frozenset(p.path for p in approved.paths))` with
   `clear_declared_paths()` in a `finally` — because a fresh process has never
   installed a declared scope and the facade refuses every declared-effect
   target without one; the forward spine's call gains the same exact
   `frozenset(...)` the facade type-checks for, and its `finally` explicitly
   spans the catch so `_roll_back`'s transforms run under the installed
   authority. The scope's clearance is asserted in the lifetime tests.
2. Task 4's mutation calls map operation-specific determinate errnos
   (`ENOENT`/`EISDIR` on unlink, `ENOENT`/`ENOTDIR`/`ENOTEMPTY`/`EEXIST` on
   rmdir, `ENOENT`/`EEXIST`/`ENOTDIR` on transfer, `ENOENT`/`ENOTDIR` on
   exchange) to `EffectMismatch`, so a post-authorization race rides Task 5's
   halt path instead of escaping as a raw `OSError`; `EIO` and kin still
   propagate. Race-injection and `EIO`-propagation tests are specified per
   operation. (Superseded in scope by the eighth round: the seam moved to
   `effects/common.py`, shared with the forward modules, and the table gained
   `link_anchor` and the boundary errnos.)
3. The kill matrix's compensation cuts each configure the same adverse
   scenario their Task 1–3 unit tests define, and their rehearsals run that
   identical adverse scenario un-killed — asserting the recorded sequence
   contains the compensation's own events — before reading off the countdown;
   a clean rehearsal never reaches a compensation.
4. The boundary rule is uniform: planned children and the
   `metadata_root/work` open also emit `MOUNT_BOUNDARY` on a determinate
   `EXDEV` (`MOUNT_CHANGED` only where a mount id is readable), the
   planned-node test expects it, and the fifth-round history's superseded
   `MOUNT_CHANGED` claim is annotated.

## Eighth-round findings closed (2026-08-14)

1. The errno-conversion seam moved from Task 4 into Task 1's
   `effects/common.py` as `run_determinate(operation, slot, call, *,
   passthrough=())`, and **forward** mutations route through it too: design
   §11 requires post-mutation interference to end as a clean refusal after
   restoration, and the spine's catch only translates
   `EffectMismatch`/`PreconditionRefused` — a raw forward `OSError` would
   roll back and then escape raw. Create-file's and mkdir's publication
   transfers pass `passthrough=(EEXIST,)` so their compensating occupancy
   branches keep the raw errno; compensation mutations inside `except`
   blocks stay raw. Task 8 gains the spine-level test: a mid-run race
   converges to `PreconditionRefused`, never a raw `OSError`. (The
   compensation carve-out is superseded by the ninth round: compensations
   route through the same seam.)
2. The `_DETERMINATE` table is completed: `link_anchor` joins it
   (`ENOENT`/`EEXIST`/`ENOTDIR`/`EXDEV`) because the anchor-only repair
   restores the source by hard-linking from the retained anchor
   (`variants.py:1023-1040`), and the boundary errnos join the existing
   operations (`EXDEV` on transfer/exchange/link, `EBUSY` on
   unlink/rmdir/transfer/exchange) — the plan already rules `EXDEV`
   determinate in the diff walk, so "every other errno propagates" was
   wrong. Task 4's race tests gain the anchor-removal, source-occupancy,
   and bind-mount cases.
3. The create/mkdir compensation rehearsals inject the occupant beneath the
   facade immediately before the publication `transfer_noclobber`, mid-run —
   a pre-planted occupant refuses at capture (step 3, before `PREPARED`), so
   the compensation would never be reached and no countdown could be read.

## Ninth-round findings closed (2026-08-14)

1. Create-file's `except OSError` branch regained its explicit
   `caught.errno != errno.EEXIST` guard — the seam re-raises indeterminate
   errors raw, so the broad branch would have treated an `EIO` as occupancy,
   removed the staging, and refused. Both create-file and mkdir gain
   apply-level tests: an injected `OSError(EIO)` from the publication
   transfer propagates raw with no compensation and the staged object intact.
2. Compensation mutations route through the same `run_determinate` seam —
   an `ENOENT` during staging cleanup, an `EEXIST` during rename-back, an
   `ENOTEMPTY` during work-slot removal are determinate drift like any
   other, and a raw compensation `OSError` would survive rollback in
   violation of §11. Delete's reappeared-live `EEXIST` case is now spelled
   as this conversion rather than a hand-raised mismatch.
3. `create_exclusive` and `mkdir_child` joined the `_DETERMINATE` table
   (`ENOENT`/`EEXIST`/`ENOTDIR` each) and the routing rule —
   `build_staged_file`'s exclusive create and mkdir's work-slot creation
   included — because a scratch or work occupant racing in after admission
   surfaces after `STARTED` and must convert, not leak.
4. `run_determinate` validates before it invokes: `_DETERMINATE[operation]`
   is resolved (unknown → `ProtocolError`, callable never invoked) and
   `passthrough` members type-checked before the callable runs; the table
   test asserts equality against a literal expected mapping (a missing
   operation fails the equality) and the unknown-operation test asserts a
   sentinel callable was left uninvoked.
5. The spine snippet's `_roll_back` comment now reads "returns after
   restoring; raises only on halt/failure" — the translation lines after it
   are reachable.

## Tenth-round findings closed (2026-08-14)

1. The verification lookups joined the `_DETERMINATE` table (`lstat`,
   `open_regular_nofollow`, `symlink_fingerprint`) and the routing rule:
   a raced `ENOENT` between a mutation and its verifying lookup is the same
   drift the mutation errnos are, and `verify_live_file`, displaced-pre,
   tombstone, anchor, and mkdir-identity lookups all route through the seam.
   `EIO` from the same lookups still propagates.
2. Recovery observation became total (Task 5 substrate change):
   `ObservedUnrecognized(st_mode)` joins A3's closed `ObservedEntry` union,
   `Observation.observe` returns it where it refused FIFO/socket/device
   kinds, `_classify_entry` maps it to `EntryClass.EXTERNAL` (landing on the
   existing `HaltPlan` arms), and capture — the one consumer entitled to
   judge before `PREPARED` — keeps §11's refusal. Fresh recovery (Task 7),
   caught rollback (Task 8), and step authorization (Task 5) each pin the
   FIFO case: halt with evidence preserved, never `ProtocolError` from the
   moved-world rule or an escaping refusal. Design amendment recorded (§9.1
   observation phase); the amendment count is six.
3. Call-site routing is tested at the sites the helper test cannot see:
   injected determinate failures in the exchange-back, staging cleanup,
   tombstone return, anchor removal, work-slot removal, rename-back,
   `create_exclusive`, `mkdir_child`, and the verification lookups each
   surface as `EffectMismatch` — a raw `OSError` escaping any `apply` fails
   the test, reusing the existing injecting backend.
