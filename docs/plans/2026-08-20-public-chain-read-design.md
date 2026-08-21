# `read_chain` — the public read-only chain projection

**Status:** Designed on 2026-08-20; not yet implemented. A9 remains unimplemented.

**Authority:** [`2026-07-23-recoverable-fs-effect-engine-design.md`](2026-07-23-recoverable-fs-effect-engine-design.md)
§4.1, §4.3, §7.1, §11, §12.2, §13.5.

**Sub-plans below it:** [`2026-08-13-a7-effect-recovery-execution-design.md`](2026-08-13-a7-effect-recovery-execution-design.md)
§10 (the chain), [`2026-08-02-a5b-recovery-lease-design.md`](2026-08-02-a5b-recovery-lease-design.md)
§5 (the lease).

**Consumer contract:** science's `2026-08-20-world-index-slice-2-design.md` §2, whose four call
boundaries this design is sized to and nothing more.

---

## 1. Decision

The coordinator's public surface is three commands, all mutators: `register_root`, `append_intent`,
and `run_transaction` (`atoms/coordinator/commands.py`). A consumer that has registered a root has
no supported way to *read* the history those commands wrote. The first production consumer needs
exactly that and nothing else: it must capture a corpus chain head inside its own capture hold, and
it must force the engine's recovery to have run before it reads world files.

Both needs are already satisfiable by internals. `_recovery_lease` acquires the project lock,
reclaims orphans, and resolves the active transaction; `_registered_root` then opens `.#~chain/`,
validates it, and hands back a complete `ValidatedChain`. `run_transaction` and `append_intent`
already compose exactly that pair. The fourth command is the *projection* of that pair's result and
nothing else:

> `read_chain` acquires the same lease, receives `_registered_root`'s already-computed
> `ValidatedChain`, projects it into a frozen `ChainView`, and returns after the lease releases.

The alternative — letting a consumer call `atoms.chain.read.validate_chain` itself — is what this
command exists to prevent. An unleased read holds no project lock, so it races cooperating writers,
and it observes the `.#~stage` survivor mid-append as chain state. Under the lease neither is
possible: `resolve` discharges every survivor at lease entry (`coordinator/recover.py:605`), and
`_registered_root` raises `ChainStateInvalid` if one is nevertheless present. Reading through the
lease is not a convenience; it is the only way to read the chain and get an answer that was true.

## 2. Scope and non-scope

**In scope:**

- One public command, `read_chain`, in `atoms/coordinator/commands.py`, with the signature frozen in
  §4.
- One public frozen dataclass, `ChainView`, in the same module.
- Promoting the existing `Entry` union to the public surface of that module, unchanged.
- The tests and status-guard updates §10 and §11 name.

**Non-scope, each with its reason:**

- **A second reader.** No new validation path, no second traversal of `.#~chain/`, no alternative to
  `validate_chain`. The command reads nothing itself; `_registered_root` has already read.
- **A partial, ranged, tail, or streaming mode.** The consumer's four boundaries need the whole
  validated chain or a refusal; a partial view would be a second contract to keep honest, and the
  invariants in §5 would no longer hold. YAGNI.
- **Any mutating operation, capability, or consumer-visible lease.** `Lease` stays private; the
  command returns after the lease releases and hands back no descriptor, no fd, no store handle.
- **Anchor verification and replay.** See §9. The command makes no anchor claim and no replay claim.
- **A package-wide convenience re-export.** `atoms.coordinator.__all__` is `()`, and
  `test_coordinator_architecture.py:78` (`test_the_coordinator_exports_nothing`) enforces it. That is
  a concrete precedent *against* a re-export, so the three names are exported from
  `atoms.coordinator.commands` only.
- **Chain compaction or a size bound.** A7's design §16 gap 4 owns that; see §3.

## 3. Seam review against the deferred-obligation ledger

[`docs/deferred-obligation-ledger.md`](../deferred-obligation-ledger.md) has **no open obligations**.
This design adds none. Each candidate admission was considered against the tree rather than waved
past.

