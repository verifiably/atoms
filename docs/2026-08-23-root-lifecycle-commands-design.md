# Root lifecycle commands and fail-closed writability

**Status:** Implemented on 2026-08-23. Approved by atoms-side human review
(findings landed through `6555e46`); the implementation landed on
`design/root-lifecycle` with the full suite and gates green.

**Authority:** `~/d/beliefs/docs/superpowers/specs/2026-08-23-world-index-root-lifecycle-design.md`
§2–§4.

**Directly inherits:** the current coordinator command/lease surfaces and the
SQLite-WAL store. The 2026-08-22 chain-inspection design remains the structural
chain authority; this design creates no second validator.

**Consumer contract:** world-index slice 4 consumes this API only through
`science.root`. Atoms remains root-kind agnostic: it sees lifecycle state,
paths, filesystem facts, and opaque bytes, never Beliefs subjects, verdicts,
manifests, or root kinds.

---

## 1. Decision

Use the existing `atoms.db` as the durable carrier. Schema v3 adds one
singleton lifecycle row and one retained singleton root-creation operation row.
The lifecycle row carries the host/root binding. The operation row is the
retained exact-retry record; a temporary root-local claim published with the
destination directory is the cross-metadata-root atomic no-clobber claim.

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

- Writability is a durable grant in host bookkeeping. Every cooperative
  coordinator mutation of an existing root refuses without it; a creation
  command necessarily writes its claimed destination pre-grant, under its own
  recorded operation. A metadata-less tree cold-bootstraps read-only
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
  exposable (the root-local operation claim is bookkeeping, not payload or
  chain); copies chain and payload unchanged; grants neither writability nor
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

One refinement of the frozen wording is accepted explicitly: "stamp durable
before exposure" admits pre-stamp publication of the claimed destination
directory containing exactly the root-local operation claim. The claim is
engine bookkeeping — never payload, chain, or a snapshot entry — and
cross-carrier no-clobber needs a filesystem-level ownership point (§5.1,
§15). No byte a consumer can read as content exists before the stamp.

In scope are the carrier, schema transition, signatures, types, errors,
validation, durability order, and exact retry conditions. Out of scope are
Beliefs semantics; authentication of the serviceability grant; proof of
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
| Cold copy carrying `.#~root-claim` or a staging survivor | Grant refuses; the residue marks an incomplete creation. |

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
gains a `project_root_path` slot to retain it; today only the metadata-root
spelling is retained, on the lock. For an absent copy destination, atoms
guarded-opens the parent, validates the final leaf, and joins the normalized
parent spelling to that exact leaf; creation later occurs relative to the held
parent descriptor.

Under the metadata lock, lifecycle reading classifies the carrier, reads the
machine identity, derives the canonical root path, compares
`(machine_id, root_path)`, and only then interprets state. Mismatch returns
`BINDING_MISMATCHED` regardless of claimed state.

