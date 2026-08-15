# A6 — coherent capture and the observation mechanism

**Status:** Implemented on 2026-08-07. A8b remains unimplemented.

**Authority:** [`2026-07-23-recoverable-fs-effect-engine-design.md`](2026-07-23-recoverable-fs-effect-engine-design.md)
§4.1, §4.2, §5.5, §6, §7.3, §10, §11, §13.1, §13.2, §14.

**Sub-plans below it:** [`2026-08-02-a5b-recovery-lease-design.md`](2026-08-02-a5b-recovery-lease-design.md),
[`2026-07-31-a5a-metadata-store-design.md`](2026-07-31-a5a-metadata-store-design.md),
[`2026-07-30-a4b1-path-resolution-design.md`](2026-07-30-a4b1-path-resolution-design.md).

---

## 1. Decision

A6 fills exactly one hole: authority §7.3 **step 1** — coherently capture and verify the complete
initial surface, streaming each captured file into `staging/<txid>/`. A5b left that hole open by name,
and its two neighbours already fit around it:

```python
with open_workspace(lease, approved) as workspace:            # A5b
    with capture_initial_surface(                              # A6 — this design
        lease, approved, workspace, payloads
    ) as captured:
        prepare_transaction(                                   # A5b — §7.3 steps 2-4
            lease, approved, workspace, captured.manifest
        )
        # A7 — execute(lease, approved, captured.descriptors)
```

The seam that worked three times already repeats here: A4b-1 observed and A4b-2 judged; A5a stored and
A5b decided; **A6 observes and A7 acts.** A6 produces primitive facts about the filesystem and never a
verdict — the recovery verdict is A3's, and the mutation is A7's.

Two deliverables, in two packages, because the existing dependency DAG already decides where each
belongs:

- **`atoms/fs/observe.py`** — the coherent observation mechanism. Given a held parent descriptor and a
  leaf name, produce the `ObservedEntry` values A3 consumes, under a token discipline that makes
  identity equality mean one thing. It imports `fs` and `core` only, so A7 gets it without dragging in
  a lease, a store, or a workspace.
- **`atoms/coordinator/capture.py`** — forward capture. Hold the descriptor table, drive the observer
  over the approved surface, verify against the declared initial state, stage preimages and planned
  postimages, and hand `prepare_transaction` the manifest it already expects.

## 2. Scope and non-scope

### 2.1 In scope

1. The **descriptor table** (§5): one held, guarded descriptor per directory the transaction will act
   relative to, built by single-component traversal from the physical roots, re-validated against the
   approved baseline, and living long enough for A7 to execute against it.
2. The **observation mechanism** (§6): file, directory, symlink, and absence observation from held
   descriptors, with the `EntryIdentity` token discipline and directory-occupancy evidence.
3. **Forward capture** (§7): initial-surface verification, preimage staging, planned-postimage staging
   from a consumer-supplied payload source, and the promotion manifest.
4. **Absence inference** (§8): §6's two cases — the missing ancestor and the non-directory ancestor —
   as two separate code paths.
5. The **`referenced_digests` repair** (§10.3): widening A5a's helper to every `FileState` the spec
   states, not only those in its two surfaces.

### 2.2 Not in scope

1. **Any project mutation.** A6 writes only into engine-owned `staging/<txid>/`. No effect executes, no
   staging object is published to a project path, nothing is exchanged, moved, or deleted.
2. **Restoration and materialization.** §10's staging-object classification table — complete,
   attributable prefix, wrong-mode staging directory, displaced preimage, undo quarantine — describes
   objects only A7's effects create. It ships with them.
3. **Recovery assembly.** A6 supplies observations; A7 assembles them into the `RecoverySnapshot` and
   `JointObservation` values A3 adjudicates, sequences committed cleanup, and issues fresh
   authorization observations.
4. **Removing A5b's A7 trap.** A live record at lease entry still raises. A6 supplies no executor, and
   the trap's removal remains the signal that the A6/A7 seam closed.
5. **`CERTIFIED_ALLOWLIST`.** It stays empty until A8 crash-certifies a configuration tuple.

### 2.3 Relationship to the layers below

| Layer | A6 uses it for |
| --- | --- |
| A4a `Backend` | Guarded traversal, no-follow reads, symlink fingerprints, and `flush_file` for staged bytes (§7.3) |
| A4a `ProjectBinding` | The borrowed project-root descriptor and `evidence.mount_id` |
| A4b-1 `read_lookup_constraints`, `filesystem_type_of`, `read_mount_id`, `EntryKind` | Re-validation and blocker classification (§10.2) |
| A4b-2 `ProjectApprovedSpec` | The approved topology, directories, paths, and scratch slots |
| A5b `_parent_paths` | The node-to-path table the walk is spelled from (§5.2) |
| A5a `Workspace` | `staging_fd` as the capture sink; `work_fd` as the physical work root |
| A5a `StagedBlob` | The promotion manifest's element type |
| A3 `core.recovery.model` | The observation value types and `EntryIdentity` |

A6 enters the **same** lease A5b holds rather than taking its own lock, which is what keeps resolution,
capture, and mutation one critical section (authority §7.1).

## 3. Seam review against the deferred-obligation ledger

### 3.1 Existing entries

**This table states the expected ledger state _after_ implementation.** The design commit discharges
nothing.