| Candidate shape | Verdict | Why |
| --- | --- | --- |
| A consumer receives engine history and could treat it as anchor-verified or replayable | **Not an admitted shape** | No atoms sub-plan will ever own anchoring. A7's design §2 already assigns "anchor carriage, head capture, verification, and every L-row test" to science's side of the log design §9 split. An entry with no owner is the fictional-owner case the ledger's prose already refuses (A5a's threat-model limitation, A4b-1's fail-closed platforms). It is a documented non-guarantee (§9), not a deferred obligation. |
| The returned view could be mutated, or could alias engine state | **Closed by construction** | `ChainView` is `frozen=True`; `entries` is a tuple; every `Entry` arm is a frozen slotted dataclass whose fields are `str`, `bytes`, `ChainOutcome`, `str \| None`, or `tuple[tuple[str, PathStateJSON], ...]` where `PathStateJSON = tuple[tuple[str, str], ...]` (`chain/model.py:27,36-63`). The transitive closure is immutable. No descriptor, fd, `Lease`, `Store`, or `ProjectBinding` crosses the boundary. |
| The whole chain is materialized in memory, and the chain is unbounded | **Pre-existing, not admitted here** | `validate_chain` already reads and decodes every durable entry on **every** command, including the three that exist today (`chain/read.py:129-180`). `read_chain` adds no read. Unbounded chain size is A7 design §16 gap 4, owned by a future compaction design. |
| A read command now enters the mutating commands' lease, so it takes the project lock and can complete recovery | **The lease's existing meaning, not new behavior** | Authority §7.1 requires *every mutating* command to enter the lease; it does not forbid a non-mutating one from entering, and §13.5's architecture test asserts the mutators do enter, which stays true. `resolve` runs at `root.py:56` before the lease yields, so recovery completion is already a property of lease *entry*, identical for all four commands. See §8. |
| Exporting `Entry` freezes the four arms as a compatibility surface | **Accepted deliberately, no obligation** | The arms are already the durable canonical envelope's decoded form; the envelope, not the dataclass, is the compatibility surface, and it is pinned by `entry_digest` naming (`chain/read.py:153`). Promoting the union adds no field and no arm. |

**No authority amendment is claimed.** §4.1's "the engine's public submission input is a
`TransactionSpec`" and §12's "its mutation boundary is a `ProjectApprovedSpec` plus the
recovery-resolve lease" both describe the *submission* and *mutation* boundaries. `register_root`
and `append_intent` already sit outside the submission boundary; a read command sits outside the
mutation boundary. Neither sentence becomes false.

## 4. The public surface, frozen

In `atoms/coordinator/commands.py`:

```python
@dataclass(frozen=True)
class ChainView:
    genesis_digest: str
    entries: tuple[tuple[str, Entry], ...]
    tip: str


def read_chain(
    backend: Backend,
    project_root: str,
    metadata_root: str,
    storage: StorageProfile,
) -> ChainView: ...
```

The four parameters, their order, their types, and their names are copied exactly from the three
existing commands (`commands.py:112-119`, `161-167`, `177-184`), so the module's whole public surface
takes one root context in one shape. `Backend` is `atoms.fs.backend.Backend` (`fs/backend.py:68`) and
`StorageProfile` is `atoms.fs.volume.StorageProfile` (`fs/volume.py:64`) — the same two symbols the
existing commands import.

`Entry` is the **existing** union `atoms.chain.model.Entry` (`chain/model.py:63`), re-exported
unchanged. It is not copied, re-declared, narrowed, or wrapped. `commands.py` already imports from
`atoms.chain.model`; this adds `Entry` to that import and to `__all__`.

`__all__` becomes, keeping the module's existing plain-sorted order:

```python
__all__ = (
    "ChainView",
    "Entry",
    "TransactionOutcome",
    "append_intent",
    "read_chain",
    "register_root",
    "run_transaction",
)
```

`ChainView` is declared `frozen=True` only, matching the frozen signature above. `TransactionOutcome`
carries `slots=True` as well; `ChainView` does not, because the frozen signature this design is bound
to does not, and a difference in slotting is not a behavioral difference for a value with no
subclass and no dynamic attribute.

## 5. Invariants of the returned view

`read_chain` returns a `ChainView` satisfying all four, or it raises. There is no third outcome.

1. **`entries` is nonempty and in validated chain order.** `_registered_root` raises before yielding
   when `validated.entries` is empty (`coordinator/recover.py:748-753`), so nonemptiness is proved
   upstream. Order is `_linearize`'s: it starts at the unique genesis and walks the single-successor
   relation, refusing a fork, a cycle, a missing predecessor, and an orphan history
   (`chain/read.py:88-126`).
2. **`entries[0][0] == genesis_digest`.** The projection *defines* `genesis_digest` as
   `validated.entries[0][0]`; it does not recompute or re-derive it.
3. **`entries[0]` pairs `genesis_digest` with the genesis entry.** `_linearize` seeds `ordered` with
   `genesis[0]`, the one digest whose decoded `previous` is `None` and whose entry is a
   `GenesisEntry`; anything other than exactly one such digest in a non-empty chain is
   `ChainStateInvalid` (`chain/read.py:93-99,114-119`). So `entries[0][1]` is a `GenesisEntry` by
   construction, not by a check `read_chain` performs.
