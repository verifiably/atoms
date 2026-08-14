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
    `site_for(approved: ProjectApprovedSpec, table: DescriptorTable, effect: Effect) -> Site`
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
      — `create_exclusive(parent_fd, leaf, 0o600)` → `stream_blob` → `set_mode(fd,
      state.mode)` → `flush_file(fd)` → return the retained fd.
    - `verify_live_file(backend, retained_fd, parent_fd, leaf, state: FileState) -> None`
      — identity: `os.lstat(leaf, dir_fd=parent_fd)` names the same `(st_dev,
      st_ino)` as `os.fstat(retained_fd)`; postcondition: re-read the bytes through
      `retained_fd` (`os.lseek(fd, 0, SEEK_SET)` + `os.read` loop), sha256 equals
      `state.content_hash`, size equals `state.byte_len`, `stat.S_IMODE` equals
      `state.mode`. Any disagreement raises `EffectMismatch` naming the axis.
- `replace_file.apply(backend: AuditedBackend, store: Store, site: ReplaceSite,
  effect: ReplaceFile) -> None` — design §7's sequence verbatim, spelled in Step 1.3.

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
  drift via raw `fchmod`, byte drift via raw write). `test_effects_replace.py`:
  forward success publishes the postimage at the live path with the preimage
  displaced onto the staging leaf; verification failure with the live path still
  ours exchanges back (preimage restored) and raises `EffectMismatch`; a foreign
  live entry AND altered staging raises `EffectMismatch` without further mutation
  (the both-changed case mutates nothing more — the plan loop owns what happens
  next). Build state with `prepared()`-style helpers over `make_bound_volume`.
  Run: `uv run pytest tests/test_effects_common.py tests/test_effects_replace.py -x`
  — expected: import errors (modules do not exist).
