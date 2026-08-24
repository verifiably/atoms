# The path-read command and post-state evidence return

**Status:** Draft — awaiting atoms-side review and approval before any
implementation (the root-lifecycle gate's discipline).

**Authority:**
`~/d/science/docs/superpowers/specs/2026-08-24-world-index-holdings-design.md`
§2 (the atoms seam), at science commit `eb2899a`.

**Directly inherits:** the coordinator command and lease surfaces as landed
through `bf559c2`; the one path-summary model (`_capture_path`,
`Observation`, the `PathState` vocabulary) shared by baseline capture and
`capture_states`; the lifecycle design's read arms (`read_chain`'s
writable and read-only-serviceable paths); the 2026-08-22 chain-inspection
design, which remains the structural chain authority.

**Consumer contract:** world-index slice 5 consumes this API only through
`science.root`. Atoms remains root-kind agnostic: it returns lifecycle
states, observed path states, and opaque bytes — never `found`/`absent`,
holdings vocabulary, store identities, or record kinds. The consumer's
`found`/`absent`/established-neither classification is the consumer's.

---

## 1. Decision

One new public command and one additive widening; **no new capture
machinery**.

- **`read_path_state`** — a lease-held, lifecycle-honoring observation of
  exactly one named path in a project root, returning the observed
  `PathState` or a closed, phase-bearing refusal. Built on `_capture_path`
  and `Observation` — the existing summary model, no second one — under
  `read_chain`'s two read arms.
- **`TransactionOutcome` gains `final_states`** — the committed
  registration's final path-state rows, returned to the caller. This is a
  **return channel for evidence the engine already establishes**:
  `compile_spec`'s coverage phase requires the final surface to name every
  path an effect mutates, and `verify_committed_surface` observes every
  final-surface path on disk under the lease — content hash streamed from
  a pinned descriptor — and refuses commit on any mismatch. The consumer's
  post-write hash, post-delete absence, and move dual-location result are
  those rows.

There is no per-effect opt-in selection, no `CreateDirectory`
special-casing, no capture flag on `capture_states`, no consumer-facing
`Lease`, and no second summary or validation model.

## 2. Frozen semantics and scope

Requirements inherited from the consumer's spec, restated as this API's
obligations:

- **The boundary is the claim.** The observation runs entirely under the
  held lease (writable roots) or the held lock's quiescent read
  (read-only serviceable roots), from dereference start through hash
  completion. A `FileState` returned by `read_path_state` digests a
  stable cooperative state; an `AbsentState` is a completed enumeration
  answer. Cooperative-write bound, no further.
- **Refusals are structured and phase-bearing.** The caller must be able
  to distinguish *no read attempted* from *read attempted and established
  nothing* without inspecting exception types or message text (§4).
- **Lifecycle-honoring.** Metadata-less, read-only-unserviceable,
  binding-mismatched, and exact-v2 roots refuse before any read.
  Read-only-serviceable roots refuse when they carry an incomplete root
  operation or an active transaction record — `read_chain`'s own
  quiescent-read preconditions, reused.
- **Descriptor-anchored traversal.** Path resolution reuses
  `_capture_path`: components opened child-by-child from the root
  descriptor, never re-walked from a path string, so no post-preflight
  ancestor swap can redirect the read. A path whose ancestor is a file or
  symlink observes `ABSENT` — the capture model's own answer. A final
  entry that is a symlink or directory returns its observed state,
  unfollowed and unhashed; the consumer classifies it.
- **A5b preserved.** The command acts on the consumer's behalf; no
  `Lease` is returned, and no consumer-facing lease command is added.

Out of scope: batch reads (the consumer reads one canonical location per
act; `capture_states` remains the detached batch surface), URL retrieval
(no atoms concern), any verdict or attestation channel, and any change to
`capture_states` itself.

## 3. `read_path_state`

```
def read_path_state(
    backend: Backend,
    project_root: str,
    metadata_root: str,
    storage: StorageProfile,
    path: str,
) -> PathReadResult
```

Sequence:

1. **Grammar preflight.** `require_rel_path` over `path` — a refusal is
   `ReadNotAttempted(reason="path-grammar")`.
2. **Lifecycle view.** `_lifecycle_view` exactly as `read_chain` computes
   it. `WRITABLE` proceeds under the recovery lease;
   `READ_ONLY_SERVICEABLE` proceeds under the quiescent read-only root
   (schema v2, incomplete operation, and active-transaction records
   refuse). Every other state is
   `ReadNotAttempted(reason="lifecycle-state", state=<LifecycleState>)`.
3. **Observation.** Under the held boundary, one `Observation`, one
   `_capture_path` call. The result is the observed `PathState` —
   `FileState` (content hash, mode, byte length), `AbsentState`,
   `DirectoryState`, or `SymlinkState`.