During a copy's `recorded` phase the final root exists and contains only its
durable root-operation claim until the lifecycle stamp commits. The claim and
row name the same operation and intended canonical path. A missing root cannot
validate any recorded, writable, or serviceable lifecycle row and reports
`binding-mismatched`.

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
    )),
    CHECK (length(machine_id) = 32
        AND machine_id NOT GLOB '*[^0-9a-f]*'
        AND machine_id != '00000000000000000000000000000000'),
    CHECK (length(root_path) > 0),
    CHECK (
        (origin IN ('register', 'fork') AND state IN (
            'read-only-unserviceable', 'writable'
        ))
        OR (origin = 'replicate' AND state IN (
            'read-only-unserviceable', 'read-only-serviceable'
        ))
        OR (origin = 'read-serviceability'
            AND state = 'read-only-serviceable')
        OR (origin = 'migration-v2' AND state = 'writable')
    )
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
    genesis_digest            TEXT,
    CHECK (length(operation_id) = 32
        AND operation_id NOT GLOB '*[^0-9a-f]*'),
    CHECK (length(request_hash) = 64
        AND request_hash NOT GLOB '*[^0-9a-f]*'),
    CHECK (genesis_digest IS NULL OR (
        length(genesis_digest) = 64
        AND genesis_digest NOT GLOB '*[^0-9a-f]*'
    )),
    CHECK (
        (kind = 'register'
            AND source_snapshot_json IS NULL
            AND (
                (phase = 'recorded'
                    AND destination_snapshot_json IS NULL
                    AND genesis_digest IS NULL)
                OR (phase IN ('tree-durable', 'complete')
                    AND destination_snapshot_json IS NOT NULL
                    AND genesis_digest IS NOT NULL)
            ))
        OR (kind = 'replicate'
            AND genesis_digest IS NULL
            AND (
                (phase = 'recorded'
                    AND source_snapshot_json IS NULL
                    AND destination_snapshot_json IS NULL)
                OR (phase = 'source-snapshot-durable'
                    AND source_snapshot_json IS NOT NULL
                    AND destination_snapshot_json IS NULL)
                OR (phase IN ('tree-durable', 'complete')
                    AND source_snapshot_json IS NOT NULL
                    AND destination_snapshot_json IS NOT NULL)
            ))
        OR (kind = 'fork' AND (
            (phase = 'recorded'
                AND source_snapshot_json IS NULL
                AND destination_snapshot_json IS NULL
                AND genesis_digest IS NULL)
            OR (phase = 'source-snapshot-durable'
                AND source_snapshot_json IS NOT NULL
                AND destination_snapshot_json IS NULL
                AND genesis_digest IS NULL)
            OR (phase IN ('tree-durable', 'complete')
                AND source_snapshot_json IS NOT NULL
                AND destination_snapshot_json IS NOT NULL
                AND genesis_digest IS NOT NULL)
        ))
    )
) STRICT;

CREATE TRIGGER trg_root_lifecycle_insert_gate
BEFORE INSERT ON root_lifecycle
WHEN NOT (
    (NEW.origin IN ('register', 'replicate', 'fork')
        AND NEW.state = 'read-only-unserviceable'
        AND EXISTS (
            SELECT 1 FROM root_operation
            WHERE singleton = 0
                AND kind = NEW.origin
                AND phase = 'recorded'
        ))
    OR (NEW.origin = 'read-serviceability'
        AND NEW.state = 'read-only-serviceable'
        AND NOT EXISTS (SELECT 1 FROM root_operation))
    OR (NEW.origin = 'migration-v2'
        AND NEW.state = 'writable'
        AND NOT EXISTS (SELECT 1 FROM root_operation))
)
BEGIN
    SELECT RAISE(ABORT, 'root lifecycle insert lacks initial operation state');
END;

CREATE TRIGGER trg_root_lifecycle_identity_write_once
BEFORE UPDATE OF machine_id, root_path, origin ON root_lifecycle
WHEN NEW.machine_id IS NOT OLD.machine_id
    OR NEW.root_path IS NOT OLD.root_path
    OR NEW.origin IS NOT OLD.origin
BEGIN
    SELECT RAISE(ABORT, 'root lifecycle binding and origin are write-once');
END;

CREATE TRIGGER trg_root_lifecycle_transition
BEFORE UPDATE OF state ON root_lifecycle
WHEN NOT (
    NEW.state = OLD.state
    OR (OLD.state = 'read-only-unserviceable'
        AND NEW.state = 'read-only-serviceable'
        AND NOT EXISTS (
            SELECT 1 FROM root_operation WHERE phase != 'complete'
        ))
    OR (OLD.state = 'read-only-unserviceable'
        AND NEW.state = 'writable'
        AND OLD.origin IN ('register', 'fork')
        AND EXISTS (
            SELECT 1 FROM root_operation
            WHERE kind = OLD.origin AND phase = 'tree-durable'
        ))
)
BEGIN
    SELECT RAISE(ABORT, 'illegal root lifecycle transition');
END;

CREATE TRIGGER trg_root_lifecycle_no_delete
BEFORE DELETE ON root_lifecycle
BEGIN
    SELECT RAISE(ABORT, 'root lifecycle is retained');
END;

CREATE TRIGGER trg_root_operation_insert_gate
BEFORE INSERT ON root_operation
WHEN NEW.phase != 'recorded'
    OR EXISTS (SELECT 1 FROM root_lifecycle)
