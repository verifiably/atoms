# Root lifecycle commands and fail-closed writability

**Status:** Proposed on 2026-08-23; pending atoms-side human review. No
implementation is authorized by this document yet.

**Authority:** `~/d/science/docs/superpowers/specs/2026-08-23-world-index-root-lifecycle-design.md`
§2–§4.

**Directly inherits:** the current coordinator command/lease surfaces and the
SQLite-WAL store. The 2026-08-22 chain-inspection design remains the structural
chain authority; this design creates no second validator.

**Consumer contract:** world-index slice 4 consumes this API only through
`science.root`. Atoms remains root-kind agnostic: it sees lifecycle state,
paths, filesystem facts, and opaque bytes, never Science subjects, verdicts,
manifests, or root kinds.

---

## 1. Decision

Use the existing `atoms.db` as the durable carrier. Schema v3 adds one
singleton lifecycle row and one retained singleton root-creation operation row.
The lifecycle row carries the host/root binding. The operation row is the
atomic no-clobber claim and exact-retry record.

The public surface adds the closed `LifecycleState` enum;
`replicate_root`, `fork_root`, `grant_read_serviceability`, and
`read_lifecycle_state`; the version-exact operator act
`migrate_root_to_lifecycle_v3`; `RootOperationId`,
`read_pending_fork_operation`, and `resume_fork_root`, the narrow seam that
lets a caller resume before minting new opaque child bytes;
`DestinationOverride`; and the errors `SourceSnapshotMoved`,
`RootOperationMismatch`, and `RootOperationInvalid`.

`register_root` keeps its signature but records its own initialization
operation before genesis and grants only that operation after genesis is
durable. `append_intent` and `run_transaction` enter one shared writability
gate before recovery can mutate the chain.

There is no lifecycle sidecar, writable rebind, generic migration framework,
compatibility facade, atoms `restore_root`, verdict channel, or root-kind
callback.

## 2. Frozen semantics and scope

These are requirements, not atoms-side choices:

- Writability is a durable grant in host bookkeeping. Every coordinator
  mutation refuses without it. A metadata-less tree cold-bootstraps read-only
  and unserviceable.
- Every grant binds to the stable machine identity and canonical root path.
  Binding mismatch reports `binding-mismatched` and conveys no grant. Moving
  or renaming a writable root invalidates it; no writable rebind exists.
- `register_root` grants only for its recorded local initialization
  operation. The matching retry, and only it, completes an interrupted grant.
  A bare matching existing genesis never grants.
- Migration is explicit, version-bound, operator-authorized, and records a
  fresh binding. Provenance is attested, not proven. Metadata-less roots and
  restored copies are never auto-upgraded.
- `replicate_root` holds the source lease; creates destination bookkeeping
  first; makes a read-only stamp durable before any destination tree byte is
  exposable; copies chain and payload unchanged; grants neither writability nor
  serviceability; and is no-clobber and exact-retry.
- `fork_root` treats genesis and override bytes as opaque; applies overrides
  before baseline capture, genesis, and grant; captures over `surface_paths`;
  creates a new chain; and makes genesis durable before the grant.
- Fork retry splits at the grant: pre-grant proves genesis, baseline, and tree;
  post-grant recognizes the operation identity and returns success without
  comparing a legitimately mutable tree.
- Fork bytes bind to the exact copied source snapshot. The caller supplies an
  expected source head checked under the source lease. Retry reuses the original
  operation and bytes, never re-minting a child.
- `grant_read_serviceability` performs structural rechecks only. It accepts no
  verdict or attestation, creates matching read-only-serviceable bookkeeping
  for a validated metadata-less cold root, refuses writable and
  binding-mismatched roots, and performs no write when already read-only
  serviceable.
- `read_lifecycle_state` returns the closed five-value union and validates the
  binding while reading.

In scope are the carrier, schema transition, signatures, types, errors,
validation, durability order, and exact retry conditions. Out of scope are
Science semantics; authentication of the serviceability grant; proof of
migration provenance; raw writers and bookkeeping edits; copy-on-write,
parallel, or incremental copying; and filesystem state outside the engine's
closed directory/regular-file/symlink vocabulary.

## 3. Seam review

`~/d/atoms/docs/deferred-obligation-ledger.md` has no open obligations. This
design adds none:

| Admitted shape | Closed disposition |
| --- | --- |
| Stored writable bytes under a different host/path | Query returns `binding-mismatched`; mutation refuses before recovery. |
| Genesis without a local initialization operation | `register_root` never inserts or completes a grant. |
| Same destination operation, different request bytes | `RootOperationMismatch`. |
| Fork source moved after caller derivation | `SourceSnapshotMoved` before a fresh destination claim. |
| Caller lost an interrupted fork result | Pending query plus resume reuses the retained operation and bytes. |
| Pre-grant tree contradicts its operation proof | `RootOperationInvalid`; preserve row and tree. |
| Pre-lifecycle v2 store has no binding | Reads read-only unserviceable; only explicit migration may attest it writable. |
| Validated metadata-less cold root needs read admission | Grant creates fresh matching read-only-serviceable bookkeeping. |

## 4. State and binding

### 4.1 Closed public state

```python
class LifecycleState(enum.StrEnum):
    WRITABLE = "writable"
    READ_ONLY_SERVICEABLE = "read-only-serviceable"
    READ_ONLY_UNSERVICEABLE = "read-only-unserviceable"
    METADATA_LESS = "metadata-less"
    BINDING_MISMATCHED = "binding-mismatched"
```

The enum is re-exported from `atoms.coordinator.commands`. There is no
`UNKNOWN`, `LEGACY`, fallback string, or convenience boolean.
`metadata-less` is not stored. A lifecycle row stores one of the first three
states. An exact v2 store reads `READ_ONLY_UNSERVICEABLE`: bookkeeping exists,
but no grant does.

### 4.2 Stable machine and canonical path

Linux reads `/etc/machine-id`, strips its trailing newline, and accepts exactly
32 lowercase hexadecimal characters other than all zeroes. It is read at each
lifecycle boundary, not cached across commands. Missing, unreadable, empty,
malformed, or uninitialized identity raises `CapabilityUnavailable`; it never
matches a grant. There is no caller-supplied host ID or environment override.
Tests replace the private reader.

For an existing root, the binding uses the normalized spelling returned by
`establish_root` only after its guarded no-symlink walk. `ProjectBinding`
retains this as `project_root_path`. For an absent copy destination, atoms
guarded-opens the parent, validates the final leaf, and joins the normalized
parent spelling to that exact leaf; creation later occurs relative to the held
parent descriptor.

Under the metadata lock, lifecycle reading classifies the carrier, reads the
machine identity, derives the canonical root path, compares
`(machine_id, root_path)`, and only then interprets state. Mismatch returns
`BINDING_MISMATCHED` regardless of claimed state.

During a copy's `recorded` phase the final root leaf may not exist yet. The
operation row and intended path suffice to report its durable
read-only-unserviceable stamp. Outside that phase, a missing root cannot validate
a writable/serviceable grant and reports `binding-mismatched`.

## 5. Schema v3

`SCHEMA_VERSION` changes from 2 to 3. Existing v2 DDL remains byte-for-byte;
v3 appends:

```sql
CREATE TABLE root_lifecycle (
    singleton  INTEGER PRIMARY KEY CHECK (singleton = 0),
    state      TEXT NOT NULL CHECK (state IN (
        'writable', 'read-only-serviceable', 'read-only-unserviceable'
    )),
    machine_id TEXT NOT NULL,
    root_path  TEXT NOT NULL,
    origin     TEXT NOT NULL CHECK (origin IN (
        'register', 'replicate', 'fork',
        'read-serviceability', 'migration-v2'
    ))
) STRICT;

CREATE TABLE root_operation (
    singleton                 INTEGER PRIMARY KEY CHECK (singleton = 0),
    operation_id              TEXT NOT NULL UNIQUE,
    kind                      TEXT NOT NULL CHECK (kind IN (
        'register', 'replicate', 'fork'
    )),
    phase                     TEXT NOT NULL CHECK (phase IN (
        'recorded', 'source-snapshot-durable',
        'tree-durable', 'complete'
    )),
    request_json              TEXT NOT NULL,
    request_hash              TEXT NOT NULL,
    source_snapshot_json      TEXT,
    destination_snapshot_json TEXT,
    genesis_digest            TEXT
) STRICT;
```

Lifecycle binding and `origin` are write-once. State permits equality,
read-only-unserviceable to read-only-serviceable, and
read-only-unserviceable to writable only for register/fork. Migration inserts
directly as writable inside the v2-to-v3 transaction. Nothing leaves writable
or read-only-serviceable.

Operation ID, kind, request bytes, and request hash are write-once. Phase edges
are only:

```text
recorded -> source-snapshot-durable -> tree-durable -> complete
recorded -> tree-durable -> complete  # register_root
```

Snapshot/genesis fields become write-once when set and must agree with phase.
The singleton is deliberate: register, replicate, and fork are mutually
exclusive no-clobber creation origins. The completed row is retained for the
carrier's lifetime, longer than every retry path.

### 5.1 Stamp, request, and operation identity

The first durable transaction for register and both copy commands inserts the
read-only-unserviceable lifecycle row with fresh binding and the `recorded`
operation row. Both commit together through the existing
`Store.transaction()` SQLite-WAL durability barrier. Before commit the root is
metadata-less. After commit it is read-only unserviceable and durably claimed.
No copy destination tree byte is created before this commit returns.

A fresh copy has no destination tree to pass to `bind_project_volume`.
`_destination_claim_lease` therefore reuses the existing mechanism with
the guarded parent of `dest_metadata_root` as the temporary project root and
`dest_metadata_root` as the distinct metadata root. This is the
`AuditedBackend` case where the metadata root is a direct project child, so
the volume proof, lock, and `Store` are all the existing mechanisms; no tree
mutation is authorized through the temporary binding. The lifecycle row records
the separately guarded intended destination path, never the metadata parent.
After the stamp commits, atoms creates the destination root no-clobber, binds
that real root under the still-held metadata lock, and only then writes
children. This is a coordinator composition detail, not a second binding type
or store API.

`RootOperationId` is a once-minted `secrets.token_hex(16)`, exposed through a
`NewType`. `request_json` is canonical UTF-8 JSON with domain
`atoms.root-operation.v1`, sorted keys, no insignificant whitespace, and
base64 for opaque bytes. `request_hash` is lowercase SHA-256 of those exact
bytes.

Closed request shapes:

```text
register:
  project_root, metadata_root, storage_profile, genesis_payload_b64,
  registered_surface

replicate:
  source_root, source_metadata_root, dest_root, dest_metadata_root,
  storage_profile, source_head

fork:
  source_root, source_metadata_root, dest_root, dest_metadata_root,
  storage_profile, expected_source_head, genesis_payload_b64, surface_paths,
  dest_overrides[{path, payload_b64, mode}]
```

Every shape also carries domain and kind. Exact retry compares retained
canonical bytes and hash; hash is never the sole byte-identity proof. The
engine mints the ID because the destination singleton is the atomic claim. A
lost fork ID is recovered by the pending query. For replication,
`source_head` is the head captured by the first invocation, not a caller
argument: a retained retry first compares every caller-derived field, then
reuses the stored head when reconstructing the exact request and when proving
any still-needed source bytes. A later observed head cannot silently redefine
the operation.

### 5.2 Snapshot proof

Snapshots reuse `PathStateJSON`: the sorted tuple of every root-relative
directory, regular file, and symlink, symlinks not followed. File state includes
SHA-256, byte length, and mode; directory state includes mode; symlink state
includes target and mode. Source snapshot also records the validated chain head.

The walker refuses unreadable/unrepresentable entries, non-UTF-8 names, mount
crossings, and duplicate spellings. Replication includes every entry. Fork
excludes only the reserved chain leaf because the child receives a new chain.

`source_snapshot_json` is durable before destination copying.
`destination_snapshot_json` is set only after every destination file and
directory is flushed and the final tree is re-read. It is internal retry proof,
not a consumer summary API.

## 6. Public contract

Public names live in `atoms.coordinator.commands`. Helpers live in the new
`atoms/coordinator/lifecycle.py`; `atoms.coordinator.__all__` stays empty.

```python
RootOperationId = NewType("RootOperationId", str)


@dataclass(frozen=True, slots=True)
class DestinationOverride:
    path: str
    payload: bytes
    mode: int


class SourceSnapshotMoved(PreconditionRefused): ...
class RootOperationMismatch(PreconditionRefused): ...
class RootOperationInvalid(AtomsError): ...


def replicate_root(
    backend: Backend,
    source_root: str,
    source_metadata_root: str,
    dest_root: str,
    dest_metadata_root: str,
    storage: StorageProfile,
) -> RootOperationId: ...


def fork_root(
    backend: Backend,
    source_root: str,
    source_metadata_root: str,
    dest_root: str,
    dest_metadata_root: str,
    storage: StorageProfile,
    *,
    expected_source_head: str,
    genesis_payload: bytes,
    surface_paths: tuple[str, ...],
    dest_overrides: tuple[DestinationOverride, ...],
) -> RootOperationId: ...


def read_pending_fork_operation(
    backend: Backend,
    dest_root: str,
    dest_metadata_root: str,
    storage: StorageProfile,
) -> RootOperationId | None: ...


def resume_fork_root(
    backend: Backend,
    dest_root: str,
    dest_metadata_root: str,
    storage: StorageProfile,
    operation_id: RootOperationId,
) -> RootOperationId: ...


def grant_read_serviceability(
    backend: Backend,
    root: str,
    metadata_root: str,
    storage: StorageProfile,
) -> None: ...


def read_lifecycle_state(
    backend: Backend,
    root: str,
    metadata_root: str,
    storage: StorageProfile,
) -> LifecycleState: ...


def migrate_root_to_lifecycle_v3(
    backend: Backend,
    root: str,
    metadata_root: str,
    storage: StorageProfile,
) -> None: ...
```