- [ ] **Step 1.3: Implement.** `replace_file.apply`, exactly:

  ```python
  def apply(backend, store, site, effect):
      staged_fd = build_staged_file(
          backend, store, site.parent_fd, site.staging_leaf, effect.post
      )
      try:
          backend.exchange(site.parent_fd, site.live_leaf, site.staging_leaf)
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
  identity before exchanging back; when it does not (both changed), it mutates
  nothing. `_verify_displaced_pre` validates the displaced staging entry against
  `effect.pre` through a fresh `open_regular_nofollow` descriptor, closed via
  `close_fd`.
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
  adopted (test: occupant with identical bytes still refuses). Delete: forward
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
              backend.transfer_noclobber(
                  site.parent_fd, site.staging_leaf, site.parent_fd, site.live_leaf
              )
          except OSError as caught:
              if caught.errno != errno.EEXIST:
                  raise
              _remove_attributable_staging(backend, staged_fd, site)
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
  on mismatch `transfer_noclobber(tombstone → live)` back (an `EEXIST` there is
  the reappeared-live case: raise `EffectMismatch` naming both facts, mutate
  nothing further) → `flush_directory(site.parent_fd)`.
- [ ] **Step 2.3:** Task tests pass, then the full gate (pytest, ruff, pyright).
- [ ] **Step 2.4:** `git commit -m "feat(effects): CreateFileNoClobber and DeletePath forward execution"`

---

## Task 3: `MoveNoClobber` and `CreateDirectory` forward execution

**Files:**
- Create: `python/src/atoms/coordinator/effects/move.py`
- Create: `python/src/atoms/coordinator/effects/create_directory.py`
- Test: `python/tests/test_effects_move.py`, `python/tests/test_effects_mkdir.py`

**Interfaces:**
- Consumes: `link_anchor`, `mkdir_child`, `open_child_directory`, facade `rebind`
  (measured `fs/audit.py:109`), `Provenance`/`RootKind` for the rebound declared
  path.
- Produces: `move.apply(backend, store, site: MoveSite, effect) -> None`;
  `create_directory.apply(backend, store, site: MkdirSite, effect) -> int` — returns
  the **retained directory descriptor**, provenance already rebound to the live
  declared path, which Task 8's spine hands to the `DescriptorTable` for descendant
  effects (ledger #3).

- [ ] **Step 3.1: Failing tests.** Move: forward success (anchor durable before the
  transfer — assert the anchor exists and `flush_directory(source_fd)` was reached
  by cutting with a fault-injecting backend beneath the facade between
  `link_anchor` and `transfer_noclobber`, then checking the anchor survives);
  anchor validation failure renames back and raises `EffectMismatch`; destination
  and anchor must name one inode at the expected fingerprint, else mismatch; a
  reappeared source after a failed validation raises `EffectMismatch` without
  further mutation. Mkdir: forward success publishes an empty directory with the
  approved mode at the live path, identity verified through the retained
  descriptor, and the descriptor's facade provenance now names the live path
  (assert via `provenance_of`); occupancy (`EEXIST` on the cross-directory
  transfer) raises `PreconditionRefused` after removing only the attributable
  work-slot directory. Run: expect import failures.
- [ ] **Step 3.2: Implement.** Move, design §7 order exactly: `link_anchor(source_fd,
  source_leaf, source_fd, anchor_leaf)` → `flush_directory(source_fd)` → validate
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
- Consumes: Tasks 1–3 helpers; `TransformEffectTuple`/`RemoveScratch` (measured
  shapes above); `state_from_json` is NOT involved — expected/result states arrive
  as `JointObservation` values inside the step.
- Produces, for Task 5's loop:
  - `settle.apply_transform(backend, store, approved, table, step:
    TransformEffectTuple) -> None` — dispatches on `(step.variant,
    step.settlement)` and executes exactly one variant's recovery mutation, then
    verifies the world now matches `step.result_after` for the step's covered slots
    (fresh observation through the same parent descriptors); a post-mutation
    mismatch raises `EffectMismatch`.
  - `settle.apply_remove_scratch(backend, approved, table, step: RemoveScratch) -> None`
    — `unlink_child` (file/symlink) or `rmdir_child` (directory) of exactly the
    named slot, per the observed kind in `step.expected_before`.
- The dispatch table is design §7's closing paragraph, spelled out — every cell
  either a primitive sequence or `ProtocolError` (an unreachable pairing is engine
  misuse, not a silent no-op):

| `SettlementKind` \ variant | REPLACE_FILE | CREATE_FILE_NO_CLOBBER | DELETE_PATH | MOVE_NO_CLOBBER | CREATE_DIRECTORY |
|---|---|---|---|---|---|
| `RESTORE_PRE` | `exchange(live, staging)` back when staging holds the postimage-displaced pre; else staged re-creation of `pre` (`build_staged_file` + `transfer_noclobber`); symlink pre with no survivor: `symlink_child` | `unlink_child(live)` after identity check | `transfer_noclobber(tombstone → live)`; no tombstone: staged re-creation from `pre` (file) or `symlink_child` (symlink) | `transfer_noclobber(destination → source)`; else re-link from anchor | `ProtocolError` (a published directory rolls back via `REMOVE_ATTRIBUTABLE_CREATION`) |
| `REMOVE_ATTRIBUTABLE_CREATION` | `unlink_child(staging)` | `unlink_child(staging)` or `unlink_child(live)` per the slot the step covers | `ProtocolError` | `unlink_child(anchor)` | `rmdir_child(live)` or `rmdir_child(work)` per covered slot |
| `REPAIR_INTERMEDIATE` | finish the staged build: `build_staged_file` semantics onto the covered slot | same | `ProtocolError` | `ProtocolError` | `set_mode` to the approved mode through a fresh retained descriptor |
| `FINISH_LANDED_UNDO` | `flush_directory(parent)` (the undo landed; make it durable) | same | same | `flush_directory` both parents, destination first | same |
| `REMOVE_COMMITTED_SCRATCH` | `unlink_child` of the covered scratch slot | same | same | same (anchor) | `rmdir_child(work)` |

- [ ] **Step 4.1: Failing tests.** Drive each populated cell through a real prepared
  state: build the mid-flight filesystem shape by running the forward module up to
  a cut (fault-injecting backend beneath the facade), then construct the plan with
  `tests/coordinator_support.prepared_with_transform`-style helpers and assert
  `apply_transform` lands the world on `result_after` — byte-for-byte where the
  cell restores content. Assert every `ProtocolError` cell refuses. Assert
  `apply_remove_scratch` removes exactly the named slot and nothing else (plant a
  sibling scratch leaf; it must survive). Run: expect import failure.
- [ ] **Step 4.2: Implement** `settle.py` as a dict-of-dispatch keyed
  `(EffectVariant, SettlementKind)`; every populated cell reuses Task 1–3 helpers;
  no cell re-derives scratch names (they come from `approved.scratch` via
  `site_for`, ledger #12).
- [ ] **Step 4.3:** Task tests pass, then the full gate.
- [ ] **Step 4.4:** `git commit -m "feat(effects): the variant-by-settlement recovery-mutation mapping"`

---
## Task 5: The plan executor

**Files:**
- Create: `python/src/atoms/coordinator/recover.py`
- Modify: `python/src/atoms/coordinator/transitions.py` (the `DetachActive` stop,
  `persist_detach`, and the projection-comment decision)
- Modify: `python/src/atoms/coordinator/commands.py` (`_registered_root` moves out)
- Test: `python/tests/test_coordinator_recover.py`; update
  `python/tests/test_coordinator_transitions.py` for the new stop

**Interfaces:**
- Consumes: `classify_recovery`, `persist_plan_prefix`, `authorize_recovery_step`,
  Task 4's `settle.apply_transform`/`apply_remove_scratch`, `append_entry` +
  `SettledEntry`, `_StoreTransaction.set_settlement_digest`/`set_active`.
- Produces, for Tasks 7–8:
  - `recover._registered_root(lease)` — **moved verbatim** from
    `commands.py:39-73` (body byte-identical; only the module changes).
    `commands.py` imports it from `recover` — `commands → recover` is the
    dependency direction, so no cycle forms when Task 8 adds `run_transaction`.
  - `recover.run_plan(lease, approved, table, plan: RecoveryPlan) -> RecoveryPlan`
    — the §8 loop. Returns the plan it finished (an `ActionPlan` driven to
    completion, or the `HaltPlan` it persisted — the caller decides whether that
    raises).
  - `transitions.persist_detach(lease, approved, plan, cursor) -> int` — persists
    exactly one `DetachActive` step after asserting the active record carries
    **both** `registration_digest` and `settlement_digest`; any other step type at
    `cursor`, or a missing binding, is `ProtocolError`.

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
          _execute_mutating(lease, approved, table, step)
          cursor += 1
      return plan
  ```

  `_observe_for_step` opens **one fresh `Observation`** and observes exactly the
  slots `step.expected_before` covers — its persistent paths, its scratch slots,
  its parent-occupancy parents (with the modeled-children set from
  `descriptors._modeled_children`) — nothing more (design §8 step 3: for
  committed-cleanup `RemoveScratch`, that means the named slot alone).
  `_execute_mutating` dispatches `TransformEffectTuple` to
  `settle.apply_transform` and `RemoveScratch` to `settle.apply_remove_scratch`
  and persists nothing: a mutating step has no store projection of its own —
  `transitions._persist_one` refuses both types by design (measured
  `transitions.py:100-127`; the journal transitions around a mutation are separate
  plan steps), and `_require_projection_matches` holds across the advance because
  the reducer projects no record change for filesystem-mutating steps. The loop
  just continues at `cursor + 1`. `_reconcile_settlement`: under
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
  - `recover._perform_reconciliation(lease, chain_fd, validated, actions:
    Reconciliation) -> ValidatedChain` — performs exactly the derived actions
    (chain appends via `append_entry`, bindings via one store transaction each)
    and returns the fresh proof.

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

