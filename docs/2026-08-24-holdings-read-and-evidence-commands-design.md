# The path-read command and post-state evidence return

**Status:** Approved 2026-08-24 at `558817b` against Science authority
`b231e08`; **implemented on `design/holdings-commands`** with the full
suite and gates green, the review's four post-implementation findings
closed by amendment (the writable arm corrected to the existing-only
gated lease; malformed arguments raising `ProtocolError`; the §8 matrix
completed). Not yet merged to `main`.

**Authority:**
`~/d/science/docs/superpowers/specs/2026-08-24-world-index-holdings-design.md`
§2 (the atoms seam), at science commit `b231e08` — the §2.2 corrections
this design's discoveries forced, and §4.1's engine-raise disposition, are
part of the authority, not ahead of it.

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

One new public command and one deliberate widening of
`TransactionOutcome` (a stated constructor break, §5); **no new capture
machinery**.

- **`read_path_state`** — a lease-held, lifecycle-honoring observation of
  exactly one named path in a project root, returning the observed
  `PathState` or a closed, phase-bearing refusal. Built on `_capture_path`
  and `Observation` — the existing summary model, no second one — under
  `read_chain`'s two read arms.
- **`TransactionOutcome` gains `final_states`** — the transaction's
  **canonical `final_surface`** rows, returned to the caller. This is a
  **return channel for evidence the engine already establishes**:
  `compile_spec`'s coverage phase requires the final surface to name every
  path an effect mutates, and `verify_committed_surface` observes every
  final-surface path on disk under the lease — content hash streamed from
  a pinned descriptor — and refuses commit on any mismatch. The consumer's
  post-write hash, post-delete absence, and move dual-location result are
  those rows. The **chain** durably carries only the `registered_paths`
  **subset** of those rows in the registration entry (`_registration_entry`
  projects both surfaces over `spec.registered_paths`); the return and the
  chain row set are deliberately not conflated (§5).

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

