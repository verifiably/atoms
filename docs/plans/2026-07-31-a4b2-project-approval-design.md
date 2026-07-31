# A4b-2 — rooted project approval

**Status:** Implemented on 2026-07-31. A5–A8 remain unimplemented. A4b-2 reads project space
and never writes to it.

**Authority:** [`2026-07-23-recoverable-fs-effect-engine-design.md`](2026-07-23-recoverable-fs-effect-engine-design.md).
Where this document and the authority design disagree, the authority wins.

## 1. Decision

A4b-2 delivers the judgment that A4b-1's mechanism was built to serve. It consumes A2's `CompiledSpec`
and one held project context, proves the nine rooted rules authority §5.4 assigns to A4, and issues the
single `ProjectApprovedSpec` that A5–A8 accept in place of a raw `TransactionSpec` or a raw
`CompiledSpec`.

Where A4b-1 answers *what does this path resolve to right now*, A4b-2 answers *may this specification
run against this project*. It resolves every declared path once, then reasons about the resulting table
without touching the filesystem again. The separation is deliberate and load-bearing: the judgment is
pure, so every obligation except the two that are definitionally about real limits is provable at unit
speed against synthetic resolution tables.

Its public result is a frozen, factory-issued `ProjectApprovedSpec` composing the exact `CompiledSpec`
it approved, the live `ProjectBinding` it approved against, A3's production `RecoveryTopology`, and the
approved baselines a later stage must re-observe and compare against — per-node directory facts and the
observed `metadata_root/work` facts.

## 2. Scope and non-scope

### 2.1 In scope