- [ ] **Step 6.1: Failing tests.** One test per branch above (sixteen minimum).
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
- [ ] **Step 6.2: Implement** `_derive_reconciliation` (pure pattern-match over the
  §9.2 table) and `_perform_reconciliation` (appends then bindings, each binding in
  its own `store.transaction()`; a `BACKFILL` never appends). Registration appends
  during reconciliation rebuild the entry from the record's `spec_json` exactly as
  Task 8's forward path does — extract the shared builder
  `_registration_entry(record.spec, txid)` now, in `recover.py`, so Task 8 imports
  it rather than duplicating (consumer tag and `fulfills` come from the spec; the
  frozen-intent digest from `intent_digest(spec)` — Task 8 adds that function to
  `core/spec.py`, so this task adds it first with its unit test:
  `intent_digest(spec) == sha256(canonical_json(spec).encode()).hexdigest()`,
  matching the measured `tests/coordinator_support.py:102` helper, which then
  delegates to it).

  Wait — `RegisteredEntry.consumer_tag` is a **submission-time** argument, not a
  spec member (measured: `TransactionSpec` v2 carries `fulfills` and
  `registered_paths` only). A reconciliation append happens when the entry was
  never written, so the tag is not recoverable from the chain either. Decision,
  from the design's crash-window clause (§9.2 backfill window = `PREPARED`, all
  journals `PENDING`): the forward spine (Task 8) persists its registration entry
  **payload** — canonical bytes — into the workspace before the PREPARED COMMIT,
  as `registration.entry` alongside the staged blobs, so the reconciliation append
  replays those exact bytes (`decode_entry` → `append_entry`) instead of
  rebuilding. `_registration_entry` therefore lives in Task 8 only for the forward
  path; reconciliation reads `workspace.reopen` + the staged payload, and a
  missing payload in the backfill window is `ChainStateInvalid` (the PREPARED
  COMMIT that created this window durably included it). This keeps the appended
  entry byte-identical to the one the crash interrupted — the digest the record
  would have bound.

  **Design-amendment candidate:** §9.2 pins when reconciliation appends but not
  where the appended bytes come from, and the consumer tag is submission-time
  data no other durable surface carries. The staged-payload rule above is this
  plan's resolution of that gap; the landing commit should amend §9.2 with one
  sentence recording it (the design wins, so the design must say it).