BEGIN
    SELECT RAISE(ABORT, 'root operation insert requires empty lifecycle and recorded phase');
END;

CREATE TRIGGER trg_root_operation_identity_write_once
BEFORE UPDATE OF operation_id, kind, request_json, request_hash ON root_operation
WHEN NEW.operation_id IS NOT OLD.operation_id
    OR NEW.kind IS NOT OLD.kind
    OR NEW.request_json IS NOT OLD.request_json
    OR NEW.request_hash IS NOT OLD.request_hash
BEGIN
    SELECT RAISE(ABORT, 'root operation identity is write-once');
END;

CREATE TRIGGER trg_root_operation_proof_write_once
BEFORE UPDATE OF source_snapshot_json,
    destination_snapshot_json, genesis_digest ON root_operation
WHEN (OLD.source_snapshot_json IS NOT NULL
        AND NEW.source_snapshot_json IS NOT OLD.source_snapshot_json)
    OR (OLD.destination_snapshot_json IS NOT NULL
        AND NEW.destination_snapshot_json IS NOT OLD.destination_snapshot_json)
    OR (OLD.genesis_digest IS NOT NULL
        AND NEW.genesis_digest IS NOT OLD.genesis_digest)
BEGIN
    SELECT RAISE(ABORT, 'root operation proof fields are write-once');
END;

CREATE TRIGGER trg_root_operation_phase_transition
BEFORE UPDATE OF phase ON root_operation
WHEN NOT (
    (OLD.phase = 'recorded'
        AND NEW.phase = 'source-snapshot-durable'
        AND OLD.kind IN ('replicate', 'fork'))
    OR (OLD.phase = 'recorded'
        AND NEW.phase = 'tree-durable'
        AND OLD.kind = 'register')
    OR (OLD.phase = 'source-snapshot-durable'
        AND NEW.phase = 'tree-durable')
    OR (OLD.phase = 'tree-durable' AND NEW.phase = 'complete')
)
BEGIN
    SELECT RAISE(ABORT, 'illegal root operation phase transition');
END;

CREATE TRIGGER trg_root_operation_complete_gate
BEFORE UPDATE OF phase ON root_operation
WHEN NEW.phase = 'complete' AND NOT EXISTS (
    SELECT 1 FROM root_lifecycle
    WHERE singleton = 0
        AND origin = NEW.kind
        AND state = CASE NEW.kind
            WHEN 'replicate' THEN 'read-only-unserviceable'
            ELSE 'writable'
        END
)
BEGIN
    SELECT RAISE(ABORT, 'root operation completion lacks lifecycle state');
END;

CREATE TRIGGER trg_root_operation_no_delete
BEFORE DELETE ON root_operation
BEGIN
    SELECT RAISE(ABORT, 'root operation is retained');
END;
```

The DDL is the contract, including trigger names and error strings. Lifecycle
creation inserts only these shapes: a matching recorded
register/replicate/fork operation followed by read-only-unserviceable
lifecycle state; operation-less read-only-serviceable
`read-serviceability`; or operation-less writable `migration-v2`. Root creation
inserts operation first and lifecycle second in the same transaction. The
operation insert gate prevents either operation-less origin from acquiring an
operation afterward. A committed operation-only row conveys no lifecycle or
grant and is invalid on store read; cooperative code never commits that
transaction-local intermediate.

Lifecycle binding and `origin` are write-once. State permits equality,
read-only-unserviceable to read-only-serviceable only with no incomplete
operation, and read-only-unserviceable to writable only for a tree-durable
register/fork. Migration inserts directly as writable inside the v2-to-v3
transaction. Nothing leaves writable or read-only-serviceable.

Operation ID, kind, request bytes, and request hash are write-once. Phase edges
are only:

```text
recorded -> source-snapshot-durable -> tree-durable -> complete
recorded -> tree-durable -> complete  # register_root
```

Snapshot/genesis fields become write-once when set; table checks pin their
nullability to kind and phase. The lifecycle transition occurs before the
operation's `tree-durable -> complete` update in their one transaction, which
lets the completion trigger validate the final lifecycle state. The singleton
is per carrier; cross-carrier exclusion comes from §5.1's root-local claim.
The completed row is retained for the carrier's lifetime, longer than every
retry path.

`schema.py` freezes the current version-2 values as
`V2_SCHEMA_STATEMENTS` and `V2_EXPECTED_CATALOG`. Version 3 defines
`ROOT_LIFECYCLE_V3_STATEMENTS` as exactly the two tables and ten triggers
above, in shown order, then defines
`SCHEMA_STATEMENTS = (*V2_SCHEMA_STATEMENTS,
*ROOT_LIFECYCLE_V3_STATEMENTS)`. `EXPECTED_CATALOG` uses the existing
`_catalog_row` derivation over that full tuple plus the existing v2 automatic
indexes and exactly
`('index', 'sqlite_autoindex_root_operation_1', 'root_operation', None)`.
No extra table, index, trigger, or view is accepted in either frozen catalog.

### 5.1 Stamp, request, and operation identity

The root-local claim leaf is the fixed engine-reserved regular file
`.#~root-claim`, mode `0o600`. Its canonical object is exactly
four fields: `domain` is `atoms.root-claim.v1`; `operation_id` is the retained
32-hex ID; `request_hash` is the retained 64-hex hash; and `request_json` is the
complete canonical string, not a second parsed representation. The object uses
§5.1's serializer. The file is temporary bookkeeping, never a surface or
snapshot entry.