- `approve_for_project`, `ProjectContext`, and `ProjectApprovedSpec` with its factory guard (the
  completed factory half of ledger #9).
- Resolution of every declared persistent path and every scratch parent through A4b-1 (ledger #4, #5).
- Endpoint distinctness under each parent's actual lookup policy, covering `ABSENT`-declared paths
  (ledger #2).
- Ancestor legality: every unresolved component is a directory this transaction creates, ordered before
  the effects beneath it — including the ancestor that exists as a file or symlink and is converted
  (A4's part of ledger #3).
- Scratch-leaf instantiation and pairwise distinctness per concrete parent (ledger #11).
- Required-capability adjudication against the bound volume's supplied set (ledger #6).
- Construction of A3's production `RecoveryTopology` and the re-run of surface consistency and
  created-directory ordering over its resolved nodes (ledger #10).
- Retention of the live binding in the proof (ledger #16), and of the approved baselines a later stage
  compares against: per-node directory facts and the observed `metadata_root/work` facts.
- `lookup_equivalence_key`, the pure per-policy name-equivalence function both endpoint distinctness
  and planned-node assignment use. It is added to A4b-1's `lookup.py`, which judges no specification.
- Categorical refusal propagation from A4b-1 (ledger #20).

### 2.2 Not in scope

- Any write to project space, and any write at all. Approval is read-only.
- Scratch-path occupancy. A4b-2 proves the generated names are intrinsically distinct; whether one is
  already occupied by external state is ledger #7, owned by A5.
- Comparing live state against declared preconditions. Approval deliberately does not capture; that is
  A6's, and §6.4 states why conflating them would be a defect.
- Creating `metadata_root/work/<txid>/`. A4b-2 derives its constraints and emits the logical edge; A5's
  preparation creates it.
- Re-resolution after approval, and the drift window it closes (ledger #19).
- Transaction-identifier generation, and the durable transaction record.
- Support for ext4 casefold directories, XFS `ascii-ci`, or Btrfs. A4b-1 refuses them and A4b-2
  inherits that floor unchanged.

### 2.3 Relationship to A4b-1 and A5

A4b-1 supplies observation; A4b-2 supplies judgment. The seam is enforced rather than documented:
`test_resolution_modules_judge_no_specification` forbids `resolve.py` and `lookup.py` from importing
`atoms.core.compiler`, `atoms.core.spec`, or `atoms.core.recovery`. A4b-2's modules import all three.
The guard's module list stays exactly `["resolve", "lookup"]`, which is what makes it a seam rather
than a lint.

Toward A5, the proof is the whole interface. A5 receives a `ProjectApprovedSpec` and may not
reconstruct any part of it. Ledger #19 constrains what A5 may *do* with the retained facts: compare,
never authorize. Ledger #9 remains open until the future A5–A8 entry points enforce that interface;
A4b can prove only the factory half and that no consumer exists yet.

## 3. Seam review against the deferred-obligation ledger

`AGENTS.md` requires this review before the plan is written.

### 3.1 Existing entries

A4b-2 completes its part of ten entries. #3 remains open for A6 and #9 remains open for the future
A5–A8 entry points; each completed part is recorded only when the verification tier named beside it
lands.

| # | What discharges it | Tier |
| --- | --- | --- |
| 2 | Endpoint distinctness keyed by (resolved parent node, exact leaf bytes), covering `ABSENT`-declared paths, before any capture or mutation | §11.1, §11.4 |
| 3 | **A4's part only.** An ancestor that exists as a file or symlink is admitted iff the timeline removes it — by `DeletePath` or a `MoveNoClobber` source — then creates a directory before every descendant effect. The entry stays open against A6 | §11.1, §11.4 |
| 4 | `PathResolver.resolve` enforces `PATH_MAX` and every component it reaches; pure judgment validates every derived component after the first frontier and every final leaf against its actual or inherited parent's `NAME_MAX`; generated scratch leaves are checked against their concrete parents too | §11.1, §11.4 |
| 5 | The same resolution enforces containment, metadata-root exclusion by `st_dev`/`st_ino`, and mount membership | §11.4 |
| 6 | `compiled.spec.required_capabilities() ⊆ binding.evidence.supplied_capabilities`, refused before any traversal and long before any record write | §11.1, §11.4 |
| 9 | **A4's factory half only.** Frozen, token-guarded `ProjectApprovedSpec`; ordinary construction and `dataclasses.replace` both refuse. The entry stays open until A5–A8 accept only this proof | §11.6 |
| 10 | Topology built over resolved identity; surface consistency and created-directory ordering re-derived from its nodes, never from A2's verdict | §11.2, §11.3, §11.4 |
| 11 | The complete `.#~<txid>.<effect-id>.<role>` set instantiated and proved pairwise distinct under each concrete parent's actual policy | §11.1, §11.4 |
| 16 | The proof retains the live `ProjectBinding`; an architecture test asserts its presence | §11.6 |
| 20 | `approve_for_project` catches nothing A4b-1 raises, asserted by AST guard rather than by convention | §11.5, §11.6 |

Nothing here discharges an entry owned by another sub-plan. #7 stays with A5, #13 with A6 and A7, #19
with A5–A7.

### 3.2 New entries this design creates

One entry, #21, is added to the ledger in the same commit as this design.

| # | Admitted shape | Admitted by | First owner | Required behavior |
| --- | --- | --- | --- | --- |
| 21 | `ProjectApprovedSpec` binds to one caller-supplied txid, and nothing in A4b-2 prevents a consumer from executing it under a different one | A4b-2 approval contract | A5 | A5 refuses to execute when the txid it holds differs from the proof's. Regenerating a txid under ledger #7 voids the approval: A5 must call `approve_for_project` again and use the fresh proof. Never substitute regenerated scratch names into an existing proof |

The txid seam is real and not merely theoretical. Ledger #7 explicitly permits A5 to regenerate the
txid when a bound scratch path is externally occupied. Every approved scratch leaf embeds the txid, so
a regenerated one silently invalidates the whole instantiated set — the exact class of confidently
wrong approval this layer exists to prevent. Making the invalidation structural, by binding the proof
to the txid, converts a rule someone must remember into a value someone must pass.

A fail-closed platform is **not** an admitted shape. A4b-2 inherits A4b-1's refusal of casefold, XFS,
and Btrfs; that admits nothing and §2.2 records it as non-scope.

### 3.3 Delivery obligations that are not ledger entries

- **Authority §11 gains approval-time drift as a `PreconditionRefused` case.** A4b-1 raises that
  exception for an incoherent walk or changed constraints, and A4b-2 propagates it under #20. Pure
  topology construction raises the same type when separate resolutions disagree about one lexical
  prefix. §11 as written defines it only for drift detected "at capture or by
  validating an atomically displaced entry", and conditions its return on the current effect and every
  earlier one having been restored — none of which describes approval, which holds no transaction
  record and has mutated nothing. The exception *name* was already declared, which is why A4b-1's
  review did not catch this; its *meaning* did not cover the case. The amendment lands in the same
  commit as this design.
- `ProjectContext`'s concrete fields need no amendment: §5.4 already assigns them to A4's reviewed plan.
- `AGENTS.md` and both A4b-2 status paragraphs say implemented. The synchronized-status architecture
  test keeps all three claims honest.

## 4. Architecture and ownership

### 4.1 Module layout

Three new modules under `~/d/atoms/python/src/atoms/fs/`:

| Module | Holds | Sees a filesystem |
| --- | --- | --- |
| `approval.py` | `ProjectContext`, `ProjectApprovedSpec`, the construction token, `approve_for_project` | Yes — phase B only |
| `topology.py` | Node assignment, edge construction, the re-run of surface and ordering rules | No |
| `judgment.py` | Ancestor legality, endpoint distinctness, scratch instantiation and distinctness | No |

One addition to an existing module: `lookup_equivalence_key` joins `lookup.py`, beside
`read_lookup_constraints` and `inherited_constraints`. It belongs with the other per-policy functions
rather than in `judgment.py`, and it introduces no specification dependency, so §2.3's seam guard is
unaffected.

`topology.py` and `judgment.py` are pure: they consume a resolution table and produce values or raise.
Neither imports `atoms.fs.resolve` for anything but its types. This is what makes §11.1 the largest
tier and the real-volume tier the smallest.

`ProjectApprovedSpec` lives in `atoms/fs/` and not `atoms/core/` because it retains a live
`ProjectBinding`, and `test_core_never_imports_the_filesystem_layer` forbids the reverse direction.
`CompiledSpec` staying in `core` while the proof composing it lives in `fs` is the layering working as
intended, not a wart: A2's proof is filesystem-independent and A4's is not.

### 4.2 Dependency direction

```
atoms.core.compiler ─┐
atoms.core.spec ─────┼──> atoms.fs.approval ──> atoms.fs.resolve ──> atoms.fs.binding
atoms.core.recovery ─┘         │                atoms.fs.lookup
                               ├──> atoms.fs.topology
                               └──> atoms.fs.judgment
```

`resolve.py` and `lookup.py` sit below the specification-aware modules and must never import them or
anything they import from `core`. The existing architecture guard enforces exactly that.

## 5. Inputs

### 5.1 `ProjectContext`

```python
@dataclass(frozen=True, slots=True)
class ProjectContext:
    binding: ProjectBinding
    txid: str
```

Authority §5.4 fixes the parameter name and requires the context to represent the held project-root and
metadata-root identities and the selected capability backend. `ProjectBinding` already carries all
three — `project_root_fd`, `evidence.metadata_root_device`/`_inode`, and `backend` — so the context adds
exactly one thing: the transaction identifier that every scratch leaf embeds.

It is an ordinary frozen dataclass with no construction token. It is an *input*, not a proof; guarding
its construction would protect nothing, since a caller who can build one can call `approve_for_project`
anyway.

The txid is validated at the approval boundary rather than in the constructor, and a malformed value
becomes `ProtocolError` with the underlying `SpecValidationError` as its `__cause__`. This follows
`PathResolver.resolve`'s existing precedent for `require_rel_path`. The engine supplies the txid, so a
malformed one is a violated internal contract, not consumer error — and validating it at the boundary
means the failure names the txid rather than surfacing from inside `scratch_leaf` on the twentieth
effect.

### 5.2 What approval does not check

Approval proves the specification *may* run. It does not prove it *will* succeed:

- It does not compare any live entry against a declared precondition, **with one bounded exception**:
  a *blocking* ancestor, where resolution stopped because the entry is not a directory. There approval
  has no choice — the topology cannot be built without deciding whether that path becomes a directory,
  and the observation is already in hand — so §6.3.1 requires the timeline to convert it. Everywhere
  else, A6's capture does the comparing, against a coherent descriptor, and §6 of the authority is
  explicit that capture-time verification is not compare-and-swap authority. A general approval-time
  precondition check would be a third observation of the same entry, weaker than capture's and stale
  by the time capture runs.
- It does not check scratch-path vacancy (ledger #7).
- It does not check free space, permissions, or quota.

Stating this is not pedantry. The single most likely way to get this layer wrong is to treat the
frontier observations approval already has as a cheap head start on capture, which would produce a
proof that appears to guarantee more than it does.

## 6. The approval pipeline

```python
def approve_for_project(
    compiled: CompiledSpec, context: ProjectContext
) -> ProjectApprovedSpec
```

Four phases, ordered so the cheapest refusal comes first.

### 6.1 Phase A — context, no path I/O

1. **Exact-type gates**, each raising `ProtocolError`, following A3's `_require_exact` convention in
   `snapshot.py`: `type(compiled) is CompiledSpec`, `type(context) is ProjectContext`,
   `type(context.binding) is ProjectBinding`, and `type(context.txid) is str`.
2. **Liveness**, before any other field of the context is read: `context.binding.backend`, whose
   property routes through `_require_active()` and issues no I/O.
3. `require_valid_identifier("txid", context.txid)`, translated to `ProtocolError` per §5.1.
4. Capability adjudication (ledger #6):
   `compiled.spec.required_capabilities() - context.binding.evidence.supplied_capabilities` must be
   empty, else `CapabilityUnavailable` naming the sorted missing capability values.
5. `PathResolver(context.binding)`, which refuses a non-Linux backend, a casefold project root, and a
   project root that *is* the metadata root.

**Why every type is gated exactly, not just `compiled`.** `ProjectContext` is freely constructible by
design (§5.1), so the only thing standing between a duck-typed binding and proof issuance is this
gate. A delegating object exposing `backend`, `project_root_fd`, and `evidence` would satisfy every
attribute access in `PathResolver` and reach phase D, producing a proof that retains something which
is not a live `ProjectBinding` — precisely what ledger #16 forbids, and an architecture test asserting
"the retained binding is present" would not catch it. The `str` gate on the txid is load-bearing for a
different reason: `require_valid_identifier` calls `re.Pattern.fullmatch`, which raises `TypeError` on
a non-string, so without this gate a non-string txid would escape as `TypeError` rather than the
`ProtocolError` §5.1 promises.

**Why liveness precedes evidence.** `ProjectBinding.evidence` is a detached value and its property
performs no liveness check — deliberately, and A4b-1 §6.1 records that as the reason evidence must not
be the first thing touched. Adjudicating capabilities against a closed binding's evidence would
therefore report `CapabilityUnavailable` for a specification whose real problem is that the lease is
gone, and would report it *successfully* for a specification that needs nothing missing. Reading
`backend` first makes the closed binding fail as `ProtocolError` regardless of what the capability sets
happen to contain.

Capability adjudication precedes every traversal deliberately. An unsupported capability makes each
subsequent path check pointless work, and the refusal is more actionable than the first path refusal
that happens to trip. The allowlist half of authority §5.4's capability bullet is already discharged
structurally: `bind_project_volume` refuses a volume whose configuration tuple is not on the supplied
durability allowlist, so no `ProjectBinding` exists for an unlisted volume.

### 6.2 Phase B — resolution, the only I/O

6. Resolve every path in `compiled.timelines`, in sorted order, into
   `dict[str, ResolvedPrefix]`. A2 phase 10 already proves that path set equals the declared surface
   exactly, so using `timelines` needs no separate coverage argument and matches the key A3's
   `_validate_persistent_coverage` uses.
7. If any effect is a `CreateDirectory`, call `resolver.work_base_facts()`, retain the result as
   `work_base`, check the concrete `<txid>` component against `work.name_max`, and derive
   `work/<txid>/`'s constraints through `inherited_constraints`. Skipped entirely otherwise, matching
   A4b-1's reason for making that method lazy: a specification with no `CreateDirectory` must not be
   refused by an unapprovable `work/`.

This phase discharges #5 and the `PATH_MAX`/walked-prefix part of #4. `resolve()` necessarily returns
at the first missing or blocking ancestor, so phase C validates the remaining derived components and
final leaf against the actual or inherited parent constraints before #4 is discharged.

Sorted order matters for diagnostics only, but it matters: an approval that refuses a different path on
each run because dictionary order shifted is much harder to act on.

### 6.3 Phase C — judgment, pure

**Construction runs first, and every judgment below is keyed on the nodes it produces.** §7's topology
decides which directories exist and which of them are the *same* directory; it judges nothing. That
order is forced rather than stylistic: §5.4's motivating case is `CreateDirectory("A")` with an effect
on `a/x`, where the creation is what supplies `a`'s ancestor, and a judgment keyed on path spelling
looks for a creator of the string `"a"`, finds none, and refuses a legal specification. A check cannot
ask "does this transaction create that directory" before something has decided which directories there
are.

The order within phase C is therefore:

1. Build the topology (§7).
2. Endpoint leaf limits and distinctness (§6.3.2). It comes before the rest so every later phase may
   key a map by declared path: once it has passed, no two declared paths name one entry.
3. Ancestor legality and derived-component limits (§6.3.1).
4. The resolved surface and ordering re-run (§7.4).
5. Scratch instantiation (§6.3.3).

The subsection numbering below is by topic, not by sequence.

#### 6.3.1 Ancestor legality

For each declared path, `ResolvedPrefix` reports how far resolution got. Three cases:

```
frontier is the leaf (remainder == ())        -> the parent chain exists; nothing to prove
frontier absent, remainder non-empty          -> missing-ancestor case
frontier present, remainder non-empty         -> existing-non-directory-ancestor case
```

In the second case, the missing directories are `frontier_name` followed by `remainder[:-1]`, each
relative to the deepest resolved hop. Every one of them must be a directory some `CreateDirectory`
effect in this transaction creates, and that effect's index must precede every effect touching a path
beneath it. A missing component no effect creates is `ProjectApprovalRefused` — authority §5.4's "a
parent that neither exists nor is created by the transaction cannot be captured."

Resolution has not looked up those components. Each is therefore checked here, in order, against its
actual or inherited parent node's `NAME_MAX`; endpoint distinctness performs the same byte-width check
for every final leaf. This closes the suffix left unvalidated when `resolve()` returns at the first
frontier.

**"Is a directory some `CreateDirectory` creates" is decided by node, not by spelling.** Each missing
prefix is mapped through §7.1's assignment to the node it names, and each `CreateDirectory` endpoint
through the same assignment; the check compares those nodes. Under a folding parent
`CreateDirectory("A")` and the missing prefix `a` are one node, so the creation is recognised —
which is the whole reason construction precedes judgment.

In the third case the frontier's `EntryKind` decides:

| Frontier kind | Verdict |
| --- | --- |
| `REGULAR_FILE`, `SYMLINK` | Admitted iff the timeline converts it — the path is declared, removed by `DeletePath` or as a `MoveNoClobber` source, and re-created as a directory, with the `CreateDirectory` ordered before every descendant effect |
| `OTHER` | Refused. No closed effect variant converts a socket, FIFO, or device node into a directory, so no admissible timeline reaches a directory there |
| `DIRECTORY` | Unreachable. `open_child_directory` succeeds on a directory, so resolution would not have stopped |

The `OTHER` case is why A4b-1 kept the kind distinguishable rather than collapsing every non-directory
into one bucket. `DIRECTORY` being unreachable is asserted rather than assumed, because it is a claim
about `openat2` behavior rather than about this module.

#### 6.3.2 Endpoint distinctness

Both this check and §7.1's planned-node assignment need one shared notion of "these two names reach the
same entry in this parent." A single pure function supplies it, added to `lookup.py` — which judges no
specification, so the seam guard in §2.3 still holds:

```python
def lookup_equivalence_key(constraints: DirectoryConstraints, name: str) -> str
```

For `EXACT_BYTES` it returns `name` unchanged. It raises `CapabilityUnavailable` for any other
`LookupProof`, matching `inherited_constraints`' treatment of an unapproved filesystem: a policy whose
relation the engine cannot reproduce has no equivalence key either, and inventing one would be the
silent fallback this engine refuses. When the floor widens, the new policy's key lands here and every
caller inherits it.

**No member of today's vocabulary is both insensitive and reproducible.** `LookupProof` holds exactly
`EXACT_BYTES` and `UNREPRODUCIBLE_CASEFOLD`, and the second raises. So the merging behavior this
function exists to enable — two spellings reaching one directory — has no production path today, and
cannot be exercised by choosing a different enum member. §11.2 states how the tests reach it anyway.
This is the honest form of the floor-agnosticism claim: the *call sites* are policy-derived now, and
the policy that makes them do something different does not exist yet.

Two declared paths name one entry iff they resolve to the same parent node and their leaves share a
`lookup_equivalence_key` under that parent's constraints. Under `EXACT_BYTES` that reduces to exact
byte equality, which is why the check is currently equivalent to comparing leaf bytes and is
nonetheless not written that way.

The check runs over all declared paths including those declared `ABSENT`, where identity comparison
does not apply and is therefore not what is being compared. Authority §5.4 gives the reason a
mutation-time no-clobber check is not a substitute: `create x`, `delete x`, `create y` can make every
no-clobber operation succeed while `x` and `y` aliasing leaves the declared final states unsatisfiable.

A collision is `ProjectApprovalRefused` naming both declared spellings and the shared parent.

#### 6.3.3 Scratch instantiation

For each effect, in effect order, instantiate
`scratch_leaf(txid, effect.effect_id, required_scratch_role(effect).value)` and bind it to a concrete
parent:

- `ScratchRole.WORK` — the derived `work/<txid>/` directory.
- Every other role — the resolved parent of the effect's persistent path, using `source` for
  `MoveNoClobber`. This is the placement A3's `_validate_topology` requires, so instantiation and
  topology construction cannot drift apart.

Then, per concrete parent, prove the bound leaves pairwise distinct under that parent's policy and each
within its `name_max`. A collision is `ProjectApprovalRefused` and txid regeneration is explicitly not
a remedy — ledger #11 says so, and the reason is that an intrinsic collision recurs under every txid.

Under `EXACT_BYTES` this follows from A2 phase 6's exact-string and portability-key uniqueness of
effect IDs, plus the fixed role per variant. It is nonetheless computed rather than asserted, because
the deduction depends on the approved policy and the policy floor is a platform fact that may widen.

### 6.4 Phase D — issue

The module-private token constructs `ProjectApprovedSpec`. Nothing between phase A and here writes to
project space, and no `except` clause encloses any resolver call.

## 7. The resolved topology

### 7.1 Nodes

Before assigning nodes, construction compares every repeated lexical prefix observation. Two directory
observations must carry the same identity and constraints; a directory and a leaf-frontier observation
are compatible only when the frontier is that same directory; repeated absent or blocking frontiers
must be equal. Any other pair raises `PreconditionRefused` before a fact can overwrite another. This is
pure cross-resolution drift detection, not an exception handler around A4b-1.

| Node | When |
| --- | --- |
| `ProjectRoot()` | Always |
| `WorkRoot()` | Iff some effect carries `ScratchRole.WORK`, i.e. some `CreateDirectory` exists |
| `PersistentNode(path)` | One per `compiled.timelines` entry |
| `ScratchNode(effect_id, role)` | One per effect, `role = required_scratch_role(effect)` |
| `TopologyDirectory(node_id)` | One per *undeclared* intermediate directory |

The **directory candidates** are every proper prefix of a declared path *plus every `CreateDirectory`
endpoint*. The second half is load-bearing: `A` is nobody's lexical prefix, so without it `A` is not a
directory in the key space at all and `a` has nothing to merge into — §5.4's case would be
unrepresentable rather than merely unexercised.

The directory node for a candidate is `ProjectRoot()` when the prefix is empty, that prefix's own
`PersistentNode` when the prefix is itself a declared path, and a `TopologyDirectory` otherwise. A
declared path that is also an intermediate directory of another declared path therefore appears once,
as its `PersistentNode` — A3's `_validate_topology` requires exact persistent coverage, and a second
node for the same directory would break it. Where a declared and an undeclared candidate share a key,
the declared one wins, so the assignment does not depend on iteration order.

Two *declared* candidates sharing one key is a directory-level endpoint collision; construction cannot
choose which `PersistentNode` the directory is, so it refuses with `ProjectApprovalRefused` rather than
deferring to §6.3.2, which handles the leaf-level case. A2 phase 4 already subsumes this shape — it
applies `portability_equivalence_key` to the whole path, so `CreateDirectory("A")` alongside
`CreateDirectory("a")` never compiles — making the branch a fail-closed guard against a wider floor
rather than a reachable refusal.

`node_id` values are assigned in first-encounter order over lexically sorted declared paths, so the
topology is reproducible across runs. The assignment *key* is the `FilesystemIdentity` for an existing
directory, and for a planned one it is
`(parent node, lookup_equivalence_key(parent.constraints, leaf))` — the same function §6.3.2 uses for
endpoint distinctness.

Keying planned directories by exact bytes would break the floor-agnosticism this scheme claims.
`CreateDirectory("A")` alongside an effect on `a/x` compiles under A2, whose phase 4 refuses only
whole-path aliases — `portability_equivalence_key` maps `A` to `a` but `A` and `a/x` to different keys,
so both survive. Under an insensitive parent those are one directory, which is exactly authority
§5.4's motivating example; an exact-bytes key would emit two nodes for it. Both halves of the key are
therefore policy-derived, and both are no-ops under today's `EXACT_BYTES`-only floor: widening the
floor changes `lookup_equivalence_key` and nothing here.

### 7.2 Edges

- Each `PersistentNode` and `TopologyDirectory` parents to its prefix's directory node.
- `WorkRoot` parents to `ProjectRoot`. A3 requires this logical edge while `work/<txid>/` sits
  physically under `metadata_root`; the two are not in conflict, and A4b-1 §6.6 records the same point.
- `WORK`-role scratch parents to `WorkRoot`.
- Every other scratch parents to the same node as its effect's persistent path — `source` for
  `MoveNoClobber`. `snapshot.py` enforces exactly this equality.

### 7.3 Two invariants

**Every `PersistentNode` acting as a parent is transaction-created.** No effect variant declares a
`DirectoryState` precondition: `DeletePath.pre` is a file or symlink, `ReplaceFile.pre` and
`MoveNoClobber.source_pre` are files, and `CreateFileNoClobber`, `CreateDirectory`, and a move
destination all take `ABSENT`. A live directory may nevertheless occupy a `CreateDirectory` endpoint;
that is a declared-precondition mismatch for A6 capture, not an approval refusal. Even when another
path traverses that live directory, its `PersistentNode` remains planned and carries constraints
inherited from its approved parent, never the live endpoint's observed constraints.

**Every `TopologyDirectory` exists at approval time.** An absent undeclared intermediate would need a
`CreateDirectory` naming it, which would make it declared.

Together these partition the fact table exactly: `ApprovedExistingDirectory` is precisely `ProjectRoot`
plus the `TopologyDirectory`s, and `ApprovedPlannedDirectory` is precisely `WorkRoot` plus the
`PersistentNode`s that serve as parents. §11.2 asserts the partition, which catches a
node-classification defect that no single case would surface.

### 7.4 The re-run

Both A2 rules are re-derived from resolved parentage. A2's verdict is never consulted.

- **Surface consistency** — for the initial and final surfaces, a declared path beneath a node whose
  declared state is a non-directory must itself be `ABSENT`.
- **Created-directory ordering** — for every occurrence at path *P*, each ancestor node created by a
  `CreateDirectory` carries a strictly earlier effect index.

Authority §5.4 is explicit that this may not reuse A2's lexical tree verdict, and gives the reason: on
an insensitive parent, `A` and `a/x` do not collide as endpoints yet `A` is genuinely the ancestor of
`a/x`.

**That case cannot arise under today's floor, and saying so is part of the design rather than a reason
to skip the work.** It needs a case-insensitive parent, and A4b-1 refuses every directory that is not
`EXACT_BYTES`, alongside symlink ancestors (`ELOOP`), mount crossings and bind mounts (`EXDEV`), and
hard-linked directories (which ext4 does not permit). So the resolved topology is provably identical to
the lexical one *right now*. That is a property of the current platform floor, not a rule, and §11.4
asserts the agreement as a property test — so if the floor widens and the two diverge, a test says so
rather than a production refusal discovering it.

**The two halves differ in how a synthetic equivalence reaches them, and the design states which.** The
ordering half is reachable: an injected folding key makes `CreateDirectory("A")` the ancestor of an
earlier effect on `a/x`, a pair A2 phase 13 admits because it sees the two paths as unrelated. The
surface half is not. A declared path becomes an *ancestor node* only by being a §7.1 directory
candidate, and a candidate is either a lexical proper prefix — which A2's own trie already walks — or a
`CreateDirectory` endpoint, whose declared state is a `DirectoryState` and therefore never a blocker. A
folding *file* at `A` above `a/x` is refused one phase earlier by §6.3.1, because nothing creates the
directory `a`. The surface branch is written and correct; it is a fail-closed guard, and no test
asserts a refusal it cannot produce.

### 7.5 `work/<txid>/`

The chain is A4b-1 §6.6's, executed here:

```
metadata_root/work facts                       (A4b-1, observed)
  -> retained verbatim as ProjectApprovedSpec.work_base
  -> validate the concrete <txid> component against work.name_max
  -> inherited_constraints(work.constraints, "ext4")
  -> constraints of physical work/<txid>
  -> WorkRoot's ApprovedPlannedDirectory entry
  -> bounds every WORK scratch leaf name
```

**The observed work-base facts are retained, not consumed.** `WorkRoot`'s planned entry carries only
the *derived* constraints of `work/<txid>/`; the observed identity and constraints of physical
`metadata_root/work` appear nowhere in it. Ledger #19 requires A5 to re-resolve that namespace under
the held lock before creating `work/<txid>/`, and a re-resolution with no approved baseline is not a
comparison — it is a fresh observation authorizing itself, which is the failure mode #19 exists to
prevent. So the proof retains `work_base` as its own field.

This is the one place where the §8 rule about consuming observations does not apply, and the
distinction is exactly the one that rule draws: `metadata_root/work` is a *directory* whose facts A5
must compare, not a leaf whose state A6 must capture.

## 8. The proof

```python
ApprovedExistingDirectory(
    node: TopologyNode,
    identity: FilesystemIdentity,
    constraints: DirectoryConstraints,
)
ApprovedPlannedDirectory(node: TopologyNode, constraints: DirectoryConstraints)
ApprovedDirectory = ApprovedExistingDirectory | ApprovedPlannedDirectory

ApprovedPath(path: str, parent_node: TopologyNode, leaf: str)
ApprovedScratch(effect_id: str, role: ScratchRole, parent_node: TopologyNode, leaf: str)

ApprovedWorkBase(identity: FilesystemIdentity, constraints: DirectoryConstraints)

ProjectApprovedSpec(
    compiled: CompiledSpec,
    binding: ProjectBinding,
    txid: str,
    topology: RecoveryTopology,
    directories: tuple[ApprovedDirectory, ...],
    paths: tuple[ApprovedPath, ...],
    scratch: tuple[ApprovedScratch, ...],
    work_base: ApprovedWorkBase | None,
)
```

`work_base` is present exactly when the specification contains a `CreateDirectory`, because
`work_base_facts()` is lazy for the reason A4b-1 §6.6 gives. An `X | None` here rather than a
two-variant union is deliberate and not in tension with the `ApprovedDirectory` union below: this
`None` encodes *applicability* — the work namespace is irrelevant to this transaction — while the
directory union encodes a *fact* about a directory that certainly matters. A3 draws the same line, with
`rollback_result` and `halt_diagnostic` as `X | None` validated present-exactly-when-required, and
§13's criteria state that rule for `work_base`.

A two-variant union rather than `identity: FilesystemIdentity | None`, because "this transaction
creates the directory" is a different fact from "I could not observe it", and every consumer branches
on the distinction anyway. Fail early over defensive optionals.

`ProjectApprovedSpec` is guarded exactly like `CompiledSpec`, `VolumeEvidence`, and `RecoverySnapshot`:
an explicit `__init__` demanding a module-private construction token, so ordinary construction *and*
`dataclasses.replace` both refuse — `replace()` re-enters the same `__init__` without the token.
Authority §5.4 is clear this is a conventional API boundary and not a claim of unforgeability.

**Leaf frontier observations are consumed, not retained.** Ancestor legality and endpoint distinctness
both read the frontier, and it is real evidence for those judgments. But A6's capture must observe each
leaf again against a coherent descriptor regardless, so a retained leaf observation would be the one
field in the proof that a consumer could mistake for authority while it silently went stale. Per-node
directory facts *are* retained, and so is `work_base` (§7.5), because those are exactly what ledger #19
requires a later stage to re-resolve and compare against. The line is drawn between a directory whose
facts a later stage compares and a leaf whose state a later stage captures — not between cheap and
expensive observations.

Mount membership is likewise not stored. `open_child_directory` passes `RESOLVE_NO_XDEV`, so every hop
is proved on the bound mount at resolution time and re-proved on re-resolution; there is no comparison
for a stored value to serve.

## 9. Error contract

| Raised | For |
| --- | --- |
| `ProjectApprovalRefused` | endpoint collision or over-limit leaf (§6.3.2), directory-level collision (§7.1), illegal or over-limit derived ancestor, scratch-leaf collision, resolved-topology surface or ordering violation — and A4b-1's own `ProjectApprovalRefused` instances, passing through untouched |
| `PreconditionRefused` | raised by A4b-1 for an incoherent walk and by pure topology construction when two path resolutions report incompatible facts for one lexical prefix |
| `CapabilityUnavailable` | required ⊄ supplied, and `lookup_equivalence_key` on an unapproved `LookupProof` |
| `ProtocolError` | a `compiled`, `context`, `binding`, or `txid` of the wrong exact type; a malformed txid; a closed binding or released lock |
| bare `OSError` | everything else, unwrapped |

A4b-2 introduces no new exception *type*. It does require one amendment to what an existing type
means: authority §11's `PreconditionRefused` did not cover approval-time drift, per §3.3.

**Ledger #20 is structural.** `approve_for_project` contains no `except` clause enclosing any resolver
call — not a discriminating one, not a re-raising one. The correct count is zero, which is a stronger
statement than A4b-1's `test_no_blanket_oserror_handler` makes about its own modules, and §11.6 asserts
it by AST rather than by review.

## 10. Limits

- Approval is O(total path components) in resolution and O(declared paths log declared paths) in
  judgment. It issues one `openat2` per distinct hop per path; there is no per-path caching of hops
  across paths beyond A4b-1's identity memo, which interns facts but still re-reads constraints.
- The proof holds a live binding, so it is not serializable and must not outlive the lease. This is the
  intended shape: ledger #16 requires the live binding precisely so a detached value cannot authorize
  access.
- Approval says nothing about the interval after it returns. Ledger #19 owns that window.

## 11. Verification

Six tiers. The pure tiers carry most of the weight, which is the payoff for keeping phase C free of
I/O.

### 11.1 Tier 1 — pure judgment

Hand-built resolution tables drive ancestor legality, endpoint distinctness, scratch instantiation and
distinctness, and capability adjudication. Synthetic `ResolvedPrefix` values make the awkward cases
cheap: a missing ancestor no effect creates; a `REGULAR_FILE` ancestor correctly converted; the same
ancestor left unconverted; a `MoveNoClobber` source converted into a directory; planned-ancestor and
planned-leaf `NAME_MAX` violations; and an `OTHER` ancestor.

The cases that need an injected equivalence use a *different relation for each job*, because one
relation cannot do both. Ancestor merging and ordering-too-late use case folding, which A2 admits since
its phase 4 key is the whole path and `A` differs from `a/x`. Endpoint and scratch collisions use a
truncating relation instead: two leaves fold in one parent only when their whole paths fold too, and A2
phase 4 already refuses every such pair, so a case-folding double cannot reach those checks at all.
Truncation is a real filesystem equivalence class A2's key does not subsume. The fixture is therefore a
factory over the relation rather than a fixed double, and both limits §11.2 states apply to every use
of it.

`lookup_equivalence_key` gets its own cases: identity on `EXACT_BYTES`, and `CapabilityUnavailable` on
every other member of the current `LookupProof` vocabulary — today only `UNREPRODUCIBLE_CASEFOLD`.
The test is parametrized over the enum rather than over that one member, so adding a future member
fails the suite until someone decides what its key is.

Phase A's gates are pure and tested here rather than against a volume: each of `compiled`, `context`,
`context.binding`, and `context.txid` given a wrong exact type raises `ProtocolError`; a subclass of
each is refused, not accepted; a duck-typed object exposing `backend`, `project_root_fd`, and
`evidence` is refused before it can reach proof issuance; a non-string txid raises `ProtocolError`
rather than the `TypeError` `re.Pattern.fullmatch` would produce; and a closed binding whose evidence
lacks a required capability raises `ProtocolError`, not `CapabilityUnavailable` — the assertion that
pins the §6.1 ordering, since both exceptions are reachable and only the order distinguishes them.

### 11.2 Tier 2 — topology construction

Node assignment, edge construction, the `ApprovedExistingDirectory`/`ApprovedPlannedDirectory`
partition of §7.3, `node_id` reproducibility across runs, and `WorkRoot` present exactly when a
`CreateDirectory` exists. Repeated-prefix cases cover directory replacement and directory-to-absent or
blocking drift. A live `CreateDirectory` endpoint traversed for a descendant remains planned with
inherited constraints.

Planned-node keying gets a dedicated case, because it is the half of §7.1's key that is easy to get
wrong: `CreateDirectory("A")` alongside an effect on `a/x` must yield **one** directory node when the
parent's policy folds case, and **two** under `EXACT_BYTES`. An exact-bytes key passes every other test
in this tier and fails only the first half.

**How the merging half is reached.** §6.3.2 records that no `LookupProof` member is both insensitive
and reproducible, so the case cannot be produced by picking a different enum value. An
`injected_equivalence` fixture supplies it instead, monkeypatching `lookup_equivalence_key` on the
*consuming* modules — `atoms.fs.topology` and `atoms.fs.judgment` — with a case-folding double. This
follows the existing `injected_lookup` fixture, which patches `atoms.fs.resolve.read_lookup_constraints`
by the same consuming-module path for the same reason.

Two limits on what that test proves, both stated so nobody later reads more into it. It exercises the
*call sites* — that both keys route through the function and merge whatever it says merges — and not
any real folding relation, which remains unreproducible and refused. And it is a double, so it cannot
detect a call site that bypasses the function while still passing exact-bytes cases; §11.6's AST guard
covers that instead, asserting neither module compares leaf names directly.

The `EXACT_BYTES` half needs no injection and runs against real fixtures.

### 11.3 Tier 3 — A3 acceptance

Every constructed topology is fed to `build_recovery_snapshot` and must validate. This is the strongest
single test in the design: `_validate_topology` independently encodes exact persistent and scratch
coverage, single-parent-ness, the `WorkRoot` parent rule, the scratch/persistent shared-parent equality,
and acyclicity — written before A4b-2 existed, so #10 is checked against an authority rather than
against a restatement of this document.

### 11.4 Tier 4 — real ext4

`approve_for_project` end to end against a bound volume, on the `ext4_*` fixtures A4b-1 added:

- a specification whose paths, ancestors, and scratch leaves all approve;
- a genuine `FILE → ABSENT → DIRECTORY` ancestor conversion on disk;
- the same conversion when a `MoveNoClobber` source produces the absence;
- a `PATH_MAX`, a walked per-directory `NAME_MAX`, and planned-ancestor/planned-leaf `NAME_MAX`
  refusal;
- a live `CreateDirectory` endpoint classified planned while approval remains read-only;
- the nested-metadata-root refusal, reached through `approve_for_project` rather than the resolver;
- `work_base` retained with the identity and constraints of the real `metadata_root/work` — asserted
  equal to an independent `fstat` and `read_lookup_constraints` on that directory, so the retained
  baseline is checked against the filesystem rather than against the same call that produced it;
- `work_base is None` for a specification with no `CreateDirectory`, and non-`None` with one;
- the A2-agreement property: for every compiled specification the generator of criterion 18 produces,
  the §7.4 re-run reaches A2's verdict, and the produced topology validates through
  `build_recovery_snapshot`. The generator is pure, so this runs at Tier 3 speed; the volume fixtures
  above are what make this tier real ext4.

The last is a property test rather than a case: under today's floor the two cannot disagree, so a
failure means the re-run drifted or the floor moved.

### 11.5 Tier 5 — refusal propagation

Ledger #20's categorical obligation. For each declared A4b-1 exception type —
`ProjectApprovalRefused`, `PreconditionRefused`, `CapabilityUnavailable`, `ProtocolError`, and a bare
`OSError` — and for each load-bearing branch producing one, a stored exception object is raised from
the injected resolver and the test asserts the object caught by the caller **is** that object, alongside
the call count and arguments. Identity rather than type, because a type assertion is satisfied by any
same-class exception the code might raise on its own.

### 11.6 Tier 6 — architecture

- `ProjectApprovedSpec(...)` and `dataclasses.replace(proof, ...)` both raise `TypeError`.
- The retained binding is present and is the object passed in (ledger #16).
- No `except` clause in `approval.py` encloses a `PathResolver` call, by AST.
- Neither `topology.py` nor `judgment.py` decides a name directly. Two complementary checks, because
  neither is sufficient alone. **Negative:** an AST guard rejects comparisons and mapping keys over a
  raw component or a local aliased from one — a lint over those shapes, explicitly not a soundness
  proof, since alias discovery is one hop and a component recovered through `split` or a key built
  from a length would slip past. **Positive:** §11.2's double records the names it is asked about, so
  a call site that bypasses `lookup_equivalence_key` contributes nothing to that list however it is
  written. Behaviour alone cannot distinguish the two, because under `EXACT_BYTES` the function is
  the identity.
- `topology.py` and `judgment.py` import nothing from `atoms.fs.resolve` beyond its types, and issue no
  syscalls — asserted by AST, not by trust.
- `resolve.py` and `lookup.py` still import none of `compiler`, `spec`, or `recovery`; the new modules
  may. The guard's module list is asserted to be exactly `["resolve", "lookup"]`.
- `test_no_consumer_of_the_approved_spec_exists_yet`, arming open ledger #9 before there is anything to
  guard, as A4a armed `test_no_production_caller_of_bind_exists_yet`.
- The `AGENTS.md` A4b status line and both A4b-2 document status paragraphs match the implementation
  state, while the architecture guard keeps ledger #9 open against A5–A8.

The casefold tier stays skipped by default, exactly as A4b-1 left it.

## 12. Deferred and delivery obligations

**Ledger entries discharged:** #2, A4's part of #3, #4, #5, #6, #10, #11, #16, #20 — each when its
tier lands. A4's factory half of #9 is complete, but #9 remains open until A5–A8 enforce their entry
points.

**Ledger entries created:** #21, the txid binding, owned by A5.

**Ledger entries untouched:** #1 (A6); #7, #12, #17, #18 (A5); #8 (A3, A7); #13 (A6, A7); #14 (A7);
#15 (A8); #19 (A5, A6, A7).

**Not a ledger entry:** the `AGENTS.md` status line, and authority §11's `PreconditionRefused`
amendment covering approval-time drift (§3.3).

## 13. Acceptance criteria

1. `approve_for_project(compiled, context)` returns a frozen `ProjectApprovedSpec` composing the exact
   `CompiledSpec` object passed in, the live `ProjectBinding`, the txid, the topology, the three fact
   tables, and `work_base`.
2. Ordinary construction of `ProjectApprovedSpec` and `dataclasses.replace` on one both raise.
3. A wrong exact type for any of `compiled`, `context`, `context.binding`, or `context.txid` raises
   `ProtocolError`, subclasses included; a malformed `str` txid raises `ProtocolError` with the
   `SpecValidationError` as `__cause__`; a non-`str` txid raises `ProtocolError`, never `TypeError`.
4. A closed binding or released lock raises `ProtocolError` before any capability is compared, even
   when a required capability is also missing.
5. A required capability the bound volume does not supply raises `CapabilityUnavailable`, before any
   `openat2` is issued.
6. Every path in `compiled.timelines` is resolved exactly once, in sorted order; incompatible repeated
   lexical-prefix observations raise `PreconditionRefused` before topology construction overwrites one.
7. `work_base_facts()` is called iff the specification contains a `CreateDirectory`, and `work_base` is
   non-`None` on exactly those approvals, carrying the observed identity and constraints of physical
   `metadata_root/work`.
8. A declared path whose missing ancestor no `CreateDirectory` creates raises
   `ProjectApprovalRefused`; one whose `CreateDirectory` is ordered after a descendant effect does too.
9. A `REGULAR_FILE` or `SYMLINK` ancestor converted by the timeline is admitted, with either
   `DeletePath` or a `MoveNoClobber` source recognized as the removal; the same ancestor left
   unconverted, and any `OTHER` ancestor, raise `ProjectApprovalRefused`.
10. Two declared paths resolving to the same parent node whose leaves share a
    `lookup_equivalence_key` under that parent's constraints raise `ProjectApprovalRefused` naming both
    spellings.
11. `lookup_equivalence_key` is the identity on `EXACT_BYTES` and raises `CapabilityUnavailable` on
    every other member of the `LookupProof` vocabulary, the test parametrized over the enum so a new
    member fails until its key is decided.
12. Every derived path component, persistent leaf, and complete scratch set member is within its
    actual or inherited parent's `name_max`; scratch is bound per §6.3.3 and proved pairwise distinct.
13. The produced `RecoveryTopology` validates through `build_recovery_snapshot` for every specification
    the suite approves.
14. `ApprovedExistingDirectory` covers exactly `ProjectRoot` and the `TopologyDirectory`s;
    `ApprovedPlannedDirectory` covers exactly `WorkRoot` and the parent `PersistentNode`s, including a
    transaction-created endpoint observed live and traversed for a descendant, using inherited
    constraints.
15. Planned directories are keyed by `lookup_equivalence_key`. `CreateDirectory("A")` with an effect on
    `a/x` yields two directory nodes under `EXACT_BYTES` against a real fixture, and one under the
    `injected_equivalence` double of §11.2 — the folding half being unreachable in production, since no
    `LookupProof` member is both insensitive and reproducible.
16. Neither `topology.py` nor `judgment.py` decides a name directly; every name comparison routes
    through `lookup_equivalence_key`. Asserted twice: by an AST guard over comparisons and mapping
    keys, which is a lint rather than a proof, and by the injected double recording the names each
    phase asks about, which catches an omission the guard's shapes miss. Behaviour cannot distinguish
    them, because under `EXACT_BYTES` the function is the identity.
17. `node_id` assignment is identical across repeated approvals of one specification.
18. The §7.4 re-run reaches A2's verdict on every compiled input, asserted over a deterministic
    generator — all ordered effect sequences of length 1–3 over a fixed pool, filtered to those A2
    admits — rather than over a hand-picked corpus.
19. Every exception A4b-1 raises reaches the caller as the same object, for every declared type and
    every load-bearing branch.
20. `approval.py` contains no `except` clause enclosing a resolver call.
21. No leaf frontier observation and no mount identifier appears in the retained proof; `work_base` is
    the only observation retained beyond the per-node directory facts.
22. `resolve.py` and `lookup.py` import none of `atoms.core.compiler`, `atoms.core.spec`, or
    `atoms.core.recovery`, and the guard covering them names exactly those two modules.
23. No production consumer of `ProjectApprovedSpec` exists, asserted rather than assumed; ledger #9
    remains open until each A5–A8 entry point accepts only that proof.
24. Approval issues no write of any kind to project space.
