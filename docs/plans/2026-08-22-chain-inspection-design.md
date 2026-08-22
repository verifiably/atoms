# Chain inspection, batch path-state capture, and the pending gate

**Status:** Designed on 2026-08-22; unimplemented. A9 remains unimplemented.

**Authority:** [`2026-07-23-recoverable-fs-effect-engine-design.md`](2026-07-23-recoverable-fs-effect-engine-design.md)
§4.1, §4.3, §6, §7.1, §11, §12.2, §13.5.

**Sub-plans below it:** [`2026-08-13-a7-effect-recovery-execution-design.md`](2026-08-13-a7-effect-recovery-execution-design.md)
§10 (the chain), [`2026-08-02-a5b-recovery-lease-design.md`](2026-08-02-a5b-recovery-lease-design.md)
§5 (the lease), [`2026-08-07-a6-coherent-capture-design.md`](2026-08-07-a6-coherent-capture-design.md)
(the observation mechanism `capture_states` publishes).

**Directly inherits:** [`2026-08-20-public-chain-read-design.md`](2026-08-20-public-chain-read-design.md).
`read_chain` is untouched by this design; its four invariants, its choreography, and its
non-claims all stand. This document adds the *non-raising* sibling it deliberately did
not build, and it reuses that document's reasoning about lease entry, view inertness,
and the anchor/replay split rather than restating it.

**Consumer contract:** science's `2026-08-22-log-verification-design.md` §2 and §8 — four
obligations, sized exactly to them and nothing more.

---

## 1. Decision

`read_chain` gives a registered consumer the validated chain or an exception. That is the
right shape for a consumer that is *about to write*, and the wrong shape for one whose
whole job is to render a verdict about a chain that may be damaged. Science's log
evaluator needs three things the raising surface cannot express:

- a **typed** structural verdict, because "malformed" is one of its four outcomes and an
  exception is not an outcome;
- the same verdict over a root that is **not registered here at all** — a directory that
  arrived by copy, with no metadata store, no volume binding, and no lock to take;
- the set of **unsettled registrations**, because an unsettled registration is
  evidence-starved and must both appear in a report and refuse further mutation.

So this design adds two inspection commands, one batch capture command, and one shared
refusal, over **one typed validation core**:

> `validate_chain` stops being the validator and becomes the *raising disposition* of a
> validator that classifies. One core computes at most one defect from a fixed taxonomy in
> a fixed traversal order. The inspecting paths return it; the raising paths raise
> `ChainStateInvalid` carrying its message. There is exactly one place a defect is
> recognized and exactly one place its wording is minted.

The alternative — a second validator for the inspecting paths — is what the science
contract's "one typed validation core shared with the raising paths so the defect taxonomy
cannot fork" forbids, and rightly: two validators would let a chain be simultaneously
appendable and reported malformed, which is the precise failure the log design exists to
make impossible.

## 2. Scope and non-scope

**In scope:**

- One new module, `atoms/chain/inspect.py`: `DefectKind`, `ChainDefect`, `ChainScan`, the
  three `ChainInspection` arms, and the two-pass core (`scan_chain_directory`,
  `inspect_scan`).
- Rewriting `validate_chain` in `atoms/chain/read.py` as the raising disposition of that
  core. `ValidatedChain`, `SurvivorAction`, `SurvivorDisposition`, and `STAGING_LEAF`
  keep their exact current shapes, so `chain/append.py` and `coordinator/recover.py` are
  not touched by the refactor.
- Six new invariants in the core that `validate_chain` does not check today (§4.3), and the
  **submission gate** (§11) — two independent checks in `run_transaction` — that keeps the
  engine from minting a chain those invariants would condemn.
- Three public commands in `atoms/coordinator/commands.py`: `inspect_chain`,
  `inspect_chain_detached`, `capture_states`.
- Splitting `_recovery_lease` into `_project_lease` + `resolve` (§6), so structural
  inspection can interpose between reclamation and resolution.
- One detached, read-only construction of `AuditedBackend` (§7.1), because the current
  constructor requires a metadata root and a detached root has none.
- One new error, `PendingUnresolved`, and the shared post-recovery gate that raises it
  from `register_root`'s existing-chain arm, `append_intent`, and `run_transaction` (§10).
- The tests, architecture-guard changes, and status-guard checks §14 and §15 name.

**Non-scope, each with its reason:**

- **A third validator, or a partial/ranged/streaming inspection.** One core, whole chain
  or one defect. The reasoning is `read_chain` design §2's, unchanged.
- **Repairing a malformed chain.** Inspection classifies; it never unlinks, finishes, or
  rewrites a leaf. Detached mode cannot mutate at all, structurally (§7.1).
- **Anchoring, replay, payload semantics, intent qualification.** `read_chain` design §9
  already refuses these for the reading surface, and the same refusal applies verbatim
  here. atoms carries `fulfills`' *meaning* opaquely; §4.3 checks only its referent's
  class and ancestry, which are chain structure.
- **A chain/metadata-store agreement verdict.** The taxonomy is a taxonomy of *chain*
  defects. A record that contradicts the chain is metadata-side corruption; it keeps
  raising (§12), and detached mode cannot see a store at all.
- **Directory walking in capture.** `capture_states` states exactly the paths it is
  given (§9). Enumeration is the consumer's projection, by the consumer's grammar.
- **A package-wide re-export.** `atoms.coordinator.__all__` stays `()`; the precedent in
  `test_coordinator_architecture.py:78` is unchanged.
- **Chain compaction or a size bound.** Still A7 design §16 gap 4.

## 3. Seam review against the deferred-obligation ledger

[`docs/deferred-obligation-ledger.md`](../deferred-obligation-ledger.md) has **no open
obligations**. This design adds none. Each candidate was checked against the tree.

| Candidate shape | Verdict | Why |
| --- | --- | --- |
| A consumer receives a structural verdict and could read it as an anchoring or replay verdict | **Not an admitted shape** | Identical to the entry `read_chain` design §3 already refused: no atoms sub-plan will ever own anchoring, and A7 design §2 assigns anchor carriage and verification to science. It is a documented non-guarantee (§13), not an entry with a fictional owner. |
| Detached inspection runs with no lock, so a concurrent writer can make its verdict false | **Documented non-guarantee, no owner** | The remedy is not a future sub-plan; it is the caller's obligation to inspect a root no engine is live on, and the one production caller inspects an arriving copy by construction (§7.3). Adding a lock would require a metadata root the arriving root does not have. |
| `capture_states` can be asked for a path whose entry kind is outside the closed `PathState` vocabulary (FIFO, socket, device) | **Refused, not admitted** | §9.3 refuses with `PreconditionRefused` naming the path and the observed kind. The vocabulary is closed by authority §6; a widening is a design act, not a capture liberty. Refusing is the A4b-1 fail-closed-platform precedent, which the ledger's prose already excludes from admission. |
| The six new invariants condemn a chain some earlier engine version legitimately produced | **Real, bounded, and closed in this commit** | §11 is the whole answer: five of the six cannot fire on an engine-produced chain and the sixth can, through today's unvalidated `fulfills`. The submission gate closes it prospectively and §11.3 enumerates the one existing artifact that trips it. Nothing is left for a later sub-plan to own. |
| A record durable *before* the submission gate lands carries an unvalidated `fulfills` that reconciliation would append | **Closed by construction at landing, stated** | §11.4. Reconciliation must complete a durable record — refusing would strand it — so the gate is deliberately a submission gate only. The set of such records in existence is empty (§11.3), and the gate makes it stay empty. |
| The public preimage/blob-read command science's §10.4 names | **Not created here** | This design neither admits nor discharges it. It is a science-side deferral whose atoms entry is written at that slice's banking, by the design act that admits it. Naming it here would add an entry with no admission. |

**No authority amendment is claimed.** §4.1's submission boundary and §12's mutation
boundary describe `TransactionSpec` submission and `ProjectApprovedSpec` mutation. The two
inspection commands and `capture_states` sit outside both, exactly as `read_chain` does.
§7.1's "every mutating command enters the lease" stays true: §6's split preserves it, and
§13.5's architecture assertion is widened rather than weakened (§14.4).

## 4. The typed validation core

`atoms/chain/inspect.py`. Two passes over a chain directory, and a third over the
linearized result. At most one defect is produced, and it is the first in the traversal
order §4.4 pins.

### 4.1 Types

```python
class DefectKind(enum.Enum):
    FOREIGN_LEAF = "foreign-leaf"
    NAME_BYTES_MISMATCH = "name-bytes-mismatch"
    UNDECODABLE_ENTRY = "undecodable-entry"
    GENESIS_COUNT = "genesis-count"
    MISSING_PREDECESSOR = "missing-predecessor"
    SIBLING_BRANCH = "sibling-branch"
    CYCLE = "cycle"
    ORPHAN_HISTORY = "orphan-history"
    SETTLEMENT_WITHOUT_REGISTRATION = "settlement-without-registration"
    SETTLEMENT_TXID_MISMATCH = "settlement-txid-mismatch"
    DUPLICATE_SETTLEMENT = "duplicate-settlement"
    DUPLICATE_REGISTRATION = "duplicate-registration"
    FULFILLS_UNRESOLVED = "fulfills-unresolved"
    DUPLICATE_FULFILLMENT = "duplicate-fulfillment"


@dataclass(frozen=True, slots=True)
class ChainDefect:
    kind: DefectKind
    subject: str | None
    detail: str


@dataclass(frozen=True)
class WellFormedChain:
    genesis_digest: str
    entries: tuple[tuple[str, Entry], ...]
    tip: str
    pending: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class MalformedChain:
    defect: ChainDefect


@dataclass(frozen=True)
class AbsentChain:
    pass


ChainInspection = WellFormedChain | MalformedChain | AbsentChain
```

