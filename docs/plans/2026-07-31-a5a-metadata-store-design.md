# A5a — the durable metadata store

**Status:** Implemented on 2026-08-01. A7–A8 remain unimplemented.
**Authority:** [`2026-07-23-recoverable-fs-effect-engine-design.md`](2026-07-23-recoverable-fs-effect-engine-design.md)
authority §7, §7.2, §11. Where this design and the authority disagreed, the authority was amended in the same
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
- Typed record read/write with explicit transaction ownership, and the coherence predicate applied on
  both sides of the store (§7).
- Content-addressed blob storage: promotion from a staging workspace, the cross-substrate flush
  sequence, source and pre-existing-blob verification, and verified reads (§7.2, §8).
- The mechanism for authority §7.3's pre-COMMIT survivor reclamation — enumerating and removing
  surviving workspaces and unindexed blobs (§7.3, §8.3). A5a supplies it; A5b invokes it.
- Per-txid workspace directories under `staging/` and `work/`, created and durably removed (§8.3).

### 2.2 Not in scope

- The recovery-resolve lease, and everything that composes over it (A5b).
- Transition legality, recovery planning, and plan-order persistence (A3 decides, A5b sequences).
- Capture (A6) and effect execution (A7).
- Terminal garbage collection. authority §7.5 defers it and states it is not part of transaction
  correctness; A5a ships no implementation and no `unreferenced_digests` query until a caller exists.
  This is distinct from authority §7.3's pre-COMMIT orphan reclamation, whose mechanism A5a **does**
  supply (§7.3) — see that section for why the two are different questions.
- Schema migration machinery. One version exists; a second one earns the machinery.
- The hardened VFS. Authority §7 records why, and §10 records what that leaves undefended.

### 2.3 Relationship to A4a, A4b, and A5b

A4a already built three things authority §7 needs, so A5a composes rather than invents:

| A4a provides | A5a uses it for |
| --- | --- |
| `ProjectBinding.verified_metadata_path` | The authority §7 verify-then-open anchor (`bootstrap.py:140`) |
| `certify_sqlite_wal` | Proof the volume hosts SQLite-WAL with cross-process exclusion |
| `ensure_metadata_layout` | `staging/`, `work/`, `blobs/sha256/` already exist at bind time |
| `SYNC_IGNORE_ATTRIBUTE` | The single-host marker is already set (`lock.py:214`) |

A4b provides `ProjectApprovedSpec`. A5a **does not accept one** — it stores rows, and the proof is
A5b's admission ticket, not A5a's. The reason is simply that **A5b is the transaction admission
boundary and A5a is a storage mechanism**: ledger #9's enforcement belongs at the layer that decides
whether a transaction may proceed, not at the layer that writes its rows. Demanding a proof here would
not add a second gate so much as put the only gate in the wrong place, where it would have to be
re-justified for every internal write A5b makes.

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

Two:

> **#22** — authority §7.3's cross-substrate rule: every blob a record references — from both its
> initial/preimage and final/planned-postimage `FileState`s — must be durable on the filesystem before
> the COMMIT that references it. Admitted by the A5a store contract. First owner:
> **A5a** — reassigned from A5b, because the mechanism that would have needed enforcing from above no
> longer exists. `promote_staging` is the sole writer of a `blob` row (§7.1), it writes rows only from
> the manifest step 1 verified, and only after step 3 has flushed the leaves; §7.6 requires every
> referenced digest to have a row. The property is therefore structural rather than delegated.
> Required behavior: keep it that way and prove it — fresh-process create-from-absent and replace tests
> that a committed record never names a missing preimage or postimage blob, plus §11.6's assertion that
> no second writer of `blob` rows exists.
> Verification: authority §13.4. The entry stays open until A5a lands with that suite, per the ledger's
> own rule; it is not discharged by a design claiming the shape is unreachable.

> **#23** — A5a supplies `list_workspaces`, `reopen_workspace`, `remove_workspace`,
> `list_unindexed_blobs`, and `remove_unindexed_blob`, but never invokes any of them: it holds no lease
> and cannot know a crash occurred. Admitted by the A5a store contract. First owner: **A5b**. Required
> behavior: at every lease entry, under the held lock, enumerate surviving workspaces and unindexed
> blobs and reclaim those authority §7.3 leaves as pre-COMMIT orphan scratch — never removing scratch a
> durable record still references — and prove it by a crash-cut test that a reopened project retains no
> orphan `staging/`, `work/`, or unindexed blob. Verification: authority §7.3 and authority §13.4.

A third candidate was considered and rejected: "A5a writes whatever state it is told, so nothing
prevents an illegal transition sequence." That is already ledger #12, whose required behavior is
"persist each A3 transition ... in plan order." A duplicate entry would drift from the original.

### 3.3 Authority amendments in this commit

A sub-plan must not knowingly disagree with its authority, so four amendments land with this design.

1. **Authority §7, temp-file surface** (lines 681–684). The text claimed the profile "sets `temp_store=MEMORY`
   and a `metadata_root`-local temp directory so no temp file escapes the store," and that the WAL
   transition's rollback journal "is created and consumed inside bootstrap (authority §5.5)." Both are wrong, and
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

2. **Authority §7.2, table shapes.** The sketch is marked informative; two deviations are recorded so the
   deviation is deliberate rather than discovered. `blob.refcount` is dropped (§6.1). The per-row
   `schema_version` is dropped (§6.3).

3. **Authority §11, refusal vocabulary.** Adds `MetadataStoreInvalid` (§9).

4. **Authority §7, cooperating-process assumption.** The sentence named only `metadata_root` and its
   ancestors. The preflight of §5.2 is a time-of-check operation with the same window, so the four
   `atoms.db{,-wal,-shm,-journal}` entries are named alongside it (§10).

## 4. Architecture and ownership

### 4.1 Module layout

New package `atoms/store/`. `atoms/core/` is ruled out — it is stdlib-only and pure, and this layer
imports `sqlite3` and issues I/O. `atoms/fs/` is ruled out because it is coherently about filesystem
capability and path resolution, and is already fourteen modules.

| Module | Responsibility |
| --- | --- |
| `schema.py` | DDL text, `SCHEMA_VERSION`, `APPLICATION_ID`, the enum-derived CHECK clauses, and the expected `(type, name, tbl_name, sql)` catalog (§5.2). Pure — no I/O, no `sqlite3` connection. |
| `connection.py` | Creation, reopen, the pinned profile and its verification, the authorizer, liveness, and transaction ownership — `_StoreTransaction` and its spent/active rules (§7). |
| `errors.py` | `MetadataStoreInvalid`, the two result codes §9.1 translates, and the one narrow translation context manager. Its own module because `connection.py`, `records.py`, `blobs.py`, and `workspace.py` all raise it, and putting it in any of them would make the other three import that one for an exception type. |
| `records.py` | Typed row read/write, §7.6's coherence predicate — used by both the loader and the pre-COMMIT check — and the private `HaltDiagnostic` codec. |
| `blobs.py` | The digest-to-leaf mapping, promotion, pre-existing-blob verification, the flush sequence. |
| `workspace.py` | The `Workspace` resource, `staging/<txid>/` and `work/<txid>/` creation, reopening, enumeration, and durable removal, and §5.5's name validation. |

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

1. **Preflight all four entries, not just `atoms.db`.** `fstatat(metadata_root_fd, name,
   AT_SYMLINK_NOFOLLOW)` for `atoms.db`, `atoms.db-wal`, `atoms.db-shm`, and `atoms.db-journal`; every
   one must be absent. A sidecar surviving with no database is not a store this engine can create
   into — it is an invalid store shape, and creation refuses it with `MetadataStoreInvalid`.

   The `O_EXCL` of step 2 covers `atoms.db` alone and says nothing about the sidecars, so without this
   step creation is the *only* path with no sidecar check while reopen has one. That gap is
   exploitable: with `atoms.db` absent and `atoms.db-journal` a symlink, the first WAL transition
   **removed that symlink**, measured. It is a silent unlink of an attacker-planted name today, and a
   write through a planted name is the same class of bug one SQLite behavior change away. Refusing the
   shape costs one `fstatat` per entry.
2. Gate (§5.4), then `openat(metadata_root_fd, "atoms.db",
   O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC | O_RDWR, 0o600)`. A
   zero-length file is what SQLite treats as a fresh database. `O_RDWR` rather than the implicit
   `O_RDONLY`: step 3 flushes this descriptor, and while Linux accepts `fsync` on a read-only
   descriptor, POSIX permits `EBADF` and macOS's `F_FULLFSYNC` — the very call §5.3 pins — is a write
   barrier. Opening writable removes the platform question instead of depending on how it is answered.
3. **Publish the entry durably before SQLite touches it**, holding the descriptor from step 2: gate
   (§5.4), then `fchmod` it to the exact intended mode rather than trusting the umask to have left `0o600`;
   `backend.flush_file` on it; `backend.flush_directory` on `metadata_root_fd`; then close.

   This step exists because SQLite did not create the directory entry. SQLite's own COMMIT flushes
   the database and its WAL, and fsyncs a directory it created a file in — but `atoms.db`'s entry was
   created by *us*, before SQLite ever opened it, so nothing in SQLite's durability contract promises
   that entry survives power loss. A record could then be durable inside a file the directory does
   not name.

   The flushes go through **A4a's `Backend`** (`backend.py:87`, `:89`), never a raw `os.fsync`. The
   backend is where the platform difference lives: `linux.py:67` implements both as `os.fsync`, and the
   macOS implementation is where `F_FULLFSYNC` belongs. A5a calling `os.fsync` directly would state a
   durability claim the same design calls insufficient on macOS two tables below (§5.3, `fullfsync`).
   §8.1's promotion flushes go through the same two methods for the same reason.
4. Open through `binding.verified_metadata_path("atoms.db")` (§5.2), gate, and apply the pinned profile
   (§5.3), including the WAL transition — a persistent mutation of the file (§5.3), so it is gated like
   any other. This is the store's first WAL transition and writes a
   transient rollback journal beside `atoms.db`.
5. In **one explicit transaction**: the complete DDL, `PRAGMA user_version = SCHEMA_VERSION`, and
   `PRAGMA application_id = APPLICATION_ID`; then COMMIT.

   **The DDL is a tuple of single-statement constants executed one by one, never an `executescript`.**
   `sqlite3.Connection.executescript` issues a `COMMIT` before it runs, which under the pinned
   `isolation_level=None` (§5.3) silently ends the transaction opened above: measured, `in_transaction`
   goes from `True` to `False` across the call, and the `application_id` written before it stays durable
   afterwards. Using it here would destroy the exact property this step is for — an interrupted
   initialization would leave a half-built schema with a committed `application_id`, which is neither
   the completed shape nor the `(0, 0, empty)` resumable one, and §5.2 would refuse the store forever.
   The atomicity claim below is only true because every statement goes through `execute` inside the
   transaction; §11.6 forbids `executescript` in the package outright rather than carving out an
   exception for this site.

   **The gate runs immediately before this COMMIT** (§5.4), for §7.7's reason applied to the one
   transaction that is not a `_StoreTransaction`: the DDL is a loop of statements, and a lock released
   partway through would otherwise reach the durability barrier unauthorized. A failure there rolls
   back, leaving the `(0, 0, empty)` shape a later lease resumes.

`PRAGMA user_version` and `application_id` **are** transactional: set inside an explicit transaction,
both revert with a `ROLLBACK`, measured on SQLite 3.50.4. Initialization is therefore atomic in the
strong sense — an interrupted step 5 leaves a database with an empty schema, `user_version = 0`, and
`application_id = 0`, which is exactly the one resumable shape §5.2 accepts. That correspondence is not
a coincidence to be re-derived at reopen; it is why the shape is safe to resume.

**Creation is the only path that configures a completed store.** Setting `journal_mode` is a persistent
mutation of the database file, not a connection setting: on a database created in `delete` mode, issuing
`PRAGMA journal_mode=WAL` leaves it in `wal` mode for every later opener, measured. Reopen therefore
never sets it on a store it recognizes as complete (§5.2).

**Resumable initialization spans steps 2 through 5, and every one of them can be cut.** A crash between
step 2 and step 3 leaves the file created but not yet published — the mode is whatever the umask left
and the directory entry is not yet flushed; a crash between step 3 and step 4 leaves a published,
zero-length, still-`delete`-mode file; a crash between step 4 and step 5 leaves a WAL-mode file with an
empty schema. All are `(application_id 0, user_version 0, empty schema)`, and a zero-length file reports
`journal_mode = delete`, measured. §5.2 must therefore recognize the resumable shape **without** having
already required WAL, and must resume by re-running steps **3–5**.

Steps 3–5, not 4–5, and the earlier draft's omission was not cosmetic. Step 2's `openat` mode is a
*request* the umask reduces, and step 3 is where the mode is made exact and where the directory entry
becomes durable at all — the one thing SQLite's own contract never covers, since SQLite did not create
that entry. Resuming at step 4 would leave a store whose entry was never flushed and whose mode was
never corrected, permanently, because the resumable shape is consumed once and never revisited. Every
part of step 3 is idempotent — `fchmod` to an exact mode, two flushes — so re-running it on a cut that
already completed it costs two `fsync`s and asserts nothing was lost. On the resume path the descriptor
comes from opening `atoms.db` through the same guarded traversal rather than from step 2, and step 3
runs before any connection is opened, exactly as in the uninterrupted order.

### 5.2 Reopen

Three refusals happen **before SQLite sees the path**, because `verified_child_path` verifies the
metadata root's identity and returns a pathname; it inspects no leaf (`bootstrap.py:140`).

1. `fstatat(metadata_root_fd, name, AT_SYMLINK_NOFOLLOW)` for `atoms.db`, `atoms.db-wal`,
   `atoms.db-shm`, **and `atoms.db-journal`**. Each that exists must be a regular file. A symlink at
   any of the four is refused. The rollback journal is included because an interrupted first WAL
   transition (§5.1 step 4) can leave a hot one that SQLite must recover on the next open — a symlink
   there redirects that recovery.
2. **If `atoms.db` is zero length, re-run §5.1 step 3 here**, before SQLite is involved at all, in this
   order:

   1. gate (§5.4);
   2. `openat(metadata_root_fd, "atoms.db", O_PATH | O_NOFOLLOW | O_CLOEXEC)`;
   3. `chmod` that descriptor through `/proc/self/fd/<n>` to the exact intended mode;
   4. `openat` again with `O_RDWR | O_NOFOLLOW | O_CLOEXEC`, now permitted;
   5. `backend.flush_file` on it, `backend.flush_directory` on `metadata_root_fd`, close both.

   **The mode must be fixed before the file can be opened at all, which is why the repair cannot simply
   re-run step 3's `fchmod`.** §5.1 step 2 requests `0o600`, and the umask *subtracts*: under `0o277` the
   file lands at `0o400`, and under `0o777` at `0o000`. Creation itself is unaffected — step 3 holds the
   descriptor step 2 opened, and an open descriptor's access is already resolved. The resume path has no
   such descriptor and must obtain one from a name.

   Measured at `0o000`: `O_RDONLY | O_NOFOLLOW` and `O_RDWR` both fail `EACCES`, so there is no
   descriptor to `fchmod`. `O_PATH | O_NOFOLLOW` opens regardless of mode — it grants no read or write,
   only identity — but `fchmod` on it fails `EBADF`. `chmod` through `/proc/self/fd/<n>` on that same
   descriptor succeeds, and `O_RDWR | O_NOFOLLOW` succeeds afterwards. That is the route, and it is
   race-free for the reason it exists: the descriptor pins the inode step 1 stat'd, so nothing between
   the two can substitute a symlink or a different file.

   The two rejected alternatives are rejected on measurement, not taste. `os.chmod(name, mode,
   dir_fd=..., follow_symlinks=False)` appears to work here, but `os.chmod` is **not** in
   `os.supports_follow_symlinks` on Linux, so the behavior is uncontracted and a future release may
   raise `NotImplementedError` instead. Dropping `follow_symlinks` does work and re-opens the symlink
   window step 1 just closed. `/proc/self/fd` is therefore a requirement rather than a convenience:
   `open_store` checks it once and raises `CapabilityUnavailable` if it is not a directory, per §9's
   rule for semantics the platform does not supply. There is no fallback to a path-based `chmod`.

   Ordering the repair after classification made it unreachable, which is the defect this step fixes.
   At `0o400` SQLite falls back to opening read-only, so the classification reads in step 3 all succeed
   and return `(0, 0, empty)` — and then the very first write of the resume, the WAL transition, fails
   with `attempt to write a readonly database`. Opened without that fallback the failure moves earlier,
   to `unable to open database file` at connect. At `0o000` neither happens, because nothing opens at
   all. Three different failures, one cause: the repair was ordered from a position that could no longer
   carry it out.

   **A zero-length file is why this does not violate "read before writing."** That rule exists so
   reopen cannot mutate a database it has not recognized — but a zero-length file has nothing to
   recognize and nothing to rewrite. It cannot be a completed store, since a completed store has a
   schema and therefore a nonzero length, so the only classification this anticipates is one the
   `fstat` of step 1 has already settled. The repair changes a mode and issues two flushes; it writes no byte of database
   content. Every nonzero file keeps the read-before-write path unchanged, including the resumable cut
   after §5.1 step 4, which is nonzero because SQLite has written a header and whose mode step 3 already
   made exact.
3. Open, and **read before writing anything at all.** Reopen must not mutate a database it has not yet
   recognized, and `PRAGMA journal_mode=WAL` is a mutation: on an existing `delete`-mode database it
   persistently converts the file, measured. Applying the profile first would silently convert a
   foreign database *while deciding whether to refuse it*.

   **Identity comes first, journal mode second.** This ordering is load-bearing, not stylistic: the
   resumable shape of §5.1 includes a cut where WAL was never reached, and a zero-length file reports
   `journal_mode = delete`, measured. Demanding `wal` before reading the version would make the
   protocol refuse the one state it exists to resume. So step 4 decides *what this database is* from
   `application_id`, `user_version`, and the catalog — all reads — and only then does step 5 apply the
   journal-mode rule that the verdict selects.
4. Identity and version, from reads alone:

   | `application_id` | `user_version` | schema | Verdict |
   | --- | --- | --- | --- |
   | `APPLICATION_ID` | `SCHEMA_VERSION` | matches | **Completed store.** Continue at step 5. |
   | `APPLICATION_ID` | other | any | Refuse — incompatible store version. |
   | `APPLICATION_ID` | `SCHEMA_VERSION` | differs | Refuse — same version, wrong schema. |
   | `0` | `0` | empty | **Resumable initialization.** Continue at step 5. |
   | `0` | `0` | non-empty | Refuse — foreign database. |
   | other | any | any | Refuse — foreign database. |

   `application_id` identifies a *completed* store; it does not disambiguate the zero case, because an
   interrupted initialization and a foreign empty database both have zero application id. The
   `(0, 0, empty schema)` shape is accepted as resumable for one honest reason: it contains no durable
   evidence, and it sits inside an engine-owned namespace the consumer is forbidden to target.

   Schema-catalog validation is part of this step, and "matches" is exact: compare the full
   `(type, name, tbl_name, sql)` of every `sqlite_schema` row against the expected catalog as a set,
   **including implicit objects**. Two measured facts make the naive comparison wrong. A `PRIMARY KEY`
   creates an implicit `sqlite_autoindex_*` row whose `sql` is `NULL`, so a comparison over source DDL
   strings alone has no counterpart for it — and ignoring `sql IS NULL` rows would let an attacker's
   extra index pass. And SQLite **strips the terminal semicolon**: the stored text of
   `CREATE TABLE ... ) STRICT;` comes back without it. The expected catalog is therefore built as
   explicit `(type, name, tbl_name, sql)` tuples with `sql` normalized by stripping the trailing
   semicolon, and with each implicit autoindex listed by name with `sql = None`. Set equality means an
   extra object fails just as loudly as a missing one. `user_version` alone does not prove the schema
   is the expected schema.