- [ ] **Step 6.3:** Task tests pass; full gate.
- [ ] **Step 6.4:** `git commit -m "feat(recover): derive-then-perform chain reconciliation with every §9.2 refusal"`

---

## Task 7: The widened resolver, the assembly halt, and full assembly

**Files:**
- Modify: `python/src/atoms/coordinator/recover.py` (the `resolve` entry, phases 1–7)
- Modify: `python/src/atoms/coordinator/root.py` (call `recover.resolve`; `Lease`
  constructed after resolution)
- Modify: `python/src/atoms/coordinator/lease.py` (delete `_resolve`; the trap is gone)
- Modify: `python/src/atoms/coordinator/prepare.py` (persist the registration
  payload into the workspace — see Task 6's decision; small, co-committed here if
  Task 6 landed without it)
- Test: `python/tests/test_coordinator_resolve.py`,
  `python/tests/test_coordinator_assembly_halt.py`; update
  `python/tests/test_coordinator_lease.py` (trap tests convert to recovery tests
  preserving record/`active`/lock/descriptor discipline, per design §9.1)

**Interfaces:**
- Consumes: Tasks 5–6, `approve_for_project` + `ProjectContext` with the existing
  txid, `encode_approval_evidence`, `compile_spec`, `build_recovery_snapshot`,
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
3. `apply_survivors` + `_perform_reconciliation`.
4. `compiled = compile_spec(record.spec)` — pure.
5. `approved = approve_for_project(compiled, ProjectContext(binding, record.txid))`;
   compare `encode_approval_evidence(approved) == record.approval_evidence`
   byte-for-byte. Equal → proceed with this factory proof. Unequal → build the
   `AssemblyHalt` (below), persist it through the narrow path, raise
   `TransactionHalted`. A re-resolution error is split by determinacy: a
   **determinate** shape refusal (`ProjectApprovalRefused` whose cause is a
   determinate `ENOENT`/`ENOTDIR` finding) also becomes an `AssemblyHalt` with
   `NODE_MISSING`/`WRONG_ENTRY_KIND` findings derived from the decoded expected
   evidence; an indeterminate errno (`EIO` and kin) propagates as the error it is
   (design §9.3: never encoded as drift). `CapabilityUnavailable` propagates.
