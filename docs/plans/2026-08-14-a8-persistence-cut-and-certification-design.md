# A8 — persistence-cut model, synthetic exerciser, and durability certification

**Status:** Designed 2026-08-14; unimplemented. Owner of deferred-obligation ledger
entry #15 and of the empty-`CERTIFIED_ALLOWLIST` refusal. Implements authority
§12.1 (the synthetic exerciser), §13.2 (the persistence-cut model and the
crash-certification method), §13.4 (end-to-end recovery), and §14 Plan A items 6–7.
A9 (macOS) follows.

## 1. Decision

A8 is verification, not engine change: no transaction semantics move, no new
transaction entry point is added, and every mutation the new code drives goes
through the public commands or the existing guarded lease. Four artifacts land:

1. **The persistence-cut model** — a deterministic record–reconstruct–recover
   harness in `python/tests/`. One recorded run per scenario yields, offline, every
   cut of the durability stream and every representable survivor subset at each
   cut; each reconstructed world runs real recovery and is judged by A3.
2. **The synthetic exerciser** — a data-declared scenario library in
   `python/tests/`, the §12.1 vertical-slice consumer, shaped to the documented
   deferred-consumer shapes (corpus write, archive/import move) without importing
   any real consumer. It is also the certification harness's in-guest workload.
3. **The end-to-end and A3 agreement matrix** — scenario × interruption mechanism
   × first recovery execution, with mandatory second-pass verification, extending
   today's two-cell `test_coordinator_conformance.py` agreement into ledger #15's
   three legs.
4. **Durability certification** — a QEMU + dm-log-writes harness in
   `python/tools/certify/`, one real run certifying one exact
   `(VolumeConfiguration, StorageProfile)` pair, a canonical JSON certification
   record under `docs/certification/`, and `CERTIFIED_ALLOWLIST` populated with
   the single entry that names it.

One deliberate boundary refinement rides along (§7.4): `build_configuration`'s
`durability_features` stops collapsing every ext4 feature set into `()` and gains
a minimal superblock-feature resolver for the barrier-relevant vector
(`fast_commit` and `orphan_file` at minimum). This refines what the verification
boundary can distinguish; it changes no transaction semantics.

## 2. Scope and non-scope

**Scope:** the four artifacts above; the feature resolver; the allowlist
population and the four assertion updates it requires (§8); the landing status
moves (§10); the named §9.4/§9.5 tuple tests, including the §9.5
identity-injection ruling (§4.5); instrument self-verification, including one
sabotage arm per §13.2 cross-substrate ordering (§9).

**Non-scope, each with its owner:**

- The macOS backend and its suites — **A9**.
- Terminal-record GC — the structural gate exists (A7); the command still does not.
- Science's L-row tests and every Plan B adoption item — Plan B, after A8.
- Chain compaction and size — a future design under the log design's anchor rules.
- Any weakening of fail-closed binding. A kernel upgrade or `BACKEND_REVISION`
  bump de-certifies by design; the mitigation is a one-command recertification,
  not a looser match. No recertification ledger entry is added — exact matching
  already makes staleness fail closed, and this document plus the certification
  record carry the command.

## 3. Seam review against the deferred-obligation ledger

| # | Required behavior (A8's half) | Mechanism |
| --- | --- | --- |
| 9 | Every A5–A8 transaction entry point accepts `ProjectApprovedSpec`, never raw specs | A8 adds **no** production entry point: the exerciser drives the public commands, and every matrix cell enters recovery through the existing guarded `_recovery_lease`. The existing architecture tests continue to carry the proof; the A8 clause closes by construction |
| 15 | Run real-filesystem, subprocess, and persistence-cut cases against A3 decisions; observed terminal states and second-pass behavior match A3's fixed points, including token-free halt-diagnostic equality after restart with regenerated snapshot-local identity tokens | The §5 agreement matrix: every cell compares the executor's durable projection against `apply_recovery_plan`'s fixed point; the subprocess arm restarts in a fresh process and compares the persisted diagnostic exactly, token-free, with identity-bearing observations compared up to alpha-renaming (`recovery_support`'s existing machinery) |

Both entries are removed only after their named verification passes on the
landed implementation; this document discharges nothing.

## 4. The persistence-cut model

### 4.1 Event stream and durability units

A recording backend implements the `Backend` protocol and sits beneath the
audited facade — the same seam `KillingBackend` occupies — delegating every call
to the real Linux backend. Recording is **success-only**: operands are captured
before the delegated call, resulting identities (`os.fstat`, a read) after it,
and a failed delegated call appends nothing.

Each successful mutation decomposes into **durability units** keyed by
durability location:

- **Directory-entry units**, keyed `(directory inode, name)`: insert, remove, or
  replace of one entry, carrying the object inode. A rename is two units —
  `remove(source dir, source name)` + `insert(destination dir, destination
  name)`; `exchange` is two replaces; `link_anchor` is an insert naming an
  existing inode; `unlink`/`rmdir` are removes.
- **Inode-data units**, keyed by inode: pending file content. `write` calls
  **coalesce** per inode — a later pending write extends or replaces the pending
  data at the same key, so payload streaming never inflates the pending set.
- **Inode-metadata units**, keyed by inode: mode (and the metadata-root marker
  xattr, treated identically).

A later pending update **replaces** the earlier pending update at the same key.
The survivor enumeration of §4.3 therefore ranges over keys, never raw events.

Inode identity is captured from the real filesystem at record time, so hard-link
relations (a move's anchor and destination; §9.5's descriptor identity) are
preserved in the model as *relations*, independent of the inode numbers any
later reconstruction assigns.

The stream is **seeded**: event 0 is the initial filesystem world (project and
metadata trees) plus an initial SQLite backup. Store COMMITs enter the same
stream through a wrapper on `Store.transaction` sharing one sequencer with the
recording backend: each successful exit appends an atomic-durable COMMIT event
and a fresh backup taken with the SQLite backup API. SQLite's internal atomicity
is out of scope exactly as §13.2 rules — the database's own file I/O is opaque
to the model, represented solely by the COMMIT snapshots, while the database
*files'* directory entries (bootstrap creation) are ordinary entry units.

### 4.2 Barrier coverage

Barriers cover exactly what POSIX promises and nothing more:

- `flush_file(fd)` makes the covered inode's pending data **and** pending
  metadata durable.
- `flush_directory(fd)` makes the covered directory's pending child-entry units
  durable **and** that directory inode's own pending metadata.
- A file fsync does **not** persist the file's directory entry.
- A store COMMIT is atomic and durable at its stream position.

This is deliberately weaker than ext4's journal ordering. The adversary may be
stronger than any real filesystem because the cell oracle is A3 itself (§5), not
a hand-written expectation of what "should" survive.

### 4.3 Cuts, survivor subsets, and representability

A **cut** is an index into the recorded stream. At each cut the surviving world
is the durable state (all units covered by barriers before the cut, plus all
COMMIT snapshots up to the cut) extended by a **survivor subset** of the pending
units. Enumeration is the full powerset over pending keys, applied in stream
order, with three rules:

- **Structural applicability.** A unit is applicable iff its target exists in
  the durable base or through a preceding included unit — a remove of an entry
  that is durably present applies (this is exactly how §9.4's anchor-only tuple
  arises); a remove of an entry whose insert was never durable and is not
  included does not.
- **Skips are counted by reason**, never silent, and the sweep asserts that each
  named required tuple — §9.4's dual-name and anchor-only, forward and reverse —
  was *generated*, not skipped. The §9.5 same-inode tuple is asserted at its
  named injected test instead (§4.5).
- **The pending-set size is capped with a loud failure**, not sampling. The
  engine's barrier discipline keeps pending sets small; a cap breach means the
  model or the engine changed, and the matrix must say so.

### 4.4 Reconstruction

Each cell's surviving world is replayed into fresh `project_root` and
`metadata_root` directories on the real filesystem: model inodes become fresh
files, link relations are reproduced by `link`, directory modes applied, and the
cut's database snapshot placed as the metadata store. Reconstruction is exact up
to inode renaming, which is safe because nothing durable stores inode numbers —
observation identity tokens are snapshot-local and halt diagnostics are
token-free — and the §5 comparison is already up-to-renaming for
identity-bearing values.

### 4.5 The named intermediates, and the §9.5 ruling

§9.4's four tuples (dual-name and anchor-only, forward and reverse) are
host-reconstructible — file hard links are legal — and are swept physically:
each is asserted generated by §4.3 and additionally carries a named directed
test asserting the designed repair reaches the pre-state.

§9.5's same-inode intermediate — the live parent durably holding the published
directory while the `work/` staging name's removal is not yet durable, both
names one directory inode — is **not host-reconstructible, and the design
records why stronger is unavailable**:

1. No in-process cut can materialize it: after `transfer_noclobber` returns, the
   `work/` name is already gone in-process. The state exists only as a
   durability intermediate.
2. A fresh-world reconstruction cannot produce it: Linux forbids hard-linking
   directories.