Sequence: grammar preflight, lifecycle view (`_lifecycle_view` exactly as
`read_chain` computes it), boundary acquisition (`WRITABLE` under the
**existing-only writability-gated lease** — never the create-capable
`_recovery_lease`, whose creating lock could rebuild a carrier that
vanished after classification and answer against a fresh store;
`READ_ONLY_SERVICEABLE` under the quiescent read-only root), then one
`Observation`, one `_capture_path` call. The command never creates or
upgrades a root, metadata directory, lock, database, schema, row, or WAL
(the lifecycle design's read discipline).

**The translation table is normative.** Every condition the read path can
produce lands in exactly one row; the implementation may not invent a
translation this table does not pin:

| condition | result |
|---|---|
| malformed arguments (non-str, NUL, wrong types) | raise `ProtocolError` — a programming error, not a read outcome |
| `path` fails the project-relative grammar | `ReadNotAttempted("path-grammar")` |
| lifecycle classifies `METADATA_LESS`, `READ_ONLY_UNSERVICEABLE`, or `BINDING_MISMATCHED` — classification is **existing-only**, so an absent root or metadata directory lands *here* (absent metadata is metadata-less; an absent root over a stored row is binding-mismatched), never below | `ReadNotAttempted("lifecycle-state", lifecycle_state=<state>)` |
| exact schema-v2 root (pre-lifecycle) | `ReadNotAttempted("lifecycle-state", lifecycle_state=<its classified state>)` |
| the root boundary vanishes or changes **after** classification — surfacing as the existing-only gated lease's refusal, or as `ENOENT`/`ENOTDIR` at boundary acquisition. Classification never observes the chain, so chain absence is not this row's to claim — it is solely the quiescence row's | `ReadNotAttempted("root-unresolvable")` |
| any quiescent-read precondition refusal on a `READ_ONLY_SERVICEABLE` root — incomplete root operation, active transaction record, no registered chain, a chain staging survivor, an entry-less chain | `ReadNotAttempted("quiescence")` |
| `CapabilityUnavailable`, storage-profile or certified-allowlist refusal | raise — an environment failure, unattributable to this path, **whatever its position** |
| `TransactionHalted`, `ChainStateInvalid` (e.g. a live transaction record on a root with no grant) | raise — alarm-class engine states demand attention; a routine result variant would under-report them |
| routine observation failure **after** the observation begins — an `OSError` mid-traversal or mid-hash **that survives the errno classification below**, a `PreconditionRefused` from `translated_lookup`'s namespace-contradiction arm (for a bare read, concurrent raw-mutation evidence, not an approval violation), or an entry outside the closed path-state vocabulary | `ReadUnestablished("io-failure" \| "outside-vocabulary")` — never coerced to `ABSENT`, never widened |
| **any other exception, at any position** | raise unchanged — the table pins translations; it does not convert what it does not name |

**The invariant translates routine failures only.** From the moment the
observation begins (boundary held, `_capture_path` entered), no **routine
observation failure** escapes as a raise — every `OSError` and
closed-vocabulary violation there is caught into `ReadUnestablished`.
Programming errors (`ProtocolError`), environment failures
(`CapabilityUnavailable`), and alarm-class states propagate **regardless
of position** — `Observation` itself can raise them mid-lookup, and a bug
or a lost backend capability is not a read outcome and must not be
laundered into one.

**"Routine" is decided by `translated_lookup`'s errno classification,
applied to every raw `OSError` of the observation phase.**
`_capture_path`'s ancestor opens and descriptor cleanup run outside
`Observation`'s own `translated_lookup` wrappers, so a raw `EOPNOTSUPP`
or `EBADF` can escape them as a bare `OSError`; converting those
wholesale would launder exactly the failures the propagation rule names.
The command therefore applies the same classification before converting:
`_UNSUPPORTED` errnos (`ENOSYS`, `EOPNOTSUPP`, `ENOTSUP`) re-raise
`CapabilityUnavailable`; `EBADF` re-raises `ProtocolError`; only the
remainder converts to `ReadUnestablished("io-failure")`. (`_capture_path`'s
own `_ABSENT_PARENT` handling — `ENOENT`/`ENOTDIR`/`ELOOP` on an ancestor
→ `ABSENT` — is traversal semantics, ahead of this classification, and
unchanged.)

A raise therefore no longer encodes phase; what it means for the consumer
is the science spec §4.1's ruling, recorded in §6. This invariant —
routine failures translated under the pinned classification, non-routine
propagated from both positions — is a test obligation (§8).

## 4. `PathReadResult` — a closed union for read outcomes

On the `inspect_chain` family's precedent, read *outcomes* are a closed
union rather than an exception taxonomy, because the phases are contract,
not diagnostics. The union is not "never-raises": alarm-class,
environment, and programming failures raise **whatever their position**
(§3's table); only routine observation failures translate.

```
PathReadResult =
    PathObserved(state: PathState)
  | ReadNotAttempted(reason: NotAttemptedReason,
                     lifecycle_state: LifecycleState | None,
                     detail: str)
  | ReadUnestablished(reason: UnestablishedReason,
                      detail: str)

NotAttemptedReason = path-grammar | root-unresolvable
                   | lifecycle-state | quiescence
UnestablishedReason = io-failure | outside-vocabulary
```

Reasons are closed enums and are the contract; `detail` is human-readable
diagnostic text and is never the contract. `lifecycle_state` is populated
exactly for `reason="lifecycle-state"` and `None` otherwise.

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

- **Source of truth:** the transaction's **canonical
  `compiled.spec.final_surface`** — exactly the `(path, PathState)` rows
  `verify_committed_surface` matched against the observed disk under the
  lease. The return hands them back typed, in the canonical surface
  order; it re-observes nothing.
- **Every mutated path is present** (coverage phase 10): a delete's path
  with `AbsentState`, a move's source and destination both, a directory
  creation with its `DirectoryState`. Consumers ignore rows they do not
  need; there is no selection parameter to get wrong.
- **The return and the chain rows are two different sets, not conflated.**
  `_registration_entry` projects the durable `initial`/`final` rows over
  `spec.registered_paths` only — a subset of the final surface the
  caller's spec chooses. `final_states` is the **complete verified
  surface**; the chain carries the **registered subset** of it. The two
  agree row-for-row where they overlap, and a consumer that needs a row to
  be durable in the chain must put its path in `registered_paths` — the
  consumer's own registered-surface discipline, not this command's.
- **Committed outcomes only:** `run_transaction` returns only committed
  outcomes today (a rolled-back transaction raises); if a rolled-back
  return path is ever added, its `final_states` is empty — a rolled-back
  transaction verified no final surface.
- **Compatibility decision, stated rather than waved at:** adding a
  required field to a public frozen dataclass **breaks direct
  constructors and equality** — existing tests that build
  `TransactionOutcome` literals must add the field, and they are updated
  in the same change. The field deliberately has **no default**: a
  defaulted empty tuple would fabricate "no mutated paths" for any
  construction site that forgot it, which is exactly the silent evidence
  loss this return exists to prevent. No entry codec or chain byte
  changes.

## 6. What the consumer builds on this (recorded, not owned)

For the science boundary's evidence mapping — recorded here so review can
check fitness, owned by the science spec:

- `PathObserved(FileState)` → `found(sha256:<hex>)` from `content_hash`;
- `PathObserved(AbsentState)` → `absent`;
- `PathObserved(DirectoryState | SymlinkState)` → established-neither, an
  inconclusive attempt;
- `ReadNotAttempted` → `byte-locator-untested`;
- `ReadUnestablished` → `retrieval-failed`;
- an engine **raise** aborts the consumer's act: no report and no
  observation is minted, the durable unmatched intent marking an
  intent-bearing attempt, and exception types never classified into the
  two report classes — the science spec §4.1's own ruling, which this
  bullet records rather than owns;
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
- **the translation table row-by-row**, each condition constructed and
  asserted to land in its exact variant or raise — including at least one
  raising row per raise class the table names;
- **the invariant, both halves, under the pinned classification**: a
  routine failure injected after the observation begins returns
  `ReadUnestablished` and does not raise; a non-routine failure injected
  there propagates unchanged — including a **raw** `EOPNOTSUPP` injected
  in the ancestor walk propagating as `CapabilityUnavailable` and a raw
  `EBADF` as `ProtocolError`, the classification applied where
  `translated_lookup` does not already wrap;
- `final_states` presence and typed decoding for each effect variant, the
  move's two rows from one effect among them; and **the subset
  distinction**: a transaction whose `registered_paths` is a proper subset
  of its final surface returns the complete surface in `final_states`
  while `read_chain`'s registration entry carries exactly the registered
  subset, the overlap agreeing row-for-row.

## 9. Review gate

This design is implemented only after atoms-side human review approves it.
Findings are closed by amendment commits to this document; the approval
commit is recorded in the science spec's status block, and the
implementation merges to `main` and is pushed before the consuming slice's
discharge (the root-lifecycle precedent).