5. Apply the journal-mode rule the verdict selects.

   - **Completed store:** query `journal_mode`; it must already be `wal`. Never set it. A completed
     store not in WAL was not written by this engine's creation protocol, and converting it would
     rewrite a database on the strength of a guess.
   - **Resumable initialization:** §5.1 step 3 has already been re-run by step 2 above, so this
     completes steps **4–5** — the profile including WAL, then the DDL, `user_version`, and
     `application_id` transaction. Applying the profile alone would leave the database exactly as
     unfinished as it was found, and the next reopen would resume it again forever; skipping step 3
     altogether, as an earlier draft did, would leave the publication and mode correction undone
     permanently, since the resumable shape is consumed exactly once (§5.1).
6. `PRAGMA quick_check` and `PRAGMA foreign_key_check`, both of which must be clean, then the
   **connection-local** pragmas of §5.3, set and read back.

**Nothing is silently rewritten.** Every mismatch above raises, and the only *application-issued*
metadata write on the reopen path is the resumable-initialization case, which writes into a provably
empty schema.

The qualifier is deliberate. Opening a database with a hot rollback journal makes SQLite perform its
own recovery, and recovery writes. A5a cannot prevent that and should not claim to: the preflight of
step 1 is what makes that recovery safe, by proving the journal is a regular file rather than a
symlink aimed elsewhere.

### 5.3 The pinned profile

The profile has two halves, and conflating them is what created the reopen defect above.

**Persistent — a property of the database file. Set only while initializing (§5.1, or §5.2 step 5's
resumable case, which is the same initialization finishing). On a completed store it is verified, never
set.**

| Pragma | Value | Why |
| --- | --- | --- |
| `journal_mode` | `WAL` | Authority §7. A `COMMIT` is the durability barrier. |

**Connection-local — set on every connection, including reopen, after verification.**

| Pragma | Value | Why |
| --- | --- | --- |
| `synchronous` | `FULL` | Authority §7.2. Each COMMIT flushes the WAL. |
| `fullfsync` | `1` on macOS | Authority §5.5. Plain fsync is not power-loss durable there. |
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

**Gating each record operation is not enough.** The lock can be released after the last write and
before the transaction commits, and that COMMIT is the durability barrier — the one operation whose
authority matters most. So the gate runs **again immediately before COMMIT**, inside
`transaction().__exit__`.

**The same rule covers the filesystem barriers, not only the COMMIT one**, and stating it once here
keeps three sections from each deciding it separately. Every operation that verifies before it mutates
runs unbounded work in between — promotion streams and hashes every staged file and every already-indexed
destination (§8.1 step 1), orphan removal hashes the leaf (§7.3), workspace removal stats every capture
(§8.3) — and the lock can be released inside any of it. A single gate at entry would prove only that the
lock was held when the verification began, which is the window the late COMMIT gate already exists to
close. A rename issued after the lease has ended writes into a namespace the next lease owner may
already be reclaiming, which is worse than a stale read: it is not observable from the store at all.

**The unit is the syscall, not the operation.** Promotion renames once per manifest entry and workspace
removal unlinks once per capture, so gating "before the first mutation" fixes the window at the front of
the batch and reopens it immediately: release the lock after entry one and every remaining entry races
the next owner. So the gate runs **immediately before each mutating syscall** — every rename, every
unlink. The shape is gate, verify, then gate-and-mutate repeatedly, with nothing between a gate and the
syscall it authorizes. This is affordable because the gate is an attribute read on a live object
(`binding.py:110`), against a loop that is already paying a full file hash per entry.

**Opening the store is not exempt, and an earlier draft's inventory silently began after it.** Both
§5.1 and §5.2 mutate before any `Store` exists — `openat(O_CREAT)` creates a name, `fchmod` changes a
mode, `PRAGMA journal_mode=WAL` persistently converts the database file, and step 5's transaction ends
in a COMMIT — so every argument this section makes applies to them first, not last. `open_store` takes
the binding as its argument, so the gate is available from its first line. The DDL is the sharpest case:
it is now executed statement by statement (§5.1 step 5), which is a loop, and without a gate adjacent to
its COMMIT a lock released partway through reaches the one barrier §7.7 gates everywhere else. The
inventory is therefore: before `openat(O_CREAT)`, before each `fchmod`, before the WAL transition, and
immediately before the initialization COMMIT. Reopen's step 2 repair carries the same gate before its
`fchmod`.

Flushes stay ungated throughout, in initialization as in promotion, for the reason already given: an
`fsync` on a held descriptor changes no name, and refusing one abandons a half-published state to raise
faster.

A batch that fails partway through a mutating loop is **not** a new disposition. It is exactly §8.2's
post-mutation failure: sources removed, some content published, the staging half spent, and the mixed
state preserved as evidence for §7.3 and §8.3 to reclaim. The gate refusing at entry seven is the same
shape as a mismatched blob refusing at entry seven.

If that late gate fails, `ROLLBACK` still runs before `ProtocolError` escapes. Abandoning an open
write transaction to raise faster would leave the database locked with uncommitted pages and no
owner. For the same reason, **`ROLLBACK` and `close` remain available after the binding or lock
dies**: they are how a dead session releases what it holds, and gating them would strand it.

**Rollback and descriptor or connection close are the only exemptions.** Reads gate, and so does every
operation that mutates the engine-owned namespace — `remove_workspace` issues `rmdir` and flushes, which
is a durable mutation that could race the next lease owner, not a release (§8.3).

A read taken without the lock is a read of a volume another process may be recovering, and A5b's whole
purpose is to *act* on what it reads — an unlocked read that looks live is worse than a refusal, because
it becomes the premise of a decision. So the rule is exactly: **every operation gates except `ROLLBACK`,
`Store.close`, and `Workspace.close`** — the three that give something up rather than take or change
something.

Nested transactions are refused with `ProtocolError` rather than mapped to savepoints. A5b chooses
one barrier per lease step; a nested `BEGIN` would mean two callers each believe they own the
boundary.

### 5.5 Caller-supplied names are validated before any filesystem mutation

§3.1 calls the txid "an opaque key," which is true of the `transaction_record` column and false of
`staging/<txid>/`. Three caller-supplied values become pathname components, and each is validated
before it reaches a syscall:

| Value | Rule |
| --- | --- |
| `txid` | Exactly `str`, then `atoms.core.identifiers.is_valid_identifier` — the A1 rule: 1–64 characters of `[A-Za-z0-9_-]`. |
| Manifest leaf names | Exactly `str`, then a single component: non-empty, not `.` or `..`, no `/`, no NUL. |
| Blob digests | Exactly `str`, then the grammar `sha256:[0-9a-f]{64}` (§8.1 covers how the leaf is derived from it). |

**Every one of these raises `ProtocolError`**, which is what §9's table promises for caller misuse.
Getting there takes an explicit gate rather than a reused helper: `require_valid_identifier` raises
`SpecValidationError` for `"../x"` and a **raw `TypeError`** for `3`, `None`, and `b"tx"` — measured for
all four — because it hands its argument straight to a compiled pattern. A `TypeError` from inside a
validator is indistinguishable from a bug in A5a's own code, and it is the response to the exact input a
hostile caller supplies.

So A5a validates in two steps, in this order:

1. `type(value) is not str` → `ProtocolError`. Exact type, per the A4b precedent at `approval.py:96`:
   a `str` subclass or a `PathLike` is refused, because either passes an `isinstance` gate and can then
   behave differently at the syscall.
2. The value rule — `is_valid_identifier`, the single-component test, or the digest grammar — with
   failure raising `ProtocolError` naming the value and the rule.

Step 1 is what makes step 2 total: `is_valid_identifier` is only safe to call once the argument is
known to be a `str`. A5a reuses A1's *predicate* and supplies its own refusal, rather than reusing a
raising helper whose exception type belongs to a different contract.

Guarded traversal does **not** make this unnecessary, and that is the trap worth naming: anchoring
`mkdirat(parent_fd, name)` to a held descriptor prevents the *parent* from being substituted; it does
nothing about `name` being `"../x"`, which is still resolved relative to that descriptor. A4b-1 refuses
multi-component names for exactly this reason, and A5a does not get to skip it because its namespace is
engine-owned — A5b will pass a txid that A4b regenerated (#7) and a manifest A6 produced.

The fixed engine-owned parents are guarded too. `staging`, `work`, `blobs`, and `sha256` are opened
one component at a time through `Backend.open_child_directory`, which carries A4a's `NO_SYMLINKS` and
`NO_XDEV` contract. A slash-containing `os.open("blobs/sha256", ...)` or a plain open of `staging`
would silently follow a substituted parent before the leaf-level checks ran. §11 proves the routing
statically and substitutes symlinks at both levels behaviorally. It does not create a private mount:
mount setup is privileged and A4a's backend suite already owns the `NO_XDEV` syscall contract; the
static helper proof is the non-privileged evidence that A5a cannot bypass it.

## 6. The schema

### 6.1 Tables

`transaction` is a SQL keyword, so the table is `transaction_record` rather than quoted at every use.

Each `CHECK` list below is written out for review, but in the source it is **generated** from its enum
(§6.2) — these are the values `TransactionState`, `CommitDecision`, `RollbackResult`, `EffectVariant`,
and `JournalState` currently hold. The block below is one listing for reading; in the source it is a
**tuple of single-statement constants**, executed one at a time inside §5.1 step 5's transaction. That
is not a style choice: `executescript` would commit that transaction out from under the protocol
(§5.1), and a per-statement inventory is what §11.6's blob-writer rule is stated over.

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

`blob.refcount` from authority §7.2's informative sketch is **dropped**. Its only consumer is garbage collection,
which authority §7.5 states is not part of transaction correctness. A blob is referenced iff a live
`transaction_record` names it, which is derivable when GC is written; a maintained counter would have
to be adjusted correctly by every future write path in A5b through A8, and a counter that reaches zero
early deletes a blob a live transaction still references.

### 6.2 Constraints

`STRICT` on every table, so column typing is engine-enforced. Note what `STRICT` does *not* do: it
coerces values that convert losslessly. `'42'` and `42.0` both store as integer `42` in an `INTEGER`
column; `42.5`, `'abc'`, and `b'x'` raise. §11.2 tests it with the latter.

Every `CHECK (... IN ...)` list is **generated from the A1/A3 enum** rather than retyped, so a new
member cannot silently diverge from the values the engine writes. §11.1 asserts the generated DDL
against the enums.

`foreign_keys=ON` (§5.3) makes `active.txid` and `effect.txid` real references: `active` can never name
a transaction that does not exist, which is precisely the shape authority §7.3 promises recovery will never see.

The single-active-transaction rule is the `CHECK (singleton = 0)` primary key from authority §7.2 — two active
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

The per-row `schema_version` from authority §7.2's sketch is **dropped**. It duplicates `user_version` and is
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

**Holding the transaction object is not holding a transaction, so the object must check.** Putting the
writes on `_StoreTransaction` makes a write hard to reach by accident; it does not make one
*unreachable*. Nothing stops a caller from retaining `txn` past the `with` block, and under
`isolation_level=None` a retained object's write autocommits: measured, an `INSERT` issued after context
exit landed durably with `connection.in_transaction == False` — one statement, its own barrier, exactly
the failure the explicit-boundary rule exists to prevent. It is not an exotic misuse either; storing the
yielded object on `self` is an ordinary thing to write.

So every `_StoreTransaction` method begins by asserting that **this object is the store's current active
transaction**, and raises `ProtocolError` otherwise. Two conditions, because one is not enough:

- The object is **spent** — set on *both* exit paths, commit and rollback alike. A rolled-back
  transaction's object must be as dead as a committed one's; it is the more dangerous of the two,
  since a caller retrying after an exception is precisely who still holds it.
- It **is** `store._active_transaction` — identity, not a boolean. A spent flag alone would let a stale
  object from an earlier transaction write into a later one that happens to be open, which is worse
  than an autocommit: the write lands inside a barrier some other caller owns.

`Store.transaction()` sets `_active_transaction` at `BEGIN` and clears it at both exits, so the two
conditions are one assignment maintained in one place. §11.2 tests the retained object on both paths and
the stale-object-into-a-later-transaction case.

### 7.1 The public surface

```python
__all__ = ("StagedBlob", "Store", "StoredRecord", "Workspace", "open_store")

def open_store(binding: ProjectBinding) -> Store: ...

class Store:                                   # context manager
    def transaction(self) -> AbstractContextManager[_StoreTransaction]: ...
    def read_record(self, txid: str) -> StoredRecord | None: ...
    def read_active(self) -> StoredRecord | None: ...
    def open_blob(self, digest: str) -> int: ...
    def list_unindexed_blobs(self) -> tuple[str, ...]: ...
    def remove_unindexed_blob(self, digest: str) -> None: ...
    def create_workspace(self, txid: str) -> Workspace: ...
    def reopen_workspace(self, txid: str) -> Workspace: ...
    def list_workspaces(self) -> tuple[str, ...]: ...
    def remove_workspace(self, workspace: Workspace) -> None: ...
    def close(self) -> None: ...

class _StoreTransaction:                       # yielded by Store.transaction()
    def promote_staging(self, workspace: Workspace,
                        manifest: tuple[StagedBlob, ...]) -> None: ...
    def insert_record(self, txid: str, spec: TransactionSpec) -> None: ...
    def set_transaction_state(self, txid: str, state: TransactionState) -> None: ...
    def set_commit_decision(self, txid: str, decision: CommitDecision) -> None: ...
    def set_journal_state(self, txid: str, effect_id: str,
                          state: JournalState) -> None: ...
    def set_rollback_result(self, txid: str, result: RollbackResult) -> None: ...
    def set_halt_diagnostic(self, txid: str, diagnostic: HaltDiagnostic) -> None: ...
    def set_active(self, txid: str | None) -> None: ...

@dataclass(frozen=True, slots=True)
class StagedBlob:
    name: str                                  # single component in staging/<txid>/ (§5.5)
    digest: str                                # "sha256:<64 hex>" (§5.5)
    byte_len: int
```

`__all__` is sorted because the toolchain requires it, not as a style preference: an earlier draft
listed `Store` first, and ruff's default rule set refuses that — `RUF022: __all__ is not sorted`,
measured, with ruff's own fix producing exactly the tuple above. A design whose stated surface fails
the project's lint gate is a design the plan cannot implement literally.

Record *writes* live on `_StoreTransaction`, not on `Store`. That placement makes a write hard to reach
without a transaction; the checks above make it *impossible*. `Store` keeps what is genuinely
transaction-free — open, close, reads, blob access, and workspace lifecycle.

**`promote_staging` is on the transaction too, and an earlier draft had it on `Store`** on the
reasoning that it is filesystem work with its own barrier. That reasoning was wrong in a way §8.1 now
states in full: publishing a blob and inserting its `blob` row are two substrates, and any interval
between them is a window in which the blob is on disk with nothing referencing it — indistinguishable,
to a concurrent reclaimer, from an authority §7.3 crash orphan. Holding both inside one write
transaction is what makes that window unobservable, and it costs no new lock.

**`promote_staging` writes the `blob` rows itself, and there is no separate `insert_blobs`.** Two
drafts ago the index write was its own transaction method, with a barrier at COMMIT asserting that every
promoted digest had a row. That barrier checked presence and nothing else, so it admitted exactly the
disagreement it looked like it prevented: promote `(digest, 10)`, insert `(digest, 11)`, and the row
satisfies the check while contradicting the bytes A5a itself hashed. It also let a caller insert a row
for a digest that was never promoted at all, for which no leaf need exist — the residue ledger #22 was
carrying.

Both vanish when one operation owns the whole publication. The digest and the `byte_len` written to the
index are the ones step 1 verified against the staged file, so they cannot disagree with the bytes and
cannot name a leaf that is absent. This removes a public method rather than adding a check, and it
removes the barrier with it: with no second way to write a `blob` row, "every promoted digest has a row"
is true by construction and asserting it at COMMIT would be asserting that the same function did both
halves of its own body.

Nothing needed the separate method. Every digest a record references is preparation material for that
transaction: initial-surface file states are captured preimages, and final-surface file states are
planned postimages which authority §7.3 step 2 explicitly requires preparation to write or verify
before the record COMMIT. Both sets therefore enter the transaction's promotion manifest (or the
verified `EEXIST` path when content is already present). §8.4's idempotency is unchanged; it is now
reached from inside promotion instead of from a method A5b had to remember to call.

**`_StoreTransaction` is private and stays private.** It is absent from `__all__` because no caller
constructs one or annotates against one — it is obtained only by entering `Store.transaction()`, used
inside that block, and dead after (§7). The underscore says so at the point of use; a public-looking
name absent from a documented-exact `__all__` would read as an oversight, and §11.6 asserts the surface
against `__all__` exactly. `Store`, `StoredRecord`, `StagedBlob`, and `Workspace` are all named by
callers and are exported.

**`StagedBlob` is an ordinary dataclass, deliberately unguarded.** A6 builds manifests — that is the
whole point of the type — so a construction token would block the intended caller and protect nothing:
the values are validated on arrival at `promote_staging` (§8.1), which is where a
forged one would have to do its damage. It is the opposite case from `Workspace` (§8.3), which A5a
issues because it carries descriptors A5a owns.

`insert_record` takes **no `variants` argument**. Every effect's variant is already in the spec:
`TransactionSpec.effects` is a `tuple[Effect, ...]`, so a separate mapping would be a second copy of a
fact the first argument already carries — and §7.6 has to *check* that the `effect` rows agree with
`spec_json` anyway, so the argument's only reachable effect is to disagree with the spec and be
rejected. The rule is the one §7.5 applies to row order: where `spec_json` is the authority, A5a derives
rather than accepts.

**Deriving it takes an explicit mapping, not `variant_name`.** That helper returns
`type(effect).__name__` (`effects.py:106`) — `"ReplaceFile"` — while the `effect.variant` column accepts
`EffectVariant`'s values, `"replace_file"` (`recovery/plan.py:37`). Measured for all five. The two
spellings are near enough to read as the same thing and are not; and while a `CHECK` violation on the
first `INSERT` would at least fail loudly, the tempting repair — deriving one spelling from the other by
case transformation — is what must be avoided, because it makes the schema value depend on a class name
that no longer has any reason to keep matching.