| # | Expected outcome | Why |
| --- | --- | --- |
| 1 | **Open (capture half discharged)** | Capture hashes the actual stream from one descriptor and compares against the frozen `FileState`, refusing on mismatch (§7). Materialization's half is A7's. |
| 3 | **Open (capture/inference half discharged)** | §8's two branches infer descendant absence from the ancestor's verified state. A7 still owns handing §9.5's published-directory descriptor down to descendants at execution. |
| 13 | **Open (observation-mechanism half discharged)** | §6 produces state, identity, prefix relation, and occupancy coherently from held descriptors under the token discipline, and cannot name a verdict. A7 still owns complete recovery assembly, committed-cleanup sequencing, and fresh authorization observations. |
| 19 | **Open (A6's half discharged)** | The descriptor table re-resolves identity, constraints, and mount against the approved baseline before relying on any of it, and refuses on mismatch (§5). A7's execution half remains. |
| 9 | **Open** | A6 extends the entry-point gate set to its one new entry (§10.1); A7–A8 extend it further as they land. |
| 12, 17 | **Open** | Unchanged by A6; both wait on A7's executor. |

### 3.2 New entries this design creates

**None.** Three candidates were considered and rejected:

- *The `referenced_digests` widening.* An immediate repair inside A5a's surface, landing in the same
  commit as the code that needs it (§10.3). A deferred-obligation entry records a shape a boundary
  admits but does not execute; this one is executed.
- *Scratch observation without a producing effect.* A6 delivers the mechanism and exercises it over
  real files and real descriptors (§11.3). It is not an admitted-but-unhandled shape; it is a handled
  shape whose production caller arrives later, which is what #13's retained A7 residue already records.
- *Payload supply.* The `PayloadSource` contract refuses every malformed or divergent case at capture
  (§9). Nothing is admitted and deferred.

### 3.3 Authority amendments required at implementation

**This commit contains the design only.** The amendments below land with the implementation, in the
same commit as the code that depends on them.

**Authority §14, Plan A item 4.** The current text reads "Coherent capture and restartable atomic
materialization (§6, §10)". Most of §10 is rollback restoration over objects only A7's effects create,
and building the classifier before its inputs exist repeats the error A5b avoided at the A7 trap. The
item becomes:

> 4. Coherent capture and the observation mechanism (§6, and §10's coherent-observation contract).
>    Restartable materialization's staging-object classification ships with the effects that create the
>    objects it classifies (item 5).

**Authority §6, the second absence-capture case.** The current text says guarded traversal "fails at
`p` itself with `ENOTDIR`", which selects the regular-file branch by errno alone. `ENOTDIR` does not
distinguish a regular file from a socket, FIFO, or device node. A4b rejects `OTHER` at approval, but
capture-time drift can introduce one between approval and capture. The paragraph gains:

> `ENOTDIR` establishes only that the blocker is not a directory — it does not distinguish a regular
> file from a socket, FIFO, or device node. Neither branch is selected by the errno: the regular-file
> branch is selected by verifying the blocker against the timeline's first `FileState`, and the symlink
> branch by verifying it against the timeline's first `SymlinkState`. A blocker matching neither
> declared state refuses — including a symlink whose target or mode has drifted, which is a symlink but
> not the declared one.

**Authority status header.** It reads "A1–A5b are implemented … A6–A8 (coherent capture,
effect/recovery execution, synthetic exerciser) remain", and becomes "A1–A6 are implemented …
A7–A8 (effect/recovery execution, synthetic exerciser) remain". Its wording is its own — it does not
contain the `"A6–A8 remain unimplemented"` sentence every other document uses — so a grep for that
string leaves the authority document, the one that outranks all the others, still denying A6 exists.
The status test asserts this header positively for that reason.

**The status guard reads headers and status sections, never whole files.** The paragraph you are
reading quotes both retired spellings verbatim, because a record of an amendment has to state what it
replaced. A whole-file scan would therefore fail on this section — and the only ways to make it pass
would be to delete the record or to stop guarding the document, both of which trade away the thing the
guard protects. The check is scoped to each document's `**Status:**` field or `## Status` section: the
claim about the present, which is the only part that can go stale. The same reasoning excludes the two
architecture test files, which name these strings as the strings they forbid.

**Status synchronization.** `AGENTS.md`'s A3 and A5 paragraphs (the latter justifies the A7 build-stage
trap partly by "A6 supplies no observations", which stops being true while the trap itself stays) and
the `README.md` `## Status` section — already three sub-plans stale, stopping at A3 — gain A6's state,
and a new `test_a6_status_is_synchronized_across_authority_documents` asserts the strings, following
`test_a4a_…`, `test_a4b_…`, and `test_a5_…`. (Those four were replaced on 2026-08-08 by
`tests/test_docs_status.py`: two of them had begun pinning claims later sub-plans falsified, which
is the failure mode a per-sub-plan snapshot cannot avoid.)

## 4. Architecture and ownership

### 4.1 Module layout

| Module | Owns |
| --- | --- |
| `atoms/fs/observe.py` | `Observation` — the token universe, per-kind observation, occupancy evidence, and the planned-blob prefix comparison |
| `atoms/coordinator/descriptors.py` | `DescriptorTable` — the held, re-validated, node-keyed descriptor set |
| `atoms/coordinator/capture.py` | `capture_initial_surface`, `Captured`, `PayloadSource`, and the staging loop |

`DescriptorTable` lives in `coordinator/` rather than `fs/` because building it needs
`workspace.work_fd`, and `Workspace` is a store type. `Observation` needs neither, which is what keeps
it in `fs/` and reusable by A7.

There is no new `errors.py` and no new exception type. The existing hierarchy already carries every
meaning A6 needs (§9).

### 4.2 Dependency direction

The DAG is unchanged:

```
coordinator → {store, fs, core}
store       → {fs, core}
fs          → {core}
```

`observe.py` adds one narrower rule of its own, asserted by an architecture test: **within
`atoms.core.recovery`, it may import `model` and nothing else.** A blacklist on `snapshot` would be
insufficient — `atoms/core/recovery/__init__.py` re-exports `classify_recovery` and
`authorize_recovery_step`, so a classifier is reachable through the package facade. The whitelist is
how ledger #13's "may not pre-classify them into a recovery outcome" becomes a mechanical property
instead of a review promise.