Fourteen kinds, one per taxonomy row of the consumer contract's §2.1 list, in that list's
order. `subject` names the offending leaf name or entry digest, and §4.4 pins what it names
for each kind. **It is `None` for exactly one kind, `GENESIS_COUNT`** — a chain with zero or
several genesis entries has no single offending entry to name, and naming an arbitrary one
of several would invent a fact. Every other kind names something, including the two the
consuming design's gate of 2026-08-22 ruled on: `ORPHAN_HISTORY` names the **lowest
unvisited digest**, and `DUPLICATE_FULFILLMENT` names the **second committed settlement's
digest**. Both rulings are adopted here; §4.4 steps 10 and pass 3 carry them. `detail` is
the defect's one-sentence wording, minted here and
nowhere else: it is what `MalformedChain` carries to a consumer and, byte for byte, what
`ChainStateInvalid` carries on the raising path. `FULFILLS_UNRESOLVED` covers the
contract's single "`fulfills` naming a missing, non-ancestor, or non-intent entry" row;
its `detail` begins with exactly one of `missing`, `non-ancestor`, `non-intent`, so the
three variants are distinguishable without a fifteenth kind.

`WellFormedChain`'s first three members are `ChainView`'s, with the same meanings and the
same derivations (`read_chain` design §5). `pending` is defined in §4.5. The arms are
`frozen=True` and their transitive closure is immutable for the reason `read_chain`
design §3 gives for `ChainView`; `pending` is a tuple of pairs of `str`.

`ChainInspection` and its arms live in `atoms/chain/inspect.py` and are re-exported
unchanged from `atoms.coordinator.commands`, the way `Entry` already is. They are not
copied, narrowed, or wrapped.

### 4.2 Pass 1 — the leaf pass

```python
@dataclass(frozen=True, slots=True)
class ChainScan:
    found: tuple[tuple[str, str | None, Entry, bytes], ...]
    staged: bytes | None


def scan_chain_directory(
    backend: AuditedBackend, chain_fd: int
) -> ChainScan | ChainDefect: ...
```

Provenance is checked first and raises `ProtocolError` if `chain_fd` does not name the
reserved chain directory — that is engine misuse, not chain damage, and it keeps
`chain/read.py:137-138`'s guard in exactly one place. The directory is listed with
`sorted(os.listdir(chain_fd))`, which is what `chain/read.py:140` already does; an
unlistable directory keeps raising `ChainStateInvalid`, because a directory that cannot be
read yields no evidence at all and therefore no verdict.

`found` is in sorted-name order — the listing order, stated as a contract rather than
inherited from dict insertion, so §4.4's determinism does not rest on an implementation
accident.

### 4.3 Pass 2 and pass 3 — linkage and entry class

```python
def inspect_scan(scan: ChainScan) -> ChainInspection: ...
```

Pure: no backend, no descriptor, no filesystem. This is the injection point §14.2's three
unfixturable defect classes are certified through.

Pass 2 is `_linearize`'s work (`chain/read.py:88-126`), unchanged in substance and
returning a defect instead of raising: genesis count, missing predecessor, sibling branch,
cycle, orphan history.

Pass 3 is new. Over the linearized sequence, ancestry is position: the entry at index `j`
is a strict ancestor of the entry at index `i` exactly when `j < i`. This is not an
approximation — pass 2 has already proved the sequence is the single genesis-rooted chain
containing every scanned entry.

- **`SETTLEMENT_WITHOUT_REGISTRATION`** — a `SettledEntry` at index `i` whose
  `registration` does not name a `RegisteredEntry` at some index `j < i`.
- **`SETTLEMENT_TXID_MISMATCH`** — that registration's `txid` differs from the
  settlement's.
- **`DUPLICATE_SETTLEMENT`** — a second `SettledEntry` with a `txid` already settled
  earlier in the chain.
- **`DUPLICATE_REGISTRATION`** — a second `RegisteredEntry` with a `txid` already
  registered earlier in the chain.
- **`FULFILLS_UNRESOLVED`** — a `RegisteredEntry` at index `i` whose non-`None` `fulfills`
  names no entry in the chain (`missing`), names an entry at index `j >= i`
  (`non-ancestor`), or names an entry that is not an `IntentEntry` (`non-intent`).
- **`DUPLICATE_FULFILLMENT`** — a `SettledEntry` at index `i` with outcome `COMMITTED`
  whose registration's `fulfills` is `f`, where an earlier `SettledEntry` with outcome
  `COMMITTED` also settled a registration whose `fulfills` is `f`.

Two of these six exist today in weakened form: `coordinator/recover.py:162-163` and
`:190-191` raise `ChainStateInvalid` for duplicate registration and duplicate settlement,
but only for the *active record's* txid and only inside `resolve`. The core makes them
chain-wide and record-free. That is a strict strengthening and §11 shows it condemns no
engine-produced chain.

`DUPLICATE_FULFILLMENT` is checked at the **second settlement**, not the second
registration, and this placement is load-bearing: at the second registration the outcome
is not yet known, so reporting there would require a forward reference and would make the
traversal order depend on entries that come after the reported one. Checking at the
settlement keeps the whole of pass 3 a single forward scan in which the first defect is
the first *position at which the chain becomes indefensible*.

### 4.4 The pinned traversal order

Deterministic first defect means: the passes run in order, and within a pass the checks
run in the order below. The first check that fires wins; nothing later is computed.

**Pass 1 — leaves, in `sorted(os.listdir(...))` order, one leaf fully before the next.**
For each leaf, in this sub-order:

1. the leaf is `.#~stage` → set aside as bookkeeping and continue, **unless** it is not a
   readable no-follow regular file, which is `FOREIGN_LEAF` (`subject` = `.#~stage`): the
   staging leaf is engine bookkeeping only when it *is* a staged envelope; a directory or
   symlink squatting on the reserved name is a foreign object, not bookkeeping, and this
   is the one carve-out in the contract's "the staging leaf is never a foreign-leaf
   defect". **Ratified at the consuming design's gate, 2026-08-22, with the exemption's
   scope pinned to exactly this wording: the staging exemption applies to a readable,
   no-follow regular staging file, and to nothing else.** A `.#~stage` that is anything
   else — a directory, a symlink, a device, or a regular file that cannot be opened
   no-follow and read — is `FOREIGN_LEAF`. The pin is what makes the carve-out testable:
   the three conditions are exactly `_read_regular`'s (`chain/read.py:41-66`), so the
   exemption's boundary is the same predicate the engine already applies to every entry
   leaf, not a second notion of readability;
2. the name is not sixty-four lowercase hexadecimal characters → `FOREIGN_LEAF`
   (`subject` = the name); this is `chain/read.py:150-151`;
3. the leaf is not a readable no-follow regular file → `FOREIGN_LEAF` (`subject` = the
   name); this is `_read_regular`'s three raises at `chain/read.py:47,54,62`, which today
   produce a `ChainStateInvalid` with no taxonomy row. A digest-named symlink, directory,
   or unreadable file is not an entry, so it is classified as what it is;