So `schema.py` carries a literal, total mapping, in the same module that generates the `CHECK` lists
from the enum, so both facts about a variant live together:

```python
EFFECT_VARIANTS: Mapping[type[Effect], EffectVariant] = {
    ReplaceFile:         EffectVariant.REPLACE_FILE,
    CreateFileNoClobber: EffectVariant.CREATE_FILE_NO_CLOBBER,
    DeletePath:          EffectVariant.DELETE_PATH,
    MoveNoClobber:       EffectVariant.MOVE_NO_CLOBBER,
    CreateDirectory:     EffectVariant.CREATE_DIRECTORY,
}
```

Lookup is by `type(effect)`, exact — not `isinstance`, which would accept a subclass and record it as
its base. A missing key raises `ProtocolError`. §11.1 asserts **totality in both directions**: every
member of the `Effect` union (`effects.py:56`) is a key, and every `EffectVariant` member is a value,
each exactly once. A sixth variant then breaks the build rather than the store — the same property §6.2
gives the `CHECK` lists, which is why the two belong in one module.

`StoredRecord` is a frozen value carrying the decoded `TransactionSpec`, the txid, `state`,
`committed`, `rollback_result | None`, `halt_diagnostic | None`, and the journal vector as a
`tuple[EffectJournalState, ...]` in `spec_json` order (§7.5). It is a plain value, not a live cursor.

**Descriptor ownership.** `Store` owns the connection and closes it in `close()`; `Store.__exit__`
calls `close()`. `Workspace` owns the directory descriptors it opened and closes them on its own exit.
`open_blob` returns a descriptor the **caller** owns and must close — the one place A5a hands out
ownership, marked as such in the signature's docstring because it is the exception. Nothing here owns
anything belonging to the binding: the `metadata_root` descriptor stays A4a's, and A5a only ever
borrows it, exactly as `ProjectBinding.__exit__` closes only what it opened.

### 7.2 Reading a blob

A5a owns the blob substrate — it decides the leaf name, performs promotion, and verifies digests — so
it is the only layer that can open a blob without re-deriving a mapping that is private to it (§8.1).
Authority §10 requires A6/A7 to compute "a file prefix relation ... by comparing that object with the
planned blob," which is a read of blob bytes during recovery. Without an operation here, the caller's
only route is to rebuild `blobs/sha256/<hex>` itself, and then the mapping is no longer private and a
second copy of it can drift.

`open_blob(digest) -> int` is the whole surface, and it does three things in this order.

**1. Index membership, before the filesystem.** The `blob` row is looked up first, and the two ways it
can be absent are different questions with different answers:

| Row | Leaf | Verdict |
| --- | --- | --- |
| absent | either | `ProtocolError` — the caller named content this store does not index |
| present | absent, or not a regular file | `MetadataStoreInvalid` — the index references a blob that is not there |
| present | present | Continue to step 2 |

An earlier draft opened by digest alone and called every miss `MetadataStoreInvalid`. Both halves of
that were wrong. A syntactically valid digest that was never stored is an invalid *request* against a
perfectly healthy store — reporting it as a store that "cannot be safely interpreted" would send A5b
into evidence preservation over an empty database, which is the substitution authority §11 forbids
(§9). And opening by leaf alone would happily return an **orphan** blob: promotion precedes the COMMIT
that references it (authority §7.3 step 4), so a crash in between leaves promoted blobs "with no
referencing row" — the authority's words — which are scratch awaiting reclamation, not durable record
knowledge. Membership is what separates the two, and only the index carries it.

**2. Content verification, before the descriptor is trusted.** The leaf is opened from the retained
`blobs/sha256/` descriptor with `O_RDONLY | O_NOFOLLOW | O_CLOEXEC`, `fstat`ed for kind and length
against the row's `byte_len`, then streamed to completion and hashed; a SHA-256 that is not the digest
raises `MetadataStoreInvalid`. The descriptor is then `lseek`'d back to zero and returned, so the
caller reads from the start.

An earlier draft skipped this on the grounds that the caller is already streaming the bytes. That does
not hold, and it costs the engine a refusal it has promised. Authority §11 lists "a blob whose bytes do
not match its digest" as a `MetadataStoreInvalid` condition, and A5a is the only layer positioned to
detect it — but the caller's own read cannot substitute, because authority §10's use is a **file prefix
relation**, which stops at the first difference or at the shorter length and never reaches EOF. A
truncated or tampered blob whose surviving head matches the live file would pass a prefix comparison
and be treated as an authentic preimage. Verification here is not duplicated work; it answers a
question the caller's read structurally cannot.

The cost is real and stated plainly: `open_blob` reads the blob once before the caller reads it again.
§8.2's verification is the same check at the other end of the blob's life, where the decision to trust
a *pre-existing* file is made; this one guards every subsequent read of it. If a future profiling
result makes the double read unacceptable, the fix is a caller-supplied verified-stream API, not a
silent removal of the guarantee.

**3. Ownership transfers.** The returned descriptor is the caller's to close (§7.1).

`open_blob` reads the index, so it is a public read and **refuses inside this store's own write
transaction** (§7.4), for that section's reasons applied to blobs: `promote_staging` followed by
`open_blob` in one transaction would otherwise report as durable a row that is not yet committed and a
blob that a rollback turns back into an orphan. The refusal is `ProtocolError`, and §11.4 arms it —
without a test the general rule stated in §7.4 would not actually cover this method.

### 7.3 Reclaiming a promoted blob nothing references

Promotion precedes the COMMIT that references it (authority §7.3 step 4), so a crash or a failed batch
between the two leaves what the authority calls "promoted blobs with no referencing row." It also says
what must become of them: "recovery reclaims the scratch **and unreferenced blobs** under the lock,
regardless of count." Nothing in `list_workspaces`, `reopen_workspace`, or `open_blob` can reach one —
the first two see only `staging/` and `work/`, and `open_blob` refuses an unindexed digest by design
(§7.2). §8.2's claim that a failed promotion's mixed state is reclaimable was therefore true of the
staged half and false of the promoted half.

Two operations close it, both minimal:

- **`list_unindexed_blobs()`** returns the digests whose leaf exists under `blobs/sha256/` with no
  `blob` row, by one anchored `scandir` reconciled against the index inside a deferred read
  transaction. Its result is advisory — removal re-checks — so it needs no isolation stronger than any
  other read.

  **A leaf that is not well-formed 64-character hex is not returned. It raises `MetadataStoreInvalid`
  and is left in place.** An earlier draft promised to report it "under the digest string it would
  imply," which this API cannot express: the return type is digests, `remove_unindexed_blob` validates
  its argument against §5.5's `sha256:[0-9a-f]{64}` grammar, and no string satisfying that grammar
  names a leaf like `tmp.part`. The enumeration would have handed back a value its own removal refuses,
  so the malformed leaf was unreclaimable either way. Raising is also the substantively correct answer
  rather than a retreat to the cheap one. Every leaf under `blobs/sha256/` is created by §8.1 step 2
  from a digest that already passed §5.5, so leaf names are fixed-width hex *by construction*, and
  authority §7.3's genuine promoted orphans therefore always have valid names. A leaf that does not is
  not a crash survivor: it is a foreign write into an engine-owned directory, which is exactly the
  "cannot be safely interpreted" condition authority §11 gives `MetadataStoreInvalid`, and whose stated
  response is to stop and preserve evidence — not to unlink something A5a cannot account for. §8.5
  states the general rule this is one row of.
- **`remove_unindexed_blob(digest)`** unlinks the leaf and flushes `blobs/sha256/`. It **re-checks
  membership and unlinks under one `BEGIN IMMEDIATE`**, refusing an indexed digest with
  `ProtocolError`. That fail-closed direction is the whole safety property: the operation can only ever
  delete something no record names, so a caller passing a stale digest from an earlier enumeration
  destroys nothing.

  **The exclusion must be `IMMEDIATE`, not the deferred read every other read takes — and `IMMEDIATE`
  here is necessary without being sufficient.** A deferred transaction under WAL fixes its snapshot at
  its first query and holds no write lock, so another connection — and §7.4 already establishes that
  two `Store` objects may share one live `ProjectBinding` — can insert and commit the blob row while
  the recheck still reads the older snapshot. Measured: the writer's `COMMIT` succeeded and the
  remover's re-read inside its own open transaction still returned no row.

  Escalating to `BEGIN IMMEDIATE` blocks that writer, but blocking is not excluding. If the writer had
  already promoted the leaf and was waiting only to index it, the remover still sees no row, unlinks,
  commits, and the writer then commits the row — measured as `writer_committed=True`, row present,
  leaf absent. **What actually closes it is §8.1**: promotion moved inside the writer's own write
  transaction, so no writer can be *between* publishing a leaf and indexing it while some other
  connection holds the write lock. The two rules are one mechanism seen from its two ends, which is why
  neither is sufficient alone, and why §11.4 tests the interleaving rather than the isolation level.

  This does not contradict §7.4's deferred rule. That rule is about *reads*, which must not block
  A5b's writer. This operation mutates the engine-owned namespace, so it is a writer itself and takes
  a writer's lock — the same distinction §5.4 draws when it refuses to exempt `remove_workspace` from
  the liveness gate. For the same reason it is refused inside this store's own write transaction, by
  §5.4's no-nesting rule, where the `BEGIN IMMEDIATE` could not be taken at all.

  **The leaf is verified before it is unlinked, and an unverifiable one is preserved** (§8.5). It runs
  §7.2's verification — the shared helper, not the public `open_blob`, which refuses inside a write
  transaction and would refuse an unindexed digest anyway — with the one difference that an orphan has
  no row to supply a `byte_len`, so the check is kind plus streamed SHA-256 against the name, and the
  length follows from the hash. Hashing is unbounded work, so the §5.4 gate runs again between the
  verification and the `unlink`. A well-formed name is not enough: a symlink, a directory, or a regular
  file whose bytes do not
  hash to its name is as impossible a product of promotion as a name that is not hex, and unlinking it
  would destroy the only evidence of a foreign write. `MetadataStoreInvalid`, leaf intact. Refusing the
  name while accepting any content under it was the asymmetry an earlier draft left. A leaf that is
  already gone raises `ProtocolError` rather than `ENOENT` or a silent success, matching
  `remove_workspace`'s non-idempotence (§8.3): the argument is stale and the caller re-enumerates.

This is not the garbage collection §2.2 excludes. That non-goal cites authority §7.5, which is
**terminal cleanup after `COMMITTED` is durable**, and turns on whether a live `transaction_record`
still references a blob — the `unreferenced_digests` query A5a does not ship. What is needed here is
authority §7.3's pre-COMMIT reclamation, and its predicate is strictly simpler: *is there a row at
all*. One is about the lifetime of referenced content; the other is about debris that was never
published. A5a supplies the mechanism for the second and still ships nothing for the first.

**Calling them is A5b's, and that is a tracked obligation.** A5a never invokes either operation — it
has no lease, no entry point, and no way to know a crash occurred. Ledger #23 records that A5a supplies
enumeration and removal for both survivor kinds while nothing yet runs them at lease entry (§3.2).
A5b discharged #23 on 2026-08-02: every lease entry now enumerates and reclaims both survivor
kinds under the held lock.

### 7.4 Reads take one snapshot

`read_record` and `read_active` each issue **several** queries — the record row, its `effect` rows, the
`blob` rows for its digests, and for `read_active` the `active` row first. Without a transaction those
are separate snapshots, and §7.6's cross-row rules would then be checked against a state that never
existed as a whole.

The window is real, not theoretical. Two `Store` objects may share one live `ProjectBinding` — nothing
in A5a or A5b forbids it — and each holds its own connection. With one writer committing between a
reader's queries, a transaction-free reader saw `state = PREPARED` from before the commit and a journal
state of `started` from after it: measured, torn across a commit boundary. Under WAL that reader takes
no lock and blocks nothing, which is exactly why it slides.

So **every read is wrapped in one read transaction** — `BEGIN`, the queries, `COMMIT` — giving all of
them a single WAL snapshot. The same interleaving under a read transaction returned the pre-commit
values for both queries, measured. `BEGIN` deferred rather than `IMMEDIATE`: a reader takes no write
lock and must not block A5b's writer.

**Every exit from a read closes its transaction, including the failing ones.** A read's most likely
ending is not `COMMIT`: it is a decode failure or a §7.6 refusal, both raised from Python after the
queries have run. Leaving on that path without a `ROLLBACK` leaves `in_transaction` true, and the next
`BEGIN IMMEDIATE` then fails with `cannot start a transaction within a transaction` — measured. The
damage is displaced twice over: the store still holds valid data, A5b's *next write* is what fails, and
it fails as an `OperationalError` naming nesting, which is a true statement about a state some earlier
read created. So the read transaction lives in a `try/finally`, and any failure rolls back before the
exception leaves.

**Liveness is gated again before the record is returned** (§5.4). A load runs several queries plus
`compile_spec` and the full §7.6 predicate; the lock can be released during that work, and a
`StoredRecord` handed back afterwards is evidence from a volume this process no longer holds. A5b's
whole purpose is to *act* on what it reads, so the second gate is placed where the value crosses the
boundary — the same reasoning as §7.7's gate before COMMIT, and the same shape: gate, work, gate,
release. A failure raises `ProtocolError` after the rollback.

This does not make `read_record` a *write* path, and the transaction it opens is A5a's own, not one
A5b may nest into — `transaction()` still refuses to nest (§5.4).

**A public read issued while this store owns a write transaction raises `ProtocolError`.** An earlier
draft had it reuse the open transaction's snapshot, which reads reasonably and is wrong: that snapshot
includes the caller's own uncommitted writes, so the value returned is not durable state. Two concrete
failures follow, and they point in opposite directions:

- the setters are independent (§7.7), so mid-sequence the record is *legitimately* incoherent —
  `ROLLED_BACK` set, its result not set yet. A read there would run §7.6's predicate over a state the
  caller is halfway through building and report `MetadataStoreInvalid` about a store with nothing
  wrong with it;
- or the sequence is complete and coherent, the read returns a `StoredRecord`, and the transaction
  then rolls back. A5b would be holding a record of something that never happened.

`read_record` and `read_active` mean *what is durable*. Refusing is the smallest contract that keeps
that true, and no consumer needs the alternative: A5b writes what it just decided, so it already knows
it. §7.7's pre-COMMIT validation still reads inside the transaction — that is internal, it is
explicitly checking uncommitted state, and it returns a finding rather than a `StoredRecord`.

### 7.5 Effect order is reconstructed, never queried

The journal vector must be in `spec_json` order, and **the schema cannot express that order**. Its
primary key is `(txid, effect_id)`, so SQLite answers `SELECT ... WHERE txid = ?` through the covering
index in `effect_id` order: rows inserted `z, a, m` come back `a, m, z`, measured.

No ordinal column is added to fix this. The order already exists, exactly once, in `spec_json` — which
is immutable (§6.3) and is the thing A2 proved. A second copy in the `effect` table could disagree with
it, and then the store would hold two answers to one question.

Instead the load builds a `dict[str, JournalState]` keyed by effect id, proves **exact coverage** in
both directions — no effect id in `spec_json` missing a row, no row naming an effect id `spec_json`
does not contain — and then emits the journal vector by walking `spec_json`'s effects in order. Row
order becomes irrelevant rather than trusted.

### 7.6 Cross-row validation

A single well-typed row is not evidence the record is coherent. These rules define what a coherent
record *is*, and they are checked on **both** sides of the store — before returning a `StoredRecord`,
and before every COMMIT (§7.7). The list is written once because it is one predicate:

- `spec_json` decodes, re-encodes to exactly the stored text (§6.3), and **passes `compile_spec`** —
  a stored spec that A2 would refuse was never one this engine wrote;
- `effect` rows cover the `spec_json` effect ids exactly, in both directions (§7.5);
- each `effect.variant` matches that effect's variant in `spec_json`;
- every digest the record references has a `blob` row, and that row's `byte_len` equals the
  `FileState.byte_len` of every reference to it;
- `rollback_result` is present **exactly** when `state` is `ROLLED_BACK`;
- `halt_diagnostic` is present **exactly** when `state` is `HALTED`;
- a present diagnostic's `commit_decision` and journal vector equal the durable row values;
- if an `active` row exists, its transaction record exists — also a foreign key, checked here so a
  store an earlier writer opened with `foreign_keys` off is still caught;
- the diagnostic encoding is well-formed: a malformed payload, a missing or extra field, an unknown
  enum member, or a duplicate key all refuse.

**The predicate returns a finding; the caller chooses the exception.** It is one function used from two
sides, and the two sides mean different things. On a load, a violation is durable state that cannot be
interpreted: `MetadataStoreInvalid`. On a write (§7.7), the same violation is a caller assembling an
incoherent record, caught before anything is durable: `ProtocolError`. So the shared code returns a
structured finding — the rule that failed and the values — and each site raises its own type from it,
rather than the predicate raising and the write path catching and relabelling.

These are all checks for **malformed persisted input**, not transition judgments — the line §6.2 draws.
A journal vector that is legal SQL and legal encoding but describes an impossible history, or a commit
decision that conflicts with the transaction state, is left for A3 to classify: those are recovery
inputs, and pre-empting them here would put a second classifier in the store.

### 7.7 The same predicate runs before COMMIT

A5a's own typed write API can otherwise produce a record its own loader rejects. The setters in §7.1
are independent by design — A5b advances one column at a time — so nothing in the surface stops a
caller from committing `ROLLED_BACK` with no `rollback_result`, `HALTED` with no `halt_diagnostic`, or
an `effect` row whose variant contradicts `spec_json`. The database cannot catch these either: `CHECK`
constraints are per-row, and every one of these is a relationship *between* rows or between a row and
`spec_json`.

Validating only on read is the wrong half. It means the store accepts the write, makes it durable, and
refuses it later — reporting the defect to whichever process reopens the database, with the writer that
caused it long gone, as `MetadataStoreInvalid`, the exception that tells a caller to stop and preserve
evidence. A5a would have manufactured the corruption it reports.

So `_StoreTransaction.__exit__` runs §7.6's predicate over **every txid the transaction touched**, inside
the still-open transaction. A failure **rolls back** and raises; nothing partial reaches the WAL. The set
of touched txids is accumulated by the setters themselves, so the cost is proportional to what was
written rather than to the store.

**A second check runs in the same slot: every digest this transaction promoted is referenced by the
record for the workspace's txid.** `promote_staging` registers both the txid — so §7.6's predicate runs
for it, which promotion alone would not have caused — and the digests it published.

The check exists because the alternative is an orphan nothing can ever reclaim. `blob` has no foreign
key to `transaction_record` and cannot have one, since the reference lives inside `spec_json`, so a
transaction that promotes and commits without inserting a record leaves `(transaction_record=0,
blob=1)` — reproduced. That row is invisible to both reclaimers by construction: §7.3's predicate is
*is there a row at all*, and the row is there; §7.5's terminal GC, which asks whether a live record
still references the blob, is the one A5a explicitly does not ship (§2.2). The blob would be
permanently stranded, and §7.3's claim that pre-COMMIT reclamation reduces to a row lookup would be
false in exactly the case that matters.