6. `lease = Lease(_binding=binding, _store=store)` (internal — resolution is past);
   reopen the workspace, build the `DescriptorTable`, observe **every** persistent
   path and **every** effect's required scratch slot in one `Observation`
   universe, `build_recovery_snapshot` from the `StoredRecord` + observations
   (its validators enforce complete coverage — trust them, add none).
7. `plan = classify_recovery(snapshot)`; `run_plan(lease, approved, table, plan)`;
   a returned `HaltPlan` raises `TransactionHalted` with its diagnostic.

The `AssemblyHalt` diff: decode both evidence documents (`json.loads` — the
encoding is measured canonical JSON with sorted keys); for each directory node in
the union, emit findings under the closed vocabulary with `NODE_MISSING`/
`WRONG_ENTRY_KIND` as a node's **sole** finding when applicable, else every
applicable changed-kind finding; `MOUNT_CHANGED`/`WORK_ROOT_CHANGED` from the top-
level members; order by `(path, finding-kind enum order)`; `expected` is
`record.approval_evidence` verbatim. The narrow persistence path is one function,
`recover._persist_assembly_halt(store, halt)` — it takes **no proof** (that is the
point: the proof is exactly what could not be issued) and is the architecture
registry's one recorded exception (Task 10 pins it).

- [ ] **Step 7.1: Failing tests.** Resolution: a clean store with no active record
  resolves to a no-op (lease enters, no `NotImplementedError` anywhere — grep the
  tree in the test); a mid-flight `PREPARED` record with untouched world rolls
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
- [ ] **Step 7.2: Implement** phases 1–7 as one `resolve` function calling private
  per-phase helpers in order, with an early return per short-circuit; wire
  `root.py`; delete `lease._resolve`; convert the A5b trap tests. The workspace
  registration payload lands in `prepare.py` here if Task 6 deferred it: stage
  `registration.entry` (the canonical `RegisteredEntry` bytes) through the
  workspace staging seam so the PREPARED COMMIT covers it.
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
- Modify: `python/src/atoms/core/spec.py` (`intent_digest` — if Task 6 landed it,
  skip)
- Modify: `python/src/atoms/coordinator/prepare.py` (stage the registration payload
  — if Task 7 landed it, skip)
- Test: `python/tests/test_coordinator_execute.py`,
  `python/tests/test_coordinator_commit.py`, `python/tests/test_coordinator_run.py`

**Interfaces:**
- Consumes: everything above, plus `admit`, `capture_initial_surface`,
  `prepare_transaction`, `open_workspace`, `set_declared_paths`/
  `clear_declared_paths`, `set_registration_digest`, journal/state setters.
- Produces (the public seam):
  - `commands.TransactionOutcome(txid: str, outcome: ChainOutcome,
    registration: str, settlement: str)` — frozen dataclass.
  - `commands.run_transaction(backend, project_root, metadata_root, storage,
    spec: TransactionSpec, payloads: PayloadSource, consumer_tag: str)
    -> TransactionOutcome` — `__all__` becomes
    `("append_intent", "register_root", "run_transaction")`. The consumer tag is
    validated by `require_valid_identifier` before the lease is acquired.
  - `core/spec.intent_digest(spec) -> str` — sha256 hex of
    `canonical_json(spec).encode("utf-8")`; `tests/coordinator_support.spec_digest`
    delegates to it.

The spine (design §6, numbered as there):