4. `entry_digest(bytes) != name` → `NAME_BYTES_MISMATCH` (`subject` = the name);
5. `decode_entry(bytes)` raises `ChainStateInvalid` → `UNDECODABLE_ENTRY` (`subject` = the
   name, `detail` derived from the decoder's own message).

Sorted-name order is byte order over the leaf names, so `.#~stage` sorts before every
digest name. That is stated, not relied on: step 1 is a set-aside, so its position in the
order changes no verdict except in the squatting case, where a defect at the reserved name
deserves to be reported ahead of a foreign leaf later in the directory.

**Pass 2 — linkage, over `ChainScan.found`.** An empty scan yields `AbsentChain()` (§4.6).
Otherwise:

6. exactly one entry has `previous is None` → otherwise `GENESIS_COUNT` (`subject` =
   `None`). An entry with `previous is None` is a `GenesisEntry` and conversely, proved at
   decode: `chain/model.py:380` requires `previous is None` for a genesis and
   `chain/model.py:387` requires a digest for every other class, so a class/linkage
   disagreement is already `UNDECODABLE_ENTRY` in pass 1;
7. for each non-genesis entry in **ascending digest order**, its `previous` is in the scan
   → otherwise `MISSING_PREDECESSOR` (`subject` = the naming entry's digest);
8. for each predecessor in **ascending digest order**, at most one successor → otherwise
   `SIBLING_BRANCH` (`subject` = the predecessor's digest);
9. walk from genesis along the successor relation; revisiting a digest → `CYCLE`
   (`subject` = the revisited digest);
10. the walk covered every scanned entry → otherwise `ORPHAN_HISTORY` (`subject` = **the
    lowest unvisited digest**, in ascending digest order over the scanned set minus the
    walked set). Naming a member of the disconnected component is strictly more useful than
    naming nothing — it is the operator's entry point into the orphan — and taking the
    lowest keeps the choice deterministic under the same ordering steps 7 and 8 use, rather
    than exposing set-iteration order. Ruled at the consuming design's gate, 2026-08-22.

Ascending digest order is the order `found.items()` already iterates in today, because
`names` is sorted and dicts preserve insertion order (`chain/read.py:140,158`). Pinning it
changes no current behavior and stops the determinism from resting on that coincidence.

**Pass 3 — entry class, over the linearized sequence, index ascending, one entry fully
before the next.** At a `RegisteredEntry`: `DUPLICATE_REGISTRATION`, then
`FULFILLS_UNRESOLVED` in the sub-order `missing`, `non-ancestor`, `non-intent`. At a
`SettledEntry`: `SETTLEMENT_WITHOUT_REGISTRATION`, then `SETTLEMENT_TXID_MISMATCH`, then
`DUPLICATE_SETTLEMENT`, then `DUPLICATE_FULFILLMENT`. `subject` is the offending entry's
digest in all six — which for `DUPLICATE_FULFILLMENT` means **the second committed
settlement's digest**, not either registration's. That follows from §4.3's timing rather
than being a separate choice: the defect is recognized at the settlement that completes the
second commitment, so the entry at the reported position is that settlement, and reporting
a registration instead would name an entry the traversal had already passed without fault.
Ruled at the consuming design's gate, 2026-08-22.

Pass 1 before pass 2 before pass 3 is not merely convenient: a leaf-level defect means the
directory does not decode to a set of entries at all, and a linkage defect means "ancestor"
is undefined, so the later passes have no well-formed input to run on. The order is forced
by what each pass presupposes.

### 4.5 `pending`

Over the linearized sequence, `pending` is

> the `(txid, entry_digest)` pair of every `RegisteredEntry` for which no `SettledEntry`
> with that `txid` appears anywhere in the chain, in the registrations' chain order.

Pass 3 has already run, so this is unambiguous: at most one registration per txid, at most
one settlement per txid, and every settlement's txid agrees with the registration it
names. `entry_digest` is the registration entry's own content name — the same string that
appears as its leaf name and as `TransactionOutcome.registration`.

**The invariant `pending` carries, stated exactly, because it differs by mode.** In
**registered** mode every digest in `pending` is the digest of an entry of `entries`; the
pairs are drawn from the linearized sequence and from nothing else. In **detached** mode
that is *not* guaranteed: §8's staged-registration rule can add one pair whose digest names
staged, non-durable bytes, so **a detached `pending` digest need not occur in `entries`**.
This is the weaker invariant deliberately, ratified at the consuming design's gate on
2026-08-22 — that contract will state that detached pending digests need not occur in
`entries` — and it is stated here rather than left for a consumer to discover, because a
reader of `WellFormedChain(genesis_digest, entries, tip, pending)` would otherwise assume
the stronger one from the shape alone. Nothing else about `pending` differs between the
modes: the pair's meaning is identical, and a detached pair is still `(txid, the content
name those bytes take)`.

A **rolled-back** settlement settles: `ChainOutcome.ROLLED_BACK` is a decided outcome, and
a rolled-back transaction is not evidence-starved. Only the *absence* of a settlement is.

### 4.6 `validate_chain` as the raising disposition

`chain/read.py`:

```python
def validate_chain(backend, chain_fd, planned=()) -> ValidatedChain:
    planned_entries = _planned(planned)
    scan = scan_chain_directory(backend, chain_fd)
    if type(scan) is ChainDefect:
        raise ChainStateInvalid(scan.detail)
    result = inspect_scan(scan)
    if type(result) is MalformedChain:
        raise ChainStateInvalid(result.defect.detail)
    entries, tip = ((), None) if type(result) is AbsentChain else (result.entries, result.tip)
    ...  # the planned-envelope check and the survivor classification, unchanged
```

`ValidatedChain` keeps its exact fields and meanings, including `tip: str | None` and the
`entries == ()` / `tip is None` shape for an existing but empty chain directory, so
`chain/append.py` and every `coordinator/recover.py` call site are unchanged. `_planned`
runs first, preserving the current ordering of `ProtocolError` against `ChainStateInvalid`.
The `AbsentChain` mapping is the one place a returned arm is flattened, and it is exact:
`_linearize` already returns `((), None)` for an empty `found`.

Consequences, stated because they are behavior changes on the raising path:

- `validate_chain` now raises for the six §4.3 invariants as well. §11 is the assessment.
- `_read_regular`'s failures now arrive with a `FOREIGN_LEAF` wording rather than their
  three current bespoke messages. The exception type is unchanged.
- `coordinator/recover.py:162-163` and `:190-191` become unreachable for the chain-wide
  cases the core now catches earlier. They stay, because they also express the
  *record-relative* claim ("this transaction's registration"), which the core does not
  make; a duplicate-free chain can still contradict a record (§12).

## 5. The public surface, frozen

In `atoms/coordinator/commands.py`:

```python
def inspect_chain(
    backend: Backend,
    project_root: str,
    metadata_root: str,
    storage: StorageProfile,
) -> ChainInspection: ...


def inspect_chain_detached(
    backend: Backend,
    project_root: str,
) -> ChainInspection: ...


def capture_states(
    backend: Backend,
    root: str,
    paths: tuple[str, ...],
) -> tuple[tuple[str, PathState], ...]: ...
```

`inspect_chain`'s four parameters are the four every other command takes, in the same
order with the same names and types. `inspect_chain_detached` takes **two**: an arriving
root has no metadata root and no storage profile, and accepting either would be a
parameter the caller could only fabricate. `capture_states` takes a bare `root` because it
serves both a project root and a world root and asserts nothing about either being
registered.

`__all__` becomes, in the module's existing plain-sorted order:

```python
__all__ = (
    "AbsentChain",
    "ChainDefect",
    "ChainInspection",
    "ChainView",
    "DefectKind",
    "Entry",
    "MalformedChain",
    "TransactionOutcome",
    "WellFormedChain",
    "append_intent",
    "capture_states",
    "inspect_chain",
    "inspect_chain_detached",
    "read_chain",
    "register_root",
    "run_transaction",
)
```

`PathState` is **not** re-exported: it is `atoms.core.fingerprint.PathState`, and the one
production consumer already imports it from there directly, along with `ABSENT` and the
three arms it currently needs — `AbsentState`, `DirectoryState`, `FileState`
(`science/root.py:42`; `SymlinkState` it does not import today, and will when its
projection covers symlink states). That is exactly how this module's `Entry` re-export
leaves the four entry arms in `atoms.chain.model`. Adding a second spelling of one type is
how a vocabulary forks.

## 6. `inspect_chain` — registered mode

### 6.1 The lease split

`coordinator/root.py:27-57` today is one context manager that acquires the lock, reclaims
probe survivors, binds the volume, opens the store, reclaims orphans, calls `resolve`, and
yields. The consumer contract pins an order in which structural inspection runs **before**
resolution, so the manager splits at exactly that seam:

```python
@contextlib.contextmanager
def _project_lease(backend, project_root, metadata_root, storage) -> Iterator[Lease]:
    # lines 41-55 of today's _recovery_lease, verbatim: audited backend, project lock,
    # reclaim_probe_survivors, bind_project_volume, open_store, _reclaim_orphans.
    ...
    yield Lease(_binding=binding, _store=store)


@contextlib.contextmanager
def _recovery_lease(backend, project_root, metadata_root, storage) -> Iterator[Lease]:
    with _project_lease(backend, project_root, metadata_root, storage) as lease:
        resolve(lease._binding, lease._store)
        yield lease
```

`_recovery_lease` keeps its name, its signature, and its meaning — "recovery has resolved
before this yields" — so `register_root`, `append_intent`, `run_transaction`, and
`read_chain` are unchanged, and `read_chain` design §8's recovery-barrier property is
preserved exactly.

**What the split gives up, stated.** `Lease` today carries an implicit invariant that
nothing else does: every `Lease` a caller can hold is one `resolve` has already run over.
`_project_lease` yields a `Lease` for which that is *not* true, so the invariant weakens
from a property of the type to a property of the two constructors — `_recovery_lease`'s
holds it, `_project_lease`'s does not. Nothing in `Lease` or `Store` enforces the
difference, so the compensation is the guard: §14.4's clause makes `inspect_chain` the
**only** public function permitted to name `_project_lease` or `resolve`, and asserts
`_recovery_lease` is literally `_project_lease` plus `resolve`. A second unresolved-lease
caller cannot appear without turning that guard red.

**Why reclamation stays above the split.** Ledger #17 requires probe reclamation at *every*
lease entry, and #23 requires orphan reclamation at every lease entry — the comments at
`root.py:45-47` and `:52-54` say so, and #23's holds "only if it runs even when resolution
then refuses, halts, or traps." `inspect_chain` is a lease entry. If inspection could skip
reclamation on a damaged chain, both ledger sentences would become false at the one
entry point most likely to meet a damaged root. They stay above the split because neither
reads or writes the chain and neither can: `reclaim_probe_survivors` empties
`metadata_root/probe/` under the held lock (`fs/bootstrap.py:114-133`), and
`_reclaim_orphans` removes workspaces no `transaction_record` names and blobs no `blob`
row names (`coordinator/lease.py:24-42`). An orphan is unreferenced by definition, so its
disposal depends on no chain fact. `resolve` is the opposite: it interprets the chain and
can append to it (`recover.py:625,640`), which is exactly what must not happen over damage.
The split is drawn along "does this step read or write the chain", and only `resolve`
does.

### 6.2 Choreography

```python
def inspect_chain(backend, project_root, metadata_root, storage) -> ChainInspection:
    with _project_lease(backend, project_root, metadata_root, storage) as lease:
        first = _inspect_under(lease)                 # structural inspection
        if type(first) is MalformedChain:
            return first                              # recovery never runs over damage
        resolve(lease._binding, lease._store)
        return _inspect_under(lease)                  # inspect again, and return
```

In order: lock → reclaim probe survivors → bind → open store → reclaim orphans →
**structural inspect** → malformed **returns without resolving** → resolve →
**inspect again** → return.

`_inspect_under` opens `.#~chain/` under the held project-root descriptor and runs the
core. It does **not** go through `_registered_root`, which raises where inspection must
classify; `_registered_root` stays exactly as it is for the three mutators and
`read_chain`. Its errno handling is reproduced with different dispositions:

| Chain leaf | `_registered_root` (`recover.py:731-742`) | `_inspect_under` |
| --- | --- | --- |
| absent, no live record | `PreconditionRefused` | `AbsentChain()` |
| absent, live record | `ChainStateInvalid` | `ChainStateInvalid`, unchanged (§12) |
| `ENOTDIR`/`ELOOP`/`EXDEV` | `ChainStateInvalid` | `MalformedChain(FOREIGN_LEAF, ".#~chain")` |
| present, zero entries, no live record | `PreconditionRefused` | `AbsentChain()` |
| present, zero entries, live record | `ChainStateInvalid` | `ChainStateInvalid`, unchanged (§12) |

The third row is the taxonomy doing its job: the reserved chain name occupied by a file or
a symlink is a foreign object at a reserved position, which is what `FOREIGN_LEAF` means,
and it is precisely the tampering an arriving-root verdict must *report* rather than crash
on. Registered mode classifies it identically because the structural inspection runs before
`resolve` ever reaches its own raise at `recover.py:619-622`.

The last two rows rule the **existing but empty chain directory**, and they split on the
store exactly as rows 1 and 2 do, because it is the same question. With **no live record**,
an empty chain directory is `AbsentChain()`: a chain with no entries makes no claim, and
the empty directory is a legitimate state — `bootstrap_chain` creates it before the genesis
append (`chain/append.py:264-277`), so a crash between those two steps leaves exactly this.
It is not `GENESIS_COUNT`, which is reserved for a *non-empty* chain with zero or several
genesis entries. With a **live record**, it is a chain/store contradiction and keeps
raising `ChainStateInvalid` — that is `recover.py:748-752`'s existing rule ("a live
transaction record exists without a chain genesis", also raised by `resolve` at
`recover.py:626-629`), and §12's rule that a record contradicting the chain is metadata-side
corruption with no taxonomy row. Reporting `AbsentChain()` there would have `inspect_chain`
tell a consumer that a root with a live transaction has no history.

**Detached mode has no store to consult**, so it cannot make the split and does not try:
an empty chain directory is `AbsentChain()`, unconditionally. That is not the registered
rule weakened — it is the same rule under strictly less evidence, and it is the reason the
contradiction has no taxonomy row in the first place (§12).

All three dispositions above are **ratified at the consuming design's gate, 2026-08-22**,
and unchanged from this design's own ruling: that contract now defines `AbsentChain` as
*no durable chain claim*, under which an empty directory with no live record is absent, an
empty directory with a live record is the chain/store contradiction §12 keeps raising, and
detached mode's unconditional `AbsentChain()` is the correct reading of the same
definition with no store to consult. Nothing here is provisional.

### 6.3 Why twice

The first inspection decides whether resolution is safe. The second describes what
resolution left: `resolve` can append a registration or a settlement
(`recover.py:281-287`) and discharges the staging survivor (`recover.py:263`), so a
verdict taken before it would be stale in exactly the way `read_chain` design §8 explains.
Both inspections run the same core over the same directory; only the disposition differs.

The second inspection can be `MalformedChain` where the first was `WellFormedChain`, in one
bounded case: reconciliation completing a record whose `spec.fulfills` predates the §11
submission gate. That result is returned as it stands — the command reports the chain it
found. §11.4 shows the set of such records is empty.

## 7. `inspect_chain_detached` — detached mode

### 7.1 The detached facade

`AuditedBackend.__init__` (`fs/audit.py:67-96`) requires a non-empty `metadata_root`
distinct from `project_root`, and `validate_chain`'s scan requires the chain descriptor to
carry `Provenance(RootKind.PROJECT, CHAIN_LEAF)`. A detached root has no metadata root, and
fabricating one would be a silent fallback of exactly the kind the conventions forbid —
worse, a fabricated metadata root under the project root would reclassify chain leaves as
`METADATA` targets. So this design adds an explicit second construction:

```python
@classmethod
def detached(cls, inner: Backend, *, project_root: str) -> AuditedBackend: ...
```

It registers one root — `Provenance(RootKind.PROJECT, "")` — leaves `_metadata_leaf`
`None` (so `_is_metadata_root_child` is constantly false), and sets a `_detached` flag
whose sole effect is that every mutating method raises `ProtocolError("a detached backend
performs no mutation")` as its first statement. That is stronger than a convention: the
detached scan's read-only property is a property of the *facade*, not of the care taken by
its callers, and §14.4 asserts it over the method list rather than over one call site.

The read path needs nothing else, and the argument has to cover **both** detached callers,
not only the chain scan. The methods the scan uses — `open_root`, `open_child_directory`,
`open_regular_nofollow`, `close_fd` — touch only `register`/`provenance_of` and never
`_classify_child`, `_classify_provenance`, or `_audited`
(`fs/audit.py:261-296,342-348`). §9's `capture_states` reaches two more through
`_capture_path` and `Observation`, and both have the same shape:
`open_directory_handle` (`fs/audit.py:291-296`) resolves provenance, joins the path, opens,
and registers; `symlink_fingerprint` (`fs/audit.py:349-354`) resolves provenance, validates
the join for its side effect, and delegates. Neither classifies a target and neither is
`_audited`, so both run unchanged against a facade holding one root — which is what makes
one facade serve inspection and capture rather than two.

### 7.2 Choreography

```python
def inspect_chain_detached(backend, project_root) -> ChainInspection:
    audited = AuditedBackend.detached(backend, project_root=project_root)
    root_fd = audited.open_root(project_root)
    try:
        return _inspect_detached(audited, root_fd)
    finally:
        audited.close_fd(root_fd)
```

No lock, no volume binding, no storage profile, no store, no `resolve`, no
`_require_chain_publication`, no `bootstrap_chain`. The same `_inspect_under` table of
§6.2 applies with one difference: there is no store, so the "absent, live record" row
cannot be distinguished and an absent chain is simply `AbsentChain()`.

A project root that cannot be opened at all raises the backend's `OSError`. That is a
caller error — a path that is not a directory is not a root whose chain could be absent —
and inventing a verdict for it would be the silent fallback again.

### 7.3 What the detached scan does and does not give

It **gives**, unconditionally and without a lock:

- **content-name integrity of every leaf it read.** Each entry's bytes hash to its own
  name, which is self-certifying and cannot be made false by concurrency;
- **canonical decodability** of those bytes, and their entry class;
- **the linkage and entry-class relation over exactly the set it read** — genesis
  uniqueness, single-successor linearity, ancestry, settlement and `fulfills` invariants;
- **`pending` over that set**, honestly unresolved: no recovery has run, so an unsettled
  registration is reported as unsettled even where a live engine would have settled it.

It **does not give**:

- **that the set it read is the durable chain at any instant.** There is no lock. On a root
  that *is* live elsewhere, a cooperating writer can append between the `listdir` and the
  reads, in which case the scan reports a tip that is no longer the tip; or unlink a stage
  leaf mid-`transfer_noclobber`, in which case a leaf listed a moment earlier fails to open
  and the scan reports `FOREIGN_LEAF` on a chain that is in fact intact. **Detached mode
  can report a spurious defect against a concurrently written root.** That is not a bug to
  be fixed by retrying; it is the price of having no metadata root to lock against, and it
  is why the import boundary is its only supported caller: an arriving root is a directory
  copy in the importer's possession, with no engine live on it by construction.
- **recovery completion.** A staged survivor, a registration awaiting settlement, and an
  orphan workspace all stay exactly as they arrived. This is the point: the arrival
  boundary must judge what was *sent*, not what a local recovery would have made of it.
- **agreement with any metadata store**, because there is none to read.
- **anchoring or replay**, as always (`read_chain` design §9).

## 8. The staging leaf in both modes

The fixed staging leaf `.#~stage` is engine bookkeeping and is **never** a foreign-leaf
defect, in either mode, with the single carve-out §4.4 step 1 states. That carve-out's
scope is pinned, as ratified at the consuming design's gate on 2026-08-22:

> **The staging exemption applies to readable, no-follow regular staging files only.** A
> `.#~stage` that is anything else — a directory, a symlink, a device, or a regular file
> that cannot be opened no-follow and read — is `FOREIGN_LEAF`, not bookkeeping.

The three conditions are `_read_regular`'s (`chain/read.py:41-66`), so the exemption ends
exactly where the engine's own notion of a readable entry leaf ends.

**Registered mode** handles survivors exactly as `validate_chain` does today. Concretely:

- the **first** (pre-resolve) inspection sets the survivor aside and reports it in neither
  `pending` nor a defect. It is about to be adjudicated; classifying it would be a claim
  made microseconds before the evidence that decides it;
- if the first inspection is `MalformedChain`, the survivor is left untouched along with
  everything else. Recovery never runs over damage, and the survivor is not the damage;
- `resolve` adjudicates it through the unchanged `SurvivorAction` path
  (`chain/read.py:167-179`, `chain/append.py:133-182`): `FINISH` when it is a planned
  envelope not yet durable, `REMOVE` otherwise;
- the **second** inspection therefore sees no survivor. If one is nevertheless present,
  that is `recover.py:746-747`'s existing impossibility and it keeps raising
  `ChainStateInvalid`; `_inspect_under` reproduces that check rather than inventing a
  fifteenth defect for an engine-internal contradiction.

**Detached mode** cannot adjudicate: `FINISH` versus `REMOVE` is decided by the active
record's planned envelope (`chain/read.py:170-178`), and there is no store. It must
neither finish nor remove — it cannot, structurally (§7.1) — and it must not call the
survivor a defect. So it classifies it as **pending-adjacent bookkeeping**:

- the bytes decode canonically, link from the validated tip, and decode to a
  `RegisteredEntry` → its `(txid, entry_digest(staged))` pair joins `pending`, after the
  durable pending pairs. The digest is well-defined — it is the content name those bytes
  would take — and the claim the pair makes is true and is the one the consumer needs:
  a registration for this txid is unsettled. It errs toward reporting *more* pending, which
  is the safe direction: the consequence of a pending report is a refusal, and the
  consequence of a missed one is an admission;
- the bytes are undecodable, do not link from the tip, name an already-durable entry, or
  decode to any other entry class → **inert**: not a defect, not pending, not reported. A
  stale or non-registration survivor makes no claim about settlement, and it is exactly the
  debris `resolve` would have removed.

**This first rule is ratified at the consuming design's gate, 2026-08-22**: staged
registration evidence **stays** in `pending`, and that contract will state that detached
pending digests need not occur in `entries`. What it weakens is stated plainly rather than
buried, because a reader of the pinned
`WellFormedChain(genesis_digest, entries, tip, pending)` would otherwise assume from the
shape that every digest in `pending` also appears in `entries`: under this rule a detached
inspection can return a pair whose digest names bytes that are staged and **not durable**,
resolving to no member of `entries` (§4.5). The ruling went this way because the
alternative — dropping the survivor entirely — silently under-reports pending at exactly
the boundary whose job is to refuse an arrival carrying unsettled work, and the contract's
own instruction is that the survivor be *reported*, never silently finished or removed.
The over-report is a refusal; the under-report is an admission.

## 9. `capture_states`

### 9.1 What it is

`_capture_path` (`commands.py:108-132`) is the engine's own single-path fingerprint: it
walks the named path's components with `open_child_directory`, observes the leaf through
one `Observation`, and returns a `PathState` from the closed vocabulary — `AbsentState`,
`FileState`, `DirectoryState`, `SymlinkState` (`core/fingerprint.py:16-47`). It stays
private: its signature leaks the audited backend and a root descriptor. `capture_states`
is the public batch wrapper.

```python
def capture_states(backend, root, paths) -> tuple[tuple[str, PathState], ...]:
    audited = AuditedBackend.detached(backend, project_root=root)
    root_fd = audited.open_root(root)
    try:
        with Observation(audited) as observation:
            return tuple(
                (path, _capture_path(audited, root_fd, path, observation))
                for path in _require_capture_paths(paths)
            )
    finally:
        audited.close_fd(root_fd)
```

**Exactly the named paths, in the caller's order, one pair each.** No directory walking, no
enumeration, no inference of siblings. What to enumerate is the consumer's projection under
the consumer's grammar; what a path *is* is the engine's, and this command is what makes
"no second summary model" a mechanism rather than a promise.

**One `Observation` across the whole batch.** That is the coherence the command exists to
provide: identity pinning is shared, so no inode observed early in the batch can be
recycled onto a different entry later in it (`fs/observe.py:73-84`). A caller looping over
a single-path command would get *n* incomparable observations.

The same detached facade as §7.1 serves it: `capture_states` takes no lock and asserts
nothing about registration. Serialization is the caller's, and science's audit boundary
holds the subject's operation lock across inspection and capture precisely so the verdict
and the surface it judged are one view.

### 9.2 Path admission

`_require_capture_paths` refuses, with `PreconditionRefused`, a `paths` that is not an
exact tuple of exact `str`, that contains a duplicate, or that contains a path
`require_rel_path` rejects. It does **not** require sortedness: sorted order is
`registered_surface`'s chain requirement (`commands.py:96-97`), not a property capture
needs, and the returned pairs are in the caller's given order so the caller can zip them
against its own list. Duplicates are refused rather than deduplicated, because two rows for
one path invite a coherence question that has no good answer.

### 9.3 Two `_capture_path` corrections

Both fix present defects and both are shared with `register_root`'s baseline capture,
which is the point of not writing a second implementation.

- **Unrepresentable and unreadable entries.** `Observation.observe` can return
  `ObservedUnrecognized` (a FIFO, socket, or device), `ObservedInaccessible`, or
  `ObservedContended` (`fs/observe.py:117,131,133`). `_capture_path` casts to the three
  stateful arms and reads `.state` (`commands.py:130`), so today those three produce an
  `AttributeError` — an internal traceback for an external condition. They become
  `PreconditionRefused` naming the path and the observed class. The `PathState` vocabulary
  is closed by authority §6; a path outside it is refused, never coerced to `ABSENT` and
  never widened here.
- **A non-directory in an intermediate component.** `_capture_path` returns `ABSENT` for
  `ENOENT` on an intermediate component (`commands.py:120-122`) but lets `ENOTDIR` and
  `ELOOP` escape as `OSError`. They join `ENOENT`: the named path does not exist when its
  parent is a file or a symlink, and the parent's own state is separately capturable by
  naming the parent — which the consumer's projection does. This makes `register_root`'s
  baseline capture more permissive; no live chain is affected, because both science root
  initializers register an empty surface and therefore capture no baseline path at all.

## 10. The pending gate

### 10.1 The rule

> After recovery has resolved, a chain carrying **any** unsettled registration refuses
> `register_root`'s existing-chain arm, `append_intent`, and `run_transaction` with
> `PendingUnresolved`.

Post-recovery is what makes the refusal honest. `resolve` rolls the active transaction
forward or back and appends the settlement (`recover.py:216-234,270-292`), and a durable
registration always had a durable record before it (`execute.py:167-178` appends the
registration only after `prepare_transaction`). So a registration that is still unsettled
*after* resolution has no record to settle it: the metadata store that held its evidence is
gone. That is the copied-root case exactly, and the log design's "further mutation refused"
is what this gate finally makes true.

### 10.2 One check, three placements

```python
def _require_no_pending(entries: tuple[tuple[str, Entry], ...]) -> None:
    pending = _pending_of(entries)
    if pending:
        raise PendingUnresolved(
            "the chain carries unsettled registrations: "
            + ", ".join(f"{txid} at {digest}" for txid, digest in pending)
        )
```

`_pending_of` is §4.5's function, imported from the core — the same computation that fills
`WellFormedChain.pending`, so the gate and the report can never disagree.

- **`register_root`** — immediately after `_validate_chain` returns and the survivor
  assertion passes, *before* the `if validated.entries:` branch at `commands.py:156`. One
  unconditional call, which is nonetheless the existing-chain arm's gate and only its: a
  fresh chain has no entries, so `_pending_of(())` is `()` and the check is vacuous on the
  bootstrap path. Placing it before the genesis/surface comparison rather than after is
  deliberate — the dangerous outcome on that arm is the *idempotent success return* at
  `commands.py:164`, and one uniform rule ("an unsettled chain is not a chain you may
  re-register against") is worth more than the marginally more informative
  `PreconditionRefused` a mismatched payload would otherwise have produced. Both refuse.
- **`append_intent`** — inside the `_registered_root` block, before `_append_entry`
  (`commands.py:194-197`).
- **`run_transaction`** — inside the `_registered_root` block, before `_run_under_lease`
  (`commands.py:213-216`), and before §11's `fulfills` gate: an unsettled chain is refused
  whatever the new spec says.

`read_chain`, `inspect_chain`, `inspect_chain_detached`, and `capture_states` do **not**
gate. Reads must serve the copied-root diagnosis: the whole purpose of the inspection
commands is to let a consumer *see* the pending set and report it, and a gate on the read
would make the diagnosis unreachable by the very state it diagnoses. For the inspection
commands the refusal would also be redundant — `pending` is already in the returned value,
as data.

### 10.3 The error

```python
class PendingUnresolved(AtomsError):
    """The chain carries a registration that survived recovery unsettled."""
```

In `atoms/chain/errors.py`, beside `ChainStateInvalid`, as a direct `AtomsError` subclass.
Not a `PreconditionRefused` subclass and not a `ChainStateInvalid` subclass, for the reason
`ProjectApprovalRefused`'s docstring already states about non-substitutable refusals: the
chain is *not* invalid — it is well-formed and says something definite — and a consumer
catching concurrent-drift refusals must not swallow "your evidence is gone, and no retry
will bring it back." The three commands' contracts gain one error each.

## 11. The new invariants and the `fulfills` submission gate

The consumer contract requires that the raising paths refuse the six §4.3 invariants too —
one core, one taxonomy — and that this design assess whether any legitimately produced
existing chain could trip them. It could. Here is the whole of it.

### 11.1 Five that cannot fire on an engine-produced chain

`SETTLEMENT_WITHOUT_REGISTRATION`, `SETTLEMENT_TXID_MISMATCH`, `DUPLICATE_SETTLEMENT`,
`DUPLICATE_REGISTRATION`, `DUPLICATE_FULFILLMENT`. The argument is the append discipline,
and it is short because the discipline is narrow:

- **Every chain entry is appended by `append_entry`**, which re-validates the whole
  directory, refuses if it changed, and links the new envelope from the validated tip
  (`chain/append.py:211-253`). There are exactly two callers that append a registration or
  a settlement — the executor (`execute.py:174`) and reconciliation
  (`recover.py:281-287`) — and both run under the held project lock inside the lease. So
  the chain is linear and append-only, and **every earlier entry is a strict ancestor of
  every later one**. `SETTLEMENT_WITHOUT_REGISTRATION` and the ancestry half of the
  taxonomy cannot fire on ordering grounds.
- **A settlement's `registration` and `txid` are copied from the record**, never
  reconstructed: `recover.py:216-221` builds `SettledEntry(txid=record.txid,
  registration=record.registration_digest, ...)` where `registration_digest` was written
  by the same transaction that appended the registration (`execute.py:176-178`). So
  `SETTLEMENT_TXID_MISMATCH` cannot fire.
- **Both duplicates are already refused per-txid inside `resolve`**
  (`recover.py:162-163,190-191`), and the append side is guarded by
  `record.registration_digest is None` / `record.settlement_digest is None`
  (`recover.py:172,200`). A chain-wide duplicate therefore needs *two different
  transactions to share a txid*, and a txid is 128 bits of `secrets.token_hex(16)` checked
  against the store before use (`admission.py:32-38,72-73`). The chain-wide check rests on
  exactly the collision assumption `admit`'s own loop already rests on.
- **`DUPLICATE_FULFILLMENT`** requires two committed registrations naming one intent. The
  engine mints no `fulfills` value at all, so this can only follow from a consumer doing it
  — which §11.2's **separate** duplicate-fulfillment submission check gates, at a strictly
  earlier state than the settlement-time check §4.3 performs.

### 11.2 The one that can — and the gate that closes it

`FULFILLS_UNRESOLVED` **can** fire on a chain the current engine produced. `fulfills`
reaches the chain from `TransactionSpec.fulfills` through `compile_spec` and
`_registration_entry` (`core/compiler.py:559`, `recover.py:136`), and the only validation
anywhere is shape: `core/spec.py:47-51` requires sixty-four lowercase hex characters and
nothing else. Nothing checks that the digest names an entry, that the entry is an
`IntentEntry`, or that it is an ancestor. A consumer can pass any hex string and the engine
will durably record it.

So the core cannot simply start condemning those chains without the engine also stopping
producing them. The gate:

> **`run_transaction` validates `fulfills` against the validated chain in hand, before the
> transaction's record becomes durable.** Two independent checks, both refusing with
> `PreconditionRefused`:
>
> - **the referent check** — a non-`None` `fulfills` that **is not the digest of an entry
>   of the validated chain**, or names an entry that is not an `IntentEntry`;
> - **the duplicate-fulfillment check** — a `fulfills` naming an intent that is **already
>   committed-fulfilled** in the validated chain, i.e. some earlier `RegisteredEntry` names
>   the same intent and carries a `SettledEntry` with outcome `COMMITTED`.

**The referent condition is membership, not strict ancestry of the tip**, and the
difference is exactly one entry — the tip itself. A consumer that appends an intent and
then immediately submits the transaction fulfilling it names a `fulfills` that *is* the
tip, which is the ordinary post-intent shape, not an anomaly: science's operation port does
exactly this on its post-intent refusal path (`root.py:469-478`'s `execute_fulfilling`
called from `corpus.py:1116` with the digest `corpus.py:1075`'s `append_intent` returned,
with no intervening append), and its acceptance suite pins the resulting three-entry
sequence `(IntentEntry, RegisteredEntry, SettledEntry)` at
`tests/acceptance/test_n2_cut5.py:251`. A strict-ancestor-of-the-tip condition would refuse
that live path. Membership is also not a weakening: the entry the gate is about to append
lands *after* the tip, so under the append discipline (§11.1) every entry of the validated
chain — the tip included — is a strict ancestor of it.

**On sharing with §4.3 — the referent check only.** The earlier draft of this section
claimed one shared predicate covering all of it. That claim conflated two *times* and is
withdrawn. The two checks divide as follows, and the division is not stylistic:

- **The referent check is genuinely one predicate at two indices.** The gate asks "is
  `fulfills` a member of the validated chain, and is that member an `IntentEntry`"; §4.3
  asks "is the referent's index strictly below the referencing registration's index, and is
  it an `IntentEntry`". For a to-be-appended registration these are equal, by the paragraph
  above: the hypothetical next entry's index is `len(entries)`, so "index < len(entries)"
  *is* membership. The code makes the equivalence structural rather than argued — the core
  exports one helper taking the referencing entry's index, §4.3 calls it with the
  registration's own index, and the gate calls it with `len(validated.entries)`, the index
  the registration is about to occupy. This helper covers exactly the `missing`,
  `non-ancestor`, and `non-intent` referent faults, and nothing else.
- **The duplicate-fulfillment check cannot be the same predicate, because the two run
  against different states.** §4.3 recognizes `DUPLICATE_FULFILLMENT` only at the **second
  committed settlement** — the position at which the second commitment exists at all — and
  at submission time that settlement has not been appended, has not been decided, and may
  never be: the transaction being gated can still roll back, in which case no defect would
  ever have arisen. So the gate cannot evaluate §4.3's condition; it evaluates a *different,
  strictly earlier* condition over the committed fulfillments already durable in the
  validated chain. Two checks, two states, two call sites, and the design says so instead of
  claiming a sharing that does not exist.

What the gate therefore guarantees about duplicates is correspondingly narrower and is
stated exactly: **a `fulfills` the duplicate check rejects would have become
`MalformedChain(DUPLICATE_FULFILLMENT)` if the transaction had been appended *and
subsequently committed*** — not merely appended. A registration naming an
already-committed-fulfilled intent that then rolls back yields a well-formed chain, which is
why the gate is a deliberate over-refusal at submission rather than a mirror of the
inspection. It refuses the attempt because the attempt's success is what would condemn the
chain, and refusing before any record exists is the only point at which refusing is free.

Placement: inside `run_transaction`'s `_registered_root` block, after the pending gate and
**before** `_run_under_lease` (`commands.py:213-216`) — that is, before `admit`, before
`prepare_transaction`, before any record exists. A spec whose referent is unresolvable would
mint a condemnable entry outright; a spec the duplicate check rejects would mint one only on
commit. Both are refused at the one point where refusing is free.

`append_intent` needs no such gate: an `IntentEntry` carries only an opaque payload.
`register_root` needs none: a `GenesisEntry` carries no `fulfills`.

### 11.3 The existing artifacts that trip it

**The gate is approved**, at the consuming design's gate on 2026-08-22, including the
`test_coordinator_run.py` edit below with its ledger-row-27 carriage assertion preserved.
Nothing in this section is provisional; it records what lands with the implementation.
Checked against both trees rather than assumed:

- **science does pass `fulfills`, on a live path**, and the mechanism is what makes it
  safe rather than its absence. `root.py:469-478`'s `execute_fulfilling(plan, fulfills)`
  threads a consumer-supplied digest into the spec, and `corpus.py` calls it at `:1116`
  (the post-intent refusal path) and `:1125` (the success path). In both, the digest is
  the return value of `corpus.py:1075`'s own `append_intent` on **the same chain, earlier
  in the same operation**. So the referent always exists, is always an `IntentEntry`, and
  is always an ancestor: `missing`, `non-intent`, and `non-ancestor` are all unreachable by
  construction, not by abstention. The two call sites are in mutually exclusive branches of
  one `try`/`except` — the refusal path returns or re-raises, the success path is reached
  only when no `ScienceError` was caught — so **one intent digest is fulfilled at most
  once** and `DUPLICATE_FULFILLMENT` cannot fire either. **No science-built chain is
  condemned**, for that stated reason. This is also why the C1 correction above is
  load-bearing rather than cosmetic: `:1116`'s fulfillment names the tip.
- **atoms** has exactly one artifact that trips the gate:
  `python/tests/test_coordinator_run.py:351-382`
  (`test_registration_carries_the_specs_consumer_intent_and_surface_projection`) runs a
  transaction with `fulfills="b" * 64` — a digest naming nothing — and asserts the
  registration carries it. Under the gate that call refuses.

  That test is not free to be deleted or relaxed: it is one of the two suites the
  deferred-obligation ledger names as the **verification of row 27** (the intent API and
  the engine's opaque carriage of a boundary-supplied `fulfills` into the fulfilling
  transaction's `registered` entry, discharged by A7b on 2026-08-14). The ledger's rule is
  that an entry is removed only when its owning sub-plan lands *and its verification suite
  covers it*; an edit that dropped the carriage assertion would silently un-verify a
  discharged row. So the edit is constrained: the test changes in the same commit to append
  a real intent through `append_intent` and fulfill *that* digest, and it **keeps the
  carriage assertion verbatim** — `registered.fulfills == <the appended intent digest>` —
  so row 27's verification is preserved with a resolvable referent instead of an
  unresolvable one. A new sibling test asserts the `PreconditionRefused` for the
  unresolvable spelling. The test is corrected, never exempted, and nothing it proved
  before is weakened.

Nothing else in either tree sets `fulfills`.

### 11.4 Reconciliation is deliberately not gated

`_derive_reconciliation` can append a registration derived from an already-durable record
(`recover.py:179-183`). Applying the gate there would let a record become unresolvable —
refusing would strand a durable transaction with no way forward, which is the one thing
recovery may never do. So the gate is a **submission** gate: a record that exists must be
completed, and if its `spec.fulfills` predates the gate the resulting chain is reported
`MalformedChain` by the second inspection (§6.3) rather than silently accepted. That set is
empty at landing by §11.3 and stays empty because the gate runs before any record is
created.

## 12. Errors

`inspect_chain` and `inspect_chain_detached` **never raise on structural damage**. They can
still raise, and the distinction matters:

| Condition | `inspect_chain` | `inspect_chain_detached` |
| --- | --- | --- |
| Any of the fourteen structural defects | `MalformedChain(defect)` | `MalformedChain(defect)` |
| No chain directory, or an empty one | `AbsentChain()` | `AbsentChain()` |
| Chain absent, or present and empty, but a live record exists | `ChainStateInvalid` (`recover.py:734,750`) | n/a — no store |
| A record contradicts the chain | `ChainStateInvalid` (`recover.py:169,178,209,214,225,232,262`) | n/a — no store |
| Recovery halts, or a halt is durable | `TransactionHalted` (`recover.py:634,639,721`) | n/a |
| A staging survivor after resolution | `ChainStateInvalid` (`recover.py:747`) | n/a |
| The volume, mount, lock, or store refuses at lease entry | the existing errors (`root.py:44-55`) | n/a |
| `project_root` is not openable | the existing errors | the backend's `OSError` |
| Engine misuse (wrong types, unregistered descriptor) | `ProtocolError` | `ProtocolError` |

**A chain/store contradiction is not a structural defect and gets no taxonomy row.** It is
a claim about the *metadata store*, which detached mode by construction cannot see and
which the chain alone cannot decide; giving it a row would produce a defect that one mode
could report and the other could never even look for — a forked taxonomy in the one place
the contract most needs one. It keeps raising, and it escapes `inspect_chain` the way
`TransactionHalted` already escapes `read_chain` today. This is a **stated non-guarantee**
(§13): a consumer's audit of a live root can meet an exception rather than a verdict, and
its remedy is operator intervention over the metadata store, not a report.

`capture_states` raises `PreconditionRefused` for a bad `paths` tuple or an
unrepresentable/unreadable entry (§9.2, §9.3), and `ProtocolError` for engine misuse.

`PendingUnresolved` joins the contracts of `register_root`, `append_intent`, and
`run_transaction` only.

## 13. Guarantees and obligations

**The two inspection commands guarantee**, over the entries they read:

1. every leaf decoded from canonical bytes whose digest equals its filename, or a defect
   naming the leaf;
2. genesis-connected linearity — one genesis, one successor per entry, one tip, no cycle,
   no orphan — or a defect naming the offending digest;
3. the settlement and `fulfills` linkage invariants of §4.3, or a defect naming the
   offending digest;
4. **at most one defect, deterministically the first in §4.4's order**, for a given
   directory content — so two callers, two processes, and two atoms versions of this core
   report the same defect for the same bytes;
5. `pending` exactly as §4.5 defines it over the durable entries, computed by the same
   function the gate uses. In **registered** mode every `pending` digest is the digest of an
   entry of `entries`. In **detached** mode that invariant is deliberately weaker: §8's
   staged-registration rule may add one pair whose digest names staged, non-durable bytes,
   so **a detached `pending` digest need not occur in `entries`** (§4.5, §16.10 — ratified
   2026-08-22);
6. no staging leaf counted as chain state, and never a foreign-leaf defect except when the
   reserved name is occupied by a non-entry;
7. an inert return value: every field is a `str` or a tuple whose transitive closure is
   immutable, and no descriptor, `Lease`, `Store`, or `ProjectBinding` crosses the boundary.

**`inspect_chain` additionally guarantees** the project lock was held throughout, probe and
orphan reclamation ran at entry, and — for a `WellFormedChain` — that recovery resolved
before the returned verdict was taken.

**They do not claim** anchoring, replay, payload semantics, intent qualification, that a
mutation on the same root would be admitted (`inspect_chain` skips
`_require_chain_publication` for `read_chain` design §6's reason), or — for
`inspect_chain_detached` — that the entry set is complete or uncontended (§7.3).

**Obligations this design takes on and discharges in its own implementation:** the widened
architecture guards (§14.4), the `test_coordinator_run.py` change (§11.3), and the
`__all__` update (§5). **Obligations it leaves for none:** the ledger stays empty (§3).

## 14. Verification

### 14.1 Directory-constructible defect cases

Twelve of the fourteen kinds keep directory fixtures, in
`python/tests/test_chain_inspect.py`, each planting bytes in a real `.#~chain/` and
asserting the exact `DefectKind` and `subject`: foreign leaf (three spellings — a
non-digest name, a digest-named symlink, and `.#~stage` as a directory), name/bytes
mismatch, undecodable entry, zero genesis, two genesis entries, missing predecessor,
sibling branch, settlement without ancestor registration, settlement/registration txid
mismatch, duplicate settlement, duplicate registration, `fulfills` missing, `fulfills`
non-intent, duplicate committed fulfillment. Each is asserted **twice**: once as
`MalformedChain` through `inspect_chain_detached`, and once as `ChainStateInvalid` through
`validate_chain`, with the same message — that pair is what proves the taxonomy has not
forked.

Two `subject` assertions are named individually because §4.1's ruling makes them
non-obvious. The `.#~stage`-as-a-directory case asserts `subject == ".#~stage"`, and the
suite carries the ratified pin's other spellings alongside it — `.#~stage` as a symlink and
as an unreadable regular file are `FOREIGN_LEAF` too, while a readable no-follow regular
`.#~stage` is exempt (§8). The duplicate-committed-fulfillment case asserts `subject` is the
**second committed settlement's** digest, not either registration's, and its fixture makes
the distinction visible by placing the two registrations non-adjacently so a
registration-naming implementation reports a different string.

### 14.2 The three core-only classes

**`CYCLE`, `ORPHAN_HISTORY`, and the `non-ancestor` variant of `FULFILLS_UNRESOLVED`
cannot be built from a directory at all**, and the reason is the content naming itself:

- a **cycle** needs an entry whose `previous` names an entry that transitively depends on
  it; since a leaf's name is `sha256` of an envelope containing `previous`, that is a hash
  fixed point;
- an **orphan history** needs a disconnected component with valid internal linkage. In a
  directory, that component's root entry either has `previous is None` and a non-genesis
  class (refused at decode, `chain/model.py:387`), or names an entry outside the scan
  (`MISSING_PREDECESSOR`), or names one inside the connected part (`SIBLING_BRANCH`). Every
  directory spelling lands on a different defect;
- **`fulfills` naming an existing non-ancestor** needs a registration to name an entry at a
  later index, and that entry's digest depends on the registration that references it — the
  same fixed point.

So all three are certified through `inspect_scan` with an **injected `ChainScan`**: a
hand-built entry map with digests that are deliberately not their envelopes' hashes,
exercising pass 2 and pass 3 in isolation. This is honest rather than convenient — a
fabricated directory could only be built by disabling the name check, which would certify
the wrong code path. The test obligation splits exactly here: twelve kinds through both
surfaces on real directories, three classes through the pure core, and the corresponding
`ChainStateInvalid` raises for those three are asserted by calling the core-level helper
`validate_chain` delegates to.

The `ORPHAN_HISTORY` case additionally asserts §4.1's ruled subject: with an injected scan
carrying a disconnected component of several entries, `subject` is the **lowest unvisited
digest**, and the fixture is built so the lowest unvisited digest is not the component's
own root — otherwise a "name the component root" implementation would pass by accident.

### 14.3 Command, gate, and capture tests

1. `inspect_chain` on a healthy registered root returns `WellFormedChain` whose
   `genesis_digest`, `entries`, and `tip` equal the `ChainView` `read_chain` returns for
   the same root, and whose `pending` is `()`.
2. `inspect_chain` on a root with a planted structural defect returns `MalformedChain` and
   **runs no recovery**: a planted `.#~stage` survivor is still present afterwards, and the
   active record is unchanged. This is the pinned order's whole point and it is asserted,
   not narrated.
3. `inspect_chain` on a well-formed root with a `.#~stage` survivor and no active record
   returns `WellFormedChain` and the survivor is gone afterwards — the §6.3 second
   inspection, mirroring `read_chain`'s barrier test.
4. `inspect_chain` on an unregistered root returns `AbsentChain()` and leaves no chain
   directory, workspace, or record behind — the shape
   `test_append_intent_refuses_an_unregistered_root_without_transaction_artifacts` uses.
   A root whose `.#~chain` is a regular file returns `MalformedChain(FOREIGN_LEAF)`. An
   **empty** `.#~chain/` returns `AbsentChain()` with no live record and raises
   `ChainStateInvalid` with one, pinning §6.2's last two rows as a pair rather than one of
   them alone; `inspect_chain_detached` over the same empty directory returns
   `AbsentChain()` either way.
5. `inspect_chain_detached` over a copied root directory — no metadata root, no store —
   returns the same `WellFormedChain` `inspect_chain` returned for the original, and
   returns `AbsentChain()` for a copy with no `.#~chain/`.
6. `inspect_chain_detached` reports a copied root whose settlement leaf was deleted with a
   non-empty `pending` naming the surviving registration's txid and digest; a copy carrying
   a `.#~stage` registration linking from the tip reports that pair too (§8), and a copy
   carrying an undecodable `.#~stage` reports neither a defect nor a pending pair. The
   staged case asserts the ratified weaker invariant directly: that pair's digest is
   **absent** from `entries`, while every other `pending` digest is present — so the split
   §4.5 and §13 describe is pinned rather than narrated, and a later implementation that
   quietly restored the stronger invariant would turn the test red.
7. The detached facade refuses to mutate: every mutating `AuditedBackend` method raises
   `ProtocolError` on a `detached` instance, asserted over the method list.
8. The pending gate: over a root whose settlement leaf was removed by hand,
   `register_root` with the *same* payload and surface, `append_intent`, and
   `run_transaction` each raise `PendingUnresolved`, while `read_chain`, `inspect_chain`,
   `inspect_chain_detached`, and `capture_states` all still succeed and the two inspection
   commands report the pending pair. A healthy root passes all seven.
9. The gate is post-recovery: a root crash-interrupted mid-transaction, with its record
   intact, is *not* refused — resolution settles it and the command proceeds.
9a. The `fulfills` submission gate, four arms plus the boundary case: `run_transaction`
    **accepts** a `fulfills` naming the intent that is currently the tip — appended by an
    immediately preceding `append_intent`, the science post-intent-refusal shape
    (§11.2) — and the resulting chain slice is `(IntentEntry, RegisteredEntry,
    SettledEntry)`, the sequence `tests/acceptance/test_n2_cut5.py:251` pins downstream. It
    **refuses** with `PreconditionRefused` a `fulfills` naming no entry, naming a
    `RegisteredEntry` or `SettledEntry`, and naming an intent an earlier committed
    registration already fulfilled. Two companions, one per check, because the two have
    different obligations (§11.2): for the **referent** check, the gate's helper and §4.3's
    inspection agree on the same chain — every spec the gate accepts yields a
    `WellFormedChain`, and every one the referent check refuses would have yielded
    `MalformedChain(FULFILLS_UNRESOLVED)` had it been appended, asserted by appending the
    condemned entry through the core rather than through the gated command. For the
    **duplicate** check the claim is the weaker, true one: a rejected spec would have
    yielded `MalformedChain(DUPLICATE_FULFILLMENT)` had it been appended **and
    subsequently committed**, and the companion pins the other half too — the same
    registration appended and then *rolled back* leaves a `WellFormedChain`, which is why
    the gate is a deliberate over-refusal and not a mirror.
10. `capture_states` returns exactly the named paths in the given order, across all four
    vocabulary arms plus absence, and returns the identical states `register_root`'s
    baseline capture records for the same paths. It refuses a duplicate, an absolute path,
    and a FIFO with `PreconditionRefused`, and returns `ABSENT` for a path under a
    non-directory parent.
11. `capture_states` walks no directory: given a directory path it returns exactly one
    `DirectoryState` pair and no children, on a directory with children.
12. The returned arms are frozen and inert: assignment raises `FrozenInstanceError`, and a
    verdict taken under a lease is fully readable after the lease released.

### 14.4 Existing guards this changes

- `test_fs_architecture.py:1177` (`test_chain_commands_keep_the_lease_and_approval_proofs_private`)
  pins `commands.__all__` to seven names and the public function set to four. Both are
  rewritten in the same commit to §5's sixteen names and the seven public functions. Its
  "every public command enters `_recovery_lease` through a `with`" assertion is **widened,
  not weakened**, into three explicit clauses: the four mutating-or-reading commands still
  enter `_recovery_lease`; `inspect_chain` enters `_project_lease` and is the only public
  function naming `resolve`; and `inspect_chain_detached` and `capture_states` are a named
  exempt pair that must enter neither lease and must name neither
  `acquire_project_lock` nor `resolve`. A fourth clause asserts `_recovery_lease` is
  literally `_project_lease` plus `resolve`, so the split cannot drift into two lease
  bodies. The existing "no public command's annotations mention `Lease` or
  `ProjectApprovedSpec`" assertion is unchanged and covers the three new commands.
- `test_no_unregistered_public_function_accepts_the_proof` stays green: none of the three
  accepts a `ProjectApprovedSpec`.
- `test_coordinator_architecture.py:78` (`test_the_coordinator_exports_nothing`) stays
  green: no package-level re-export is added.
- `test_coordinator_run.py:351` changes as §11.3 states.

Gates: `uv run --frozen pytest`, `uv run --frozen ruff check .`, `uv run --frozen pyright src`,
all from `python/`. The pytest count is read from the summary line, not from a tail.

## 15. Documentation impact

- **`AGENTS.md`** — its `## Status` section is the A1–A8b roadmap plus the A8 note. Neither
  counts the public commands nor asserts the coordinator is write-only, so both stay true.
  No change.
- **`README.md`** — lists sub-plan documents and their state; names no command inventory.
  No change.
- **`docs/deferred-obligation-ledger.md`** — no open obligations, and §3 adds none. No
  change.
- **`python/tests/test_docs_status.py`** — `FIRST_UNIMPLEMENTED` stays `"A9"`. This is not
  a Plan A sub-plan and adds no stage. Per the conventions no per-sub-plan status test is
  added, and this document's `**Status:**` field is written so the existing guard parses
  it: it claims only A9 unimplemented and uses no ASCII-hyphen stage range.
- **[`2026-08-20-public-chain-read-design.md`](2026-08-20-public-chain-read-design.md)** —
  its §7 error table cites `chain/read.py` line numbers that the §4.6 refactor moves, and
  its §6 quotes `read_chain`'s body, which is unchanged. When the implementation lands,
  that document takes a **dated amendment parenthetical** naming this design as the
  refactor — the idiom the authority §13.5/§14 already uses. Its line citations are a
  record of what the code was on 2026-08-21, not a status claim, so they are not rewritten.
  Its `__all__` claim in §4 is likewise historical; the live machine claim is the
  architecture test, which §14.4 rewrites.
- **[`2026-08-13-plan-a7b-executor.md`](2026-08-13-plan-a7b-executor.md)** step 10.2 quotes
  the `__all__` tuple A7b's guard added. It is a historical record; it takes the same dated
  parenthetical rather than a rewrite, for the reason the `read_chain` design §11 already
  gives.

**Merge and disclosure.** This design's implementation merges on local atoms `main`. The
new head joins the consumer's recorded unpushed-disclosure ledger row: **pushing remains a
prerequisite of any integration that expects a fresh checkout to build**, and the consumer
slice's implementation must not be reported as integrable until it has been pushed.

## 16. Limitations

1. **A chain/metadata-store contradiction escapes as an exception, not a verdict** (§12).
   `inspect_chain` over a live root can raise `ChainStateInvalid` or `TransactionHalted`
   where a consumer wanted an outcome. This is deliberate — the taxonomy is chain-only so
   that both modes can compute all of it — and it is a documented non-guarantee with no
   owner, not a deferred obligation.
2. **Detached mode can report a spurious defect or a stale tip against a concurrently
   written root** (§7.3). It holds no lock and there is no metadata root to lock against.
   Its supported caller is the import boundary, whose subject is a copy no engine is live
   on.
3. **`fulfills` is checked structurally, never semantically.** The referent's class and
   ancestry are chain facts; what fulfillment *means* is the consumer's, and atoms carries
   the payload opaquely (A7 design §10.3, §10.4). Intent qualification is not built here
   and is not claimed.
4. **The `FULFILLS_UNRESOLVED` and `DUPLICATE_FULFILLMENT` invariants are enforced at
   submission, not at reconciliation** (§11.4). A record durable before the gate could
   still reconcile into a chain the core condemns; the set of such records is empty at
   landing and the gate keeps it empty, but the asymmetry is real and stated rather than
   argued away.
5. **`register_root`'s existing-chain arm loses a distinction.** An unsettled chain now
   refuses with `PendingUnresolved` before the payload/surface comparison runs, so a
   caller with *both* a pending registration and a mismatched payload learns about the
   pending one. Both are refusals; only the wording differs.
6. **The `PathState` vocabulary stays closed.** `capture_states` refuses a FIFO, socket, or
   device rather than describing it. Widening the vocabulary is an authority §6 design act.
7. **Chain size is still unbounded.** Both inspection commands read and decode every
   durable entry, as `validate_chain` already does on every command. A7 design §16 gap 4
   owns compaction; this design adds no read that was not already happening.
8. **No repair.** Inspection never fixes what it finds, in either mode. A malformed chain
   stays malformed until an operator acts, and that is the tamper-evidence property, not a
   gap.
9. **`inspect_chain` is not read-only on the metadata side, even when it returns
   `MalformedChain`.** §6.1 puts probe and orphan reclamation *above* the split, so by the
   time the structural inspection runs, `metadata_root/probe/` has been emptied and every
   orphan workspace and unindexed blob has been removed — and the malformed return happens
   after that, not instead of it. So **auditing a damaged root mutates metadata state**,
   irreversibly, before the caller learns the chain is damaged. This is stated as a
   limitation rather than defended as a detail because a reader could reasonably expect a
   command that returns "malformed, I did nothing" to have done nothing. It did not touch
   the chain — that is the guarantee §13 makes and §14.3's test 2 pins — but it did discard
   metadata-side debris. The alternative, skipping reclamation on the damage path, would
   falsify ledger #17 and #23's "at every lease entry" at the one entry point most likely to
   meet a damaged root, so the disclosure is the honest resolution rather than a behavior
   change. A consumer that must inspect a live root with *nothing* disturbed has
   `inspect_chain_detached`, which takes no lease at all — at the cost of §7.3's weaker
   guarantees.
10. **Two rulings were escalated and are now decided** at the consuming design's gate,
    2026-08-22. Both are recorded here as limitations because each is a real weakening a
    reader must know about, not because either is open.
    - **Detached `pending` may name a non-durable entry — ratified.** §8's staged
      registration evidence stays in `pending`, so a detached inspection can return a
      `(txid, digest)` pair whose digest is the content name of staged bytes and therefore
      occurs in **no** member of `entries`. The consuming contract will state that detached
      pending digests need not occur in `entries`. §4.5 and §13 carry the invariant
      explicitly, split by mode. Registered mode is unaffected: there, every `pending`
      digest is an entry's.
    - **The staging carve-out — ratified with a tightening.** The exemption applies to
      **readable, no-follow regular staging files only**; a `.#~stage` that is anything else
      is `FOREIGN_LEAF`. §4.4 step 1 and §8 carry that wording verbatim, and its three
      conditions are `_read_regular`'s, so the exemption's boundary is the engine's existing
      predicate rather than a second one.

## 17. Acceptance criteria

1. `atoms.chain.inspect` holds one core: fourteen `DefectKind` members, `ChainDefect`, the
   three `ChainInspection` arms, `scan_chain_directory`, and a **pure** `inspect_scan` that
   takes no backend and no descriptor.
2. `validate_chain` computes no defect of its own; it calls the core and raises
   `ChainStateInvalid(defect.detail)`. `ValidatedChain`, `SurvivorAction`, and
   `STAGING_LEAF` are unchanged, and `chain/append.py` and `coordinator/recover.py` need
   no edit for the refactor.
3. Every one of the fourteen kinds is reachable, and §14.1's paired assertions show the
   same defect from the returning and the raising surface for every directory-constructible
   kind. Every kind's `subject` is the one §4.4 pins, and `subject is None` for
   `GENESIS_COUNT` and for no other kind — in particular `ORPHAN_HISTORY` names the lowest
   unvisited digest and `DUPLICATE_FULFILLMENT` names the second committed settlement's
   digest, as ruled 2026-08-22.
4. §4.4's traversal order is implemented as written, and the first-defect determinism is
   pinned by a test that plants two defects and asserts which one is reported.
5. `inspect_chain`'s body is §6.2's: `_project_lease`, inspect, return on malformed,
   `resolve`, inspect, return. `_recovery_lease` is `_project_lease` plus `resolve` and the
   other four commands are byte-for-byte unchanged in their lease usage.
6. `inspect_chain_detached`'s signature is §5's two parameters; it acquires no lock, opens
   no store, and its backend facade refuses every mutation structurally.
7. `capture_states` returns exactly the named paths in the caller's order over one
   `Observation`, walks no directory, and shares `_capture_path` with `register_root`'s
   baseline capture.
8. `PendingUnresolved` refuses from all three mutating placements and from none of the four
   reading ones, through one shared check over the core's own `pending`.
9. The `fulfills` submission gate refuses in `run_transaction` before any record exists, as
   **two independent checks** (§11.2). Its **referent** check uses the **membership**
   condition — a `fulfills` naming the current tip is **accepted**, pinned by a test that
   appends an intent and immediately fulfills it, the shape
   `tests/acceptance/test_n2_cut5.py:251` depends on — and calls the same core helper §4.3
   calls, the gate passing `len(validated.entries)` as the referencing index. Its
   **duplicate-fulfillment** check is a **separate** check against the committed
   fulfillments already durable in the validated chain, sharing no predicate with §4.3's
   settlement-time check, and its acceptance claim is the weaker true one: a spec it rejects
   would have become `MalformedChain(DUPLICATE_FULFILLMENT)` only if the transaction had
   been appended **and subsequently committed**, never merely appended.
   `test_coordinator_run.py:351` is corrected rather than exempted, and keeps its
   ledger-row-27 carriage assertion (§11.3).
10. `__all__`, the widened architecture guards, `test_docs_status.py`, and the full pytest,
    ruff, and pyright gate all pass.