For a fresh copy, atoms mints the operation and creates mode-`0o700` sibling
directory `.#~<operation_id>.root-claim` under the guarded destination parent.
It writes and flushes the claim file, flushes that directory, then uses the
existing `transfer_noclobber` to publish the prepared directory at the final
destination leaf. That one rename atomically binds the claim bytes and
operation identity to the newly created destination directory. Atoms
immediately flushes the held destination-parent descriptor. A caught failure
before publication removes only that invocation's private sibling; a crash may
leave it, but the final destination remains absent and no operation was
claimed. An existing destination is resumable only when its claim bytes are
canonical and match the exact operation request, including
`dest_metadata_root`; absence or mismatch is no-clobber refusal. Thus two
metadata roots cannot claim one destination.

Only after the root claim is durable does `_destination_claim_lease` acquire
the distinct, non-nested destination metadata root and create the store. Its
first durable transaction inserts the matching `recorded` operation row, then
the read-only-unserviceable lifecycle row with fresh binding. Both commit
together through the existing `Store.transaction()` SQLite-WAL durability
barrier. Before that commit the root still classifies metadata-less and exposes
only engine bookkeeping. After it, the root is read-only unserviceable. No
payload, chain, or override entry is created before the stamp commits.

`register_root` uses the same fixed claim file, created no-clobber and flushed
in its already-existing root before it stamps its operation. This prevents two
metadata carriers from registering one root. Copy and register keep the marker
through `tree-durable`; the final suffix verifies and removes it, flushes the
root directory, then atomically performs the lifecycle/`complete` transaction.
Once removed, the retained external operation row owns retries; a different
metadata carrier sees an occupied root without a claim and refuses.

`RootOperationId` is a once-minted `secrets.token_hex(16)`, exposed through a
`NewType`. Request, claim, and snapshot encoding reuse one private serializer:

```python
json.dumps(
    value,
    sort_keys=True,
    separators=(",", ":"),
    ensure_ascii=False,
    allow_nan=False,
).encode("utf-8")
```

The value grammar is closed to exact `dict[str, ...]`, `list`, `str`, `int`,
and `None`; booleans and floats are absent. Strings are emitted as supplied,
without Unicode normalization; validation rejects NULs, lone surrogates, and
non-canonical path spellings before encoding. `ensure_ascii=False` leaves
non-ASCII code points as UTF-8 and the stdlib encoder escapes JSON controls,
quote, and backslash. Opaque bytes use padded RFC 4648 standard base64 via
`base64.b64encode(value).decode("ascii")`. `StorageProfile` encodes exactly as
`{"profile_id": storage.profile_id}`. All absolute paths in a request are the
canonical guarded spellings, and all path tuples become JSON arrays in their
already-validated order.