3. The certification sweep cannot be *required* to produce it: ext4 journals a
   rename as one atomic journal transaction, so no completion-ordered replay
   prefix splits the two directory updates. The tuple belongs to the engine's
   deliberately weaker filesystem contract, and a required certification case
   that the certified filesystem cannot generate would be vacuous.

Coverage is therefore a **named identity-injected executor test at the
observation seam**: the physical world holds a live published directory and a
`work/` survivor as distinct inodes, and the injected observation reports the
identical identity §9.5 describes; the test asserts recovery removes the stale
`work/` name and treats the effect as landed, never misreading it as a blocker.
One supporting fact makes the injected shape faithful: the tuple can only
coexist with an **empty** published directory — the `work/` flush is durable
before `DONE`, and no descendant reaches `STARTED` before its parent's `DONE` —
so recovery's `rmdir` of the stale name cannot face a non-empty directory, and
the injected world models exactly the reachable state. If a certification
replay ever surfaces the physical tuple, the harness logs and verifies it as a
bonus; it is never required.

## 5. Recovery execution and the A3 agreement matrix

Every reconstructed cell runs recovery through the real composition path — a
fresh lease entry under the test allowlist — and the oracle is A3:

- The executor's snapshot and plan are captured with the `classify_recovery` spy
  idiom; `apply_recovery_plan`'s fixed point is computed independently; and the
  **durable projections** are compared: `(state, committed, rollback_result,
  halt_diagnostic, journals, active)` plus the world tree. Cells where A3 plans
  assert convergence to the fixed point; cells where A3 halts assert the halt
  agrees and is preserved, never reclassified.
- **Second-pass verification is mandatory after every cell**, not a recovery
  mode: the second resolution must produce an identical canonical durable
  projection and child-visible output — not identical physical SQLite bytes —
  and an unchanged world.
- The **first recovery execution** axis is `in-process | fresh subprocess`
  (`coordinator_child`). The subprocess arm compares the persisted token-free
  halt diagnostic **exactly**; identity-bearing observations compare up to
  alpha-renaming with `recovery_support`'s existing machinery.
- Chain registration/settlement, scratch and workspace reclamation, and
  unindexed-blob accounting are asserted by **separate end-to-end checks** per
  cell family, because A3 deliberately does not model them.

## 6. The synthetic exerciser and the end-to-end matrix

A data-declared scenario library (the `n2_arms` shape): each scenario names its
spec builder, initial world, optional external-drift mutation, and an expected
classification family — a **coverage assertion only**; A3 remains the cell
oracle. Coverage: each effect variant minimal; compound specs (ancestor
`CreateDirectory` chains, move + replace + delete in one transaction); repeated
paths; caught rollback; external drift at destructive boundaries. Scenario
surfaces are shaped to the documented consumer shapes — corpus write,
archive/import move — per §12.1, importing no consumer.

The §13.4 matrix is **scenario × mechanism × first recovery execution**, with
§5's second pass mandatory everywhere:

- mechanism: `clean commit | caught rollback | SIGKILL at rehearsed barriers |
  persistence cut`. Caught rollback injects failure at the effect-`apply` seam
  **after `PREPARED`** — the existing conformance idiom — because a failing
  payload occurs during capture, before a durable transaction exists, and
  exercises refusal rather than rollback. The SIGKILL arm extends the existing
  kill-matrix rehearsal idiom to exerciser scenarios rather than duplicating it.
- **Capability-refusal scenarios sit outside this Cartesian product.** They are
  exerciser cases proving `CapabilityUnavailable` lands **before any
  transaction-record metadata or project mutation** — a refusal has no recovery
  row to classify.

The same scenario library is the certification harness's in-guest workload: one
library, two consumers.

## 7. Durability certification

### 7.1 Harness architecture

`python/tools/certify/` — repo tooling, never collected by pytest. The host
driver:

1. **Fails early on prerequisites**: `qemu-system-x86_64`, a readable host
   kernel image, the `dm-log-writes` module, `dmsetup`, ext4 userspace tools,
   the pinned `replay-log` build (below), and a clean atoms checkout. On this
   host today, qemu and `replay-log` are absent and everything else is present;
   the driver reports exactly what is missing.
2. Boots QEMU by **direct kernel boot** of the host's own kernel with a
   generated initramfs and a read-only 9p root sharing the host filesystem, so
   the guest runs the host's kernel, Python, and atoms checkout. 9p supplies
   files, not identity: the in-guest driver **verifies** `uname -r`, the atoms
   `BACKEND_REVISION`, the Python executable and version, and the checkout
   commit (clean) before any workload, and the run refuses on any mismatch.