`register_root` keeps exactly:

```python
def register_root(
    backend: Backend,
    project_root: str,
    metadata_root: str,
    storage: StorageProfile,
    genesis_payload: bytes,
    registered_surface: tuple[str, ...],
) -> str: ...
```

### 6.1 Boundary validation

- Root spellings are exact non-empty NUL-free `str` and pass guarded
  traversal. Source/destination differ. Metadata roots differ from tree roots.
- `expected_source_head` is exact 64-character lowercase hexadecimal.
- Genesis and override payloads are exact `bytes`.
- `surface_paths` uses the current sorted, duplicate-free surface validator.
- `dest_overrides` is an exact tuple of exact `DestinationOverride`, sorted
  and duplicate-free by path. Paths pass `require_rel_path`, contain no
  reserved `.#~` component, and have no ancestor conflict. `mode` is exact
  `int` in `0..0o7777`; bool refuses.
- Override targets are absent or regular files and parents already exist as
  directories. Overrides create no directory and follow no symlink.
- Runtime operation IDs are exact 32-character lowercase hexadecimal.
- One exact `StorageProfile` applies to source and destination; each volume
  independently passes the certified allowlist/probe.

Wrong Python types raise `ProtocolError`. Structurally invalid well-typed input
raises `PreconditionRefused`, except the named conditions below.

### 6.2 Exact retry

An invocation is an exact retry only when it targets the retained destination
operation and canonical request bytes match byte-for-byte. Kind, paths, storage
profile, source head, genesis, surfaces, and every override field participate.

Different request raises `RootOperationMismatch` before changing tree or
lifecycle. Fresh destination/tree or non-resumable metadata occupancy is
no-clobber `PreconditionRefused`. Evidence claiming the same operation but
failing its snapshot is `RootOperationInvalid`; preserve row and tree.

Concurrent duplicates serialize on the destination metadata lock. The second
observes the first row and never starts a second operation because the first
appears slow.

## 7. Query and writability gate

`read_lifecycle_state` never creates or upgrades a root, metadata directory,
lock, database, schema, row, or WAL. It uses an existing-lock/read-only-store
path:

| Carrier | Result |
| --- | --- |
| metadata root, database, or committed lifecycle row absent | `METADATA_LESS` |
| exact schema v2 store | `READ_ONLY_UNSERVICEABLE` |
| exact v3 row with binding delta | `BINDING_MISMATCHED` |
| exact v3 row with matching binding | stored three-value state |
| impossible row pair, malformed row, or other catalog | `MetadataStoreInvalid` |

An exact empty pre-stamp initialization residue has neither row and reads
metadata-less. A `root_operation` row without its atomic lifecycle row is
invalid. A lifecycle row without an operation row is valid only for
`origin IN ('read-serviceability', 'migration-v2')`; every root-creation
origin requires the pair.

`append_intent`, `run_transaction`, and future cooperative tree mutators
enter one `_writable_recovery_lease`:

1. acquire/bind existing project and metadata roots;
2. validate schema and binding;
3. require `LifecycleState.WRITABLE`;
4. only then reclaim debris, resolve recovery, validate registration, and enter
   the existing body.

Other states raise `PreconditionRefused("root lifecycle state <value> does not
grant writability")`. A non-writable pending root stops at lifecycle; a
writable pending root reaches `PendingUnresolved`. `read_chain` and
inspection remain read surfaces and do not require writability.

## 8. `register_root`

Fresh initialization validates/inspects; requires no genesis/non-resumable
carrier; durably records the operation and unserviceable stamp; captures the
baseline through the existing helper; appends and flushes genesis; stores and
proves the digest, payload, baseline, and tree; then atomically changes
lifecycle to writable and operation to complete before returning the digest.