`request_json` is the UTF-8 decode of the canonical bytes for the
`atoms.root-operation.v1` object. `request_hash` is lowercase SHA-256 of those
exact bytes. Decoding accepts only bytes that re-encode identically; alternate
escaping, key order, whitespace, numeric spelling, or base64 refuses as
`MetadataStoreInvalid` for stored evidence and `RootOperationInvalid` for a
root claim.

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
engine mints the ID and the root claim, not the destination-store singleton,
is the cross-carrier atomic ownership point. A lost fork ID and request are
recovered from the claim before the stamp or from the retained row after it.
For replication,
`source_head` is the head captured by the first invocation, not a caller
argument: a retained retry first compares every caller-derived field, then
reuses the stored head when reconstructing the exact request and when proving
any still-needed source bytes. A later observed head cannot silently redefine
the operation.

### 5.2 Snapshot proof

Snapshots use the same serializer over this exact object:

```text
{
  "domain": "atoms.root-snapshot.v1",
  "chain_head": <64-lowercase-hex string>,
  "entries": [[<root-relative path>, <PathStateJSON>], ...]
}
```

`entries` is sorted by the exact Python string ordering of path. Each
`PathStateJSON` is `state_to_json(state)` represented as a JSON array of
two-item arrays in that function's existing canonical field order. File state
therefore contains lowercase SHA-256, byte length, and mode; directory state
contains mode; symlink state contains target and mode. Symlinks are not
followed. Source and destination snapshots both record their validated chain
head.

The walker refuses unreadable/unrepresentable entries, non-UTF-8 names, mount
crossings, and duplicate spellings. Copy path pairs are non-nested (§6.1), so a
source walk cannot encounter live metadata. Replication includes every source
entry. Fork excludes only the reserved chain leaf because the child receives a
new chain. An incomplete root carrying `.#~root-claim` is not an admissible copy
source.

`source_snapshot_json` is durable before destination copying.
`destination_snapshot_json` is set only after every destination file and
directory is flushed, the containing parent flush from destination publication
has completed, and the final tree is re-read. The root claim is excluded from
the proof, removed and followed by a root-directory flush before `complete`.
Snapshots are internal retry proof, not a consumer summary API.

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
  traversal. For a copy, the canonical source root, source metadata root,
  destination root, and destination metadata root are pairwise non-overlapping:
  no two are equal and none is an ancestor or descendant of another. This
  rejects both copying live source bookkeeping and trying to stamp metadata
  beneath an absent destination. Existing non-copy commands retain atoms'
  current direct-child metadata support.
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

An invocation is an exact retry only when it targets the retained root claim or
destination operation and canonical request bytes match byte-for-byte. Kind,
paths, storage profile, source head, genesis, surfaces, and every override field
participate. A fresh call mints an ID only after proving the destination absent.
When a claim already exists, an exact request adopts its retained ID; it never
mints and then compares a new ID.

Different request or a claim/carrier identity disagreement raises
`RootOperationMismatch` before changing tree or lifecycle. Fresh occupied
destination without a valid claim and non-resumable metadata occupancy are
no-clobber `PreconditionRefused`. Evidence claiming the same operation but
failing its snapshot is `RootOperationInvalid`; preserve claim, row, and tree.

Concurrent fresh calls race only at `transfer_noclobber` of the claimed root.
Exactly one publishes the destination directory; every loser opens that root
and either adopts the exact claim or refuses. The destination metadata lock
then serializes work for the winning claim. A different metadata-root spelling
is part of the request and therefore can never adopt the winner.

## 7. Query and writability gate

`read_lifecycle_state` never creates or upgrades a root, metadata directory,
lock, database, schema, row, or WAL. It uses an existing-lock/read-only-store
path:

| Carrier | Result |
| --- | --- |
| metadata root or database absent, or neither lifecycle nor operation row committed | `METADATA_LESS` |
| exact schema v2 store | `READ_ONLY_UNSERVICEABLE` |
| exact v3 row with binding delta | `BINDING_MISMATCHED` |
| exact v3 row with matching binding | stored three-value state |
| impossible row pair, malformed row, or other catalog | `MetadataStoreInvalid` |

An exact empty pre-stamp initialization residue has neither row and reads
metadata-less. A `root_operation` row without its atomic lifecycle row is
invalid. A lifecycle row without an operation row is valid only for
`origin IN ('read-serviceability', 'migration-v2')`; every root-creation
origin requires the pair.