3. Attaches **two raw images** — data and log — because dm-log-writes requires
   separate devices. In-guest (root is free there), the driver stacks
   dm-log-writes over the data device, formats ext4 **with an explicit feature
   set matching the production volume's resolved vector** (e2fsprogs 1.47
   defaults would otherwise enable `orphan_file` silently), mounts with
   production-equivalent options, and runs the exerciser scenarios through the
   real composition path while the log records every bio in completion order
   with its FLUSH/FUA marks.
4. **Replays, never crashes**: for each scenario, the log is replayed with the
   xfstests `replay-log` tool, built from a pinned commit. Every replay target
   is a **fresh baseline clone** of the pre-workload data image — replaying
   successive prefixes onto one already-mutated image would retain later blocks
   and invalidate the cut. Each clone is **never-before-mounted**, receives no
   repairing `e2fsck`, and is mounted in a fresh recovery process — a fresh
   guest boot by default; a same-boot fresh process is acceptable only where the
   plan records why cached state cannot leak into the verdict — so mount-time
   journal replay and engine recovery see the cut directly. Recovery outcomes are verified against the A3 oracle exactly as
   in §5.

### 7.2 Coverage semantics

Certification coverage is **every prefix of the observed completion-ordered bio
trace** — every FLUSH/FUA mark and every intermediate bio prefix — not every
physically possible power-loss ordering. The arbitrary-survivor-subset adversary
remains the in-process model's responsibility (§4); the certification run tests
what that model cannot: real ext4, the real kernel, and real barrier semantics
under power-loss-equivalent prefixes. Scenario workloads are small enough for
exhaustive prefixes; any cap must be declared in the record, never silent.

### 7.3 What a run certifies

One exact `(VolumeConfiguration, StorageProfile)` pair. The tuple embedded in
the record is **produced by `build_configuration` in-guest**, never hand-typed,
so exact-equality matching against production holds by construction. The
`StorageProfile` id minted here is **`flush-honoring-disk.v1`**, naming the
tested assumption: a fixed disk that honors flush ordering. A production
composition root declaring that profile is making a documented trust assertion
about its hardware; the harness proves nothing about drive firmware, and the
record says so. A guest run does not certify this host's bare-metal NVMe tuple
by side effect — it certifies the named pair.

### 7.4 The ext4 feature resolver

`build_configuration` currently hard-codes `durability_features = ()`, under the
rule that an entry may not name a feature whose resolver does not exist. That
empty vector collapses every ext4 feature set into one tuple, while `fast_commit`
changes the journal and replay path — including directory-entry operations — and
`orphan_file` changes orphan processing. One image cannot certify every
configuration the empty tuple would match, so A8 adds the **minimal resolver**:

- The resolved vector carries at least `fast_commit` and `orphan_file`, each as
  an explicit present/absent marker, so absence is stated rather than implied.
- The resolver is **unprivileged and two-sided**, reading the kernel's
  mounted-volume interfaces (`/proc/fs/ext4/<device>/`, `/sys/fs/ext4/<device>/`).
  The exact indicators are pinned at implementation by an in-guest experiment —
  mkfs with and without each feature, then compare the kernel views — because
  the host offers no unprivileged way to create the enabled case
  (`/proc/fs/ext4/<device>/fc_info` exists with identical content on a
  non-fast-commit volume, so presence alone is not the indicator).
- A volume whose vector the interfaces cannot decide is **refused** with
  `CapabilityUnavailable` — fail closed, like every other capability. If the
  pinning experiment finds no two-sided unprivileged indicator for a required
  feature, that is a design amendment to bring back here, not a silent guess.

This is verification-boundary refinement: binding decisions become *finer*,
never looser, and no transaction semantics change.

### 7.5 The certification record and allowlist population

The run emits **one canonical JSON record** to `docs/certification/` — directly
machine-checkable, with this document supplying the prose. The record carries:
the in-guest `VolumeConfiguration` (every field, including the resolved feature
vector), the `StorageProfile` id, the QEMU command line and cache mode, the mkfs
and mount commands, the QEMU/e2fsprogs/`replay-log` versions and the replay-log
format and pinned tool commit, the kernel identifier, the clean atoms commit,
per-scenario mark and prefix counts, the zero-violation assertion, and the date.

`CERTIFIED_ALLOWLIST` gains the single entry whose `certification_ref` names the
record. Four assertions that pin emptiness today become deliberate-population
assertions — exactly one entry, the ref resolves to an existing record file, and
the record's embedded tuple equals the code entry:
`test_fs_architecture.py::test_certified_allowlist_is_empty_so_population_is_deliberate`,
the emptiness assertion closing
`test_fs_architecture.py::test_the_production_bind_call_passes_the_certified_allowlist`,
`test_fs_volume.py::test_certified_allowlist_ships_empty`,
and `test_fs_binding.py::test_certified_allowlist_is_the_empty_production_constant`.
Superseded records are retained, dated; recertification after a kernel or
backend-revision change is one command, documented in the record and here.