### 4.3 `Backend` is not extended

Two additions were considered — descriptor-relative enumeration for occupancy, and a no-clobber
create for the staging sink — and both are rejected. `backend.py`'s own docstring states the rule:
"exactly one operation set per design §5.5 capability, and every method is called by the probe that
reports it." Neither addition is a probed semantic capability, so adding them would break the
invariant that makes the probe's evidence non-circular.

Both are plain stdlib operations on descriptors A6 already holds:

| Need | Call |
| --- | --- |
| Directory occupancy | `os.listdir(dir_fd)` |
| The staging sink | `os.open(name, O_WRONLY\|O_CREAT\|O_EXCL\|O_NOFOLLOW\|O_CLOEXEC, mode=0o600, dir_fd=staging_fd)` |
| Reads and `fstat` | `os.read` / `os.fstat` on the descriptor `open_regular_nofollow` returns |

Staged-byte durability is *not* in that list: `flush_file` is an existing `Backend` method and the
§5.5 `durable_publish` capability, so §7.3 below uses it rather than `os.fsync`.

## 5. The descriptor table

### 5.1 What it is

`RecoveryTopology.parents` is already a rooted tree — `snapshot.py`'s `_validate_topology` enforces
single parenthood, forbids a parent for `ProjectRoot`, requires `WorkRoot` to be parented by
`ProjectRoot`, and ends on "topology must be an acyclic tree rooted at the project root". The
descriptor table is a walk of that tree, opening each directory node from its parent's held descriptor
with **one** `open_child_directory` call, which is already
`RESOLVE_BENEATH | RESOLVE_NO_SYMLINKS | RESOLVE_NO_XDEV`. No multi-component path is ever assembled,
which is precisely §6's rule: passing a multi-component name to a syscall reopens the check/use race,
because the kernel re-resolves intermediate components at the syscall.

**The tree gives structure, not spelling.** `TopologyDirectory(node_id)` carries no name, so the walk
needs a node-to-path table to know which single component to open at each hop. That table is
`_parent_paths(approved)` in `admission.py:88`, built from `approved.paths` — a parent node's path is
its child's path minus the child's leaf — and A6 reuses it rather than restating the derivation. Each
hop's component is the child's path minus the parent's, and the walk asserts it is a single component.

`ProjectApprovedSpec` retains no `ResolvedPrefix`: A4b-1's resolution closed every descriptor it
opened, and what survives approval is `paths`, `topology`, `directories`, `scratch`, and `work_base`.
A6 rebuilds the walk from those, which is ledger #19's requirement rather than an inconvenience.

### 5.2 Which nodes bear a descriptor

**Selected by role, from `approved.directories` plus the two physical roots — never by node class.**
`CreateDirectory("p")` followed by `CreateFileNoClobber("p/q")` makes `PersistentNode("p")` both a
declared path and the parent of a declared descendant, and `expected_persistent` in `_validate_topology`
is built from every timeline, so a class-based selection would miss exactly the case §8's second branch
exists for.

| Node | Descriptor | Re-validated on entry |
| --- | --- | --- |
| `ProjectRoot` | **borrowed** `approved.binding.project_root_fd` | identity, constraints, mount |
| `WorkRoot`, when present | **borrowed** `workspace.work_fd` | constraints, mount — there is no approved identity |
| `ApprovedExistingDirectory` | **owned**, one `open_child_directory` from its parent | identity, constraints, mount |
| `ApprovedPlannedDirectory` (project) | none — the walk stops here | capture proves absence or the blocker (§8) |

The logical `WorkRoot → ProjectRoot` edge is **not physically traversed**: the work root lives under
`metadata_root`, not beneath the project root. `WorkRoot` is also the one planned directory that does
not stop the walk, because A5b has already created and opened it.

Every node's baseline is **its own record in `approved.directories`**, `WorkRoot` included: A4b stores
it as `ApprovedPlannedDirectory(WorkRoot(), inherited_constraints(work_base.constraints,
filesystem_type))`. `approved.work_base` is a different thing — the observed facts of
`metadata_root/work`, which is the **parent** of the `work/<txid>` that `workspace.work_fd` names — so
it is not what a `work/<txid>` descriptor is compared against. The two carry equal values on ext4,
which is why the distinction has to be stated rather than left to a test to discover.

**`WorkRoot` is included only when the approved topology contains it** — equivalently, when
`approved.work_base is not None`, which A5b already treats as the signal that the spec declares a
`CreateDirectory` and so has a `WORK` scratch role. When it is absent, A4b judged `work/` irrelevant
to this transaction and there is no approved constraints baseline to compare against; admitting the
node anyway would let a fresh observation authorize itself, the exact shape ledger #19 forbids.
`_parent_paths` omits `WorkRoot` for the same reason, so the two agree by construction.

### 5.3 Why `ProjectRoot` is re-validated too

Its descriptor is retained by the binding across approval, so its identity cannot drift. Re-validation
is still required, because **retention is not discharge**: `DirectoryConstraints` carries
`lookup_proof` and `name_max`, both of which are mutable directory properties, and ledger #19's rule is
that a resolved fact is compared against its approved baseline before being relied on. A held
descriptor is not an exception the ledger grants.

### 5.4 Mount membership is checked by hand

`DirectoryConstraints` is `lookup_proof` and `name_max` only. Mount membership is a separate
`read_mount_id(fd)` against `binding.evidence.mount_id`, exactly as `resolve.py` does at every hop. A
constraints comparison alone would pass a directory that had been replaced by a bind mount.

### 5.5 Ownership and lifetime