Requiring the reference restores it. After a successful COMMIT every `blob` row has a referencing
record, so "no row" and "no reference" are the same question for anything A5a can produce, and §7.3's
simple predicate is sound rather than merely cheap. A failure rolls back, which removes the rows and
leaves the published leaves as unindexed orphans — reclaimable by §7.3, the same disposition a crash
at that point produces.

An earlier draft put a *different* check in this slot: that every promoted digest had a `blob` row. That
one was necessary while indexing was a separate `insert_blobs` call, insufficient even then — presence
is not agreement, and the row could carry a `byte_len` the bytes contradict (§7.1) — and is now vacuous,
since one function writes both. It was the wrong direction throughout. What needed proving was never
that the blob reached the index, but that something in the record points at it.

**The exit sequence is gate → validate → gate → COMMIT**, and the second gate is adjacent to the COMMIT
with nothing between them. Validation runs `compile_spec` over every touched spec and re-reads rows; on
a large transaction that is unbounded work, and a single gate *before* it would prove the lock was held
when the exit began rather than when the barrier ran — reopening the exact window §5.4 added the late
gate to close, just wider than the original. The first gate is still worth running: it refuses before
doing the work, rather than after.

**A mutating method that raises poisons the transaction.** The barrier reads bookkeeping the methods
themselves maintain, so it can only be as complete as they are, and every one of them registers what it
touched *after* its last statement succeeds — the only order that does not register writes that never
happened. That leaves a hole a caller can walk through: SQLite rolls back the failing *statement*, not
the transaction. Measured — inside `BEGIN IMMEDIATE`, an `INSERT` that violates a `UNIQUE` constraint
raises, `in_transaction` is still true, the earlier `INSERT` of the same method is still visible, and
the `COMMIT` makes it durable. So a caller that catches `insert_record`'s failure inside the `with`
block and continues commits a `transaction_record` whose `effect` rows are missing, with the barrier
never told the txid existed.

The fix is not to register earlier — that would trade this hole for rows registered for statements that
never ran. It is that **any `_StoreTransaction` method which raises marks the transaction poisoned, and
the exit rolls back and re-raises rather than committing**, whatever the caller did with the exception
in between. Catching an A5a exception and carrying on cannot be made to mean "the write did not
happen", because in SQLite's semantics it does not.

**Every exit closes the SQLite transaction, including a `COMMIT` that fails.** A failed `COMMIT` leaves
the transaction open: measured, a deferred constraint violation raises at `COMMIT`, `in_transaction` is
still true afterwards, and the next `BEGIN IMMEDIATE` raises `cannot start a transaction within a
transaction`. A store that spends its `_StoreTransaction` and clears its slot on that path looks
recovered and is not — every later `transaction()` on it fails, reporting a caller error for a condition
A5a created. The exit rolls back whatever is still open on *any* failing path and re-raises the original
exception, never masking it with the rollback's own.

**A refused write raises `ProtocolError`, not `MetadataStoreInvalid`.** The transaction rolled back, so
the durable store is exactly as valid as it was — nothing about it "cannot be safely interpreted," and
the correct response is for the caller to fix its call, not to stop and preserve evidence. Authority §11
settles it: `MetadataStoreInvalid` is "raised by the store layer, never as a substitute for
`ProtocolError`, which tells a caller to fix its call." Raising it here would have been the substitution
that sentence forbids, and would have sent A5b into evidence preservation over a store with nothing
wrong with it. The predicate is shared; the verdict is not (§7.6).

Two consequences worth stating, because they are the point rather than a limitation:

- **The boundary of §6.2 is unchanged.** This validates structure — presence, agreement, coverage — not
  transition legality. `set_transaction_state(txid, APPLIED)` on a record A3 would never advance that
  way still commits; that judgment is A3's, and A5b's to enforce (ledger #12).
- **Multi-step sequences are unaffected** as long as each *transaction* leaves a coherent record. Moving
  to `ROLLED_BACK` requires setting the result in the same transaction, which is the invariant, not an
  obstacle to it.

§11.2 tests each write-side refusal independently, and asserts that after a refused COMMIT the database
still holds the pre-transaction record.

### 7.8 The store's own lifecycle

`Store` is a resource, and every resource in this repo states what happens after it is released. Left
unstated, the answer would come from pysqlite rather than from A5a: a use-after-close raises
`sqlite3.ProgrammingError("Cannot operate on a closed database.")`, measured — a `DatabaseError`
subclass carrying **no `sqlite_errorcode` attribute at all**, so §9.1's translation shape would hit an
`AttributeError` inside its own handler while trying to classify it. A5a's own misuse must not arrive
dressed as a database condition.

- **`close()` is idempotent.** Calling it twice is not an error, matching `sqlite3.Connection.close`
  and letting `__exit__` run after an explicit close.
- **`close()` rolls back any transaction it finds open, then spends it.** SQLite already discards an
  open write transaction on close — the uncommitted row was gone after `close()`, measured — but it
  does so silently, so A5a issues the `ROLLBACK` explicitly and marks the `_StoreTransaction` spent.
  Silent discard and explicit rollback have the same durable result and different diagnostics, and the
  spend is what stops a retained object from being used against the next store.
- **Every operation after close raises `ProtocolError`**, checked before the connection is touched.
  This is caller misuse in exactly authority §11's sense, and it must never surface as
  `sqlite3.ProgrammingError`, which a caller would have to know pysqlite's hierarchy to interpret.
- **`close()` does not gate on liveness.** It is a release, so it is exempt for §5.4's reason: a store
  whose binding died must still be closable, or a dead session strands its connection.
- **Closing does not close the binding.** `metadata_root` is borrowed, and `ProjectBinding.__exit__`
  closes only what it opened (`binding.py:81`). `close()` closes the connection A5a opened and any
  `Workspace` descriptors still outstanding, and nothing else.

§11.2 covers the sequence directly: double close; close with an open write transaction, asserting the
rollback and that the retained object is spent; and each public method after close raising
`ProtocolError` rather than any `sqlite3` exception.

## 8. Blobs and workspaces

### 8.1 Promotion

Promotion is a **batch over a complete manifest**, not a per-file call. A singular `promote()` cannot
honor authority §7.3 step 3: after the first file moves, `staging/<txid>/` still holds the others, so the call
cannot remove the directory it promised to remove.

**Promotion runs inside the caller's write transaction**, as a `_StoreTransaction` method, and it
**writes the `blob` rows itself** (§7.1) — publication and indexing are one operation, not two bound by
a check. The reason is that a promoted blob with no row is exactly authority §7.3's crash-orphan shape,
so any *steady state* in which one exists is a state a concurrent reclaimer is entitled to delete.

An earlier draft left promotion on `Store`, outside any transaction, and tried to close the resulting
race inside §7.3 by escalating the reclaimer to `BEGIN IMMEDIATE`. That only moves the window. The
promoting writer is blocked on the lock rather than excluded from the sequence, so the reclaimer's
recheck still sees no row, unlinks the leaf, commits, and *then* the writer proceeds to commit the row
it was holding. Measured, with the writer blocked mid-flight: `writer_committed=True`, the row present,
the leaf gone — a committed record naming a blob that no longer exists, which is the single worst state
this store can reach. `IMMEDIATE` on the reclaimer alone cannot fix it, because the defect is not in the
reclaimer: it is that publication and indexing were two intervals.

With promotion inside the write transaction they are one. The reclaimer's own `BEGIN IMMEDIATE` (§7.3)
now genuinely excludes it: the reclaimer either runs entirely before the promotion, and sees no leaf, or
entirely after the COMMIT, and sees a leaf with a row. The only way to observe a promoted-but-unindexed
blob is a crash or a rollback between the two — which is precisely the case the authority says recovery
reclaims, and which no live writer can be in the middle of.

The cost is that the SQLite write lock is held across renames and directory flushes. Under authority
§7.1's universal lease there is no second legitimate writer to delay, so this is a cost on paper only —
and it is the same cost §7.3's removal already pays.

`promote_staging(workspace, manifest)` where the manifest names every file in the workspace's staging
directory with its digest and byte length:

1. **Preflight the whole manifest before the first rename.** Promotion is one batch and has no undo:
   once entry *k* has been renamed, discovering that entry *k+1* is malformed leaves the workspace
   half-promoted, with some blobs published, some files still staged, and a `staging/<txid>/` that step
   3 can no longer remove. Every rejection must therefore happen while nothing has moved.

   The manifest must be exactly `tuple`, each element exactly `StagedBlob`, and for every element:
   `name` and `digest` pass §5.5, and `byte_len` is exactly `int` — not `bool`, which is an `int`
   subclass — and non-negative. Across elements, **source names are unique**: two entries naming the
   same file would rename it once and then fail `ENOENT` on the second, mid-batch. Duplicate *digests*
   under different names are allowed, since that is ordinary content addressing rather than a
   malformed manifest — but they must agree on `byte_len` (§8.4), because two lengths for one digest
   are two incompatible descriptions of a single content-addressed object.

   **Completeness is checked here too, not at step 4.** One descriptor-anchored `scandir` of
   `staging/<txid>/` yields its exact entry set, which must equal the manifest's names — no omission,
   no entry naming a file that is not there. Deferring this to step 4's emptiness requirement was the
   original mistake: by then every listed source has already moved, so an omitted file is discovered
   only after the batch is half-executed and no longer replayable. The emptiness check stays as the
   invariant it always was, but it should now be unreachable as a first detector, and §11.4 asserts
   that the omission case fails at step 1 with the staging directory untouched.

   **The staged sources are verified here as well, before any of them moves.** Each named file is
   opened from `staging_fd` with `O_RDONLY | O_NOFOLLOW`, must be a regular file, must have exactly
   `byte_len` bytes, and must stream to exactly `digest`. Without this, promotion's *first* publication
   of a digest was never checked at all: §8.2 verifies only a pre-existing destination, so on the
   ordinary path a non-regular source, a wrong length, or a wrong digest became a new blob under a name
   asserting content it does not have — and every later `open_blob` would then correctly report
   `MetadataStoreInvalid` for a blob A5a itself published. It also repairs §8.4's premise, which
   assumed a verification that was not being performed.

   Verification belongs in the preflight rather than beside each rename for the same reason the rest of
   step 1 does: a mismatch on entry *k* must not find entries before it already published. The cost is
   one full read of each staged file, on top of the hash A6 computed while streaming it (authority §6,
   Guarantee 3). That is the same trade `open_blob` makes (§7.2) and the same answer: a content-address
   the engine never checked is a name asserting something nobody verified.

   **An already-indexed digest has its leaf verified here, in full.** For every manifest digest that
   already has a `blob` row, the destination leaf is opened and checked exactly as §7.2 checks it —
   present, regular, and streaming to the digest — and the row's `byte_len` must equal that length.
   Any failure raises `MetadataStoreInvalid` before anything moves.

   Comparing only the row's `byte_len`, as an earlier draft did, left promotion **silently repairing
   corruption it is not entitled to repair**. If the indexed leaf is missing, step 2's rename finds no
   destination, succeeds, and re-creates it — while `open_blob` on that identical starting state calls
   it `MetadataStoreInvalid` and refuses (§7.2). One operation would have quietly healed the state
   another reports as unreadable, and the healing is not sound: the row was written against bytes that
   are gone, and nothing establishes that the manifest's file is those bytes rather than merely a file
   whose digest matches a name. Two operations disagreeing about whether the same durable state is
   valid is the defect, independently of which verdict is nicer.

   §8.2 still handles `EEXIST` at step 2, and now covers exactly one case: a destination that exists
   with **no** row — a promoted orphan from an earlier crash (§7.3). That one cannot be preflighted by
   the index because the index does not know about it.

   Each failure raises `ProtocolError` — the manifest is the caller's argument, not durable state. The
   exceptions are the leaf checks just above, which report durable state and are `MetadataStoreInvalid`
   by §9's rule.

   **The gate runs once more at the end of step 1** (§5.4), and again before every rename in step 2.
   Step 1 streams and hashes every staged file and every already-indexed destination, which is unbounded
   work proportional to the capture, and the lock can be released during it.
2. For each entry, **re-run the §5.4 gate**, then no-clobber rename `staging/<txid>/<name>` →
   `blobs/sha256/<hex>`. `EEXIST` is handled in §8.2, which unlinks the staged source **behind its own
   gate**, since it hashes the destination first and that hash is unbounded. The gate is per entry, not
   per batch: a thousand-file capture is a thousand mutations, and one gate before the first proves only
   that the lease was held when the loop started. Releasing the lock after entry one leaves the
   remaining renames racing the next lease owner, which is the same defect a single entry gate had,
   moved one level in. It is an attribute read against a live object, so per-syscall is affordable; the
   batch already pays a full hash per file.
3. `backend.flush_directory` on `blobs/sha256/` **and** `staging/<txid>/`. Flushing only the blob
   directory could leave both the blob and its staging source name durable after power loss,
   resurrecting preparation-only staging — authority §7.3's stated reason.
4. Require `staging/<txid>/` to be empty, **gate**, then `rmdir` it. **The staging half is spent the
   moment that `rmdir` succeeds**, not the moment a source is removed. The two differ on an empty
   manifest — a file-free spec has no preimage or postimage blob, so promotion removes
   the directory without removing any source — and spending on source removal alone would leave that
   workspace holding a descriptor to an unlinked directory, which is the exact state §8.3 spends the
   half to prevent. Source
   removal remains the threshold on the *failure* side (criterion 26), where the `rmdir` never runs.
5. `backend.flush_directory` on `staging/`.

   Steps 2 and 4 both carry the gate because both mutate the namespace, and §5.4's rule is about
   syscalls rather than about loops — a rule that reached only the rename loop would leave the last
   directory removal in a batch as the one unauthorized mutation. The flushes in steps 3 and 5 are not
   gated: `fsync` on a descriptor this process holds changes no name, and gating a durability barrier
   would risk abandoning a half-flushed publication to raise faster, which is the failure §5.4 already
   refuses for `ROLLBACK`.
6. Insert the `blob` rows from the manifest, `ON CONFLICT(digest) DO NOTHING` — the disagreement that
   conflict could hide was already refused in step 1 (§8.4). This is last on purpose: the index is
   written only over content that is durable on the filesystem, so authority §7.3's cross-substrate
   ordering is the statement order of one function rather than a rule two callers must observe.

**The database key and the filesystem leaf are not the same string.** The key stored in `blob.digest`
and carried by `StagedBlob.digest` is the full `sha256:<64 hex>` — it is self-describing, so a second
algorithm later is a new prefix rather than a schema change. The filesystem leaf under `blobs/sha256/`
is the **extracted 64-character hex alone**, because the directory already names the algorithm and
`blobs/sha256/sha256:abc…` would say it twice. The extraction is a single function in `blobs.py`,
called nowhere else, and it may only be applied to a digest that already passed §5.5's grammar — which
is what makes the leaf fixed-width hex by construction. §11.1 tests the mapping in both directions.

Every flush is `backend.flush_file` / `backend.flush_directory`, never a raw `os.fsync`, for §5.1
step 3's reason: the backend is where `F_FULLFSYNC` lives on macOS, and a raw `os.fsync` here would make
the blob durability claim exactly as weak as §5.3 says plain fsync is on that platform.

The call returns only after step 6, so a caller cannot get the sequence wrong. Step 4's emptiness
requirement stays as an invariant rather than a detector: **step 1 is what enforces "complete
manifest"**, by comparing the directory's entry set before anything moves. An uncovered file now raises
there, with every source still staged; reaching step 4 with a non-empty directory would mean the
entry-set comparison and the rename loop disagree, which is a bug in A5a rather than a bad manifest.

### 8.2 A pre-existing blob is verified, not assumed

`EEXIST` on step 2 is **not success by construction**. The digest names the content the engine intends,
not the content on disk: a pre-existing blob may be truncated by a previous crash, corrupted, or
externally substituted.

Before the staged source is unlinked, the existing blob is opened `O_NOFOLLOW` and must be a regular
file whose length equals `byte_len` and whose streamed SHA-256 equals `digest`. On a match the §5.4 gate
runs — the hash just performed is unbounded, and the unlink that follows is a mutation like any other —
the staged source is unlinked, and promotion continues. **On a mismatch the staged source is left in place** and
`MetadataStoreInvalid` is raised — the staged bytes are the good copy, and destroying them to tidy up
after a corrupt blob would discard the only recovery material.

**A batch that removes any source is no longer retryable, and A5a does not pretend otherwise.**
An earlier draft promised that a failed promotion left the workspace usable, so the caller could
resolve the mismatch and call again. That promise cannot be kept. `transfer_noclobber` is
`renameat2(..., RENAME_NOREPLACE)` (`linux.py:54`), and replaying an entry that already moved fails
**`ENOENT`** — the source is gone — not `EEXIST`. Measured against the repository's own backend. So a
second call would not resume the batch; it would fail on the first already-promoted entry, with an
errno that says nothing about what actually happened.

The threshold is the **first source removal**, not the first rename. `EEXIST` on a matching
pre-existing blob unlinks the staged source and continues (above) without any rename succeeding, so a
batch can lose sources through that route alone; a later entry failing then leaves the same
unreplayable state, and "after its first rename" would have missed it entirely. Both routes remove a
source, and removal is what cannot be undone.

The design follows the fact rather than patching around it. Once any source has been removed, a failure
leaves the workspace in a **mixed** state — some blobs published or already present, some files still
staged — and:

- the workspace's staging half is **spent** (§8.3), so a second `promote_staging` raises
  `ProtocolError` rather than producing that `ENOENT`;
- the surviving staged files and the promoted blobs are **preserved evidence**, not garbage. Neither is
  reachable from a committed record, because promotion precedes the COMMIT that would reference it
  (authority §7.3 step 4). This is exactly the shape the authority already names: a crash before that
  COMMIT "leaves only mutation-free orphan scratch on the filesystem (`staging/`, `work/`, promoted
  blobs with no referencing row)," which "recovery reclaims ... under the lock, regardless of count."
  A failed promotion produces the same shape as a crash at the same point, so it needs no second
  disposal mechanism. `list_workspaces` and `reopen_workspace` (§8.3) reclaim the staged half, and
  `list_unindexed_blobs` and `remove_unindexed_blob` (§7.3) reclaim the promoted half — both halves,
  which the earlier draft claimed while supplying only the first.

Preflighting the manifest completely (§8.1 step 1) is what keeps this rare: every failure A5a can
foresee happens before anything moves. What remains here is the case A5a genuinely cannot foresee —
a pre-existing blob whose bytes are wrong — and for that, stopping with the evidence intact is the
correct outcome, not a retry.

### 8.3 Workspaces

`staging/<txid>/` and `work/<txid>/` are created and removed through A4a's guarded traversal from the
retained `metadata_root` descriptor, never by absolute path. Removal is durable: `rmdir`, then
`backend.flush_directory` on the parent.

Opening the two fixed parents is one ownership operation: if opening `work/` fails after `staging/`
opened, the first descriptor is closed before the error escapes. Create, reopen, and remove all use
that helper; none can leak the first parent merely because the second parent was substituted or
otherwise refused.