## 8. Suite layout and architecture-guard conformance

- Non-collected helpers `tests/persistence_model.py` (units, recording backend,
  reconstruction) and `tests/exerciser.py` (scenario library); suites
  `test_persistence_cut_matrix.py` (the §4–§5 sweep, the named §9.4 tuple tests,
  and the §9.5 injected test) with conformance and kill-matrix extensions in
  their existing files. All new fixtures live in `conftest.py`, per the
  fixture-registry guards.
- The recording backend delegates to the injected inner backend and issues no
  raw `os` mutation (identity capture is `os.fstat`, a read). Reconstruction
  builds worlds in test code, outside the src-scoped mutation guards' reach.
- The matrix is deterministic. If measured cell counts demand cost tiering, that
  is a plan-time decision made with numbers and an explicit marker — no silent
  cap.
- **Certification stays manual and non-collected.** Normal CI validates the
  banked record and the allowlist correspondence (§7.5's assertions) without
  rerunning certification.

## 9. Verifying the instruments themselves

A check must be able to fail; the model and the harness are instruments and get
their own falsification arms:

- **Reconstruction fidelity self-check**: per recorded scenario, the cut at the
  end of the stream with every pending unit surviving must reconstruct a world
  equal to the live final world of the recorded run.
- **Replay verification**: a directed in-guest test writes a known pattern with
  explicit flushes, cuts, replays, and compares — proving the pinned
  `replay-log` build and the clone discipline before any certification claim.
- **Sabotage arms, one per §13.2 cross-substrate ordering**: with the
  corresponding barrier suppressed (blob-flush before `PREPARED`; the per-effect
  `STARTED`/mutation/`DONE` ordering; the move's destination-before-source
  parent flush; the `CreateDirectory` live-parent-before-`work/` flush; the
  `COMMITTED` decision), the matrix must produce at least one cell whose outcome
  differs from the unsabotaged run — proving each ordering is load-bearing and
  the model can see its loss.
- **Skip accounting**: the §4.3 by-reason skip counts and required-tuple
  generation assertions run on every sweep.

## 10. Landing, status moves, and acceptance criteria

Landing edits, enforced by `test_docs_status.py` in the same change:

- `FIRST_UNIMPLEMENTED` moves to `"A9"`; `AGENTS.md`, `README.md`, and the
  authority header gain A8; **every live status field is swept** — several
  pre-A7 design headers still describe A8 as unimplemented and are corrected in
  the landing commit, as are the A7 design's "A8 remains unimplemented"
  sentences.
- Ledger #15 and #9's A8 clause are removed **only after their named
  verification passes** (#9 closes because A8 adds no production entry point;
  #15 through the three agreement legs).
- Science's adoption-ledger row 4 is updated in the science repository as its
  own commit after landing; nothing here edits science.

**Acceptance criteria.** A8 is complete when:

1. The persistence-cut sweep runs every host-reconstructible survivor of every
   exerciser scenario through real-filesystem recovery with A3 agreement, the
   four named §9.4 tuples generated and repaired, and the §9.5 injected test
   passing.
2. The §13.4 matrix covers scenario × mechanism × first-execution with mandatory
   second-pass verification, and capability refusals proven outside the product,
   before any metadata or project mutation.
3. All five sabotage arms flip at least one cell; the fidelity self-check and
   skip accounting pass on every sweep.
4. The feature resolver decides `fast_commit` and `orphan_file` two-sided and
   unprivileged, refusing undecidable volumes.
5. One certification run has produced the JSON record, `CERTIFIED_ALLOWLIST`
   carries exactly the entry naming it, the four population assertions pass, and
   production binding accepts the certified volume and still refuses every
   other.
6. `uv run pytest`, `ruff check`, and `pyright` are green; `FIRST_UNIMPLEMENTED`
   is `"A9"`.

## 11. Out of scope and future work

The macOS arm (A9) reuses the model unchanged — the recording seam and unit
vocabulary are platform-neutral — and adds its own backend, probe results, and
certification tuples. A Windows arm remains excluded by the authority design.
Chain compaction, terminal-record GC, and every Plan B item proceed on their own
clocks. The certification harness certifies tuples one at a time by design;
certifying additional filesystems (xfs, btrfs) is future work that extends the
per-filesystem barrier-option table and the feature resolver the same way, never
widening a certified entry.