The table **borrows** the two root descriptors and **owns** only those it opened itself; `close()`
closes only the latter. `ProjectBinding`'s own docstring states that every descriptor it exposes is
borrowed and a consumer must never close one, and `Workspace` owns and spends its two.

`workspace.staging_fd` is **not** a topology root. It is the capture sink, and `promote_staging` spends
it — which is why capture must complete before preparation runs, and why the table, not the staging
descriptor, is what A7 receives.

The table outlives capture. §6 requires the engine to hold a descriptor to the project root "for the
transaction's lifetime" and §9.5 hands each published directory's descriptor down to its descendants.
A table that died at capture's return would force A7 to re-resolve, reopening the race the whole
section exists to close.

## 6. The observation mechanism

### 6.1 One pass, one token universe

`Observation` is a **live resource**, not a value. It owns a `(st_dev, st_ino) → EntryIdentity` map and
a descriptor per observed identity:

```python
class Observation:
    def observe(self, parent_fd: int, leaf: str, *, sink_fd: int | None = None) -> ObservedEntry: ...
    def occupancy(self, dir_fd: int, modeled: frozenset[str]) -> bool: ...
    def build_relation(self, staged_fd: int, planned_fd: int) -> FileBuildRelation: ...
    def close(self) -> None: ...
```

One underlying entry reached through two slots yields the **same** token; distinct entries yield
distinct tokens; a fresh `Observation` mints a fresh universe. That is what makes A3's identity
equality mean "the same entry, within one pass" and nothing else.

**The descriptors are the pin.** `(st_dev, st_ino)` identifies an entry uniquely only while its inode
stays allocated. If the observer opened a file, recorded its key, and closed it, a subsequent unlink
and create could recycle that inode and map two sequentially distinct entries onto one token — an
identity equality A3 would believe. Retaining one descriptor per observed identity until the pass
closes makes the reuse impossible rather than unlikely.

### 6.2 Per-kind observation

- **Regular file** — `open_regular_nofollow`, then type and mode from `fstat` on **that** descriptor,
  and the content hash streamed from **that** descriptor. One coherent observation, never a name
  resolved twice (Guarantee 3). An optional `sink_fd` lets the same single read also stream the bytes
  into `staging/`, so a retained preimage is never read twice and the retained bytes and the fingerprint
  provably derive from one descriptor.
- **Directory** — `open_child_directory`, `fstat` for mode, and **one** descriptor-relative
  enumeration reconciled against the modeled child names to produce `has_unmodeled_child`.
- **Symlink** — `symlink_fingerprint` (`lstat` + `readlink`) and nothing else. `ObservedSymlink` has no
  `identity` field, so §6's rule that a symlink never carries descriptor identity is *unrepresentable*
  rather than merely forbidden. `O_NOFOLLOW` fails by design on a symlink leaf, so no descriptor to the
  link itself exists to be coherent about.
- **Absent** — `OBSERVED_ABSENT`.

### 6.3 The prefix relation

`ScratchObservation.file_build_relation` compares a staged object against the planned blob. The
observer streams both and returns exact, prefix, or diverged. It takes the planned blob as an **open
descriptor supplied by the caller**, never a digest it resolves itself — which is both the coherence
rule and what keeps `atoms.store` out of `atoms/fs/`.

Per ledger #13, the observer does not stream-compare displaced preimages, reverse quarantines, or
committed-cleanup scratch. A3 decides those from exact fingerprints, and computing a relation A3 will
not read would be work whose only effect is to invite reliance on it.

## 7. Capture

```python
capture_initial_surface(lease, approved, workspace, payloads) -> Captured
```

`Captured` is a live resource owning the descriptor table and exposing `manifest` and `descriptors`.
The `Observation` and its pinned descriptors close when the pass ends; the table does not.

1. **Build the descriptor table** (§5). Any re-validation mismatch refuses.
2. **Observe and verify.** Every declared path is observed against its parent's held descriptor and
   compared with **its timeline's first precondition** — equivalently, the declared initial surface.
   Later occurrence-local preconditions describe intermediate states that no initial capture can
   observe, and checking them here would refuse correct transactions.
3. **Stage file preimages.** Every *regular-file* preimage an effect must retain — `ReplaceFile.pre`,
   a `FileState` `DeletePath.pre`, `MoveNoClobber.source_pre` — is streamed into `staging/` through
   step 2's descriptor, hashed once.

   A **symlink** preimage is not staged. §6 retains no content for one, `referenced_digests` filters on
   `FileState` and so never names it, and §10's rollback material for a symlink is the atomically
   transferred tombstone, which only A7 creates. Capture verifies its `lstat` + `readlink` fingerprint
   and retains nothing. Directories and absence likewise carry a fingerprint and no content.
4. **Stage planned postimages.** For each distinct required `(digest, byte_len)` pair,
   `payloads.open(digest)` supplies a byte source; capture streams it into `staging/` and verifies hash
   and length against the declared `FileState`. Mode is not a blob property — A7 applies it at
   publication.
5. **Flush every staged file** (§7.3) before closing its sink.
6. **Return.** On success `staging/` holds exactly the manifest's entries.

Steps 3 and 4 are why capture sits above the store: both concern the `blob` surface, which `fs/` may
not import.

### 7.1 The payload source