The private `_existing_read_only_lease` is the common lifecycle/read/source
entry. It guarded-opens only already-existing roots, metadata root, and lock;
acquires that lock without creating or repairing it; verifies the volume and
declared `StorageProfile` with read-only observations; and opens `atoms.db`
strictly `mode=ro`. It does not call `bind_project_volume`, `open_store`, the
bootstrap probe, either reclaimer, a write PRAGMA, or `resolve`. If SQLite
would need to create or change a WAL/SHM sidecar to read, the open refuses; it
has no writable fallback. Catalog, lifecycle, binding, operation, active-row,
and chain checks run under this lease.

`append_intent`, `run_transaction`, and future cooperative tree mutators enter
one `_writable_recovery_lease`:

1. enter `_existing_read_only_lease` and validate schema and binding;
2. require `LifecycleState.WRITABLE`;
3. while retaining the same lock, close the read-only connection and activate
   the existing writable binding/store path;
4. only then probe, reclaim debris, resolve recovery, validate registration,
   and enter the existing body.

Other states raise `PreconditionRefused("root lifecycle state <value> does not
grant writability")`. A non-writable pending root stops at lifecycle; a
writable pending root reaches `PendingUnresolved`.

`read_chain`, `inspect_chain`, and copy-source acquisition also begin with
`_existing_read_only_lease`. A matching writable root may activate the same
recovery suffix before reading. A non-writable source/read never activates it:
it requires no active transaction, no incomplete root operation, and no chain
staging survivor, then invokes the shared typed chain validator directly under
the lock. Any state requiring recovery refuses instead of reclaiming or
appending. Detached cold inspection remains its existing explicitly
non-coherent, non-mutating path. Thus every probe, reclamation, staging change,
and `resolve` append is downstream of a validated writable grant, including
operations reached through APIs named as reads.

The root-creation commands are the single deliberate exception: register and
fork write payload, overrides, and chain genesis into their claimed
destination before any grant exists — the transition trigger itself demands a
durable genesis before writable is reachable. The gate binds cooperative
mutators entering an existing root, never the command creating one.

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

Fresh invocations use this order:

1. validate types, canonicalize all four pairwise non-overlapping paths, and
   perform read-only destination preflight;
2. enter the source through §7's lifecycle-aware coherent lease, obtain its
   validated head, and for fork compare the expected head before creating any
   destination state;
3. while retaining the source lock, publish the claimed destination directory
   no-clobber and flush its containing parent;
4. acquire the destination metadata lock with existing
   `try_lock_exclusive`, never the blocking acquisition. Busy raises
   `PreconditionRefused("copy destination lock is busy")`, releases the source,
   and leaves the exact root claim resumable;
5. create the destination store and durably commit the matching lifecycle
   stamp/operation before any payload, chain, or override entry;
6. capture and store the full source snapshot under the same source lock;
7. copy parent-before-child. Retain matching entries, create missing entries,
   and raise `RootOperationInvalid` for changed/extra entries. Flush every file
   and every changed directory;
8. perform command-specific chain/override work;
9. verify that the containing-parent flush from step 3 completed, prove and
   store the destination snapshot, and move to `tree-durable`;
10. verify/remove the root claim and flush the destination root directory;
11. perform the final lifecycle transition and mark complete atomically.

This source-first/blocking then destination-second/nonblocking rule is the only
two-lock order. An A-to-B/B-to-A cycle cannot wait: at least one second-lock
attempt refuses and releases its source lock. Same-destination contenders are
already serialized by atomic root publication and then the one destination
lock.

Retry first acquires only the destination lock and re-reads the claim, row, and
tree. `tree-durable` and `complete` never open the source. An earlier phase also
stays destination-only when the retained source snapshot plus destination
evidence proves that all source bytes, overrides, baseline, and genesis are
already durable; it advances the missing proof/grant suffix locally. This is
the fork crash-after-genesis case.