A workspace is the pair, and its shape and lifetime are exact. **It is a resource, not a value, so it
is not frozen** — the distinction `ProjectBinding` already draws in the same words: "Not frozen, because
it owns descriptors and a spent flag. `VolumeEvidence` is a value; this is a resource"
(`binding.py:81`). A `Workspace` owns two descriptors and has at least two mutable states, so freezing
it would have forced the spent flags out through `object.__setattr__` — writing to a frozen object to
record that it changed.

```python
class Workspace:
    """A live resource: two directory descriptors and their spent flags.

    Created only by Store.create_workspace or Store.reopen_workspace. Both
    descriptors it holds are OWNED by it and closed by close(); every descriptor
    it EXPOSES is borrowed — a consumer must never close one. The metadata_root
    descriptor it was opened from is borrowed in turn and stays A4a's.
    """

    __slots__ = ("_closed", "_staging_fd", "_store", "_txid", "_work_fd")

    def __init__(self, *, _construction_token: object | None = None, **kwargs) -> None: ...

    @property
    def txid(self) -> str: ...            # validated by §5.5 before either mkdirat

    @property
    def staging_fd(self) -> int: ...      # BORROWED; raises if spent or closed
    @property
    def work_fd(self) -> int: ...         # BORROWED; raises if closed

    def close(self) -> None: ...          # idempotent; closes both descriptors
    def __enter__(self) -> Self: ...
    def __exit__(self, *exc: object) -> None: ...   # calls close()

    def _spend_staging(self) -> None: ...           # PRIVATE; §8.1 step 4 only
```

**Spending the staging half is private.** The listing above is the exact public surface, and
§11.6 asserts it as a set rather than as a lower bound. A public spender would let a caller mark
the half spent while `staging/<txid>/` is still on disk holding files — after which
`remove_workspace` sees a spent half, skips that directory, and strands it. Only promotion knows
the moment step 4's `rmdir` succeeded, which is the only moment the half is genuinely gone.
`_closed` is separate from the two descriptor slots because "closed" and "spent" are different
states with different refusals, and a reader who sees only `staging_fd is None` cannot tell which
one produced it.

**The descriptors are exposed, as borrowed anchors.** An earlier draft made them private on the
reasoning that no caller needs them. That was wrong, and it made the workspace unusable for its actual
consumers:

- authority §7.3 step 1 has A6 "streaming each captured file into `staging/<txid>/`", from the same
  descriptor the file was opened through (authority §6, Guarantee 3). A5a cannot perform that write —
  it does not know what is being captured — and A6 cannot perform it without an anchor.
- authority §9.5 requires A7 to construct and publish `CreateDirectory.WORK` out of `work/<txid>/`, and
  to prove it absent at `DONE`.

Neither is expressible through create/remove/promote alone. A5a owns the *namespace*; the layers above
own what goes in it, and an anchor is exactly the minimum that lets both be true. Handing back a path
instead would defeat the point, since the whole reason these are descriptors is that a path is
re-resolved.

Ownership is the borrow contract A4a already uses, in its words: **owned by the workspace, borrowed by
the consumer, never closed by the consumer** (`binding.py:81`). The token guard follows
`ProjectBinding.__init__` (`binding.py:90`), with its refusals of copy, deepcopy, and pickle: a copied
workspace would duplicate descriptor ownership. `_store` stays private — it exists only for the identity
check below, and no consumer has a use for it.

**Both properties gate on liveness, and that is the reason they are properties rather than fields.**
Reading one raises `ProtocolError` if the workspace is closed, if that half is spent, **or if the
binding or lock is dead** — the §5.4 gate, run on the read. A directory descriptor is not an
observation; it is standing authority to mutate an engine-owned namespace, and handing one out after the
lock is released grants that authority outside the lease. Every syscall the consumer makes with it
afterwards is unserialized against the next lease owner, and A5a never sees those calls, so this
retrieval is the only place the check can happen. The retained integer necessarily outlives the gate —
that is what borrowing means — which is exactly why the gate belongs at the moment authority is granted
and why §8.3 hands the descriptor out per use rather than caching it in the caller.

**Reopening is a separate operation from creating.** A fresh process after a crash finds
`staging/<txid>/` and `work/<txid>/` already on disk, and until now had no way to reach them:
`create_workspace` is the only constructor and `remove_workspace` demands a live `Workspace`, so
authority §7.3's "recovery reclaims the scratch ... under the lock" had no mechanism under it.

- **`create_workspace(txid)`** requires both directories to be **absent** and creates them, **gating
  before each `mkdirat`** (§5.4). Two `mkdir`s are two mutations, and the lock can be released between
  them: the first would then create `staging/<txid>/` under a live lease and the second would create
  `work/<txid>/` after it ended — while the next lease owner is already reclaiming the staging-only
  survivor the first left. It never adopts an existing one. Adoption would be the silent kind of convenient: a surviving
  `staging/<txid>/` is evidence about a previous attempt, and quietly reusing it would bypass the
  occupancy question ledger #7 makes A5b answer, turning a decision into a side effect of a
  constructor.
- **`reopen_workspace(txid)`** opens whichever of the two directories exist. It is the recovery path
  and says so: it adopts nothing that was not already there, and creates nothing.

  **Both halves are opened `O_NOFOLLOW | O_DIRECTORY`.** `list_workspaces` already classifies each
  child by `fstatat` with `follow_symlinks=False` and refuses a non-directory under §8.5 — but
  `reopen_workspace` is reachable without ever running that enumeration, since A5b arrives holding a
  txid read out of a record. The check therefore belongs in the open itself, not in the listing that
  usually precedes it. Without the flag the call hands back a descriptor to whatever the symlink points
  at: reproduced with these exact flags, `staging/<txid>` pointing outside `metadata_root` opened
  cleanly and listed the target's contents, and that descriptor is a *borrowed mutation anchor* — A6
  would stage captures through it, into a directory the engine does not own. With the flag both the
  symlink and the regular-file case fail `ENOTDIR` on Linux (measured; `O_DIRECTORY` pre-empts the
  `ELOOP` that `O_NOFOLLOW` alone documents), and A5a raises `MetadataStoreInvalid` for the §8.5
  reason: `create_workspace` is the only permitted producer here and it makes directories.

  Requiring *both* — as an earlier draft did — refuses the most ordinary survivor there is. A
  successful promotion removes `staging/<txid>/` and leaves `work/<txid>/` (§8.1 step 4), so a crash
  anywhere after preparation leaves work-only; and a crash inside `create_workspace` or
  `remove_workspace`, between the two `mkdir`s or the two `rmdir`s, leaves either side alone. Under the
  old rule `list_workspaces` would report txids that `reopen_workspace` then refused — an enumeration
  that names things the reopener denies exist.

  So three states are legal, and each maps to the anchors it actually has:

  | On disk | `staging_fd` | `work_fd` |
  | --- | --- | --- |
  | both | live | live |
  | `staging/<txid>/` only | live | spent |
  | `work/<txid>/` only | spent | live |
  | neither | `ProtocolError` — nothing to reopen |

  A spent half is not an error state, and it reads identically to the spending `promote_staging`
  performs (§8.3): the descriptor property raises, the other half works, and `remove_workspace` removes
  what is there. Modeling the missing side as spent rather than as a distinct kind of workspace is what
  keeps one type covering both the live and the recovered case.
- **`list_workspaces()`** returns the txids having a `staging/` or `work/` directory, read by one
  descriptor-anchored `scandir` of each parent. It is how A5b enumerates survivors "regardless of
  count." A5a reports what exists; **which** survivors are orphans and what becomes of them is A5b's
  judgment, exactly as this section's closing paragraph says of *when*.

  **An entry that is not a directory, or whose name fails §5.5's txid rule, raises
  `MetadataStoreInvalid` and is left in place** (§8.5). Both alternatives are worse and in opposite
  directions: returning it breaks the enumeration's own guarantee that every txid it reports is one
  `reopen_workspace` accepts — the exact defect the three-disposition table was added to fix — while
  skipping it leaves unaccounted debris in an engine-owned directory that nothing will ever look at
  again. Every child of `staging/` and `work/` is created by `create_workspace`, whose txid passed
  §5.5 and whose `mkdirat` makes it a directory, so neither shape can be a survivor of A5a's own work.

Both openers validate the txid (§5.5) first. Neither infers anything from the directories' contents.

- **`remove_workspace(workspace)`** removes whichever directories are present and flushes their
  parents, then closes the workspace. This is the method this section promised and §7.1 did not have;
  without it "removes it when asked" was unimplementable through the public surface. It tolerates
  either side being absent, for the same three-state reason `reopen_workspace` does.

  **`staging/<txid>/` is emptied, not merely `rmdir`ed.** An earlier draft specified `rmdir` alone,
  which reclaims only a staging directory that happens to be empty — and the ordinary pre-promotion
  crash leaves it full. Authority §7.3 step 3 has A6 stream every captured file into `staging/<txid>/`
  and step 4 promote them only "once the complete initial surface has been captured and verified," so
  a crash anywhere across a multi-file capture leaves exactly the state `rmdir` cannot remove. The
  earlier tests hid it by proving only that such a workspace is *enumerated*; enumerating a survivor
  nothing can remove is not reclamation.

  So removal is **preflight then act**, the shape §8.1 step 1 already uses, and for the same reason:

  1. `scandir` `staging/<txid>/` from the held `staging_fd` and `fstatat` each entry with
     `AT_SYMLINK_NOFOLLOW`; every one must be a regular file (§8.5). Confirm `work/<txid>/` is empty in
     the same pass.
  2. Unlink each entry, re-running the §5.4 gate immediately before each `unlinkat` — per entry, for
     the reason §5.4 gives: one gate before the loop leaves every entry after the first unauthorized.
  3. `flush_directory` on `staging/<txid>/`, `rmdir` it, `flush_directory` on `staging/`; then `rmdir`
     `work/<txid>/` and flush `work/`, each preceded by the same gate.

  **Each half is spent the instant its own `rmdir` lands**, not once the whole operation succeeds.
  Removal mutates twice and can fail between them — the parent flush raises, or the gate before the
  second `rmdir` refuses a lease that ended mid-operation — and a workspace that still reports a live
  `staging_fd` for a directory that is gone contradicts the disposition table above. It also breaks
  the retry: the second `remove_workspace` sees a live half, lists the unlinked directory
  successfully, and `rmdir`s a name that no longer exists, raising a raw `FileNotFoundError` from
  inside the store. Spending immediately leaves the partial state as the work-only row of the table —
  legal, reopenable, and finishable by a retried removal — instead of a fourth state the table does
  not describe.

  Splitting it that way is what makes §8.5's promise true. Validating each entry as it is unlinked
  satisfies the letter — every entry is checked — while an invalid entry discovered *last* leaves the
  earlier captures already destroyed and preserves an almost-empty directory as its evidence. That is
  the same defect as promoting entry *k* before validating entry *k+1*, and it deserves the same
  answer.

  Deleting the entries needs no judgment, and that is what makes it A5a's to do. A staged file is
  **mutation-free scratch** in the authority's own words — it "stays mutation-free scratch until"
  promotion, and promotion is what moves it out of `staging/` — so no durable record can reference one,
  whatever state the transaction was in. There is no classification to get wrong.

  **`work/<txid>/` is the opposite case and stays a refusal.** Removal still requires it empty and
  refuses rather than recursing, and the reason is not that A5a lacks a recursive delete — it is that
  authority §9.5 gives a `work/` survivor a *classifier*: a staging directory whose inode matches the
  live directory means the publication landed and its old-name removal was not yet durable, while a
  different, foreign inode proves publication did not land, and the two dispositions differ. That
  judgment is A7's and A5b's, not a storage mechanism's, and it must run before the directory is
  emptied. Once it has, `work/<txid>/` is empty and this method removes it. The asymmetry is exactly
  the difference between scratch that cannot be referenced and scratch whose meaning depends on the
  live filesystem.

  **It gates on liveness, and it is not idempotent.** An earlier draft exempted it alongside `close`,
  on the reasoning that it releases. That conflated two different acts: `close` releases a descriptor
  this process holds, while `remove_workspace` issues `rmdir` and directory flushes — durable
  mutations of an engine-owned namespace. Running one after the lock is released is precisely the race
  authority §7.1's universal lease exists to prevent, since the next lease owner may already be
  reclaiming the same survivor. §5.4's original rule was right: **only rollback and descriptor or
  connection close are exempt.**

  Not idempotent, because the two rules cannot both hold: a removed workspace is closed, and a closed
  workspace is refused. So a second `remove_workspace` raises `ProtocolError` — one stated behavior
  rather than a contradiction between two bullets. `Workspace.close()` remains idempotent, which is
  where a caller's `finally` belongs.
- **Every operation verifies the workspace before touching the filesystem**: it must be exactly
  `Workspace`, not closed, and `workspace._store is self`. Two stores may share one binding (§7.4), so a
  workspace from another store carries descriptors into the wrong lifetime — its issuer may close them
  underneath. A forged, closed, or foreign workspace raises `ProtocolError`, all three being caller
  misuse rather than durable-state corruption.
- **Promotion spends the staging half, on every outcome once any source has been removed** — by a
  rename or by §8.2's `EEXIST` unlink. `promote_staging` closes `_staging_fd` and marks it spent, so
  any later use raises. On success this is because §8.1 step 4 removed the directory, and a descriptor
  that outlives the directory it anchors contradicts the disposition table below, which says a half
  not on disk reads as **spent**. Measured on such a descriptor: `listdir` still succeeds and `fstat`
  reports `st_nlink == 0`, while `openat(O_CREAT)` and `mkdirat` through it fail `ENOENT` — so a
  retained anchor turns every later use into a failure reported far from the removal that caused it.

  On **failure** it is spent for a stronger reason (§8.2): the batch is not replayable, so the
  workspace is evidence rather than a resource. `work_fd` is unaffected either way, and the workspace
  stays usable for `work/<txid>/`.
- **`Workspace.close()` is idempotent and does not gate on liveness**, for §7.8's reason: it releases
  descriptors this process opened, and a workspace whose binding died must still be closable.

A5a creates a workspace when asked and removes it when asked. **When** either happens is A5b's, and the
re-resolution ledger #19 requires before creating scratch is A5b's.

### 8.4 Indexing a blob that is already indexed

`blob.digest` is a primary key, so a plain re-insert raises `IntegrityError`. That would make the
ordinary case an error: content addressing means two transactions capturing identical bytes produce one
digest, and authority §7.3 step 4 says preparation inserts "any **new** `blob` index rows" — the word
already assumes some are not new. Leaving this unstated would have pushed A5b into either catching an
integrity error as control flow or tracking which digests it had inserted before.

**Promotion's indexing is idempotent on the digest, and strict on the length.** For each manifest
entry:

| Existing row | Action |
| --- | --- |
| none | Insert, in §8.1 step 6. |
| same `byte_len` | No-op — the row already says exactly this. |
| different `byte_len` | `MetadataStoreInvalid`, raised in §8.1 step 1. |

The last row is not a caller error, which is why it is not `ProtocolError`. A SHA-256 determines its
content, and content determines its length, so a disagreement means one of the two is not what it
claims. A5a can tell which: the manifest's `byte_len` was verified against the actual bytes during
promotion — against the staged source in §8.1 step 1, and against a pre-existing destination in §8.2 —
so it is the **stored row** that contradicts the bytes on disk. That is
durable state that cannot be safely interpreted, and it is one of the shapes §9 already lists.

**The three rows are decided in step 1 and acted on in step 6**, which is why the table's last row
names an earlier step than its first. Deciding at insertion time would put a `MetadataStoreInvalid` at
the end of a batch that had already renamed and flushed every file — the half-executed shape step 1
exists to prevent. Step 6's `ON CONFLICT ... DO NOTHING` therefore encounters only the first two rows.

Within a single manifest, repeated digests must carry the same `byte_len` too, checked in §8.1 step 1
and raising `ProtocolError` there — that one *is* a caller error, since both descriptions arrived in
the same argument and A5a has no basis to prefer either.

Implementation is `ON CONFLICT(digest) DO NOTHING` after the length comparison, not instead of it:
`DO NOTHING` alone would silently accept the contradicting row and discard the disagreement.

### 8.5 A reclaimer refuses whatever no permitted producer could have written

§7.3 and §8.3 both enumerate an engine-owned directory and delete what they find, and each was written
with its own ad-hoc handling of a surprising entry. They are one rule, and stating it once is what keeps
the two from drifting apart:

> **Every directory under `metadata_root` has a closed set of permitted producers, and each producer
> has a closed output shape. An entry matching none of them is not a survivor to reclaim — it is a
> foreign write into the engine's namespace. Every reclaimer raises `MetadataStoreInvalid` and leaves
> it exactly where it is.**

The producer is not always A5a, and an earlier draft's "A5a creates every entry in its own namespace"
was flatly contradicted by §8.3 two sections earlier: A5a hands `staging_fd` and `work_fd` out as
borrowed anchors precisely so that authority §7.3's preparation pipeline writes preimages and planned
postimages while A7 builds `CreateDirectory.WORK`. What A5a actually owns is narrower and still
sufficient — it knows *who* is allowed to write into each directory, and what each of them is allowed
to produce, because both are fixed by the authority. That is the version of the claim the table below
can support.

| Directory | Producer, and the entries it can write | Enforced by |
| --- | --- | --- |
| `blobs/sha256/` | A5a's promotion alone: a regular file named with 64 hex characters whose bytes hash to that name — §8.1 step 1 verified the source and step 2 renamed it under the extracted digest. | §7.3 |
| `staging/`, `work/` | A5a's `create_workspace` alone: a directory whose name passes §5.5's txid rule — it validated the name and `mkdirat` made it a directory. | §8.3 |
| `staging/<txid>/` | **Authority §7.3's preparation pipeline**, through the borrowed `staging_fd`: regular preimage and planned-postimage files and nothing else. A6 supplies captured preimages; A5a is source-agnostic and verifies the complete manifest before promotion. | §8.3 |
| `work/<txid>/` | **A7**, through the borrowed `work_fd`, and its output is not A5a's to judge: authority §9.5 classifies these by inode against the live filesystem, so removal refuses a non-empty one outright (§8.3). | §8.3 |

The verdict is `MetadataStoreInvalid` rather than `ProtocolError` because the condition is a fact about
durable state, not about the call — §9's rule, applied. And preservation rather than deletion is the
same choice the type itself encodes: authority §11 pairs corruption with "stop and preserve evidence,"
and an unexplained entry in an engine-owned directory is the one thing an operator will need to look at.

The alternative that keeps suggesting itself is to skip such an entry and carry on. It is wrong on both
sides. Skipping leaves debris that no later pass will revisit, since every reclaimer skips it again;
deleting destroys the evidence. Refusing is the only one of the three that neither loses information
nor accumulates it — and it is loud, which is what a foreign write into `metadata_root` deserves.

This does not widen §2.2's non-goal. A5a is not scanning for corruption; each check runs on an entry a
reclaimer was already about to delete, which is the moment it must know what the entry is.

## 9. Error contract

Reuses `atoms.core.errors` unchanged except for one addition.