```python
def run_transaction(backend, project_root, metadata_root, storage,
                    spec, payloads, consumer_tag):
    require_valid_identifier("consumer_tag", consumer_tag)
    compiled = compile_spec(spec)
    with _recovery_lease(backend, project_root, metadata_root, storage) as lease:  # 1
        _require_chain_publication(lease._binding.evidence)
        with _registered_root(lease) as (chain_fd, validated):                     # 1: preflight
            return _run_under_lease(
                lease, chain_fd, validated, compiled, payloads, consumer_tag
            )
```

`execute._run_under_lease`, steps 2–9: `admit` (2) → `open_workspace` +
`capture_initial_surface` (3) → build and stage the `RegisteredEntry` payload
(`_registration_entry(spec, txid, consumer_tag)` — txid, `intent_digest(spec)`,
`consumer_tag`, initial/final = `state_to_json` of the surface states restricted
to `spec.registered_paths` in sorted order, `fulfills=spec.fulfills`) →
`prepare_transaction` (4, PREPARED COMMIT) → `set_declared_paths({p.path for p in
approved.paths})` → `append_entry` of the registration (5) →
`set_registration_digest` in one store transaction (6) → `APPLYING` transition
(7) → per effect in `compiled` order: `STARTED` journal COMMIT → `site_for` →
the variant module's `apply` (a `CreateDirectory` return descriptor is handed to
the `DescriptorTable` for descendants) → `DONE` journal COMMIT (8) → `APPLIED`
transition (9). `clear_declared_paths()` in the `finally`.

`commit.commit_prepared(lease, approved, table, chain_fd, validated)`, steps
10–14: one fresh `Observation` universe observing the **complete compiled final
surface first, then the complete scratch vector** (10) — any disagreement raises
`EffectMismatch` (a caught failure; the plan loop rolls back) → `COMMITTED` state
+ commit decision in **one** store transaction (11) → `settled(committed)` append
referencing the bound registration digest (12) → settlement binding COMMIT (13)
→ committed cleanup and detach **through the plan loop** (14): assemble a fresh
snapshot, `classify_recovery` (disposition `COMMITTED_CLEANUP`), `run_plan` — the
detach stop finds the settlement already bound and detaches.

The catch (design §6, last paragraph), in `execute.py`:

```python
try:
    ...steps 5-10...
except (ChainStateInvalid, MetadataStoreInvalid):
    raise                                # substrate-invalid: no rollback, evidence preserved
except BaseException as caught:          # KeyboardInterrupt and SystemExit included
    outcome = _roll_back(lease, approved, table, caught)
    if isinstance(caught, (EffectMismatch, PreconditionRefused)):
        raise PreconditionRefused(str(caught)) from caught
    raise
```

`_roll_back` assembles a snapshot, classifies (`ROLL_BACK`), runs the plan loop to
`ROLLED_BACK` + `settled(rolled-back)` + binding + detach, and only then lets the
refusal or the original exception surface (authority §11: `PreconditionRefused`
only after restoration is proved). A `HaltPlan` from the loop raises
`TransactionHalted` instead.

- [ ] **Step 8.1: Failing tests.** `test_coordinator_run.py`: a clean two-effect
  transaction returns `TransactionOutcome` with `outcome is ChainOutcome.COMMITTED`,
  the world holds the final surface, the chain holds genesis + registered + settled
  in that order, both digests bound, `active` empty, scratch slots gone;
  `run_transaction` on an unregistered root refuses **before** any metadata write
  (assert store byte-identical); `fulfills` and `registered_paths` land in the
  `RegisteredEntry` verbatim; a mid-apply verification failure (foreign swap
  beneath the facade between two effects) rolls back — world restored
  byte-for-byte, record `ROLLED_BACK`, `settled(rolled-back)` appended and bound,
  `PreconditionRefused` raised; `KeyboardInterrupt` injected beneath the facade
  rolls back then re-raises `KeyboardInterrupt`; a `ChainStateInvalid` planted
  mid-apply propagates with **no** rollback mutation. `test_coordinator_commit.py`:
  the proof order is observable — inject a fault that makes the scratch proof fail
  and assert the final-surface proof already ran (call recording beneath the
  facade); commit decision and state land in one transaction (kill between them
  is impossible — assert via the store's single-COMMIT counter, measured
  `_run_barrier`). Run: expect import failures.
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
  facade".

