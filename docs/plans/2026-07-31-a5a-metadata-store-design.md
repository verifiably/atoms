# A5a — the durable metadata store

**Status:** design, unimplemented.
**Authority:** [`2026-07-23-recoverable-fs-effect-engine-design.md`](2026-07-23-recoverable-fs-effect-engine-design.md)
§7, §7.2, §11. Where this design and the authority disagreed, the authority was amended in the same
commit; §3.3 lists every amendment.

**Cross-reference convention:** a bare `§N` is a section of *this* design. A reference to the authority
is written `authority §N`, because several numbers — §3.2, §7, §13 — exist in both documents.

## 1. Decision

A5 is delivered as two sub-plans. **A5a** — this design — is the durable metadata store as a
*mechanism*: it knows how to open, read, and write the engine's durable knowledge. **A5b** composes it
into the recovery-resolve lease, drives A3, and owns every ordering rule.

The seam is the one that worked for A4b: A4b-1 observed and A4b-2 judged; A5a stores and A5b decides
*what* and *when*. A5a knows A3's durable value types — `TransactionState`, `CommitDecision`,
`JournalState`, `RollbackResult`, `HaltDiagnostic` — because it must encode them. It knows neither
transition legality nor recovery planning.

The consequence is stated plainly rather than hidden: **A5a discharges none of A5's seven ledger
entries.** All seven require orchestration A5a does not perform. It supplies part of the mechanism two
of them need (#7's scratch-slot storage, #12's durable write), and is judged on its own acceptance
criteria (§13), not on ledger discharge.

## 2. Scope and non-scope

### 2.1 In scope

- Creating and reopening `atoms.db` under the verified `metadata_root`, with separate protocols for
  each (§5.1, §5.2).
- The pinned connection profile and its verification (§5.3).
- The schema, its constraints, and its version policy (§6).
- Typed record read/write with explicit transaction ownership (§7).
- Content-addressed blob storage: promotion from a staging workspace, the cross-substrate flush
  sequence, and pre-existing-blob verification (§8).
- Per-txid workspace directories under `staging/` and `work/` (§8.3).

### 2.2 Not in scope

- The recovery-resolve lease, and everything that composes over it (A5b).
- Transition legality, recovery planning, and plan-order persistence (A3 decides, A5b sequences).
- Capture (A6) and effect execution (A7).
- Garbage collection. §7.5 defers it and states it is not part of transaction correctness; A5a ships
  no implementation and no `unreferenced_digests` query until a caller exists.
- Schema migration machinery. One version exists; a second one earns the machinery.
- The hardened VFS. §5.5 records why, and §10 records what that leaves undefended.

### 2.3 Relationship to A4a, A4b, and A5b

A4a already built three things §7 needs, so A5a composes rather than invents:

| A4a provides | A5a uses it for |
| --- | --- |
| `ProjectBinding.verified_metadata_path` | The §7 verify-then-open anchor (`bootstrap.py:140`) |
| `certify_sqlite_wal` | Proof the volume hosts SQLite-WAL with cross-process exclusion |
| `ensure_metadata_layout` | `staging/`, `work/`, `blobs/sha256/` already exist at bind time |
| `SYNC_IGNORE_ATTRIBUTE` | The single-host marker is already set (`lock.py:214`) |

A4b provides `ProjectApprovedSpec`. A5a **does not accept one** — it stores rows, and the proof is
A5b's admission ticket, not A5a's. This is deliberate: making A5a take an approved spec would put
ledger #9's enforcement in the wrong layer and let A5b bypass it by calling A5a directly.

A5a takes a live `ProjectBinding` and nothing else. That is what ties the store to an allowlisted,
locked volume without duplicating A4a's checks.

## 3. Seam review against the deferred-obligation ledger

### 3.1 Existing entries

A5a discharges none of A5's seven. What it changes for each:

| # | A5a's relationship to it |
| --- | --- |
| 7 | Supplies the workspace mechanism a scratch-occupancy check runs against. The check, the external/intrinsic distinction, and the regeneration loop are A5b's. |
| 9 | Untouched. A5a accepts no `ProjectApprovedSpec` (§2.3), so enforcement lives entirely at A5b's entry points. |
| 12 | Supplies the durable write and the barrier. *Which* transitions, in *what* order, and the refusal to advance past A7 are A5b's. |
| 17 | Untouched. A5a holds no lock and opens no lease. |
| 18 | Untouched. A5a creates no composition root. |
| 19 | Untouched. A5a never touches project space. |
| 21 | Untouched. A5a stores a txid as an opaque key and compares nothing. |

### 3.2 New entries this design creates

One:

> **#22** — A5a's `promote_staging` returns only after the promoted blobs are durable, but nothing binds
> that call to the `COMMIT` that references the digests. Admitted by the A5a store contract. First
> owner: **A5b**. Required behavior: enforce §7.3's cross-substrate rule — every blob a record
> references is durable on the filesystem before the COMMIT that references it — and prove it by a
> fresh-process test that a committed record never names a missing blob. Verification: authority §13.4.

A second candidate was considered and rejected: "A5a writes whatever state it is told, so nothing
prevents an illegal transition sequence." That is already ledger #12, whose required behavior is
"persist each A3 transition ... in plan order." A duplicate entry would drift from the original.

### 3.3 Authority amendments in this commit

A sub-plan must not knowingly disagree with its authority, so three amendments land with this design.

1. **§7, temp-file surface** (lines 681–684). The text claimed the profile "sets `temp_store=MEMORY`
   and a `metadata_root`-local temp directory so no temp file escapes the store," and that the WAL
   transition's rollback journal "is created and consumed inside bootstrap (§5.5)." Both are wrong, and
   the first contradicts line 677 of the same paragraph, which already concedes SQLite "explicitly
   disclaims [temp file presence and location] as an application contract."
   - No safe per-connection redirect exists in stdlib: `SQLITE_TMPDIR` is process-global and hostile in
     a library, and `temp_store_directory` is deprecated.
   - `temp_store=MEMORY` governs temp tables, indices, and materializations. It does not govern
     rollback, super-, or statement journals, and SQLite documents that a statement journal may use a
     randomized path outside the database directory.
   - A4a switches only `probe/certify.db` into WAL (`binding.py:219` → `probe.py:460`). `atoms.db` is
     never opened before A5a, so **A5a performs the first WAL transition for the real store** and its
     transient rollback journal is A5a's, not bootstrap's.

   Amended to: the profile minimizes transients and bounds the *durable* surface; the engine does not
   claim every SQLite transient is metadata-root-local, and its authority §13.5 obligation remains proving that no
   *effect* mutation targets the store.

2. **§7.2, table shapes.** The sketch is marked informative; two deviations are recorded so the
   deviation is deliberate rather than discovered. `blob.refcount` is dropped (§6.1). The per-row
   `schema_version` is dropped (§6.3).

3. **§11, refusal vocabulary.** Adds `MetadataStoreInvalid` (§9).

## 4. Architecture and ownership

### 4.1 Module layout

New package `atoms/store/`. `atoms/core/` is ruled out — it is stdlib-only and pure, and this layer
imports `sqlite3` and issues I/O. `atoms/fs/` is ruled out because it is coherently about filesystem
capability and path resolution, and is already fourteen modules.

| Module | Responsibility |
| --- | --- |
| `schema.py` | DDL text, `SCHEMA_VERSION`, `APPLICATION_ID`, the enum-derived CHECK clauses. Pure — no I/O, no `sqlite3` connection. |
| `connection.py` | Creation, reopen, the pinned profile and its verification, the authorizer, liveness. |
| `records.py` | Typed row read/write, cross-row load validation, and the private `HaltDiagnostic` codec. |
| `blobs.py` | Digest paths, promotion, pre-existing-blob verification, the flush sequence. |
| `workspace.py` | `staging/<txid>/` and `work/<txid>/` creation and durable removal. |

The `HaltDiagnostic` codec starts as private helpers in `records.py` and is not exported. A3 owns the
semantic value; A5a owns its durable encoding. It moves to `store/diagnostic.py` only if `records.py`
becomes unwieldy.

### 4.2 Dependency direction

`atoms.store` may import `atoms.core` and `atoms.fs`. **`atoms.fs` may never import `atoms.store`**,
and neither may `atoms.core`. This extends the existing `test_core_never_imports_the_filesystem_layer`
guard rather than inventing a second scheme, and is asserted by §11.6.

## 5. Opening the store

### 5.1 Creation

`atoms.db` does not exist until A5a creates it. The protocol is exclusive so that two processes racing
the first open cannot both believe they initialized it — the project lock already prevents this among
cooperating processes, and `O_EXCL` makes it structural rather than assumed.

1. `openat(metadata_root_fd, "atoms.db", O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0o600)`, then
   close. A zero-length file is what SQLite treats as a fresh database.
2. Open through `binding.verified_metadata_path("atoms.db")` (§5.4) and apply the pinned profile
   (§5.3), including the WAL transition. This is the store's first WAL transition and writes a
   transient rollback journal.
3. In **one explicit transaction**: the complete DDL, `PRAGMA user_version = SCHEMA_VERSION`, and
   `PRAGMA application_id = APPLICATION_ID`; then COMMIT.

`PRAGMA user_version` and `application_id` **are** transactional: set inside an explicit transaction,
both revert with a `ROLLBACK`, measured on SQLite 3.50.4. Initialization is therefore atomic in the
strong sense — an interrupted step 3 leaves a database with an empty schema, `user_version = 0`, and
`application_id = 0`, which is exactly the one resumable shape §5.2 accepts. That correspondence is not
a coincidence to be re-derived at reopen; it is why the shape is safe to resume.

### 5.2 Reopen

Three refusals happen **before SQLite sees the path**, because `verified_child_path` verifies the
metadata root's identity and returns a pathname; it inspects no leaf (`bootstrap.py:140`).

1. `fstatat(metadata_root_fd, name, AT_SYMLINK_NOFOLLOW)` for `atoms.db`, `atoms.db-wal`,
   `atoms.db-shm`, **and `atoms.db-journal`**. Each that exists must be a regular file. A symlink at
   any of the four is refused. The rollback journal is included because an interrupted first WAL
   transition (§5.1 step 2) can leave a hot one that SQLite must recover on the next open — a symlink
   there redirects that recovery.
2. Open and apply the pinned profile (§5.3).
3. Identity and version:

   | `application_id` | `user_version` | schema | Verdict |
   | --- | --- | --- | --- |
   | `APPLICATION_ID` | `SCHEMA_VERSION` | matches | Open. |
   | `APPLICATION_ID` | other | any | Refuse — incompatible store version. |
   | `APPLICATION_ID` | `SCHEMA_VERSION` | differs | Refuse — same version, wrong schema. |
   | `0` | `0` | empty | **Resumable initialization.** Re-run §5.1 step 3. |
   | `0` | `0` | non-empty | Refuse — foreign database. |
   | other | any | any | Refuse — foreign database. |

   `application_id` identifies a *completed* store; it does not disambiguate the zero case, because an
   interrupted initialization and a foreign empty database both have zero application id. The
   `(0, 0, empty schema)` shape is accepted as resumable for one honest reason: it contains no durable
   evidence, and it sits inside an engine-owned namespace the consumer is forbidden to target.
4. Schema-catalog validation: the normalized `sql` text of every object in `sqlite_schema` must equal
   the expected DDL exactly. `user_version` alone does not prove the schema is the expected schema.
5. `PRAGMA quick_check` and `PRAGMA foreign_key_check`, both of which must be clean.

**Nothing is silently rewritten.** Every mismatch above raises; the only write on the reopen path is
the resumable-initialization case, which writes into a provably empty schema.

### 5.3 The pinned profile

| Pragma | Value | Why |
| --- | --- | --- |
| `journal_mode` | `WAL` | §7. A `COMMIT` is the durability barrier. |
| `synchronous` | `FULL` | §7.2. Each COMMIT flushes the WAL. |
| `fullfsync` | `1` on macOS | §5.5. Plain fsync is not power-loss durable there. |
| `foreign_keys` | `ON` | Per-connection and **off by default**; the §6 references are inert without it. |
| `temp_store` | `MEMORY` | Minimizes transients. See §10 for what it does not cover. |
| `trusted_schema` | `OFF` | A schema object cannot invoke non-trusted functions. |

Every pragma is **read back after being set** and a mismatch refuses. `journal_mode` and
`foreign_keys` both fail silently rather than raising — the first returns the mode it actually
achieved, the second simply stays off — and a store running without them is a store whose durability
and referential claims are false.

The profile check also reads `PRAGMA compile_options` and refuses `TEMP_STORE=0`, under which
`temp_store=MEMORY` is inert and the pragma read-back still reports success. The build this design was
written against reports `TEMP_STORE=1` and SQLite 3.50.4.

`sqlite3.connect(..., isolation_level=None)` — no implicit `BEGIN`, so transaction boundaries are
explicit and A5b's (§7).

An authorizer denies `SQLITE_ATTACH` and `SQLITE_DETACH`. `VACUUM` has no authorizer action code, so it
is excluded the way this repo excludes things: A5a issues a closed set of statements, and §11.6 asserts
by AST that no `VACUUM` or `ATTACH` statement appears in the package.

**SQLite ≥ 3.37 is required** and a lower version raises `CapabilityUnavailable`. `STRICT` tables
(§6.2) are load-bearing, not decorative.

### 5.4 Liveness

`Store` retains the `ProjectBinding` and gates **every** operation on it, because a connection that
outlives its binding or its lock is a connection writing to a volume nothing holds.

The gate is the established idiom, not a new method:

```python
backend = self._binding.backend  # liveness gate; raises if closed or unlocked
del backend
```

`ProjectBinding.backend` calls `_require_active`, which raises `ProtocolError` when the binding is
closed *or* when the lock was released before it (`binding.py:110`). `approve_for_project` already
does exactly this at `approval.py:116`, with the same reason in a comment. No new public method is
added to A4a.

## 6. The schema

### 6.1 Tables

`transaction` is a SQL keyword, so the table is `transaction_record` rather than quoted at every use.

Each `CHECK` list below is written out for review, but in the source it is **generated** from its enum
(§6.2) — these are the values `TransactionState`, `CommitDecision`, `RollbackResult`, `EffectVariant`,
and `JournalState` currently hold.

```sql
CREATE TABLE transaction_record (
    txid            TEXT PRIMARY KEY,
    spec_json       TEXT NOT NULL,
    state           TEXT NOT NULL CHECK (state IN (
                        'prepared', 'applying', 'applied', 'committed',
                        'rolling_back', 'rolled_back', 'halted')),
    committed       TEXT NOT NULL CHECK (committed IN ('uncommitted', 'committed')),
    rollback_result TEXT          CHECK (rollback_result IN (
                        'restored', 'external_drift_preserved')),
    halt_diagnostic TEXT
) STRICT;

CREATE TABLE effect (
    txid          TEXT NOT NULL REFERENCES transaction_record(txid),
    effect_id     TEXT NOT NULL,
    variant       TEXT NOT NULL CHECK (variant IN (
                      'replace_file', 'create_file_no_clobber', 'delete_path',
                      'move_no_clobber', 'create_directory')),
    journal_state TEXT NOT NULL CHECK (journal_state IN (
                      'pending', 'started', 'done', 'undo_started', 'undone')),
    PRIMARY KEY (txid, effect_id)
) STRICT;

CREATE TABLE blob (
    digest   TEXT PRIMARY KEY,
    byte_len INTEGER NOT NULL CHECK (byte_len >= 0)
) STRICT;

CREATE TABLE active (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 0),
    txid      TEXT NOT NULL REFERENCES transaction_record(txid)
) STRICT;

CREATE TRIGGER transaction_record_spec_json_is_write_once
BEFORE UPDATE OF spec_json ON transaction_record
BEGIN
    SELECT RAISE(ABORT, 'spec_json is write-once');
END;
```

`blob.refcount` from §7.2's informative sketch is **dropped**. Its only consumer is garbage collection,
which §7.5 states is not part of transaction correctness. A blob is referenced iff a live
`transaction_record` names it, which is derivable when GC is written; a maintained counter would have
to be adjusted correctly by every future write path in A5b through A8, and a counter that reaches zero
early deletes a blob a live transaction still references.

### 6.2 Constraints

`STRICT` on every table, so column typing is engine-enforced. Note what `STRICT` does *not* do: it
coerces values that convert losslessly. `'42'` and `42.0` both store as integer `42` in an `INTEGER`
column; `42.5`, `'abc'`, and `b'x'` raise. §11 tests it with the latter.

Every `CHECK (... IN ...)` list is **generated from the A1/A3 enum** rather than retyped, so a new
member cannot silently diverge from the values the engine writes. §11.1 asserts the generated DDL
against the enums.

`foreign_keys=ON` (§5.3) makes `active.txid` and `effect.txid` real references: `active` can never name
a transaction that does not exist, which is precisely the shape §7.3 promises recovery will never see.

The single-active-transaction rule is the `CHECK (singleton = 0)` primary key from §7.2 — two active
rows are not a bug to detect but a row the database refuses.

A5a enforces structure only. **It does not re-derive which transition may follow which**; that is A3's
machine, enforced by A5b. A second copy in SQL would have to be kept in agreement forever, and its
divergence would surface as a store refusing a transition A3 authorized.

### 6.3 `spec_json`

Written once, from `atoms.core.canonical.canonical_json` — the `str` form. `canonical_obj` returns a
`dict` and is the wrong function for a TEXT column (`canonical.py:123`, `:142`).

On read, `spec_json` is decoded and **re-encoded, and the result must equal the stored text exactly**.
A record whose stored bytes are not the canonical encoding of what they decode to is not a record this
engine wrote.

Write-once is enforced by the database trigger in §6.1. A source guard over `UPDATE` statements is also
present (§11.6) but is lint: it constrains only what A5a's own source spells.

The per-row `schema_version` from §7.2's sketch is **dropped**. It duplicates `user_version` and is
migration scaffolding before migrations exist. If a second store version ever needs per-record
provenance, it is added then, with the migration that needs it.

### 6.4 The halt diagnostic

`halt_diagnostic` holds A3's `HaltDiagnostic`: twelve fields including two `TransactionState`s, two
journal vectors, `DiagnosticEntry` and `DiagnosticIdentityRelation` tuples, a `HaltReason`, and an
`OperatorAction`. It contains no `EntryIdentity` — A3 keeps that token-free by construction, which is
what makes it durable at all.

The codec is explicit, private to `records.py`, and **verified by round-trip equality**: for every
diagnostic the tests generate, `decode(encode(d)) == d`. Ledger #12 forbids serializing snapshot-local
identity tokens; §11.1 asserts the encoded form contains no field derived from `EntryIdentity`.

## 7. The record API and transaction ownership

`Store` exposes **no `sqlite3.Connection`**. §11.6 asserts the public surface exports no connection
object and that `_connection` is private.

Transaction boundaries are **A5b's**, chosen through one context manager:

```python
with store.transaction() as txn:      # BEGIN IMMEDIATE
    ...                               # COMMIT on clean exit, ROLLBACK on exception
```

`BEGIN IMMEDIATE` rather than deferred: the write lock is taken at entry, so a writer that will
conflict fails at the start rather than at COMMIT after doing work.

**Every record operation requires an active explicit transaction** and raises `ProtocolError`
otherwise. This is the load-bearing consequence of `isolation_level=None`: without it, pysqlite issues
no implicit `BEGIN`, so a bare `INSERT` would autocommit and a caller intending one barrier would get
several. A5a will not let that happen silently.

Reads validate across rows before returning, because a single well-typed row is not evidence the record
is coherent:

- `effect` rows cover exactly the effect ids in `spec_json`, in the same order;
- each `effect.variant` matches that effect's variant in `spec_json`;
- if an `active` row exists, its transaction record exists (also a foreign key, checked here so a store
  opened with `foreign_keys` off by an earlier writer is still caught);
- every digest the record references has a `blob` row.

Each failure raises `MetadataStoreInvalid` (§9).

## 8. Blobs and workspaces

### 8.1 Promotion

Promotion is a **batch over a complete manifest**, not a per-file call. A singular `promote()` cannot
honor §7.3 step 3: after the first file moves, `staging/<txid>/` still holds the others, so the call
cannot remove the directory it promised to remove.

`promote_staging(workspace, manifest)` where the manifest names every file in the workspace's staging
directory with its digest and byte length:

1. For each entry, no-clobber rename `staging/<txid>/<name>` → `blobs/sha256/<digest>`. `EEXIST` is
   handled in §8.2.
2. fsync `blobs/sha256/` **and** `staging/<txid>/`. Flushing only the blob directory could leave both
   the blob and its staging source name durable after power loss, resurrecting preparation-only staging
   — §7.3's stated reason.
3. Require `staging/<txid>/` to be empty, then `rmdir` it.
4. fsync `staging/`.

The call returns only after step 4, so a caller cannot get the sequence wrong. Step 3's emptiness
requirement is what makes "complete manifest" *enforced*: an uncovered file raises instead of leaving a
directory that cannot be removed.

### 8.2 A pre-existing blob is verified, not assumed

`EEXIST` on step 1 is **not success by construction**. The digest names the content the engine intends,
not the content on disk: a pre-existing blob may be truncated by a previous crash, corrupted, or
externally substituted.

Before the staged source is unlinked, the existing blob is opened `O_NOFOLLOW` and must be a regular
file whose length equals `byte_len` and whose streamed SHA-256 equals `digest`. On a match the staged
source is unlinked and promotion continues. **On a mismatch the staged source is left in place** and
`MetadataStoreInvalid` is raised — the staged bytes are the good copy, and destroying them to tidy up
after a corrupt blob would discard the only recovery material.

### 8.3 Workspaces

`staging/<txid>/` and `work/<txid>/` are created and removed through A4a's guarded traversal from the
retained `metadata_root` descriptor, never by absolute path. Removal is durable: fsync the parent after
`rmdir`.

A5a creates a workspace when asked and removes it when asked. **When** either happens is A5b's, and the
re-resolution ledger #19 requires before creating scratch is A5b's.

## 9. Error contract

Reuses `atoms.core.errors` unchanged except for one addition.

| Raised | When |
| --- | --- |
| `CapabilityUnavailable` | SQLite < 3.37; `TEMP_STORE=0`; a pinned pragma that would not take. |
| `ProtocolError` | Caller misuse: a wrong exact type, a record operation outside an explicit transaction, a closed binding or released lock. |
| `MetadataStoreInvalid` | **New.** The durable store cannot be safely interpreted. |
| `OSError` | Propagated. No blanket handler; §11.6 extends the existing guard to the new package. |

`MetadataStoreInvalid(AtomsError)` covers both corruption and incompatibility, with the message
distinguishing them: a failed `quick_check`, a schema that does not match its version, a foreign
database, a non-canonical `spec_json`, a blob whose bytes do not match its digest, and *an unknown
future `user_version`*. A newer store is not corrupt — it is unreadable by this build — but both mean
"stop; do not interpret this," which is one caller response and therefore one exception. Folding either
into `ProtocolError` would tell a consumer to fix its call when the correct action is to preserve
evidence.

Authority §11 is amended to add it (§3.3).

## 10. Limits

**Opening by verified path, not by held descriptor.** `sqlite3.connect` opens by pathname through
SQLite's own VFS, so no descriptor participates and authority §3.2's held-directory reasoning does not carry
over. A5a re-verifies `metadata_root`'s identity at the moment of use and refuses a symlink or
non-regular file at any of the four database leaves (§5.2), which catches the pre-existing cases. It
does not defend against an adversary substituting `metadata_root` or an ancestor *between* that
verification and SQLite's open.

This is the authority's stated cooperating-process assumption (§7): mutating or replacing
`metadata_root` or any ancestor while a lease is active voids the recovery guarantee. The project lock
serializes the cooperating processes this engine targets, for which the case never arises. The remedy
is the optional hardened VFS, which the authority marks explicitly as hardening rather than a
correctness prerequisite.

**It is recorded here as a documented threat-model limitation, not as a ledger entry.** The ledger
tracks shapes a boundary admits and a later sub-plan owes; this shape has no owner, and an entry with a
fictional owner is worse than an honest limit — the same reasoning by which A4b-1's fail-closed
platform "admits nothing and therefore has no entry."

**Transient SQLite files.** `temp_store=MEMORY` covers temp tables, indices, and materializations. It
does not cover rollback, super-, or statement journals, and SQLite reserves the right to change its
temporary-file behavior and documents that a statement journal may use a randomized path outside the
database directory. A5a minimizes transients through the profile and the closed SQL surface; it does
not claim every SQLite transient is metadata-root-local. Authority §13.5's obligation is unaffected: it is to
prove no *effect* mutation targets the store.

## 11. Verification

### 11.1 Tier 1 — pure

No I/O, no connection. Generated DDL `CHECK` lists equal the A1/A3 enum members exactly, so a new
member breaks the build rather than the store. Digest path construction. The version policy table of
§5.2 as a pure decision function. `HaltDiagnostic` round-trip equality over generated diagnostics, and
the assertion that no encoded field derives from `EntryIdentity`.

### 11.2 Tier 2 — real SQLite, temporary directory

Profile read-back, including a connection whose `journal_mode` did not take and one under a simulated
`TEMP_STORE=0`. Each structural constraint refused independently: two `active` rows; an `effect` or
`active` row referencing no transaction; a value outside a `CHECK` list; a `STRICT` violation using
`42.5`, `'abc'`, and `b'x'` — **not** `'42'` or `42.0`, which SQLite losslessly coerces and which would
pass for the wrong reason. The `spec_json` trigger refusing an `UPDATE`. A record operation attempted
outside an explicit transaction. Each cross-row load validation of §7, failed one at a time.

### 11.3 Tier 3 — creation and reopen

Every row of §5.2's version table, including same-version/wrong-schema, version-zero partial
initialization resuming, version-zero non-empty refusing, and an unknown future `user_version` raising
`MetadataStoreInvalid`. A symlinked `atoms.db`, `-wal`, `-shm`, and `-journal`, each refused before
SQLite opens anything. A store whose `foreign_key_check` fails on reopen. A non-canonical `spec_json`.

### 11.4 Tier 4 — real ext4 volume

Through A4a's existing binding fixtures. Multi-file promotion over a complete manifest. A manifest that
omits a file, failing at the emptiness requirement. `EEXIST` with matching bytes, and `EEXIST` with
mismatching bytes asserting both the raise **and** that the staged source survives. Workspace creation
and durable removal. Store operations after the binding is closed and after the lock is released,
each raising `ProtocolError`.

### 11.5 Tier 5 — fresh process

A record written and committed in one process is read back identically in a new one. This is the
durability claim A5a actually makes. Cross-process WAL exclusion is **not** re-tested: A4a's
`certify_sqlite_wal` already proves it at bind time, and re-asserting it here would duplicate a
certified capability.

### 11.6 Tier 6 — architecture

`atoms.fs` and `atoms.core` never import `atoms.store`. The public surface is exactly the documented
names and exports no `sqlite3.Connection`. No `ATTACH` or `VACUUM` statement appears in the package. No
blanket `OSError` handler, extending the existing guard. Every `UPDATE` of `spec_json` is absent from
the source — lint beside the trigger, not a substitute for it.

## 12. Deferred and delivery obligations

**Ledger entries discharged:** none (§3.1).

**Ledger entries created:** #22, the cross-substrate promotion/COMMIT binding, owned by A5b (§3.2).

**Ledger entries untouched:** every other open entry.

**Authority amendments:** §7 temp-file surface, §7.2 table shapes, §11 refusal vocabulary (§3.3).

**Not a ledger entry:** the `AGENTS.md` status line, and §10's threat-model limitation.

## 13. Acceptance criteria

1. `atoms.db` is created only through the `O_CREAT | O_EXCL | O_NOFOLLOW` protocol of §5.1, and a
   second concurrent creation raises rather than silently reinitializing.
2. Creation writes the DDL, `user_version`, and `application_id` in one explicit transaction.
3. Reopen refuses a symlink at `atoms.db`, `atoms.db-wal`, `atoms.db-shm`, or `atoms.db-journal`
   before SQLite opens the path.
4. Every row of §5.2's version table produces its stated verdict; the only write on the reopen path is
   the resumable `(0, 0, empty)` case.
5. Schema-catalog text is validated against the expected DDL, so same-version/wrong-schema refuses.
6. `quick_check` and `foreign_key_check` run on every reopen and refuse on any finding.
7. Every pinned pragma is read back and a mismatch refuses; `TEMP_STORE=0` refuses with
   `CapabilityUnavailable`; SQLite < 3.37 refuses with `CapabilityUnavailable`.
8. `ATTACH` and `DETACH` are denied by the authorizer, and no `ATTACH` or `VACUUM` statement appears in
   the package.
9. Every `Store` operation gates on `binding.backend`, so a closed binding or released lock raises
   `ProtocolError`.
10. `Store` exposes no `sqlite3.Connection`, and every record operation outside an explicit transaction
    raises `ProtocolError`.
11. Every `CHECK` enumeration is generated from its enum, asserted equal to the enum members.
12. All tables are `STRICT`, tested with values SQLite cannot losslessly coerce.
13. `spec_json` is written from `canonical_json`, and a read that does not re-encode to the stored text
    exactly raises `MetadataStoreInvalid`.
14. An `UPDATE` of `spec_json` is refused by the database trigger, not only by lint.
15. `HaltDiagnostic` round-trips exactly, and its encoded form contains no field derived from
    `EntryIdentity`.
16. Cross-row load validation covers effect coverage and order, variant consistency, active-record
    existence, and referenced blob rows, each failing independently.
17. `promote_staging` takes a complete manifest, and a manifest omitting a file raises at the emptiness
    requirement rather than leaving an unremovable directory.
18. Promotion returns only after `blobs/sha256/` and `staging/<txid>/` are flushed, the staging
    directory is removed, and `staging/` is flushed.
19. A pre-existing blob is verified by kind, length, and streamed SHA-256 before the staged source is
    unlinked; on mismatch the staged source survives and `MetadataStoreInvalid` is raised.
20. Workspace directories are created and removed through guarded traversal from the retained
    descriptor, never by absolute path, and removal is durable.
21. `atoms.fs` and `atoms.core` import nothing from `atoms.store`.
22. A record committed in one process is read back identically in a fresh process.
23. No `ProjectApprovedSpec` is accepted anywhere in `atoms.store`, so ledger #9's enforcement cannot
    be satisfied at this layer by accident.
24. No consumer of `atoms.store` exists yet, asserted rather than assumed.