4. **`entries[-1][0] == tip`.** `_linearize` returns `current` after the walk breaks, and `current`
   is the digest of the last pair appended to `ordered` (`chain/read.py:114-126`), so the two are the
   same string. The projection takes `tip` from `validated.tip` rather than from `entries[-1][0]`, so
   the view reports the validator's tip rather than a re-derivation of it, and a test pins the
   equality.

**Narrowing `tip`.** `ValidatedChain.tip` is `str | None` (`chain/read.py:37`), because
`_linearize` returns `None` for the empty chain — the one case `_registered_root` has already
excluded. The type system cannot see that, so the projection states it:

```python
if validated.tip is None:
    raise ProtocolError("a registered chain has no tip")
```

This is the shape `register_root` already uses for the same class of internal invariant —
`if validated.survivors: raise ProtocolError("chain staging appeared after lease resolution")`
(`commands.py:131-132`). It is an engine-misuse assertion under authority §11's `ProtocolError`, not
a consumer-facing refusal, and it is not reachable through any input the consumer controls.

## 6. Command choreography

```python
def read_chain(backend, project_root, metadata_root, storage) -> ChainView:
    with _recovery_lease(backend, project_root, metadata_root, storage) as lease:
        with _registered_root(lease) as (_chain_fd, validated):
            if validated.tip is None:
                raise ProtocolError("a registered chain has no tip")
            return ChainView(
                genesis_digest=validated.entries[0][0],
                entries=validated.entries,
                tip=validated.tip,
            )
```

Nothing else happens. In order:

1. `_recovery_lease` (`coordinator/root.py:28`) wraps the backend in `AuditedBackend`, acquires the
   project lock, reclaims probe survivors, binds the project volume against `CERTIFIED_ALLOWLIST`,
   opens the store, reclaims orphans, and calls `resolve`. This is byte-for-byte the same context
   manager the three mutators enter, with the same arguments in the same order.
2. `_registered_root` (`coordinator/recover.py:725`) opens `.#~chain/` under the held project-root
   descriptor, calls `validate_chain`, refuses a surviving stage as `ChainStateInvalid`, refuses an
   empty chain, and yields `(chain_fd, validated)`.
3. The projection reads three fields off the `ValidatedChain` already in hand.
4. `_registered_root`'s `finally` closes `chain_fd`; `_recovery_lease` unwinds the store, the
   binding, and the lock in reverse acquisition order.

`validated.entries` is handed to `ChainView` **by reference**, not copied. It is already the tuple
`_linearize` built, of frozen entries; copying it would produce an equal tuple and prove nothing.
`validated.survivors` is deliberately not projected: `_registered_root` has already refused if it is
non-empty, so a `ChainView` field for it could only ever be `()`.

**`_require_chain_publication` is deliberately not called.** The three mutators call it
(`commands.py:125,170,189`) to refuse *before their own append* on a volume that cannot publish
no-clobber. `read_chain` has no append. The gate would add a `CapabilityUnavailable` refusal to a
contract that names only `ChainStateInvalid` and `PreconditionRefused`, and it would not protect the
one append that can occur on this path anyway — reconciliation's, which `resolve` performs at
`root.py:56`, *before* the lease yields and therefore before any command-level check in all four
commands. Omitting it changes nothing that the check could have caught.

**Descriptor and lease lifetime.** The chain descriptor lives inside `_registered_root`'s `with`
and is closed by its `finally` before `read_chain` returns. The `Lease`, the `ProjectBinding`, the
`Store`, and the `HeldProjectLock` all die with `_recovery_lease`'s unwind. The `ChainView` outlives
all of them and references none of them: every field is a `str` or a tuple of `(str, Entry)` pairs
whose transitive closure is immutable (§3). A consumer holding a returned view holds no engine
resource, so a view that outlives its lease is inert rather than dangerous — unlike the `Lease`,
whose docstring records the opposite property (`coordinator/lease.py:13-18`).

## 7. Errors

`read_chain` adds no error type and no new error path. Everything it can raise, it raises because an
existing internal raised it:

| Condition | Error | Raised by |
| --- | --- | --- |
| The root has no `.#~chain/`, and no live record exists | `PreconditionRefused("the project root is not registered")` | `recover.py:737` |
| `.#~chain/` is absent but a live transaction record exists | `ChainStateInvalid` | `recover.py:734` |
| The reserved chain leaf is not a stable directory | `ChainStateInvalid` | `recover.py:739` |
| The chain has entries but no genesis, two genesis entries, a fork, a cycle, an orphan history, a missing predecessor, a foreign leaf, a name/bytes mismatch, or an undecodable envelope | `ChainStateInvalid` | `chain/read.py:99,106,110,118,125,151,154`, `decode_entry` |
| A stage survivor is still present after the lease resolved | `ChainStateInvalid` | `recover.py:747` |
| Recovery halts, or a halt is already durable | `TransactionHalted` | `recover.py:634,639,721` |
| The volume, mount, lock, or store refuses at lease entry | the existing binding/lock/store errors | `root.py:44-56` |

`ChainStateInvalid` (`chain/errors.py:6`) remains exactly what A7's design §12 made it — the
invalid-chain refusal, an `AtomsError` meaning "stop, preserve evidence, refuse mutation". This
design does not redefine it, widen it, or add a partial-view escape from it. **The command either
returns the complete validated chain or raises; it never returns a partial view.**

## 8. Recovery-survivor semantics

Entering the lease can complete recovery. That is true of `read_chain` for exactly the reason it is
true of `append_intent`: `_recovery_lease` calls `resolve(binding, store)` before it yields
(`root.py:56`), and `resolve` performs chain reconciliation whether or not a record is active —
`_derive_reconciliation(record, validated)` returns `Reconciliation(None, None)` for `record is None`
(`recover.py:148-154`), and `_perform_reconciliation` still calls `apply_survivors`, discharging the
classified `.#~stage` survivor per A7 design §10.2's closed rule (`recover.py:263`, inside
`_perform_reconciliation`, reached unconditionally from `resolve` at `recover.py:640`). With no
active record there is no planned envelope, so the survivor is *removed*; only an active record's
derived append can *finish* one.

So a `read_chain` call on a crash-interrupted root can finish an append, remove a stage survivor,
reclaim orphan workspaces and unindexed blobs, and roll a transaction forward or back. **This is the
lease's existing meaning, not read-command behavior.** The design states it because the downstream
consumer uses the command *for* that effect at two of its four boundaries, and a reader of this
document must not conclude that a "read" command was given a write path. It was given the lease. The
lease was always the thing that recovers.

The consequence for the returned view is the useful one: the chain a `ChainView` describes is the
chain after recovery, under a lock no cooperating writer held during the read.

## 9. What this command does not claim

`read_chain` guarantees exactly what `validate_chain` proves and the lease serializes:

- every durable entry decoded from canonical bytes whose digest equals its filename;
- genesis-connected linearity with exactly one successor per entry and exactly one tip;
- no staging survivor counted as chain state;
- read under the held project lock, after recovery resolved.

It does **not** claim, and must not be read as claiming:

- **Anchor verification.** No entry is checked against any external anchor, mirror, notary, or
  countersignature. A7 design §2 assigns anchor carriage and verification to the consumer.
- **Replay.** The view is decoded history, not a re-execution of it. Nothing in `read_chain` replays
  an effect, re-derives a surface, or re-checks that a `SettledEntry`'s outcome matches the
  filesystem.
- **Payload semantics.** `GenesisEntry.payload` and `IntentEntry.payload` are opaque consumer bytes,
  embedded unchanged (A7 design §10.3, §10.4). atoms validates none of it.
- **Genesis or mirror audit, fork construction, or explicit anchoring.** The consumer contract
  restricts this command to four boundaries (per-corpus capture, build-start world head, an
  `open_epoch` recovery barrier whose view is discarded, and a `current_epoch`/`delete_epoch`
  recovery barrier whose view is discarded). None of the other uses is supported by this design, and
  a future one needs its own.

## 10. Verification

New tests in `python/tests/test_coordinator_commands.py`, alongside the existing command suites:

1. `read_chain` on a root registered by `register_root` returns a one-entry view whose
   `genesis_digest` equals `register_root`'s returned digest, whose single entry is that
   `GenesisEntry` with the exact payload and baseline, and whose `tip` equals `genesis_digest`.
2. After `register_root` then two `append_intent` calls, the view's `entries` are the three digests
   in append order, `entries[0][1]` is the `GenesisEntry`, `entries[-1][0] == tip` equals the second
   intent digest, and `genesis_digest == entries[0][0]`.
3. `read_chain` against an unregistered root raises `PreconditionRefused` and leaves no chain
   directory, workspace, or record behind — the shape
   `test_append_intent_refuses_an_unregistered_root_without_transaction_artifacts`
   (`test_coordinator_commands.py:332`) already uses.