4. **Mid-read failure.** An I/O error after the boundary is held, or an
   entry outside the closed path-state vocabulary, is
   `ReadUnestablished(reason=...)` — the read was attempted and
   established nothing. It is never coerced to `ABSENT` and never widened.

The command never creates or upgrades a root, metadata directory, lock,
database, schema, row, or WAL (the lifecycle design's read discipline).

## 4. `PathReadResult` — a closed, never-raises union

On the `inspect_chain` family's precedent, the result is a closed union
rather than an exception taxonomy, because the phases are contract, not
diagnostics:

```
PathReadResult =
    PathObserved(state: PathState)
  | ReadNotAttempted(reason: NotAttemptedReason,
                     lifecycle_state: LifecycleState | None)
  | ReadUnestablished(reason: UnestablishedReason)

NotAttemptedReason = path-grammar | lifecycle-state | root-unresolvable
UnestablishedReason = io-failure | outside-vocabulary
```

Reasons are closed enums; human-readable diagnostics travel beside the
variant (a `detail: str` member), never *as* the contract. Programming
errors (`ProtocolError`) still raise — a protocol violation is not a read
outcome.

## 5. `TransactionOutcome.final_states`

```
@dataclass(frozen=True, slots=True)
class TransactionOutcome:
    txid: str
    outcome: ChainOutcome
    registration: str
    settlement: str
    final_states: tuple[tuple[str, PathState], ...]
```

- **Source of truth:** the committed registration's final rows — the same
  `(path, PathState)` pairs `verify_committed_surface` matched against
  the observed disk under the lease, and the same rows the registration
  entry carries durably in the chain. The return decodes them to typed
  `PathState`; it re-observes nothing.
- **Every mutated path is present** (coverage phase 10), in the
  registration entry's canonical row order: a delete's path with
  `AbsentState`, a move's source and destination both, a directory
  creation with its `DirectoryState`. Consumers ignore rows they do not
  need; there is no selection parameter to get wrong.
- **Committed outcomes only:** `run_transaction` returns only committed
  outcomes today (a rolled-back transaction raises); if a rolled-back
  return path is ever added, its `final_states` is empty — a rolled-back
  transaction verified no final surface.
- Additive: no existing field, entry codec, or chain byte changes.
  `read_chain` consumers can independently re-derive the same rows from
  the registration entry — the return is a convenience with the same
  authority, not a second source.

## 6. What the consumer builds on this (recorded, not owned)

For the science boundary's evidence mapping — recorded here so review can
check fitness, owned by the science spec:

- `PathObserved(FileState)` → `found(sha256:<hex>)` from `content_hash`;
- `PathObserved(AbsentState)` → `absent`;
- `PathObserved(DirectoryState | SymlinkState)` → established-neither, an
  inconclusive attempt;
- `ReadNotAttempted` → `byte-locator-untested`;
- `ReadUnestablished` → `retrieval-failed`;
- a committed mutation's observation records the digest/absence from
  `final_states` — post-write hash, post-delete absence, and the move's
  dual-location result read from the same transaction's rows.

## 7. Rejected alternatives

- **A lease flag on `capture_states`.** It would mix the detached and
  leased contracts in one signature; the detached command's docstring
  promises "no lock, asserts nothing about registration", and a flag that
  silently changes that promise is how a caller holds the wrong
  guarantee.
- **Per-effect opt-in capture on `run_transaction`.** Moot: the engine
  already observes and verifies every final-surface path at commit;
  selection would add a parameter whose only power is to withhold
  evidence the engine already has.
- **Returning `found`/`absent` from atoms.** A root-kind and
  record-vocabulary leak; the consumer contract keeps atoms agnostic.
- **An exception taxonomy for the read phases.** Exception types are a
  weaker closed contract than a result union, and the phases are the
  contract the consumer's refusal mapping depends on.

## 8. Verification obligations

The implementation lands with, at minimum:

- a lifecycle matrix for `read_path_state`: writable, read-only
  serviceable, read-only unserviceable, metadata-less, binding-mismatched,
  exact-v2, incomplete-operation, active-transaction — each landing in
  its exact variant;
- traversal cases: nested file, absent path, absent-by-file-ancestor,
  absent-by-symlink-ancestor, final symlink, final directory — the
  symlink cases asserting unfollowed, unhashed observation;
- a coherence case: the observed `FileState` digests the pre-read stable
  state when a cooperative writer is serialized behind the same
  lease/lock;
- grammar refusals landing in `ReadNotAttempted("path-grammar")`;
- `final_states` presence and typed decoding for each effect variant, the
  move's two rows from one effect among them, and equality with the rows
  the registration entry carries via `read_chain`.

## 9. Review gate

This design is implemented only after atoms-side human review approves it.
Findings are closed by amendment commits to this document; the approval
commit is recorded in the science spec's status block, and the
implementation merges to `main` and is pushed before the consuming slice's
discharge (the root-lifecycle precedent).