| Raised | When |
| --- | --- |
| `CapabilityUnavailable` | SQLite < 3.37; `TEMP_STORE=0`; a pinned pragma that would not take. |
| `ProtocolError` | Caller misuse: a wrong exact type, a name or manifest failing §5.5 or §8.1, a record operation outside an explicit transaction, a public read while a write transaction is open (§7.4), an unindexed digest (§7.2), a spent or foreign `_StoreTransaction` or `Workspace`, a use after `close`, a rejected pre-COMMIT record (§7.7), a closed binding or released lock. |
| `MetadataStoreInvalid` | **New.** The durable store cannot be safely interpreted. |
| `OSError` | Propagated. No blanket handler; §11.6 extends the existing guard to the new package. |
| `sqlite3.Error` | Narrowly translated where it means corruption; otherwise propagated (§9.1). |

`MetadataStoreInvalid(AtomsError)` covers both corruption and incompatibility, with the message
distinguishing them: a failed `quick_check`, a schema that does not match its version, a foreign
database, a non-canonical `spec_json`, a blob whose bytes do not match its digest, an indexed digest
whose leaf is missing or not a regular file (§7.2), a `blob` row whose `byte_len` contradicts verified
content (§8.4), **any entry in an engine-owned directory that A5a's own operations could not have
created** (§8.5), a sidecar surviving without its database (§5.1), and *an unknown future
`user_version`*. A newer store is not corrupt — it is unreadable by this build — but both mean
"stop; do not interpret this," which is one caller response and therefore one exception. Folding either
into `ProtocolError` would tell a consumer to fix its call when the correct action is to preserve
evidence.

**The converse holds just as strictly**, and authority §11 states it: `MetadataStoreInvalid` is "raised
by the store layer, never as a substitute for `ProtocolError`." Every entry above is a fact about
durable state that already exists. A caller error caught *before* anything becomes durable is not one,
however similar the check that catches it — which is why §7.7's pre-COMMIT rejection raises
`ProtocolError` even though it runs §7.6's predicate, the same predicate whose failures on a load raise
`MetadataStoreInvalid`. The rule is the state, not the check.

Authority §11 is amended to add it (§3.3).

### 9.1 SQLite's own hierarchy

`sqlite3.Error` is **not** a subclass of `OSError` — `sqlite3.OperationalError.__mro__` is
`OperationalError → DatabaseError → Error → Exception`. SQLite reports corruption, constraint
failures, busy locks, read-only stores, disk-full conditions, and I/O errors through that hierarchy,
so a contract naming only `OSError` says nothing about the errors this layer will actually see.

Translation is **narrow and by result code**, never by exception class:

| Condition | Becomes |
| --- | --- |
| `SQLITE_CORRUPT` or `SQLITE_NOTADB`, from **any** SQLite operation A5a issues | `MetadataStoreInvalid`, with the original as `__cause__` |
| Everything else — `SQLITE_BUSY`, `SQLITE_READONLY`, `SQLITE_FULL`, `SQLITE_IOERR` | Propagated unchanged |

**Any operation, not only the opening ones.** An earlier draft scoped this to creation, reopen, and
loads, which contradicted its own criterion and, worse, its own definition. `SQLITE_CORRUPT` can
surface from a setter's `UPDATE`, from a validation query inside §7.7, or from the COMMIT itself —
SQLite discovers a damaged page when it reads or writes it, not when the file is opened. Propagating
those raw would mean the same durable condition is `MetadataStoreInvalid` when a load finds it and a
bare `OperationalError` when a write does. `MetadataStoreInvalid` is defined by the *state* it reports
(§9), so the site that happens to notice cannot change the type. The COMMIT barrier is included
explicitly, since it is where an unwritable page is most likely to be discovered.

**The catch is `sqlite3.DatabaseError`, and it must be**, because that is the class SQLite raises for
the condition being translated: reading a garbage file raises exactly `sqlite3.DatabaseError` —
`__mro__` of `DatabaseError → Error → Exception`, `sqlite_errorname` `SQLITE_NOTADB`, code 26, measured.
It is not an `OperationalError` and not any narrower subclass, so no narrower `except` clause can reach
it. Forbidding the catch syntactically, as an earlier draft of §11.6 did, would have made §9.1's own
contract unimplementable.

What was actually wrong is not the class caught but **what is done after catching**. So the rule is a
shape, at every site that issues a SQLite operation:

```python
try:
    ...                                     # one narrow operation, not a whole protocol
except sqlite3.DatabaseError as caught:
    code = getattr(caught, "sqlite_errorcode", None)
    if code is not None and (code & 0xFF) in (SQLITE_CORRUPT, SQLITE_NOTADB):
        raise MetadataStoreInvalid(...) from caught
    raise                                   # bare: original traceback, unchanged class
```

The `getattr` is not defensive padding — `sqlite3.ProgrammingError` is a `DatabaseError` that carries
**no `sqlite_errorcode` attribute at all**, measured, because it reports a pysqlite-level misuse rather
than an SQLite result. Reading the attribute directly would raise `AttributeError` from inside the
handler, replacing a clear "Cannot operate on a closed database" with a failure in the error path. It
takes the `raise` branch instead, which is correct: it is not corruption. §7.8 keeps that case from
arising through A5a's own surface by refusing use-after-close with `ProtocolError` first.

Three properties carry the weight. The `except` encloses **one narrow operation**, not a protocol, so
the codes it can plausibly see are bounded. The default is `raise`, bare — not `raise X from caught` —
so an unrecognized code keeps its class *and* its traceback, and a future SQLite code is propagated
rather than guessed at. And the discrimination is on `sqlite_errorcode & 0xFF`, the primary code, so
extended variants such as `SQLITE_CORRUPT_VTAB` still match — the idiom A4a already uses for
`SQLITE_BUSY` at `probe.py:367`.

The failure this guards against is relabelling: `DatabaseError` is also the parent of `IntegrityError`
and `OperationalError`, so a handler that translated everything it caught would report a full disk or a
busy lock as a corrupt store. Those are operational failures a caller may retry or escalate; calling
them corruption would send a consumer to preserve evidence for a condition that clears itself.

**This is verified by behavior and a total syntax inventory, not by grep.** §11.6 requires every
`execute`/`executemany` site except the named best-effort rollback to be inside `translated`, and every
such scope to contain exactly one SQLite statement. It also bans `except sqlite3.Error` (the whole
hierarchy, which would swallow programming errors too) and bare `except:`. Runtime tests in §11.2 inject
`SQLITE_BUSY` and `SQLITE_READONLY` propagate as their original classes with their original codes, and
only a corrupt or non-database file yields `MetadataStoreInvalid`. A syntactic ban on the correct catch
would have passed a lint that the contract fails.

## 10. Limits

**Opening by verified path, not by held descriptor.** `sqlite3.connect` opens by pathname through
SQLite's own VFS, so no descriptor participates and authority §3.2's held-directory reasoning does not carry
over. A5a re-verifies `metadata_root`'s identity at the moment of use and refuses a symlink or
non-regular file at any of the four database leaves (§5.2), which catches the pre-existing cases. It
does not defend against a substitution made *between* that verification and SQLite's open.

That window covers **both** the directory and the leaves. `metadata_root` or an ancestor may be
replaced after `verified_child_path` confirms its identity, and `atoms.db`, `-wal`, `-shm`, or
`-journal` may be replaced after the preflight `fstatat` and before SQLite opens them — the preflight
is a TOCTOU check like any other, and it proves what *was* there, not what will be. A5a does not narrow
this by checking again; a second check has the same window.

This is the authority's stated cooperating-process assumption (authority §7), which this design amends
to name the leaves as well as the ancestors: mutating or replacing `metadata_root`, any of its
ancestors, **or any of the four database entries** while a lease is active voids the recovery
guarantee. The project lock serializes the cooperating processes this engine targets, for which the
case never arises. The remedy is the optional hardened VFS, which the authority marks explicitly as
hardening rather than a correctness prerequisite.

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
member breaks the build rather than the store. `EFFECT_VARIANTS` is total in both directions (§7.1):
every member of the `Effect` union is a key and every `EffectVariant` member is a value, each exactly
once — and each of the five maps to the enum value the `CHECK` list accepts, not to a class name.
The digest-to-leaf mapping in both directions (§8.1),
including that the leaf is bare hex and never carries the `sha256:` prefix. The §5.5 name rules — a
txid, manifest leaf, or digest that is empty, `.`, `..`, multi-component, NUL-bearing, or off the digest
grammar, each refused **with `ProtocolError` specifically**, plus `3`, `None`, `b"tx"`, and a `str`
subclass, which are the inputs that reach a raw `TypeError` without the exact-type gate. The version
policy table of §5.2 as a pure decision function, including the `delete`-mode zero state, which is
resumable and must not be refused for its journal mode.
`HaltDiagnostic` round-trip equality over generated diagnostics, the assertion that no encoded field
derives from `EntryIdentity`, and the four malformed-encoding refusals of §7.6 — missing field, extra
field, unknown enum member, duplicate key.

### 11.2 Tier 2 — real SQLite, temporary directory

Profile read-back, including a connection whose `journal_mode` did not take and one under a simulated
`TEMP_STORE=0`. Each structural constraint refused independently: two `active` rows; an `effect` or
`active` row referencing no transaction; a value outside a `CHECK` list; a `STRICT` violation using
`42.5`, `'abc'`, and `b'x'` — **not** `'42'` or `42.0`, which SQLite losslessly coerces and which would
pass for the wrong reason. The `spec_json` trigger refusing an `UPDATE`. A record operation attempted
outside an explicit transaction, and a nested `transaction()` refused.

**The pre-COMMIT gate ordering** (§7.7): with the lock released *during* validation — after the first
gate has passed and before COMMIT — the transaction rolls back and raises `ProtocolError`. A single
gate at the start of exit passes this test only by accident of timing, so the test holds the lock
through the first gate and releases it inside the predicate.

**The retained transaction object**, which is the case a type alone does not cover (§7). Three tests: a
`txn` kept past a clean exit, a `txn` kept past a rollback, and a stale `txn` from an earlier
transaction used while a *later* one is open. Each must raise `ProtocolError`, and each asserts the
database is unchanged afterwards — the failure being prevented is a write that lands, so the assertion
has to be about the row, not only about the exception. The rollback case is tested explicitly because it
is the one a caller retrying after an exception still holds.

**One-snapshot reads** (§7.4): a reader whose multi-query load is interleaved with a committing writer
on a second connection over the same database returns a coherent record. Run against the interleaving
that tore without a read transaction — `state` read before a commit, journal state after it.

**A public read inside this store's own write transaction raises `ProtocolError`** (§7.4), tested at
both the incoherent midpoint of a setter sequence and at a coherent one, since the two would otherwise
fail differently — the first as a spurious `MetadataStoreInvalid`, the second by silently returning a
record that the following rollback erases.

**Promotion's conflict semantics** (§8.4): promoting a digest already indexed with an identical
`byte_len` is a no-op leaving one row; one already indexed with a *different* `byte_len` raises
`MetadataStoreInvalid` **at step 1, with every source still staged and no row written**; and a single
manifest carrying one digest with two lengths raises `ProtocolError` at the same preflight.

**An already-indexed digest has its leaf verified, not assumed** (§8.1 step 1), one case per failure:
the leaf missing, the leaf a directory or symlink, and the leaf a regular file whose bytes hash to
something else. Each raises `MetadataStoreInvalid` with every source still staged. The missing-leaf case
carries a second assertion that is the point of the whole check — `open_blob` on that same starting
state refuses with `MetadataStoreInvalid` too, so the two operations agree about whether the store is
readable. Against the earlier specification that test fails: promotion's rename succeeded and re-created
the leaf, healing a state `open_blob` calls corruption.

Every cross-row validation of §7.6, failed one at a time: a `spec_json` that does not decode, one that
is not its own canonical encoding, and one that `compile_spec` refuses; a missing `effect` row and an
extra one; a mismatched variant; a referenced digest with no `blob` row and one whose `byte_len`
disagrees with the referenced `FileState`; `rollback_result` present without `ROLLED_BACK` and absent
with it; `halt_diagnostic` present without `HALTED` and absent with it; a diagnostic whose commit
decision or journal vector disagrees with the row; an `active` row naming no record. Read-side by
planting the row and loading it, asserting **`MetadataStoreInvalid`**; write-side (§7.7) by issuing the
setters and reaching the transaction's exit, asserting **`ProtocolError`** — the exception types are
asserted, not just that something raised, since one predicate serving two verdicts is exactly where
they could collapse into one. The write-side cases also assert the transaction rolled back and the
pre-transaction record is intact. Plus the write-side case with no read-side counterpart: a transaction
that touches two txids where only the second is incoherent must roll back **both**.

**The matrix is proved total rather than listed.** Each finding is tagged with the rule that produced
it, drawn from a named inventory the predicate exports, so the read-side and write-side tables can
assert that between them they name every rule — a rule added to the predicate with no case on either
side fails the suite, and the inventory is asserted to hold exactly the rule constants the module
defines, so a tag no matcher can ever meet fails it too. Tagging is what makes the claim checkable: the
fragments a test would otherwise match on — `effect`, `variant`, `byte_len` — each appear in several
messages, so a fragment assertion goes green on the wrong rule and a missing case looks covered. Each
case is also measured to produce exactly **one** finding against an otherwise coherent record, so no
assertion passes on a rule that fired first.

**The two sides are not symmetric, and the asymmetry is stated rather than papered over.** Five rules
are unreachable from the public write API by construction: `insert_record` writes `canonical_json` of
an exact `TransactionSpec`, so no caller can store text that fails to decode or fails to re-encode; the
same method derives every `effect` row and its variant from that spec, and no setter adds, drops, or
retypes one; and `active.txid` is a real foreign key under `foreign_keys = ON`, so `set_active` on an
unknown txid raises from the database long before the barrier. Those five are listed once, each beside
the test that proves the property making it unreachable, and the totality assertion is over the union.
Requiring a write-side case for them would mean reaching past the public surface to manufacture a state
the surface cannot produce, which proves nothing about the barrier.

**A failed read leaves no transaction open** (§7.4): after a load that raises — a planted non-canonical
`spec_json`, then a planted §7.6 violation — assert `in_transaction` is false and that a following
`BEGIN IMMEDIATE` succeeds. Without the rollback the follow-up write fails with `cannot start a
transaction within a transaction`, which is the symptom that would otherwise surface far from its cause.

**Store lifecycle** (§7.8): `close()` twice; `close()` with an open write transaction, asserting the
row did not persist and the retained object is spent; and every public method after `close()` raising
`ProtocolError` rather than `sqlite3.ProgrammingError`. The last is asserted on the exception type
specifically, since pysqlite's own message is plausible enough to pass a laxer check.

Effect order specifically: rows inserted `z, a, m` are proved to come back in `spec_json` order, since
the raw query returns `a, m, z`.

Error translation, by behavior rather than by grep (§9.1): a database truncated to garbage raising
`MetadataStoreInvalid` — the `SQLITE_NOTADB` case, which arrives as `sqlite3.DatabaseError` itself with
primary code 26 — and a `SQLITE_BUSY` or `SQLITE_READONLY` propagating **unchanged**, asserted on the
exception's class *and* its `sqlite_errorcode`, not merely on "something raised." Translation is
covered on the **write** paths too, not only the opening ones: an injected `SQLITE_CORRUPT` from a
setter's `UPDATE`, from a validation query, and from the COMMIT barrier itself, each yielding
`MetadataStoreInvalid` — the assertion that would have caught the original scope error. And a
`sqlite3.ProgrammingError`, which carries no `sqlite_errorcode`, reaching the translation site
propagates rather than raising `AttributeError` from inside the handler.

### 11.3 Tier 3 — creation and reopen

Every row of §5.2's version table, including same-version/wrong-schema, version-zero non-empty refusing,
and an unknown future `user_version` raising `MetadataStoreInvalid`. A symlinked `atoms.db`, `-wal`,
`-shm`, and `-journal`, each refused before SQLite opens anything. A store whose `foreign_key_check`
fails on reopen. A non-canonical `spec_json`.

**Every crash cut in creation resumes**, one test per cut, each by running §5.1 to that point and then
reopening:

- **after step 2** (created but not published), run at **two umasks, because they fail differently**.
  At `0o277` the file is `0o400`: SQLite falls back to a read-only open, so classification succeeds and
  the first write of the resume raises `attempt to write a readonly database`. At `0o777` it is
  `0o000`: no `openat` for read or write succeeds at all, so a repair that begins by opening the file
  cannot even start. Both assert the resumed store ends at the exact intended mode. Both fail against a
  repair ordered after classification; only the second fails against a repair that reaches for `fchmod`
  instead of §5.2 step 2's `O_PATH` route;
- after step 3 (published, zero-length, `journal_mode` still `delete`) — the cut that a WAL-first
  reopen refuses. Assert it resumes and yields a usable store, since this is the state the protocol
  exists to recover;
- after step 4 (WAL set, schema still empty);
- interrupted inside step 5 (rolled back, so empty schema and both versions zero).

All four present as `(0, 0, empty)` and must reach the same completed store; a fifth test asserts the
resumed store is byte-for-byte equivalent in schema **and mode** to one created without interruption,
which is where the step-2 cut is actually caught.

**Initialization is atomic against an interrupted step 5**, asserted directly rather than inferred from
the pragma measurement: the transaction is cut after some of the DDL statements have run, and the
reopened database must hold an empty schema with both versions zero — never a partial schema with a
committed `application_id`. That is the state `executescript` produces, so the test is written to fail
against it (§5.1 step 5).

The reopen-does-not-mutate guarantee: a **non-empty** `delete`-mode database is refused **and is still
in `delete` mode afterwards**, which is the assertion that would have caught the original defect. A
completed store's `journal_mode` is queried and never set. The two cases are tested together, since the
difference between them — refuse versus resume — is exactly what §5.2's ordering decides.

Creation's sidecar preflight (§5.1 step 1): with `atoms.db` absent, each of `-wal`, `-shm`, and
`-journal` present alone refuses with `MetadataStoreInvalid`, and the sidecar **still exists
afterwards** — the measured failure was the first WAL transition silently unlinking a symlinked
`-journal`, so the surviving-entry assertion is the one that catches a regression.

### 11.4 Tier 4 — real ext4 volume

Through A4a's existing binding fixtures. Multi-file promotion over a complete manifest, asserting the
blob leaves are bare 64-character hex with no `sha256:` prefix. `EEXIST` with matching bytes, and
`EEXIST` with mismatching bytes asserting both the raise **and** that the staged source survives. A txid
or manifest leaf containing `..` or `/`, refused before any `mkdirat` — asserted by the absence of the
entry, not only by the exception.

Staged-source verification (§8.1 step 1), each asserting **nothing moved**: a source whose bytes do not
hash to its stated digest, one whose length disagrees with `byte_len`, and one that is a directory,
a symlink, or a FIFO rather than a regular file. Without these a first-time promotion published an
unverified blob, so each test is placed on a digest **not** already present, which is the path §8.2
never covered.