Exact retained retry resumes its missing suffix. Durable genesis without grant
is proved before grant. Completed retry returns the original digest. Different
payload/surface is `RootOperationMismatch`.

Matching genesis without a retained local register operation raises
`PreconditionRefused("matching genesis has no local initialization operation")`.
It creates no operation and never grants. Forked/migrated roots are never
register retries.

## 9. Shared copy order

Both copy commands:

1. validate without destination state;
2. enter source recovery lease and obtain validated head; fork checks expected
   head here, before destination creation;
3. require absent destination root/metadata, except exact resumable carrier;
4. create bookkeeping and durably commit stamp/operation before destination
   root or child bytes;
5. capture/store full source snapshot under the same lease;
6. create destination no-clobber and copy parent-before-child. Retry retains
   matching entries, creates missing entries, and raises
   `RootOperationInvalid` for changed/extra entries. Flush files/directories;
7. perform command-specific chain/override work;
8. prove/store destination snapshot and move to `tree-durable`;
9. perform final lifecycle transition and mark complete atomically.

Destination may be visibly incomplete while read-only unserviceable. This is
intentional crash residue: stamp first, no serviceability, exact retry.

Before `tree-durable`, retry needing missing source bytes requires the held
head to equal the stored head; otherwise `SourceSnapshotMoved`. At
`tree-durable` and later, destination proof completes without source.

## 10. `replicate_root`

Replication includes every source entry, including chain, with file
bytes/modes, directory modes, and symlink targets unchanged; symlinks are not
followed. Chain and payload are one view under the source lease.

After proof it marks operation complete and leaves state read-only
unserviceable. It appends nothing and grants neither writability nor read
serviceability. Exact retry returns the retained ID. Fresh occupancy is
no-clobber; different request is `RootOperationMismatch`.

## 11. `fork_root`

### 11.1 Source binding

After source recovery/validation but before a fresh destination claim, compare
`expected_source_head` to held validated tip. Inequality raises
`SourceSnapshotMoved` naming expected/observed. No operation, metadata, child
bytes, or grant is minted.

The lease remains held while snapshot/bytes are read. Retained request stores
head and opaque inputs. Retry never substitutes a new head.

### 11.2 Construction and retry

Fork copies every source entry except chain; applies retained overrides before
baseline; flushes override files/parents; captures baseline over exactly
`surface_paths`; creates a new chain with exactly
`GenesisEntry(genesis_payload, baseline)`; flushes it; proves destination,
genesis, baseline, and overrides; stores snapshot/digest as
`tree-durable`; then atomically grants writable and completes.

Pre-grant retry proves operation/request, required source head, tree, genesis,
baseline, and overrides, then performs only the missing suffix. A kill after
genesis but before grant completes from destination evidence after source moves.

Post-grant exact retry returns without comparing destination tree: legitimate
logged writes may have changed it. Different input remains
`RootOperationMismatch`.

### 11.3 Pending-fork seam

`read_pending_fork_operation` returns the retained ID only for a
binding-matching v3 fork whose phase is not `complete`. Metadata-less or a
binding-matching completed operation returns `None`; exact v2 and a retained
non-fork operation raise `PreconditionRefused`; binding mismatch raises
`PreconditionRefused("destination lifecycle binding mismatched")`; malformed
rows/catalog raise `MetadataStoreInvalid`.

`resume_fork_root` accepts that ID/destination, loads source paths, head,
genesis, surfaces, and overrides from retained request, and runs the same state
machine. Caller supplies no child bytes. Wrong ID/kind/completed is
`RootOperationMismatch`.

Science checks pending before minting: pending -> resume -> read child identity
from destination; none plus absent destination -> mint once -> `fork_root`.

## 12. `grant_read_serviceability`

Under one metadata lock it accepts:

1. metadata-less existing root whose detached inspection is
   `WellFormedChain` and has no operation: create v3 and insert fresh-binding
   read-only-serviceable `origin='read-serviceability'`;
2. matching read-only-unserviceable v3 with no incomplete operation, no active
   transaction, no staging survivor, and a well-formed chain under the held
   lock: update only state;
3. already read-only-serviceable: perform the same read-only checks and return
   before a write transaction; no row, WAL frame, chain, or tree write.

The v3 checks call the shared typed chain-validation core directly. They do not
enter recovery, reclaim metadata, or append/remove a staging leaf; otherwise
the already-serviceable arm could not truthfully be a no-write exact retry.