**The matrix.** Kill sites are named, not sampled — one parametrized test id per
cut:

- Backend barriers, per effect variant: before/after each `flush_file`,
  `flush_directory`, `exchange`, `transfer_noclobber`, `link_anchor` the forward
  sequences issue (the counts come from Tasks 1–3's pinned sequences).
- Store COMMIT barriers: the child monkeypatches
  `atoms.store.connection._StoreTransaction._run_barrier` to count invocations and
  `SIGKILL` before the nth — cutting exactly at PREPARED, registration binding,
  APPLYING, each STARTED/DONE, APPLIED, COMMITTED, settlement binding, detach.
- Chain barriers: registration and settlement appends killed between staging
  create/fsync/transfer/directory-fsync (drive `KillingBackend` counts through the
  append sequence, measured `chain/append.py:211-254`).
- Both terminal arms: including between settlement append and binding, and between
  binding and detach; and mid-rollback (kill inside a `RESTORE_PRE` transform).

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
  `apply_recovery_plan(classify_recovery(snapshot_before)).` The full
  model/real/persistence-cut agreement matrix stays A8's (ledger #15) — this test
  pins the fixed points only.
- [ ] **Step 10.2: Architecture.** Add to the existing guard files:
  - `commands.__all__` is exactly `("append_intent", "register_root",
    "run_transaction")`, every public command's signature accepts neither `Lease`
    nor `ProjectApprovedSpec` (source scan of `commands.py`), and each acquires
    `_recovery_lease` in its body.
  - `set_assembly_halt` is called from exactly one production site,
    `recover._persist_assembly_halt`, and that function's parameters include no
    proof type — recorded as the registry's one exception with a comment naming
    design §9.3.
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
  design-doc drift rule).
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
sequences verbatim; the recovery-mutation table). §8 → Task 5 (loop, fresh
per-step observation, halt-never-reclassified, detach prefix stop with settlement
reconciliation). §9.1 → Task 7 (seven phases, order pinned). §9.2 → Task 6 (every
case a test). §9.3 → Task 7 (halt build/persist/surface; determinacy split).
§10.4's registered/settled payloads → Tasks 6/8. §11's triggers are A7a's; Task 8
deliberately leans on them (the APPLYING gate test). §12 → `EffectMismatch` is
internal; `ChainStateInvalid`/`TransactionHalted`/`PreconditionRefused` surfaced
exactly as specified. §13 item 8 → Task 7; items 1–7 and 9–13 landed in A7a
(verified against the tree — the trap at `lease.py:59` was the one remainder).
§14 → Tasks 9 (kill matrix, chain barrier cuts), 10 (conformance, architecture),
1–4 (per-variant fault injection). §15 criteria → Task 11 step 11.4. §16's gaps
stay open and named for A8/A9.

**Placeholders.** None found on the closing scan: every step names its files,
signatures, refusal types, and expected run outcomes; the two cross-task "if
already landed, skip" notes (`intent_digest`, the staged registration payload)
name exactly which earlier step lands them.

**Type consistency.** `run_plan(lease, approved, table, plan)` is consumed with
that shape in Tasks 7 and 8; `site_for(approved, table, effect)` in Tasks 4, 5
and 8; `_registered_root` keeps its measured `(chain_fd, ValidatedChain)` yield
across Tasks 5–8; `TransactionOutcome` fields match Task 8's construction;
`intent_digest` has one definition (Task 6, Task 8 skips).

**Known open decisions surfaced to the executor-of-this-plan:** the two the tree
left open (`transitions.py`'s stop set and the projection-comparison comment) are
decided in Task 5's "Decisions" block; the one the design leaves open (the byte
source for a reconciliation registration append) is decided in Task 6 and marked
there as a design-amendment candidate for the landing commit.