**The symlink sub-case is tested on every path that opens a name, because it is the one that never
reaches the kind check.** A directory and a FIFO open with `O_RDONLY` and are refused by `S_ISREG`; a
symlink fails at the open itself with `ELOOP` — measured, and as a bare `OSError` rather than a named
subclass — so an untranslated open leaks a raw exception out of a path whose contract is
`MetadataStoreInvalid`. That is three places, not one: the staged source, the already-indexed leaf
verified in the preflight, and §8.2's pre-existing destination reached through `EEXIST`
(`renameat2(RENAME_NOREPLACE)` onto a symlink returns `EEXIST`, measured, so that branch is
reachable). One helper performs the open and the translation for all of them, and `open_blob` uses it
too, so the refusal cannot be present on some paths and missing on others.

Manifest preflight (§8.1 step 1), each case asserting **nothing moved** — the staging directory still
holds every file and `blobs/sha256/` is unchanged: a manifest that is a `list` rather than a `tuple`; an
element that is not exactly `StagedBlob`; a bad name; a bad digest; a `byte_len` that is negative, a
`bool`, or a `float`; two entries naming the same source file; one digest given two lengths; **a
manifest omitting a file present in the staging directory**; and one naming a file that is not there.
Each is placed **last** in an otherwise valid multi-entry manifest, since an invalid first entry would
pass even without a preflight. The omission case is the one that previously failed at step 4 with the
batch already executed, so its assertion is specifically that every source file is still staged.

Post-mutation failure (§8.2), run twice so both removal routes are covered: a multi-entry manifest whose
*last* entry hits a mismatching pre-existing blob, once where an earlier entry was published by a
**rename**, and once where the only earlier removal was a matching **`EEXIST` unlink** with no rename
succeeding at all. The second is the case "after its first rename" missed. Each asserts the raise, that
earlier entries stayed resolved and the remaining sources stayed staged, that the staging half is spent
so a second `promote_staging` raises `ProtocolError`, and that both `list_workspaces` **and**
`list_unindexed_blobs` report the survivors — the reclamation path, since retry is impossible. A
companion test pins the reason: replaying a promoted entry through `transfer_noclobber` raises `ENOENT`,
not `EEXIST`.

Survivor reclamation (§7.3): a promoted-but-unindexed blob is reported by `list_unindexed_blobs` and
removed by `remove_unindexed_blob`, with `blobs/sha256/` flushed; a blob that **is** indexed is refused
by `remove_unindexed_blob` with `ProtocolError` and still exists afterwards — the fail-closed direction,
tested with a digest that was indexed between enumeration and removal. Removing a leaf that is already
gone raises `ProtocolError`. Run across a fresh process over a crash-cut fixture, since that is the
situation the operations exist for.

Removal's exclusion is armed against a **second `Store` over the same binding**, in the interleaving
that defeated the earlier draft: one store promotes and indexes inside a write transaction while the
other reclaims, with the reclaimer's transaction opened after the promotion's renames and before its
COMMIT. The assertion is the pair — no committed record ever names a missing leaf, and no leaf is
unlinked while a row for it commits. The earlier arrangement, with promotion outside a transaction and
only the reclaimer escalated to `BEGIN IMMEDIATE`, is asserted to be what this test rejects: it produces
`writer_committed` with the row present and the leaf gone. A companion test pins the narrower half —
a deferred reclaimer transaction fails even the stale-argument case.

Promotion's transaction binding (§8.1, §7.1): promoting and then rolling the transaction back leaves
the blobs on disk with no rows, reported by `list_unindexed_blobs` — the rollback disposition, identical
to a crash at the same point.

**A transaction that promotes without inserting a record raises `ProtocolError` at exit** (§7.7), with
the assertion written on the durable state rather than the exception: after the rollback the store holds
`(transaction_record=0, blob=0)` and the leaves are reported by `list_unindexed_blobs`. The counterpart
is the state this forbids — a committed `(transaction_record=0, blob=1)`, which is reproducible against
the earlier specification and which **neither** reclaimer can reach, asserted directly by showing the
digest absent from `list_unindexed_blobs` while no record references it. A record referencing only
*some* of the promoted digests fails the same way, since the check is over every promoted digest. `promote_staging` outside a transaction is unreachable through the
surface, asserted against `__all__` and the `Store` attribute set, and the surface assertion is what
arms the deeper claim: **`_StoreTransaction` has no `insert_blobs`**, so no test can construct the
promote-`(digest, 10)`-then-index-`(digest, 11)` sequence that defeated the earlier barrier, and none
can write a `blob` row for a digest with no leaf. The attribute-set assertion is therefore not a
tidiness check — it is the only place that property is enforceable.

Workspaces (§8.3): creation, then `remove_workspace` removing both directories durably; removal after
promotion, where `staging/<txid>/` is already gone; removal refused when `work/<txid>/` is non-empty; a
closed workspace and a workspace belonging to a *different* `Store` over the same binding, each raising
`ProtocolError` from every operation that takes one; and a forged `Workspace` refused by the
construction token, plus refusals of copy, deepcopy, and pickle.

**Removal of a non-empty `staging/<txid>/`**, which is the ordinary pre-promotion crash and which the
earlier `rmdir`-only draft could not perform: a fresh process reopens a workspace holding several
capture files, `remove_workspace` succeeds, and both `staging/<txid>/` and its entries are gone with
`staging/` flushed. The negative that pins it is that the earlier specification fails this test with
`ENOTEMPTY`. Enumeration alone is asserted to be insufficient — `list_workspaces` reporting the
survivor is checked *and* the removal is checked, since the earlier tests proved only the first.

**Removal's preflight is armed by ordering**: the invalid entry is placed **last** in a staging
directory of several valid captures, and the assertion is that *every* file is still present after the
refusal, not merely that a refusal occurred. Validating during the unlink loop passes an unordered
version of this test and fails this one, which is the whole reason the preflight is a separate step.
The `work/`-non-empty refusal is run the same way, with a full `staging/<txid>/`, asserting that
nothing in `staging/` was unlinked before `work/` was examined.

§8.5's refusals, one per row, each asserting that the entry is still present afterwards: a
`blobs/sha256/` leaf that is a symlink, a directory, and a regular file with a valid hex name whose
bytes hash to something else; a child of `staging/` and of `work/` that is a regular file, and one whose
name fails §5.5; and a non-regular entry inside `staging/<txid>/`, which makes `remove_workspace` refuse
and leave the whole staging directory intact. The valid-name-wrong-content blob is the case a name check
alone accepts, so it is the one that distinguishes §8.5 from its predecessor.

The access surface, which is what makes the workspace usable by A6 and A7: a file created and written
through `staging_fd` lands in `staging/<txid>/` and promotes normally; a directory created through
`work_fd` is visible under `work/<txid>/`; and reading either property after `close()`, or `staging_fd`
after promotion, raises `ProtocolError` rather than returning a stale integer. **Both properties are
also read after the binding is closed and after the lock is released, each raising `ProtocolError`** —
the §5.4 gate, which the earlier draft omitted here even though retrieving an anchor grants mutation
authority. A consumer closing a borrowed descriptor is out of contract and not tested — the contract is
stated, as A4a states its own.

Creation versus reopening: `create_workspace` refuses when either directory already exists, asserting it
adopted nothing. `reopen_workspace` is tested on **all four** dispositions of §8.3's table — both
present, staging-only, work-only, and neither — asserting live anchors where the directory exists,
`ProtocolError` from the property where it does not, and `ProtocolError` from the call itself when
neither exists. The work-only case is the one a successful promotion produces and the earlier draft
refused, so it is exercised through a real promotion rather than by deleting a directory. Then
`remove_workspace` succeeds on each of the three legal dispositions. `list_workspaces` reports a
survivor created by a prior `Store` and, after `remove_workspace`, no longer reports it; every survivor
it reports is proved to be one `reopen_workspace` accepts. The survivor cases run across a fresh
process, since that is the situation the operations exist for.

Removal's gate: `remove_workspace` after the lock is released raises `ProtocolError` and leaves both
directories in place — the assertion that would have caught the earlier exemption — while
`Workspace.close()` on the same dead binding still succeeds, and a second `remove_workspace` raises
`ProtocolError` rather than being silently idempotent.

Promotion's effect on the workspace: a second `promote_staging` on a promoted workspace raises
`ProtocolError`, and the staging descriptor is closed rather than left open on a removed directory.

Blob reading (§7.2), one test per row of its table: an unindexed digest raises `ProtocolError` **on an
otherwise healthy store**, since that is the case the earlier draft mislabelled; an indexed digest whose
leaf was removed, and one whose leaf is a symlink or a directory, each raise `MetadataStoreInvalid`; a
promoted-but-unindexed orphan blob is **not** openable, which is what distinguishes membership from
existence. A digest failing §5.5 raises `ProtocolError` before any lookup.

Verification specifically: a blob truncated after indexing, and one whose bytes were substituted while
keeping the length, both raise `MetadataStoreInvalid` — the truncation case with a **matching prefix**,
so a prefix comparison would have accepted it. On success the returned descriptor reads from offset
zero, and `Store.close()` does not close it, since ownership transfers (§7.1). `open_blob` inside this
store's own write transaction raises `ProtocolError`, run specifically as `promote_staging` followed by
`open_blob` on a just-promoted digest — the sequence that would otherwise expose a row and a blob that
a rollback erases.

Liveness: store operations after the binding is closed and after the lock is released, each raising
`ProtocolError` — including `read_record` and `read_active`, since reads gate too (§5.4); and the case
the per-operation gate misses — the lock released **between the last write and the transaction's
exit**, asserting that `ProtocolError` escapes, that the transaction was rolled back rather than
committed, and that `close()` still succeeds afterwards. The same shape on the read path: the lock
released **during a load**, after the queries and before the record is returned, asserting that no
`StoredRecord` reaches the caller and that the read transaction was rolled back (§7.4). The exemptions
are asserted positively, and they are exactly §5.4's three: `ROLLBACK`, `Store.close()`, and
`Workspace.close()` each succeed on a dead binding. `remove_workspace` is **not** among them — its
refusal is asserted above, under removal's gate.

**The late gate before each filesystem barrier** (§5.4), one test per verifier, with the lock released
*inside* the verification rather than before the call: during promotion's manifest hashing, during
orphan removal's leaf hashing, and during workspace removal's entry scan. Each asserts `ProtocolError`
and — the half that actually matters — that **nothing moved**: every source still staged, the orphan
leaf still present, every capture still present. A single gate at entry passes the first assertion in
all three and fails the second, which is why the second is written down separately.

**The gate inside each mutating loop**, which is a different test and catches a different specification:
a multi-entry promotion and a multi-capture removal, each with the lock released **after the first
rename or unlink and before the second**. The assertion is on the entries that follow — every remaining
source still staged, every remaining capture still present — and on the disposition, which is §8.2's
post-mutation failure rather than a new one: the staging half spent, the mixed state reported by
`list_workspaces` and `list_unindexed_blobs`. Gating once before the loop passes every other liveness
test in this tier and fails this one.

The mutations that are neither a rename nor an unlink in a loop get their own cases, since a rule
enforced only where it was first noticed is not enforced. The inventory is exhaustive against §5.4, one
test per site, each releasing the lock immediately before the syscall in question and asserting that
**that** syscall did not happen:

- during §8.2's `EEXIST` destination hash — the staged source is still present;
- after promotion's step 3 flushes and before step 4's `rmdir` — `staging/<txid>/` survives. This is the
  last mutation of a successful batch, and an implementation that gates only inside loops leaves
  exactly it unauthorized;
- between `create_workspace`'s two `mkdirat`s — `work/<txid>/` was not created, and the staging-only
  survivor left behind is one `reopen_workspace` accepts and `remove_workspace` clears, so the refusal
  costs the next lease owner nothing;
- before `remove_workspace`'s `rmdir` of `staging/<txid>/`, and before its `rmdir` of `work/<txid>/` —
  each directory survives. Both gates were specified and neither was exercised; the tier named only
  promotion's `rmdir`, which is how a specified-but-unarmed gate stays that way;
- **inside `open_store` itself**, which the earlier inventory began after: before creation's
  `openat(O_CREAT)`, asserting `atoms.db` was not created; before §5.1 step 3's `fchmod`, asserting the
  mode is unchanged; before **§5.2 step 2's repair `chmod`**, asserting the reduced mode survives
  untouched and the store is still resumable by the next lease; before the WAL transition, asserting the file is still in `delete` mode — the
  assertion that matters, since that pragma converts the file permanently; and **partway through the
  DDL loop, before the initialization COMMIT**, asserting the reopened database is `(0, 0, empty)`
  rather than partially built. The last one is the case a gate placed only at `open_store`'s entry
  passes.

Every one of these is a `ProtocolError` naming the released lock, and none leaves the store in a shape
§5.2 cannot classify — creation's cuts are the resumable shape by construction (§5.1), and the workspace
and promotion cuts are §8.2's preserved evidence.

### 11.5 Tier 5 — fresh process

A record written and committed in one process is read back identically in a new one. This is the
durability claim A5a actually makes.

**Ledger #22's proof lives here**: create-from-absent and replace records prove that every referenced
digest from both initial and final surfaces resolves through `open_blob` in a fresh process, with the
bytes verifying against the digest. The claim is structural —
§7.1 leaves one writer of `blob` rows and §8.1 orders its flushes before its inserts — but structure is
what the test protects, not a substitute for it. Cross-process WAL exclusion is **not** re-tested: A4a's
`certify_sqlite_wal` already proves it at bind time, and re-asserting it here would duplicate a
certified capability.

### 11.6 Tier 6 — architecture

`atoms.fs` and `atoms.core` never import `atoms.store`. The public surface is exactly `__all__` (§7.1)
and exports no `sqlite3.Connection`.

**`promote_staging` is the only writer of a `blob` row.** An earlier draft asserted this by counting
occurrences of `INSERT INTO blob`, which proves almost nothing: `REPLACE INTO`, `INSERT OR REPLACE`,
`UPDATE blob`, `DELETE FROM blob`, a trigger writing the table, and the same insert spelled with
different whitespace or case all pass it. Since ledger #22 now rests on this structurally, the check has
to be about *statements*, not substrings.

It is built on a property A5a can enforce cheaply: **every SQL string the package issues is a
module-level constant**. §11.6 asserts that as a *positive* resolution rather than as a list of banned
spellings — the first argument of every `execute` and `executemany` call in `atoms.store` must be a
`Name` or `Attribute` that resolves to a module-level `str` assignment. Enumerating forbidden
constructions instead (f-string, `%`, `+`, `.format`, `.join`) is the version that fails quietly: a local
assigned from a helper, a dict lookup, or a `str` subclass all pass it while still producing text no
static check can classify. Parameter placeholders are unaffected — they are values, not statement text.

**`executescript` does not appear in the package at all.** An earlier draft carved out the schema DDL
for it, which was the one exception that made this guard awkward — and §5.1 step 5 now records that the
exception was a defect in its own right: the call commits the open transaction before running, which
destroys the atomicity the initialization protocol depends on. Forbidding it outright is both simpler
and required, and the schema becomes a tuple of single-statement constants like everything else in the
inventory.

With that, the inventory is finite and enumerable. Each constant is parsed for its statement kind and
target table, and the rule is stated over the parse, not the text: **exactly one statement in the
package writes `blob`, it is an `INSERT`, and it is the one promotion's step 6 issues.** Any other
`INSERT`, `REPLACE`, `UPDATE`, or `DELETE` targeting `blob` fails, whatever its spelling.

**Triggers are checked by their bodies, not their subject tables.** An earlier draft asserted that no
trigger names `blob` as its target, which a trigger declared `ON transaction_record` whose body runs
`INSERT INTO blob` satisfies while writing the table on every record write — the indirect path the check
existed to close. So every `CREATE TRIGGER` in the schema has each statement in its `BEGIN ... END` body
parsed for its own target, and `blob` may not appear as the target of any of them. The schema currently
has one trigger, whose body is a single `RAISE(ABORT, ...)` (§6.1); the check is written for the schema
that comes later, since a trigger is exactly the kind of indirection added without revisiting an
architecture test.

This is the static half of ledger #22, and it is the half that carries the weight: §8.1's ordering —
verify, publish, flush, then index — is worth exactly nothing if a second site can write a row without
it. No `ATTACH` or `VACUUM` statement appears in the package. No
blanket `OSError` handler, extending the existing guard.

Every SQLite execution site is also inventoried. Apart from `_rollback_quietly`, which deliberately
preserves the original failure, each `execute` or `executemany` must be inside `translated`, and each
translation scope must contain exactly one syntactic statement site. Runtime injections cover record
materialization, coherence and pre-COMMIT barrier reads, blob index reads and inserts, and COMMIT, so
the inventory proves total routing while behavior proves the result-code discrimination.

**No `os.fsync` call appears in `atoms.store`** — every durability barrier goes through
`Backend.flush_file` or `Backend.flush_directory` (§5.1 step 3). This one is worth checking statically
rather than behaviorally: on Linux the two are the same call, so a test running here would pass with
the raw version in place and the defect would surface only on macOS, where `F_FULLFSYNC` is the
difference between a flush and a durable one.

**The SQLite guard bans `except sqlite3.Error` and bare `except:`, and permits
`except sqlite3.DatabaseError`.** §9.1 requires that catch — `SQLITE_NOTADB` raises `DatabaseError`
itself, so no narrower clause can reach it — and a guard forbidding it would make the error contract
unimplementable, in the same way a guard forbidding `BEFORE UPDATE OF spec_json` everywhere would reject
the trigger below. What a static check *can* justify is banning the strictly wider catches: `sqlite3.Error`
covers `InterfaceError` too, which signals a misuse of the driver rather than a state of the database.
Whether the permitted catch behaves correctly is decided by §11.2's propagation tests, which are the
real assertion here.

An additional AST check keeps that catch honest without re-banning it: every
`except sqlite3.DatabaseError` handler in the package must contain a bare `raise` statement. That is
mechanically checkable and is precisely the property §9.1 depends on — an unrecognized code leaves with
its own class and traceback. A handler that translated unconditionally would fail it.

**One function is exempt, by name.** §7.7's rollback-on-every-failing-exit has to swallow: it runs
while an exception is already in flight, and re-raising there would replace the cause with the
consequence, which §7.7 forbids in those words. The exemption is a named string in the guard, not a
shape, and a second test asserts the package holds **exactly one** such handler — so the reasoning
cannot be inherited by a later swallow that merely looks similar. `except sqlite3.Error` stays banned
outright, exemption or not: it also covers `InterfaceError`, and swallowing that hides an A5a bug
inside a release path.

The `spec_json` guard targets **issued DML, not the DDL**. The required schema necessarily contains
`BEFORE UPDATE OF spec_json` (§6.1), so a guard forbidding that string everywhere would reject the
trigger that does the enforcing. It therefore scans for an `UPDATE transaction_record SET ... spec_json`
statement A5a issues, and excludes the `CREATE TRIGGER` text in `schema.py` by name. It remains lint
beside the trigger, not a substitute for it.

## 12. Deferred and delivery obligations

**Ledger entries discharged:** none (§3.1).

**Ledger entries created:** #22, the cross-substrate promotion/COMMIT binding, now owned by **A5a**
itself after the surface change that made it structural, and #23, invoking survivor reclamation at
lease entry, owned by A5b (§3.2).