Writable, binding-mismatched, malformed/absent-chain, incomplete-operation, and
exact-v2 roots refuse. V2 must explicitly migrate writable or have its carrier
discarded before cold admission. Grant never upgrades v2, overwrites mismatch,
or grants writable.

There is no verdict, subject, attestation, force, overwrite, or rebind
parameter. Outside Science restore orchestration it is out-of-band.

## 13. `migrate_root_to_lifecycle_v3`

Invocation is the operator attestation that this host is the pre-lifecycle
minting host. A boolean `authorized=True` would prove nothing and is absent.

Accepted input is exact atoms application ID, `user_version = 2`, retained
exact `V2_EXPECTED_CATALOG`, existing guarded roots, no active transaction,
and well-formed registered chain with no staging survivor.

Under existing lock, one transaction creates v3 tables/triggers, inserts
writable `origin='migration-v2'` with fresh binding, and sets version 3. Crash
leaves exact v2/no grant or exact v3/grant.

Metadata-less, active-v2, non-v2, and every v3 carrier refuse except a no-write
exact retry of matching migration origin. Binding-mismatched v3 always refuses.
Normal store open never migrates and names this command when refusing v2.

## 14. Errors

```python
class SourceSnapshotMoved(PreconditionRefused):
    """The held source head differs from the snapshot the operation names."""


class RootOperationMismatch(PreconditionRefused):
    """A destination operation exists, but this retry does not name it exactly."""


class RootOperationInvalid(AtomsError):
    """Durable operation evidence and destination tree cannot be reconciled."""
```

| Condition | Outcome |
| --- | --- |
| Binding delta while reading | `BINDING_MISMATCHED` |
| Non-writable mutator | `PreconditionRefused` before recovery/tree mutation |
| Source head delta while bytes needed | `SourceSnapshotMoved` |
| Different request/resume ID | `RootOperationMismatch` |
| Fresh destination occupancy | no-clobber `PreconditionRefused` |
| Operation/tree contradiction | `RootOperationInvalid`; preserve evidence |
| Bad lifecycle/catalog/canonical row | `MetadataStoreInvalid` |
| Chain damage | existing `ChainStateInvalid`/inspection disposition |
| Bad Python type | `ProtocolError` |
| Bad path/tree/override | `PreconditionRefused` |
| Machine identity unavailable | `CapabilityUnavailable` |
| Other backend I/O | existing `OSError` behavior |

## 15. Rejected alternatives

- Sidecar file: duplicates SQLite locking, codec, atomicity, durability,
  corruption, and agreement.
- Lifecycle in chain: replication must preserve chain while changing host-local
  state; copied chains must not carry grants.
- Caller host ID or writable rebind: makes copied grants satisfiable.
- Hash without request bytes: cannot recover original child bytes or prove byte
  identity.
- Re-mint then no-clobber: strands original child identity.
- Generic migration framework: one explicit v2-to-v3 transition does not
  justify it; automatic migration weakens the operator exception.

## 16. Verification

One focused `~/d/atoms/python/tests/test_lifecycle_commands.py` covers:

- fresh register writable; interrupted exact retry grants; bare matching
  genesis never grants;
- metadata-less, host/path deltas, five-member enum, and mutation-gate
  precedence against `PendingUnresolved`;
- replication byte identity, unserviceable state, no-clobber,
  before/after-stamp cuts, partial retry, request mismatch, and source move;
- fork override-before-baseline, new opaque genesis, source-moved-before-claim,
  pre-grant cuts, different opaque bytes, post-grant retry after legitimate
  writes, and pending-query/resume identity reuse;
- grant refusals, cold carrier creation, one transition, and no-write repeated
  success;
- exact v2 migration, all structural refusals, and atomic cut outcomes.

Store/schema tests pin version 3, DDL, write-once/phase triggers, exact v2
classification, normal-open v2 refusal, and migration-only transition.
Architecture guards pin cooperative mutators to
`_writable_recovery_lease`, dependency direction
`coordinator -> {store, fs, chain, core}`, and public exports.

Implementation gate, from `~/d/atoms/python`:

```text
uv run --frozen pytest
uv run --frozen ruff check .
uv run --frozen pyright
```

## 17. Review gate

Human review must accept or revise signatures, carrier, binding, operation
identity, error names, migration command, and retry conditions before
implementation or downstream Science-plan amendment begins. This document is
the hard stop.