Only when destination proof identifies missing source bytes does retry release
the destination, acquire the source lock, and reacquire the destination with
`try_lock_exclusive`; after reacquisition it re-reads the phase before acting.
The held source head must equal the retained head or `SourceSnapshotMoved` is
raised. A missing/unopenable source propagates existing `OSError` behavior.
Either failure preserves claim, row, and tree for a later retry. No phase that
can finish from destination evidence touches the source, so moved or unavailable
source roots do not block that suffix.

Before the stamp, the published destination exposes only the engine claim and
classifies metadata-less. After the stamp it may be visibly incomplete while
read-only unserviceable. Both are intentional crash residues with exact retry.

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

After lifecycle-aware source coherence/validation but before a fresh
destination claim, compare
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

Pre-grant retry first proves operation/request, tree, genesis, baseline, and
overrides from destination evidence. It checks a live source head only when
that proof shows source bytes are still missing. A kill after genesis but
before grant therefore completes after the source moves or becomes unavailable.

Post-grant exact retry returns without comparing destination tree: legitimate
logged writes may have changed it. Different input remains
`RootOperationMismatch`.

### 11.3 Pending-fork seam

`read_pending_fork_operation` first reads the destination root claim. A
canonical pre-stamp fork claim whose request names the supplied canonical
destination and metadata root returns its retained ID even though lifecycle
state is metadata-less. After the stamp, it returns the retained ID only for a
binding-matching v3 fork whose phase is not `complete`, and requires claim and
row to agree while the marker remains. No claim/carrier or a binding-matching
completed operation returns `None`; exact v2 and a retained non-fork operation
raise `PreconditionRefused`; a different valid claim is
`RootOperationMismatch`; binding mismatch raises
`PreconditionRefused("destination lifecycle binding mismatched")`; malformed
claim is `RootOperationInvalid`, and malformed rows/catalog are
`MetadataStoreInvalid`.

`resume_fork_root` accepts that ID/destination, loads source paths, head,
genesis, surfaces, and overrides from the root claim before the stamp or the
retained row after it, and runs the same state machine. Caller supplies no
child bytes. Wrong ID/kind/completed is `RootOperationMismatch`.

Beliefs checks pending before minting: pending -> resume -> read child identity
from destination; none plus absent destination -> mint once -> `fork_root`.

## 12. `grant_read_serviceability`

The command classifies through §7's explicitly read-only path first. It accepts:

1. metadata-less existing root whose detached inspection is
   `WellFormedChain`, whose chain holds no staging survivor, whose root
   carries no `.#~root-claim` leaf, and that has no operation: create v3 and
   insert fresh-binding read-only-serviceable `origin='read-serviceability'`;
2. matching read-only-unserviceable v3 with no incomplete operation, no active
   transaction, no staging survivor, and a well-formed chain under the held
   lock: update only state;
3. already read-only-serviceable: under `_existing_read_only_lease`, perform
   the same checks and return without ever calling `bind_project_volume`,
   `open_store`, a writable SQLite open, a write PRAGMA, or a sidecar-creating
   path; no metadata, SQLite sidecar, row, chain, or tree write occurs.

The v3 checks call the shared typed chain-validation core directly. They do not
enter recovery, reclaim metadata, or append/remove a staging leaf. Only after
a matching unserviceable root is selected for transition does the command,
under the same still-held lock, close the read-only store and open the existing
store writable for the one state update. The already-serviceable branch exits
before that boundary, making its exact retry a byte-for-byte no-write operation.

Writable, binding-mismatched, malformed/absent-chain, incomplete-operation,
creation-residue (a `.#~root-claim` leaf or staging survivor), and exact-v2
roots refuse. Residue is creation evidence: a tree §5.2 rejects as a copy
source must not read serviceable. V2 must explicitly migrate writable or have its carrier
discarded before cold admission. Grant never upgrades v2, overwrites mismatch,
or grants writable.

There is no verdict, subject, attestation, force, overwrite, or rebind
parameter. Outside Beliefs restore orchestration it is out-of-band.

## 13. `migrate_root_to_lifecycle_v3`

Invocation is the operator attestation that this host is the pre-lifecycle
minting host. A boolean `authorized=True` would prove nothing and is absent.

Accepted input is exact atoms application ID, `user_version = 2`, retained
exact `V2_EXPECTED_CATALOG`, existing guarded roots, no active transaction,
and well-formed registered chain with no staging survivor.