**Ledger entries untouched:** every other open entry.

**Authority amendments:** authority §7 temp-file surface, §7 cooperating-process assumption, §7.2
table shapes, and §11 refusal vocabulary (§3.3).

**Not a ledger entry:** the `AGENTS.md` status line, and §10's threat-model limitation.

## 13. Acceptance criteria

1. `atoms.db` is created only through the `O_CREAT | O_EXCL | O_NOFOLLOW | O_RDWR` protocol of §5.1,
   and a second concurrent creation raises rather than silently reinitializing.
2. Creation preflights all four database entries and refuses when any sidecar exists without
   `atoms.db`, leaving that sidecar in place. `O_EXCL` covers only `atoms.db`.
3. Creation `fchmod`s the file to its exact mode, then flushes the file and `metadata_root`
   **before** SQLite opens it, and writes the DDL, `user_version`, and `application_id` in one
   explicit transaction, statement by statement — `executescript` commits the open transaction before
   it runs, so it appears nowhere in the package. A5a created the directory entry, so no SQLite COMMIT
   publishes it. A step 5 cut leaves an empty schema with both versions zero, never a partial schema
   with a committed `application_id`.
4. Every fsync in the package goes through `Backend.flush_file` or `Backend.flush_directory`; no raw
   `os.fsync` call appears in `atoms.store`.
5. Reopen refuses a symlink at `atoms.db`, `atoms.db-wal`, `atoms.db-shm`, or `atoms.db-journal`
   before SQLite opens the path.
6. Reopen determines identity, version, and schema from reads **before** applying any journal-mode
   rule; the only *application-issued* metadata writes on the reopen path are step 2's zero-length
   repair, which touches a mode and no database content, and the resumable `(0, 0, empty)` completion.
   Every row of §5.2's version table produces its stated verdict.
7. Each of the **four** creation crash cuts — from after step 2 onward, including the one still in
   `delete` mode — is recognized as resumable, and the resumed store matches an uninterrupted creation
   in schema, version, **and mode**. The publication and mode repair of §5.1 step 3 happens in §5.2
   step 2, **before SQLite opens the file**, because a umask-reduced mode makes every later write fail
   and, at `0o000`, makes the file unopenable at all — so the repair goes through an `O_PATH` descriptor
   and `/proc/self/fd`, never `fchmod`. The remaining resume is steps 4–5. Skipping the repair, or ordering it after classification, leaves
   the entry unpublished and the mode uncorrected forever, since the resumable shape is consumed once.
8. On a completed store `journal_mode` is queried and never set; a non-empty `delete`-mode database is
   refused and is still in `delete` mode afterwards.
9. The schema catalog is compared as `(type, name, tbl_name, sql)` over every `sqlite_schema` row
   including implicit autoindexes, with `sql` normalized for the stripped terminal semicolon and set
   equality refusing extra objects as well as missing ones.
10. `quick_check` and `foreign_key_check` run on every reopen and refuse on any finding.
11. Every pinned pragma is read back and a mismatch refuses; `TEMP_STORE=0` refuses with
    `CapabilityUnavailable`; SQLite < 3.37 refuses with `CapabilityUnavailable`.
12. `ATTACH` and `DETACH` are denied by the authorizer, and no `ATTACH` or `VACUUM` statement appears
    in the package.
13. **Every** operation gates on `binding.backend`, reads included, and the gate runs again
    immediately before each barrier — the initialization COMMIT and every record COMMIT, and **every**
    mutating syscall of store creation and reopen, promotion, orphan removal, and workspace creation and
    removal, per rename, per unlink, per `mkdir`, and per `rmdir` rather than once per batch or once per
    loop, since each verifies unbounded content first and a batch is many mutations. The sites outside a
    loop are covered by name and each independently armed: §8.2's `EEXIST` unlink, promotion's step 4
    `rmdir`, `create_workspace`'s two `mkdirat`s, `remove_workspace`'s two `rmdir`s, creation's
    `openat(O_CREAT)`, `fchmod`, and WAL transition, and reopen's repair `chmod` (§5.4). Flushes are the
    deliberate exception. A lock
    released between the last write and the transaction's exit rolls
    back and raises `ProtocolError`. The only exemptions are `ROLLBACK`, `Store.close`, and
    `Workspace.close`, each asserted to succeed after the binding or lock dies. `remove_workspace` is
    **not** exempt: it mutates the engine-owned namespace and would race the next lease owner.
14. `Store` exposes no `sqlite3.Connection`; record writes exist only on the private
    `_StoreTransaction` that `transaction()` yields, **and that object refuses use unless it is the
    store's current active transaction** — a retained object raises `ProtocolError` after both a commit
    and a rollback, and a stale object cannot write into a later transaction. A nested `transaction()`
    raises `ProtocolError`. The exported surface is exactly `__all__`.
15. Every read takes one SQLite snapshot, proved against a writer committing between the queries of a
    single load; every failing read rolls back before raising, leaving no open transaction, and gates
    liveness again before returning a record. A public read while this store owns a write transaction
    raises `ProtocolError`, so no `StoredRecord` ever carries uncommitted state.
16. `Store.close()` is idempotent, rolls back and spends any open transaction, closes every
    `Workspace` it issued that is still open, and every operation after it raises `ProtocolError`
    rather than any `sqlite3` exception.
17. Every `CHECK` enumeration is generated from its enum, asserted equal to the enum members.
18. All tables are `STRICT`, tested with values SQLite cannot losslessly coerce.
19. `spec_json` is written from `canonical_json`, and a read that does not re-encode to the stored text
    exactly raises `MetadataStoreInvalid`.
20. An `UPDATE` of `spec_json` is refused by the database trigger, not only by lint, and the source
    guard targets issued DML while excluding the `CREATE TRIGGER` text that implements it.
21. `HaltDiagnostic` round-trips exactly, and its encoded form contains no field derived from
    `EntryIdentity`.
22. The journal vector is reconstructed by walking `spec_json`, never by relying on row order, with
    exact two-way coverage proved; the schema carries no ordinal column, and `insert_record` takes no
    `variants` argument — every variant is derived from the spec through `EFFECT_VARIANTS`, a literal
    total mapping asserted exhaustive in both directions and never `variant_name`, whose class-name
    spelling the `CHECK` list rejects.
23. §7.6's predicate covers `compile_spec` acceptance, effect coverage, variant consistency, blob
    `byte_len` agreement, `rollback_result` exactly for `ROLLED_BACK`, `halt_diagnostic` exactly for
    `HALTED`, diagnostic agreement with the durable row, active-record existence, and the four
    malformed-encoding cases — each failing independently, each finding tagged with the rule that
    produced it, and the read-side and write-side tables asserted to name **every** rule the predicate
    can emit between them, with the rules the public write API cannot reach listed beside the test
    that proves each unreachable. "Independently" is asserted rather than described: each case runs
    the predicate and requires **exactly one** finding, carrying that case's tag, before the verdict
    is triggered. A case that merely searched the raised message for its tag would pass on a record
    that had also broken something else, which is the opposite of what this criterion asks — and on
    the write side the reading has to happen inside the open transaction, because the barrier's own
    refusal erases the state being counted. `byte_len` agreement is checked against
    **every** reference, so a spec declaring one digest at two lengths refuses rather than being
    silently reduced to whichever reference a map happened to keep — `compile_spec` accepts that spec,
    measured, so the store is where it is caught. A finding that depends on an earlier one holding —
    the diagnostic's journal vector, which is indexed by the effect rows — is skipped once that
    earlier finding fires, so a malformed record reports its findings instead of raising `KeyError`.
24. That same predicate runs over every touched txid **before every COMMIT**, in the order
    gate → validate → gate → COMMIT with the second gate adjacent to the barrier. A transaction that
    would persist an incoherent record rolls back and leaves the prior record intact, raising
    **`ProtocolError`** — never `MetadataStoreInvalid`, which authority §11 reserves for durable state
    that cannot be interpreted. A5a cannot commit a record its own loader would reject. A mutating
    method that raises **poisons** the transaction, so a caller that catches the failure inside the
    `with` block still cannot commit the partial write SQLite left behind — statement rollback is not
    transaction rollback, measured. Every exit closes the SQLite transaction, **including a `COMMIT`
    that fails**, so a store is never left with an open transaction its own bookkeeping says is gone.
25. `promote_staging` validates the entire manifest **before anything moves** — element types, names,
    digests, exact non-negative `int` lengths, unique source names, one length per digest, exact
    agreement with the staging directory's entry set, and **each staged source's kind, length, and
    streamed SHA-256** — so a rejected manifest moves nothing and a first-time publication is never
    unverified. An omission is caught there, not at the emptiness requirement, which stays as an
    invariant that should never fire first. Existing `blob` rows are compared in the same preflight,
    so a stored `byte_len` contradicting the verified content is refused before anything moves.
26. A promotion that fails after **any source removal** — a rename or §8.2's matching-`EEXIST` unlink —
    is not presented as retryable: the staging half is spent, a second call raises `ProtocolError`, and
    both halves of the mixed state are reachable as preserved evidence, through `list_workspaces` and
    `reopen_workspace` for the staged files and `list_unindexed_blobs` for the promoted ones. Replaying
    a promoted entry raises `ENOENT` under `RENAME_NOREPLACE`, which is why.
27. Promotion returns only after `blobs/sha256/` and `staging/<txid>/` are flushed, the staging
    directory is removed, `staging/` is flushed, and the `blob` rows are written from the same verified
    manifest. It is a `_StoreTransaction` method, so publishing a blob and indexing it occupy one
    write-lock interval, and the staging half is spent once step 4's `rmdir` succeeds, including for a
    manifest with no entries. There is **no separate `insert_blobs`** — the digest and `byte_len` in the
    index are the ones step 1 verified against the staged file, so no row can contradict its bytes or
    name a leaf that does not exist. A promoted-but-unindexed blob is therefore reachable only through
    a crash or a rollback, never as a steady state another connection can observe. The transaction also
    refuses to COMMIT unless the record for the workspace's txid references every digest it promoted, so
    the converse orphan — an indexed blob no record names, which **neither** reclaimer can reach — is
    unreachable as well.
28. A pre-existing blob is verified by kind, length, and streamed SHA-256 before the staged source is
    unlinked; on mismatch the staged source survives and `MetadataStoreInvalid` is raised. A digest that
    is **already indexed** has its leaf verified the same way in the preflight, so promotion refuses a
    missing, non-regular, or mismatching leaf instead of re-creating it — the state `open_blob` calls
    `MetadataStoreInvalid` is not one promotion silently heals.
29. The `blob.digest` key is `sha256:<hex>` while the `blobs/sha256/` leaf is the bare 64-character
    hex, with the mapping tested in both directions.
30. Workspace directories are created and removed through guarded traversal from the retained
    descriptor, never by absolute path; removal is durable, reachable through `Store.remove_workspace`,
    gated on liveness, refuses a second call rather than being silently idempotent, and **spends each
    half at its own `rmdir`** rather than at the end, so a failure between the two mutations leaves one
    of the three legal dispositions instead of a live anchor on a directory that is gone. A closed,
    forged, or foreign-store `Workspace` is refused by every operation that takes one. Removal
    **empties `staging/<txid>/`** rather than only `rmdir`ing it, so the ordinary pre-promotion crash
    survivor — a staging directory full of captures — is actually reclaimable and not merely
    enumerable; it validates every entry and `work/`'s emptiness before the first unlink, so a refusal
    on the last entry still leaves the first intact. `work/<txid>/` stays a refusal when non-empty, because authority §9.5 classifies its
    contents against the live filesystem and that judgment is not a storage mechanism's.
31. `Workspace` is a token-guarded resource, not a frozen value, and **exposes both directory
    descriptors as borrowed anchors**, so A6 can stage captures and A7 can build `CreateDirectory.WORK`.
    Each anchor read gates on the binding as well as on spend and close, since handing one out grants
    mutation authority; a spent or closed half raises rather than returning a stale integer.
32. `create_workspace` refuses an existing workspace and adopts nothing. `reopen_workspace` opens
    whichever directories exist, accepting all three legal dispositions — both, staging-only, and
    work-only, the last being what a successful promotion leaves — with the missing half spent, and
    refuses only when neither exists. Both halves are opened `O_NOFOLLOW | O_DIRECTORY`, so a symlink
    or a regular file at `staging/<txid>` raises `MetadataStoreInvalid` rather than yielding a mutation
    anchor onto whatever it names; this is tested through `reopen_workspace` directly, because the
    enumeration that would otherwise have caught it is not on that path. Every txid `list_workspaces`
    reports is one `reopen_workspace` accepts.
33. `open_blob` is the only way to read a blob's bytes, so no later layer re-derives the digest-to-leaf
    mapping. It requires an indexed digest — an unindexed one raises `ProtocolError` and an orphan blob
    is not openable — verifies kind, length, and streamed SHA-256 before returning, rewinds to offset
    zero, and transfers descriptor ownership. An indexed digest whose leaf is missing or not a regular
    file raises `MetadataStoreInvalid`. Like every public read, it refuses inside this store's own write
    transaction.
34. `list_unindexed_blobs` and `remove_unindexed_blob` make authority §7.3's pre-COMMIT orphan blobs
    reclaimable, so §8.2's preserved-evidence claim covers both halves of a failed batch. Removal
    re-checks membership and unlinks under one `BEGIN IMMEDIATE` and refuses an indexed digest, so it
    can never delete content a record names. `IMMEDIATE` alone does not establish that — it blocks a
    promoting writer instead of excluding it, and the blocked writer commits its row after the leaf is
    gone — so criterion 27's transaction binding is what completes it, and the pair is proved against a
    second `Store` reclaiming across another's promotion. Removing an already-absent leaf raises
    `ProtocolError`.
35. Every reclaimer refuses an entry no permitted producer could have written and leaves it in place,
    raising `MetadataStoreInvalid` (§8.5): in `blobs/sha256/`, written by A5a's promotion alone, a name
    that is not 64 hex characters **and** a valid-named leaf that is not a regular file or whose bytes
    hash to something else; in `staging/` and `work/`, written by `create_workspace`, a non-directory
    or an invalid txid; and in `staging/<txid>/`, written by A6 through a borrowed anchor, a non-regular
    entry. Each check runs in a preflight, so a refusal preserves the whole directory even when the
    offending entry is enumerated last.
36. Promotion's indexing is idempotent on an identical digest and `byte_len`, and raises
    `MetadataStoreInvalid` when an existing row's `byte_len` contradicts verified content — at the
    preflight, before any source moves.
37. Every `execute` and `executemany` argument **resolves by name, in the scope where it is used**,
    to a module-level string constant of the package — bound in that module or imported from another
    `atoms.store` module — with the two loop bindings that carry SQL permitted by **naming their
    iterables and the element positions that are statements** (`SCHEMA_STATEMENTS`, and
    `_CONNECTION_PRAGMAS` positions 1 and 2), and every other form refused, attribute access
    included. A guard that accepts any attribute, or any name that happens to be some loop's target,
    is not a resolution and does not make the inventory finite; neither is one that unions the whole
    module, because `_execute_schema`'s `for statement in SCHEMA_STATEMENTS` and `_set_column`'s
    `statement` **parameter** share a name, and a module-wide set hands the parameter the loop's
    permission before the call-site check that would have refused it ever runs. A parameter resolves
    only when every call site in its module passes an allowed name.
    **The permitted iterable itself resolves by the same standard**: it must be a module-level binding
    of the file *and* unshadowed in the scope that iterates it. Matching its spelling alone admits

    ```python
    def hostile(connection, SCHEMA_STATEMENTS):
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
    ```

    where the DDL tuple's name is a caller-supplied parameter and every statement in it is the
    caller's. A local rebinding — `SCHEMA_STATEMENTS = build_them()` — is the same hole spelled
    differently, so parameters, assignment and loop targets, `with`/`except` bindings, walrus targets,
    and function-local imports all count as shadows.
    The inventory the rule is stated over descends into module-level literal containers: the pragma
    statements live inside a tuple of tuples, so an inventory of names bound *directly* to a string
    would omit them, and a `blob` writer hidden there would pass resolution and appear nowhere.
    Over that inventory, and with **`executescript` absent as a call** — proved over parsed calls, not
    over source text, so the docstrings explaining the ban do not have to lie about it — **exactly one
    statement writes `blob`** — promotion's step 6 `INSERT` — with any other
    `INSERT`, `REPLACE`, `UPDATE`, or `DELETE` targeting `blob` refused by statement kind and target
    table rather than by spelling. The kind includes the conflict clause: `INSERT OR REPLACE` and
    `INSERT … ON CONFLICT … DO UPDATE` are upserts, not inserts, and either could change the
    `byte_len` of a digest a committed record already names — the disagreement §8.4 refuses at the
    preflight and nothing downstream re-checks. `ON CONFLICT … DO NOTHING` is §8.4's idempotency and
    stays a plain `INSERT`. Every trigger is checked by the targets **inside its body**
    rather than by its subject table.
    The class surfaces are asserted as **sets**: `Store`, `_StoreTransaction`, and `Workspace` expose
    exactly §7.1's and §8.3's listings and nothing else. This is where `_StoreTransaction` having no
    `insert_blobs` and `Workspace._spend_staging` being private are enforceable at all — both are
    claims about absent methods, which no behavioural test can make. A fresh process resolves every
    initial- and final-surface digest of create-from-absent and replace records through `open_blob`.
    These are the two halves of ledger #22, one static and one behavioral.
38. `atoms.fs` and `atoms.core` import nothing from `atoms.store`.
39. Create-from-absent and replace records committed in one process are read back identically in a
    fresh process, including every initial- and final-surface blob.
40. No `ProjectApprovedSpec` is accepted anywhere in `atoms.store`, so ledger #9's enforcement cannot
    be satisfied at this layer by accident.
41. No consumer of `atoms.store` exists yet, asserted rather than assumed. (True at A5a's
    landing. `atoms.coordinator` became that consumer on 2026-08-02 and the guard was retired
    with it; ledger #9's entry-point registry in `test_fs_architecture.py` replaces it.)
42. Every caller-supplied pathname component — txid, manifest leaf, digest — is validated against
    §5.5 before any filesystem mutation, with exact types required and **`ProtocolError` raised**,
    never a bare `TypeError` or `SpecValidationError`.
43. `SQLITE_CORRUPT` and `SQLITE_NOTADB` translate to `MetadataStoreInvalid` with the original as
    `__cause__`; every other `sqlite3.Error` propagates with its original class and code, asserted at
    runtime. The static guard bans `except sqlite3.Error` and bare `except:`, permits
    `except sqlite3.DatabaseError` because §9.1 requires it, and requires every such handler to
    contain a bare `raise` — with **one exemption, granted by name to one function and asserted to be
    alone**. That function is the rollback §7.7 routes every failing exit through, and it must return
    rather than raise, because §7.7 forbids masking the original exception with the rollback's own.
    A second swallow anywhere in the package fails the count, so the exemption cannot spread by
    resembling itself. A total AST inventory additionally requires every SQLite execution site outside
    that exemption to be inside a one-statement `translated` scope.