Authority §4.1 forbids the engine from reaching back into consumer plan formats; §4.2 reserves
staging-path derivation to the engine. A declared `FileState` is a postcondition with a hash and no
bytes (ledger #1), so the bytes must arrive at a seam that violates neither rule:

```python
class PayloadSource(Protocol):
    def open(self, digest: str) -> IO[bytes]: ...
```

Content-addressed, so two effects writing identical content are supplied once and the consumer never
learns a staging path. The source is an **external source promised by the frozen spec**, which is what
makes §9's two-way error split principled rather than arbitrary.

`open()` returns a **fresh binary stream, owned by capture**, which closes it whether the stream is
consumed, refused, or abandoned by an earlier failure. A source that hands back a shared or already-read
stream would make a second staging attempt silently produce a short blob.

**Every distinct required digest is staged once; there is no blob-index skip in v1.**
`promote_staging` already handles a pre-existing blob by verifying the indexed leaf and unlinking the
duplicate, so skipping is an optimisation, not correctness. If it is ever added, a `blob` row lookup is
insufficient on its own — the indexed leaf must be opened and verified, because a row asserts the
bytes were durable once, not that they are intact now.

### 7.2 One length per digest, checked before writing

`referenced_digests` returns `(digest, byte_len)` **pairs**, not digests, and that is deliberate:
`compile_spec` validates each `byte_len`'s range and the empty-hash correspondence, but never
cross-checks that one `content_hash` carries one `byte_len` across entries. A spec may therefore
declare `sha256:…` at two different lengths.

The natural staging name is `digest_to_leaf(digest)` — the leaf `promote_staging` will rename it to —
and it is a single name per digest. Two lengths would collide on it, and whichever wrote second would
publish a blob one effect's `FileState` disagrees with.

So capture **first requires exactly one length per digest across the whole required set, raising
`ProtocolError` before writing anything.** This is engine misuse surfacing at the first layer that can
see it, not external drift: the contradiction is in the frozen spec, and no filesystem state is
involved. The widened `referenced_digests` (§10.3) keeps its pair semantics for the same reason.

### 7.3 Staged bytes must be flushed

`promote_staging` flushes **directories** — `flush_directory(blobs_fd)` and
`flush_directory(staging_fd)` — which makes the *names* durable. Nothing flushes the file contents.
After power loss the record could reference a blob whose directory entry survived and whose data blocks
did not, which is precisely the cross-substrate rule of authority §7.3 failing: "anything the database references
must be durable on the filesystem before the COMMIT that references it."

Capture calls `backend.flush_file(sink_fd)` on **every** staged preimage and payload, after its hash
and length verify and before its sink is closed. Doing it before verification would flush bytes that
are about to be refused; doing it after close is not possible.

The ordering is asserted by a test, not left to the code's shape, because a lost flush is invisible
until a crash.

### 7.4 Cleanliness is scoped to success

`promote_staging` asserts `staging/` is empty after its rename loop and refuses on any remainder, so a
**successful** capture must leave exactly the manifest's entries and nothing else.

A **refusal** may leave partial workspace scratch. That is correct and deliberate: the scratch is
mutation-free, no durable record exists, and A5b's reclamation removes orphan `staging/`, `work/`, and
unreferenced blobs under the held lock at the next lease entry, regardless of count (authority §7.3).

## 8. Absence inference

§6 gives two routes to a declared-absent path. They reach the same conclusion by different evidence
and are **two separate functions with separate tests**, because conflating them would silently grant a
symlink the file branch's coherence.

### 8.1 Missing ancestor

The path lies beneath an ancestor an earlier `CreateDirectory` will create, so its parent does not
exist and cannot be opened. The walk stops at the `ApprovedPlannedDirectory`, and the deepest existing
ancestor is simply the last node the walk opened before stopping — the table already holds its
descriptor, and §5.1's node-to-path table already names the component below it. Absence is confirmed by
a no-follow lookup of that **first missing component** relative to that held descriptor.

The compiler orders ancestor creation outer-to-inner (§5.4, §9.5), so at execution each
`CreateDirectory` has already retained a descriptor to the directory it published and hands it down —
descendants never re-resolve the ancestor chain. That hand-down is A7's, and is why ledger #3 keeps an
A7 residue.

### 8.2 Non-directory ancestor

`DeletePath("p")`, `CreateDirectory("p")`, `CreateFileNoClobber("p/q")`. Compilation admits this: `p`'s
timeline is continuous (`FILE → ABSENT → DIRECTORY`), and `p/q` is absent precisely *because* `p` is a
file. §8.1 does not apply, because no component of `p/q` is missing where traversal stops — it fails at
`p` itself, and there is no descriptor against which `q` could be looked up. Absence is therefore
**inferred from the ancestor's verified state rather than probed**.

**The errno does not select the branch.** `resolve.py`'s `_BLOCKER_KINDS` maps
`ENOTDIR → (REGULAR_FILE, OTHER)` and `ELOOP → (SYMLINK,)`. `ENOTDIR` establishes only that the blocker
is not a directory; a socket, FIFO, or device node produces it too. A4b rejects `OTHER` at approval,
but capture-time drift can introduce one afterwards. **Each branch is selected by verifying the blocker
against the timeline's first declared state, never by the errno**: the regular-file branch against its
first `FileState`, the symlink branch against its first `SymlinkState`. A blocker matching neither
refuses — including a symlink whose target or mode has drifted, which is a symlink but not *this*
symlink.

- **Regular-file ancestor — descriptor-coherent.** Opened `O_RDONLY|O_NOFOLLOW`; type, mode, and hash
  all from that one descriptor. The inference is *stronger* than the negative lookup it replaces,
  because it rests on one descriptor's coherent observation rather than on a name resolved twice.
- **Symlink ancestor — not.** `lstat` + `readlink`, no descriptor, no identity, and the observed
  target and mode must equal the declared first `SymlinkState`. The absence inference still holds,
  since a symlink holds no directory entries, but its identity contract is deferred to A7's destructive
  transfer validating the moved object against the frozen fingerprint.

In neither branch is capture-time verification compare-and-swap authority. If the ancestor is swapped
before execution, A7's destructive transfer validates the transferred object and refuses or halts.

## 9. Errors

No new exception type. Capture precedes the durable record, so **no A6 failure halts** — ledger #19's
rule in its "before durable transaction authority exists" branch. Authority §11 already names capture
as a `PreconditionRefused` site.

The table below is the set of conditions with a defined domain meaning. It is not exhaustive over
everything that can go wrong: §9.1 states which errnos are translated and why the rest propagate as
themselves.

| Condition | Error |
| --- | --- |
| Observed state diverges from the declared initial state | `PreconditionRefused` |
| Re-resolution mismatch: identity, constraints, or mount | `PreconditionRefused` |
| Blocker matches neither declared state (§8.2) | `PreconditionRefused` |
| A well-formed payload source whose bytes changed or disagree with the declared `FileState` | `PreconditionRefused` |
| A missing or malformed payload binding | `ProtocolError` |
| One digest declared at two lengths (§7.2) | `ProtocolError` |
| Backend cannot supply a required capability | `CapabilityUnavailable` |
| Workspace that is not exactly `Workspace`, or belongs to another `Store`, or whose txid differs | `ProtocolError` |
| Spent descriptor, closed table, other engine misuse | `ProtocolError` |

The payload split follows from what `PayloadSource` **is**. A binding that is absent or malformed is a
broken submission — the consumer did not supply what the frozen spec promised, and no external state is
involved. A well-formed source whose bytes disagree is external state diverging from frozen intent,
which is the definition of what capture exists to detect (§4.1).

An **extra** binding is deliberately not an error. `PayloadSource` exposes only `open(digest)` and
cannot be enumerated, so capture asks for what it needs and never learns what else the consumer could
have supplied. Detecting extras would mean adding enumeration machinery to the protocol for a condition
that harms nothing: an unrequested payload is never opened, never staged, and never promoted.

### 9.1 Translating direct lookups

The traversal, lookup, and staging calls A6 makes raise `OSError` with a raw errno, and §9's table
names A6 errors, so each call site translates the errnos that carry a **defined domain meaning**:

| Errno | Meaning after approval | Error |
| --- | --- | --- |
| `ENOENT`, `ENOTDIR`, `ELOOP`, `EXDEV` | The namespace no longer matches what approval established | `PreconditionRefused` |
| `EEXIST` on a staging leaf | External occupancy of an engine-derived scratch name | `PreconditionRefused` |
| `ENOSYS`, `EOPNOTSUPP`, `ENOTSUP` | The backend cannot supply the semantics | `CapabilityUnavailable` |
| `EBADF` | An internal contract was violated | `ProtocolError` |

The table is honored by `translated_lookup`, which each **lookup** site wraps around a single
statement. The streaming read/write loops and `build_relation` do not wrap, so an `EBADF` raised
there propagates as `OSError` rather than `ProtocolError` — §13 records the gap. No other row is
affected: the namespace and capability rows describe lookups by construction.

**Every other `OSError` propagates unchanged**, keeping its own class and its traceback. This is A5a's
rule for SQLite result codes, applied to errno: `translated()`'s docstring requires wrapping a single
statement rather than a protocol, "so an unrecognized code keeps its own class *and* its traceback and
a future SQLite code is propagated rather than guessed at."

The reason is not economy. Reads, writes, flushes, closes, and consumer payload streams can raise
`EIO`, `ENOSPC`, `EROFS`, `EDQUOT`, and more; none of them is external state contradicting the frozen
spec, and reporting a failing disk as `PreconditionRefused` would tell a consumer its intent had
drifted when the hardware had failed. A6 therefore does **not** promise that every failure is one of
its own error classes. It promises what is actually true and load-bearing: **no A6 path halts.** A
propagated `OSError` leaves no durable record and no project mutation, exactly as a refusal does, so
A5b reclaims its orphan scratch at the next lease entry either way.

The staging row is worth stating explicitly: authority §11, as amended by A5b, already names
"pre-existing external occupancy of an engine-derived scratch leaf" as a `PreconditionRefused` case
that need not be concurrent, because such a leaf may predate this attempt entirely.

## 10. Changes to existing code

`atoms/fs/backend.py` and `atoms/fs/linux.py` are **not** changed — see §4.3.

### 10.1 `atoms/coordinator/admission.py`

The entry-point gate set gains **one** entry, per ledger #9: `capture_initial_surface`, which requires
an admitted proof and a workspace whose txid matches it, exactly as `prepare_transaction` does. The
descriptor-table builder stays package-private and is reached only through that entry, so it is not a
second gate site.

It requires **more** than `prepare_transaction` does about the workspace, and must, because it is the
first function that writes into one. `prepare_transaction` touches the workspace only by handing it to
`promote_staging`, whose first two statements are `type(workspace) is Workspace` and
`workspace._store is store`; nothing has been written when they run. Capture streams preimages and
payloads into `workspace.staging_fd` long before `promote_staging` is reached, so a duck-typed value —
or a real `Workspace` issued by a different `Store` under the same txid, which `create_workspace` will
mint on request — would receive this transaction's bytes and be refused only afterwards. Capture makes
both checks itself, before the first write, in addition to the txid comparison. This is not a second
gate; it is the ownership precondition of the sink, asserted where the sink is first used.

`_parent_paths` gains a second in-package consumer (§5.1). It stays private and stays where it is;
`descriptors.py` sits in the same package.

**`_require_planned_absent` is relaxed — the one behavior change A6 makes to a gate that already
shipped.** A5b's branch refused whenever an `ApprovedPlannedDirectory`'s slot was occupied at
admission time, reasoning that a planned directory carries no approved identity to compare the
occupant against. That is right for drift and wrong for §8.2: `DeletePath("p")` +
`CreateDirectory("p")` + a declared child is a shape A4b approves against a **present** `p`
deliberately, and two A4b conformance tests do exactly that. Left alone, the gate would refuse every
§8.2 timeline before capture ran, making §8.2 unreachable in production and its conformance coverage
a test of a branch nothing could enter.

The branch therefore consults the timeline's first declared state for the path — a
`_declared_first_state` helper over `approved.compiled.timelines` — and returns instead of refusing
when that state is not `AbsentState`. Presence the proof's own timeline already accounts for is the
proof being right, not the world having moved. Admission holds only the presence bit `observe_child`
returns and must not judge the occupant's *kind*; capture judges it coherently, through a descriptor,
against that same first declared state (§8.2). The relaxation is narrow in exactly that sense: it
moves one question one layer down, to the layer holding the evidence to answer it.

### 10.2 `atoms/fs/resolve.py` — one helper becomes public

`read_lookup_constraints(fd, filesystem_type)` takes the filesystem type as a string, and the only
route to it that also checks the backend is `resolve._filesystem_type(binding)`, which is private.
`observe_child` calls it internally, but A6 re-validates directories it has already opened and has no
name to observe a child of.

It is renamed `filesystem_type_of` and made public. Reading
`binding.evidence.configuration.filesystem_type` directly would work and is the wrong fix: it skips the
Linux-backend check that gives the flag semantics their meaning, and would put a second copy of that
decision in the coordinator.

### 10.3 `atoms/store/records.py` — the `referenced_digests` repair

Today the helper scans `spec.initial_surface` and `spec.final_surface` only, and `connection.py` raises
`ProtocolError` for any promoted digest outside that set. An **intermediate** postimage appears in
neither surface: `ReplaceFile("p", A→B)` followed by `ReplaceFile("p", B→C)` has `A` in the initial
surface and `C` in the final, and `B` in neither. Staging `B` would refuse at the barrier; not staging
it would leave A7 without the bytes it must publish.

The helper widens to every `FileState` the spec states — both surfaces **and** every effect occurrence.
This does not weaken the barrier: an intermediate postimage is genuinely referenced by the record, and
the barrier's purpose is to reject digests the record does not reference at all. Its name stays;
its docstring, `test_referenced_digests_include_initial_and_final_file_surfaces`, and
`tests/coordinator_child.py` are updated in the same commit.

**It keeps returning `(digest, byte_len)` pairs.** Collapsing to digests would erase the contradiction
§7.2 must detect, and would do so in the one helper positioned to see every declared `FileState` at
once.

**The helper has two consumers of opposite polarity, and the widening moves both.** `connection.py`
is a *ceiling* — a promoted digest must appear in the set, or promotion raises — and that is the
consumer the paragraphs above argue. `records.py`'s coherence check is a *floor*: every pair in the
set must have a `blob` row, or the record reads back incoherent. Widening therefore does not only
admit more promotions; it also **requires** intermediate postimage blobs to be present. That is the
correct reading of authority §7.3 — a record does reference its intermediate postimages, and A6
stages them — so a durable record missing one is genuinely incoherent and should say so. The one
fixture that planted a record without them — `test_store_records.py`'s non-compiling-spec planter —
was split so that it plants the blob as well.

## 11. Verification strategy

Six tiers, all on the real ext4 test volume.

### 11.1 Observation

Token identity across two slots naming one entry; distinct tokens for distinct entries; a fresh
universe per pass. Symlinks carry no identity — asserted structurally, since `ObservedSymlink` has no
field for one.

**The inode-pin test asserts the pin, not the reuse.** Unlinking an entry and recreating one does not
*force* the kernel to hand back the same inode, so a test that unlinked, recreated, and asserted
distinct tokens would pass just as readily on a build with no pin at all — it would be testing the
allocator's mood. What is actually assertable is the mechanism: after the entry is unlinked, the
observation's retained descriptor is still open and still reads the original bytes, so the inode cannot
be reallocated while the pass lives. The invariant that no live inode is reused is the kernel's, and
the test's job is to prove A6 holds the reference that invokes it. A sabotage variant closes the
retained descriptor and asserts the mechanism, not the outcome, is what changed.

### 11.2 Capture, real filesystem

Precondition match and every divergence shape; both §8 branches, including a blocker that is `OTHER`
rather than a regular file, and a symlink blocker whose target has drifted; payload mismatch and
malformed binding; one digest declared at two lengths refusing before anything is written; `staging/`
contents exactly the manifest on success; partial scratch on refusal reclaimed at the next lease entry.

**A flush-ordering test.** `backend.flush_file` is observed for every staged file, after that file's
hash and length verify and before its sink closes. A lost flush is invisible until a crash, so the
ordering is asserted directly rather than inferred from the code's shape.

### 11.3 Scratch observation

**Over real files and real descriptors**, not hand-built model values. Building a `ScratchObservation`
by hand and feeding it to A3 tests A3, not A6. The suite creates the actual staging shapes on disk —
complete, attributable prefix, diverged, wrong-mode directory — and asserts the observer's
`FileBuildRelation` over them.

### 11.4 Conformance — two routes, not one

The **`Observation` mechanism's** outputs feed two different A3 entry points, and one route cannot
exercise both. `Captured` exposes no observations — it carries the manifest and the descriptor table —
so both routes drive the observer directly:

- **Complete observations** → `build_recovery_snapshot`, which must accept them. This proves the
  observer satisfies A3's coverage and shape validators without A7 existing.
- **Scratch-only committed-cleanup observations** → `JointObservation` consumed by
  `authorize_recovery_step`, whose contract is exactly the named retained scratch slot with empty
  persistent and occupancy coverage.

The second route's observation must be **fresh** — a new `Observation` pass taken after
classification, from the still-held descriptor, with the entry still on disk. Reusing the complete
pass's entry would test a stale value and prove nothing about re-observation, which is the half of
ledger #13 A7 inherits. This is admissible because `authorize_recovery_step` compares pairwise
identity *relations* within an observation, never a raw token across passes; the test asserts the new
pass really is a new token universe rather than assuming it.

### 11.5 Architecture

The `core.recovery` import whitelist on `observe.py`; the existing store-import test still green; the
corpus status guard. (A6 shipped `test_a6_status_is_synchronized_across_authority_documents`, the
fourth per-sub-plan guard; all four were replaced on 2026-08-08 by the derived
`tests/test_docs_status.py`, which holds the roadmap once and derives every check from it.)

### 11.6 Adversarial

**Scoped to A6.** Mount crossing mid-walk; a leaf swapped for a symlink **between the held-directory
walk and the leaf observation** — the only window A6 owns, since observation is the last thing it does
to an entry and a swap after it is A7's destructive-transfer validation; `ProjectRoot` constraints
changed after approval; the retained-descriptor pin and its close-pin sabotage (§11.1) — not inode
reuse, which cannot be forced and so cannot be asserted.

Symlink validation after a destructive transfer, and effect-staging swaps between a pre-publication
check and the publishing rename, are **A7 obligations** — the objects do not exist until an effect
creates them, and asserting them here would prove nothing about the code that will own them.

## 12. Acceptance criteria

1. `capture_initial_surface` fills authority §7.3 step 1, and `open_workspace` → capture →
   `prepare_transaction` composes without any intervening re-resolution.
2. Every mutation is a single-component leaf operation against a held, re-validated, guarded parent
   descriptor. No multi-component path reaches a syscall.
3. The descriptor table outlives capture and is what A7 will execute against.
4. `atoms/fs/observe.py` imports `atoms.core.recovery.model` and no other `core.recovery` module, and
   the architecture test proves it.
5. One `EntryIdentity` per underlying entry per pass, pinned by a retained descriptor; symlinks have
   none, structurally.
6. Both §8 branches are separate code paths, and each is selected by the declared state — the
   regular-file branch by its first `FileState`, the symlink branch by its first `SymlinkState` —
   never by an errno.
7. No A6 path raises `TransactionHalted`. Errnos with a defined domain meaning are translated per
   §9.1; every other `OSError` propagates with its own class and traceback.
8. Every staged file is flushed with `backend.flush_file` after its hash and length verify and before
   its sink closes, asserted by an ordering test.
9. One `byte_len` per digest is required across the whole set, raising `ProtocolError` before anything
   is written.
10. A successful capture leaves `staging/` holding exactly the manifest.
11. `referenced_digests` covers every `FileState` the spec states, still as `(digest, byte_len)` pairs,
    and an intermediate postimage promotes without tripping the barrier.
12. `Backend` gains no method; the probe and `CERTIFIED_ALLOWLIST` are untouched.
13. No project path is mutated by any A6 code path, asserted by the mutation-surface suite
    (authority §13.5).
14. A5b's A7 trap still raises, unchanged.

## 13. Known gaps carried to A7

None of these blocked A6, and none is a defect in what A6 promises. Each is a place where the
implementation is narrower than this design, or where A6 built something A7 is the first to depend
on. They are recorded here rather than in the deferred-obligation ledger because that file tracks
*admitted shapes* at trust boundaries, and these are ordinary gaps — filing them there would dilute
the register that A2's three review rounds justified.

1. **`DescriptorTable.stops` is readable after `close()`.** `fd_for` and `is_unreachable` both raise
   `ProtocolError` when `_closed`; `stops` is a bare slot and does not. It hands out
   `WalkStop.parent_fd`, a descriptor the table has already closed, and A7 is its first real
   consumer. Guard it the same way.

2. **`close()` abandons the descriptors after the first failure.** Both `DescriptorTable.close` and
   `Observation.close` set `_closed`, then loop over `os.close`. A raise part-way through leaves the
   remaining descriptors open with no second attempt possible, because the flag already says closed.
   The two are the same shape and should be fixed together.

3. **`fd_for` raises a bare `KeyError`.** §9 puts engine misuse at `ProtocolError`, and `capture.py`
   translates it back at its one call site — machinery that exists only because the raise is wrong.
   A7 adds call sites; each would need the same wrapper.

4. **`_verify_stops`' final `else` is untested.** It is the branch for a blocker that no declared
   file or symlink state describes: `declared.get(stop.path)` returns `None` or `AbsentState` while
   the walk was blocked there. Admission refuses that shape when it holds at admission time, so
   reaching it means the entry appeared between admission and the walk — which the cooperating-process
   assumption (authority §7) makes rare rather than impossible. Of the branches A6 left uncovered it
   is the one whose trigger is a real state of the world rather than engine misuse.

5. **"Modeled children" has two definitions.** `descriptors._modeled_children` derives the set from
   the walk's path table; `capture._modeled_under` derives it from `approved.paths`. They agree
   today. Nothing forces them to, and A7 is the first stage to act on `has_unmodeled_child` rather
   than merely record it.

6. **`EBADF` is translated at lookup sites only.** `translated_lookup` honors §9.1's table, and every
   lookup wraps it; the streaming read/write loops and `build_relation` do not, so an `EBADF` from
   those propagates as `OSError`. §9.1 now says so. Either the loops wrap or the table's row narrows
   — A7 touches both kinds of call and is the right place to settle it.

One item is not A6's and is noted only so it is not rediscovered: `coordinator_on`'s docstring says
two calls model two projects, and they share one `ext4_project_root`. It predates A6.

## 2026-08-14 per-kind observation amendment

Directory observation remains role-blind but now falls back on determinate `EACCES` to a guarded
`O_PATH` directory handle, reporting occupancy as `None`. FIFO/socket/device nodes report
`ObservedUnrecognized`; lookup/open/readlink contention reports `ObservedContended`; and `EACCES`
from child lookup, file open, readlink, or the failed handle fallback reports `ObservedInaccessible`.