The command enters through the read-only path and proves exact
`V2_EXPECTED_CATALOG` before any writable open. Under the same existing lock it
then opens the v2 database writable, begins `BEGIN IMMEDIATE`, executes each
statement of `ROOT_LIFECYCLE_V3_STATEMENTS` once, inserts writable
`origin='migration-v2'` with fresh binding, verifies the resulting catalog is
exactly `EXPECTED_CATALOG`, sets `PRAGMA user_version = 3`, and commits. A
failure rolls back. Crash leaves exact v2/no grant or exact v3/grant; no partial
catalog is accepted or repaired on normal open.

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
| Claim/row operation disagreement | `RootOperationMismatch` |
| Malformed root claim | `RootOperationInvalid`; preserve evidence |
| Fresh destination occupancy | no-clobber `PreconditionRefused` |
| Busy second copy lock | `PreconditionRefused("copy destination lock is busy")` |
| Operation/tree contradiction | `RootOperationInvalid`; preserve evidence |
| Bad lifecycle/catalog/canonical row | `MetadataStoreInvalid` |
| Chain damage | existing `ChainStateInvalid`/inspection disposition |
| Bad Python type | `ProtocolError` |
| Bad path/tree/override | `PreconditionRefused` |
| Machine identity unavailable | `CapabilityUnavailable` |
| Other backend I/O | existing `OSError` behavior |

## 15. Rejected alternatives

- Permanent lifecycle sidecar: duplicates SQLite locking, codec, atomicity,
  durability, corruption, and agreement; the temporary root claim carries only
  creation ownership and is removed before completion.
- Destination-metadata-lock-only claim: caller-selected metadata paths do not
  serialize one destination; the claimed directory must select the winner.
- Lifecycle in chain: replication must preserve chain while changing host-local
  state; copied chains must not carry grants.
- Caller host ID or writable rebind: makes copied grants satisfiable.
- Hash without request bytes: cannot recover original child bytes or prove byte
  identity.
- Re-mint then no-clobber: strands original child identity.
- Nested copy roots/metadata plus walker exclusions: exclusion would make
  "copy every entry" path-dependent and still cannot stamp beneath an absent
  destination, so copy paths are simply non-overlapping.
- Blocking acquisition of both copy locks: opposite-direction work can cycle;
  one nonblocking second acquisition uses the existing primitive and fails
  closed.
- Generic migration framework: one explicit v2-to-v3 transition does not
  justify it; automatic migration weakens the operator exception.

## 16. Verification

One focused `~/d/atoms/python/tests/test_lifecycle_commands.py` covers:

- fresh register writable; interrupted exact retry grants; bare matching
  genesis never grants;
- metadata-less, host/path deltas, five-member enum, and mutation-gate
  precedence against `PendingUnresolved`;
- replication byte identity, unserviceable state, no-clobber,
  different-metadata-root collision, before/after-claim/stamp/parent-flush
  cuts, partial retry, request mismatch, and source move;
- fork override-before-baseline, new opaque genesis, source-moved-before-claim,
  pre-grant cuts, destination-only retry with moved/missing source, different
  opaque bytes, post-grant retry after legitimate writes, and pending
  claim/row query/resume identity reuse;
- pairwise copy-path nesting refusals, second-lock contention without deadlock,
  exact request/snapshot/claim encoding including Unicode/base64/storage, and
  root/containing-parent durability barriers;
- grant refusals including claim-leaf and staging-survivor residue, cold
  carrier creation, one transition, and no-write repeated success with traps
  on writable SQLite open, sidecar change, probes, reclamation, and recovery;
- exact v2 migration, all structural refusals, and atomic cut outcomes.

Store/schema tests pin version 3, the exact two-table/ten-trigger catalog,
negative INSERT cases for premature writable register/fork and premature
serviceable replication, the legal recorded-operation-first paths, every
update-trigger refusal and kind/phase nullability check, exact v2
classification, normal-open v2 refusal, and migration-only transition.
Architecture guards pin every recovery/reclamation/probe path downstream of
the writability gate and no-write reads to `_existing_read_only_lease`,
dependency direction
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
implementation or downstream Beliefs-plan amendment begins. This document is
the hard stop.