4. A missing `.#~chain/` with a live record raises `ChainStateInvalid`, mirroring
   `test_missing_chain_with_a_live_record_is_corruption` (`:348`).
5. A forked chain — two entries naming one predecessor — raises `ChainStateInvalid` from
   `read_chain`, and the command returns no partial view.
6. A `read_chain` call is a recovery barrier: with a `.#~stage` survivor planted and no active
   record, the call returns the durable chain unchanged, `.#~stage` is gone from `.#~chain/`
   afterwards, and a fresh process reads the same view. This pins §8 rather than asserting it in
   prose, and it is the property the downstream consumer's two barrier boundaries depend on.
7. `read_chain` closes what it opens: the chain descriptor is closed on both the success and the
   `ChainStateInvalid` paths, the shape
   `test_registered_root_closes_the_chain_descriptor_when_validation_fails` (`:382`) uses.
8. `ChainView` is frozen and inert: assigning to `genesis_digest`, `entries`, or `tip` raises
   `FrozenInstanceError`, and every field of a view returned by a completed call is still readable
   after the lease released — the view holds no engine resource (§6).

**Existing guards this command changes.** `test_fs_architecture.py:1177`
(`test_chain_commands_keep_the_lease_and_approval_proofs_private`) pins
`commands.__all__` to the exact four-name tuple and the public function set to the exact three names.
Landing `read_chain` **must** extend both to the values in §4 in the same commit; the test's other
two assertions — every public command enters `_recovery_lease` through a `with`, and no public
command's annotations mention `Lease` or `ProjectApprovedSpec` — are satisfied by §6's choreography
unchanged, and `read_chain` must remain covered by them rather than exempted.

`test_fs_architecture.py:1155` (`test_no_unregistered_public_function_accepts_the_proof`) stays
green: `read_chain` accepts no `ProjectApprovedSpec`, so it never joins
`_TRANSACTION_STAGE_ENTRY_POINTS`. `test_coordinator_architecture.py:78`
(`test_the_coordinator_exports_nothing`) stays green because no package-level re-export is added.

Gates: `uv run --frozen pytest -q`, `uv run --frozen ruff check .`, `uv run --frozen pyright src`,
all from `python/`.

## 11. Documentation impact

**This design commit amends no other document.** Each candidate was checked against the tree rather
than assumed:

- **`AGENTS.md`** — its `## Status` section is the roadmap A1–A8b plus the note "A8 adds no new
  transaction writer; it drives the existing public commands and guarded lease." That claim is about
  A8 and stays true. Nothing in it counts the public commands or asserts the coordinator is
  write-only.
- **`README.md`** — its `## Status` section lists sub-plan documents and their state. It names no
  command inventory.
- **`docs/deferred-obligation-ledger.md`** — no open obligations, and §3 adds none, so there is
  nothing to add or discharge.
- **`python/tests/test_docs_status.py`** — the roadmap boundary is unchanged: `FIRST_UNIMPLEMENTED`
  stays `"A9"`. This command is not a Plan A sub-plan and adds no stage. Per `AGENTS.md`'s
  conventions no per-sub-plan status test is added, and this document's `**Status:**` field is
  written so the existing guard parses it: it claims only A9 unimplemented, uses no ASCII-hyphen
  stage range, and satisfies `test_every_design_document_declares_a_status`.

**When the implementation lands (the next commit, not this one),** two claims about the exact
`__all__` tuple go stale together and must move with it:

- `python/tests/test_fs_architecture.py:1193-1199`, the assertion itself (§10).
- [`2026-08-13-plan-a7b-executor.md`](2026-08-13-plan-a7b-executor.md) step 10.2, which quotes that
  tuple as the guard A7b added.

Both are records of what the code is, not status claims about a roadmap, so neither is stale today.

## 12. Acceptance criteria

1. `atoms.coordinator.commands` exports `ChainView`, `Entry`, and `read_chain` alongside the existing
   four names, and `atoms.coordinator.__all__` is still `()`.
2. `read_chain`'s signature is §4's, verbatim, with the same four parameters as its three siblings.
3. The implementation body is §6's: one `_recovery_lease`, one `_registered_root`, one projection,
   no second reader, no validation pass, no partial mode, no mutation, no exposed `Lease`.
4. All four §5 invariants hold on every returned view, pinned by §10's tests.
5. An invalid chain raises `ChainStateInvalid` and an unregistered root raises `PreconditionRefused`;
   neither returns a partial view.
6. `test_docs_status.py` plus the full pytest, ruff, and pyright gate pass.
