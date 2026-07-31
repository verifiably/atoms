# A4b-2 — rooted project approval

**Status:** Designed 2026-07-31; unimplemented. A5–A8 remain unimplemented. A4b-2 reads project space
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
it approved, the live `ProjectBinding` it approved against, A3's production `RecoveryTopology`, and a
node-keyed table of the resolved facts a later stage must re-observe and compare.

## 2. Scope and non-scope

### 2.1 In scope

- `approve_for_project`, `ProjectContext`, and `ProjectApprovedSpec` with its factory guard (ledger #9).
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
- Retention of the live binding in the proof (ledger #16).
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
never authorize.

## 3. Seam review against the deferred-obligation ledger

`AGENTS.md` requires this review before the plan is written.

### 3.1 Existing entries

A4b-2 discharges ten entries — every entry naming it as an owner. Each is discharged only when the
verification tier named beside it lands.

| # | What discharges it | Tier |
| --- | --- | --- |
| 2 | Endpoint distinctness keyed by (resolved parent node, exact leaf bytes), covering `ABSENT`-declared paths, before any capture or mutation | §11.1, §11.4 |
| 3 | **A4's part only.** An ancestor that exists as a file or symlink is admitted iff the timeline converts it to a directory and orders the conversion before every descendant effect. The entry stays open against A6 | §11.1, §11.4 |
| 4 | Every declared path resolved through `PathResolver.resolve`, which enforces real `PATH_MAX` and per-directory `NAME_MAX`; every generated scratch leaf checked against its concrete parent's approved `name_max` | §11.1, §11.4 |
| 5 | The same resolution enforces containment, metadata-root exclusion by `st_dev`/`st_ino`, and mount membership | §11.4 |
| 6 | `compiled.spec.required_capabilities() ⊆ binding.evidence.supplied_capabilities`, refused before any traversal and long before any record write | §11.1, §11.4 |
| 9 | Frozen, token-guarded `ProjectApprovedSpec`; ordinary construction and `dataclasses.replace` both refuse | §11.6 |
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

- **No authority amendment is required.** §11 already declares every exception A4b-2 raises, and §5.4
  explicitly assigns `ProjectContext`'s concrete fields to A4's reviewed plan. A4b-1 needed a §11
  amendment; this design needs none.
- `AGENTS.md`'s A4b status line changes from "A4b-1 implemented, A4b-2 unimplemented" when the
  implementation lands. A4a's `test_a4a_status_is_synchronized_across_authority_documents` establishes
  the pattern for keeping that line honest.

## 4. Architecture and ownership

### 4.1 Module layout

Three new modules under `~/d/atoms/python/src/atoms/fs/`:

| Module | Holds | Sees a filesystem |
| --- | --- | --- |
| `approval.py` | `ProjectContext`, `ProjectApprovedSpec`, the construction token, `approve_for_project` | Yes — phase B only |
| `topology.py` | Node assignment, edge construction, the re-run of surface and ordering rules | No |
| `judgment.py` | Ancestor legality, endpoint distinctness, scratch instantiation and distinctness | No |

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

- It does not compare any live entry against a declared precondition. A6's capture does that, against
  a coherent descriptor, and §6 of the authority is explicit that capture-time verification is not
  compare-and-swap authority. An approval-time precondition check would be a third observation of the
  same entry, weaker than capture's and stale by the time capture runs.
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

1. `type(compiled) is CompiledSpec` → `ProtocolError` otherwise, following A3's `_require_exact`
   convention in `snapshot.py`. A subclass would pass an `isinstance` gate and then break a later phase.
2. `require_valid_identifier("txid", context.txid)`, translated to `ProtocolError` per §5.1.
3. Capability adjudication (ledger #6):
   `compiled.spec.required_capabilities() - context.binding.evidence.supplied_capabilities` must be
   empty, else `CapabilityUnavailable` naming the sorted missing capability values.
4. `PathResolver(context.binding)`, which refuses a non-Linux backend, a casefold project root, and a
   project root that *is* the metadata root.

Capability adjudication precedes every traversal deliberately. An unsupported capability makes each
subsequent path check pointless work, and the refusal is more actionable than the first path refusal
that happens to trip. The allowlist half of authority §5.4's capability bullet is already discharged
structurally: `bind_project_volume` refuses a volume whose configuration tuple is not on the supplied
durability allowlist, so no `ProjectBinding` exists for an unlisted volume.

### 6.2 Phase B — resolution, the only I/O

5. Resolve every path in `compiled.timelines`, in sorted order, into
   `dict[str, ResolvedPrefix]`. A2 phase 10 already proves that path set equals the declared surface
   exactly, so using `timelines` needs no separate coverage argument and matches the key A3's
   `_validate_persistent_coverage` uses.
6. If any effect is a `CreateDirectory`, call `resolver.work_base_facts()`, check the concrete `<txid>`
   component against `work.name_max`, and derive `work/<txid>/`'s constraints through
   `inherited_constraints`. Skipped entirely otherwise, matching A4b-1's reason for making that method
   lazy: a specification with no `CreateDirectory` must not be refused by an unapprovable `work/`.

This phase discharges #4 and #5 by construction — every limit, containment, metadata-root, and
mount-membership rule is enforced inside `resolve()` — and produces the table every later phase reads.

Sorted order matters for diagnostics only, but it matters: an approval that refuses a different path on
each run because dictionary order shifted is much harder to act on.

### 6.3 Phase C — judgment, pure

#### 6.3.1 Ancestor legality

For each declared path, `ResolvedPrefix` reports how far resolution got. Three cases:

```
frontier is the leaf (remainder == ())        -> the parent chain exists; nothing to prove
frontier absent, remainder non-empty          -> missing-ancestor case
frontier present, remainder non-empty         -> existing-non-directory-ancestor case
```

In the second case, the missing directories are `frontier_name` followed by `remainder[:-1]`, each
relative to the deepest resolved hop. Every one of them must be the path of a `CreateDirectory` effect
in this transaction, and that effect's index must precede every effect touching a path beneath it. A
missing component no effect creates is `ProjectApprovalRefused` — authority §5.4's "a parent that
neither exists nor is created by the transaction cannot be captured."

In the third case the frontier's `EntryKind` decides:

| Frontier kind | Verdict |
| --- | --- |
| `REGULAR_FILE`, `SYMLINK` | Admitted iff the timeline converts it — the path is declared, deleted, and re-created as a directory, with the `CreateDirectory` ordered before every descendant effect |
| `OTHER` | Refused. No closed effect variant converts a socket, FIFO, or device node into a directory, so no admissible timeline reaches a directory there |
| `DIRECTORY` | Unreachable. `open_child_directory` succeeds on a directory, so resolution would not have stopped |

The `OTHER` case is why A4b-1 kept the kind distinguishable rather than collapsing every non-directory
into one bucket. `DIRECTORY` being unreachable is asserted rather than assumed, because it is a claim
about `openat2` behavior rather than about this module.

#### 6.3.2 Endpoint distinctness

Two declared paths name one entry iff they resolve to the same parent node and their leaf bytes are
equal under that parent's actual lookup policy. Under `EXACT_BYTES` — the only policy A4b-1 approves —
that reduces to exact byte equality of the leaves.

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

| Node | When |
| --- | --- |
| `ProjectRoot()` | Always |
| `WorkRoot()` | Iff some effect carries `ScratchRole.WORK`, i.e. some `CreateDirectory` exists |
| `PersistentNode(path)` | One per `compiled.timelines` entry |
| `ScratchNode(effect_id, role)` | One per effect, `role = required_scratch_role(effect)` |
| `TopologyDirectory(node_id)` | One per *undeclared* intermediate directory |

The directory node for a path prefix is `ProjectRoot()` when the prefix is empty, that prefix's own
`PersistentNode` when the prefix is itself a declared path, and a `TopologyDirectory` otherwise. A
declared path that is also an intermediate directory of another declared path therefore appears once,
as its `PersistentNode` — A3's `_validate_topology` requires exact persistent coverage, and a second
node for the same directory would break it.

`node_id` values are assigned in first-encounter order over lexically sorted declared paths, so the
topology is reproducible across runs. The assignment *key* is the `FilesystemIdentity` for an existing
directory and (parent node, exact leaf bytes) for a planned one. Keying by identity is a no-op under
today's floor, where no two spellings can reach one directory; it is written that way so that widening
the floor changes the floor and not this code.

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
destination all take `ABSENT`. A2 phase 11 forces `initial_surface` to equal each timeline's first
`pre`, so no declared path can be an existing directory at approval time.

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

### 7.5 `work/<txid>/`

The chain is A4b-1 §6.6's, executed here:

```
metadata_root/work facts                       (A4b-1, observed)
  -> validate the concrete <txid> component against work.name_max
  -> inherited_constraints(work.constraints, "ext4")
  -> constraints of physical work/<txid>
  -> WorkRoot's ApprovedPlannedDirectory entry
  -> bounds every WORK scratch leaf name
```

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

ProjectApprovedSpec(
    compiled: CompiledSpec,
    binding: ProjectBinding,
    txid: str,
    topology: RecoveryTopology,
    directories: tuple[ApprovedDirectory, ...],
    paths: tuple[ApprovedPath, ...],
    scratch: tuple[ApprovedScratch, ...],
)
```

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
directory facts *are* retained, because those are exactly what ledger #19 requires a later stage to
re-resolve and compare against.

Mount membership is likewise not stored. `open_child_directory` passes `RESOLVE_NO_XDEV`, so every hop
is proved on the bound mount at resolution time and re-proved on re-resolution; there is no comparison
for a stored value to serve.

## 9. Error contract

| Raised | For |
| --- | --- |
| `ProjectApprovalRefused` | endpoint collision, illegal ancestor (missing, `OTHER`, or unconverted), scratch-leaf collision, resolved-topology surface or ordering violation — and everything A4b-1 raises, passing through untouched |
| `CapabilityUnavailable` | required ⊄ supplied |
| `ProtocolError` | malformed txid, a `compiled` that is not exactly a `CompiledSpec`, closed binding or released lock via A4b-1 |
| bare `OSError` | everything else, unwrapped |

A4b-2 introduces no new exception type; authority §11 already declares each of these.

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
cheap: a missing ancestor no effect creates; an ancestor created too late; a `REGULAR_FILE` ancestor
correctly converted; the same ancestor left unconverted; an `OTHER` ancestor; an endpoint collision;
and `A` versus `a/x` merging under a hypothetical insensitive parent, which no real approved volume can
produce.

### 11.2 Tier 2 — topology construction

Node assignment, edge construction, the `ApprovedExistingDirectory`/`ApprovedPlannedDirectory`
partition of §7.3, `node_id` reproducibility across runs, and `WorkRoot` present exactly when a
`CreateDirectory` exists.

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
- a `PATH_MAX` and a per-directory `NAME_MAX` refusal;
- the nested-metadata-root refusal, reached through `approve_for_project` rather than the resolver;
- the A2-agreement property: for every compiled specification, the §7.4 re-run reaches A2's verdict.

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
- `topology.py` and `judgment.py` import nothing from `atoms.fs.resolve` beyond its types, and issue no
  syscalls — asserted by AST, not by trust.
- `resolve.py` and `lookup.py` still import none of `compiler`, `spec`, or `recovery`; the new modules
  may. The guard's module list is asserted to be exactly `["resolve", "lookup"]`.
- `test_no_consumer_of_the_approved_spec_exists_yet`, arming the A5–A8 boundary before there is
  anything to guard, as A4a armed `test_no_production_caller_of_bind_exists_yet`.
- The `AGENTS.md` A4b status line matches the implementation state.

The casefold tier stays skipped by default, exactly as A4b-1 left it.

## 12. Deferred and delivery obligations

**Ledger entries discharged:** #2, A4's part of #3, #4, #5, #6, #9, #10, #11, #16, #20 — each when its
tier lands.

**Ledger entries created:** #21, the txid binding, owned by A5.

**Ledger entries untouched:** #1 (A6); #7, #12, #17, #18 (A5); #8 (A3, A7); #13 (A6, A7); #14 (A7);
#15 (A8); #19 (A5, A6, A7).

**Not a ledger entry:** the `AGENTS.md` status line, and the absence of any authority amendment.

## 13. Acceptance criteria

1. `approve_for_project(compiled, context)` returns a frozen `ProjectApprovedSpec` composing the exact
   `CompiledSpec` object passed in, the live `ProjectBinding`, the txid, the topology, and the three
   fact tables.
2. Ordinary construction of `ProjectApprovedSpec` and `dataclasses.replace` on one both raise.
3. A `compiled` that is not exactly a `CompiledSpec` raises `ProtocolError`; a malformed txid raises
   `ProtocolError` with the `SpecValidationError` as `__cause__`.
4. A required capability the bound volume does not supply raises `CapabilityUnavailable`, before any
   `openat2` is issued.
5. Every path in `compiled.timelines` is resolved exactly once, in sorted order.
6. `work_base_facts()` is called iff the specification contains a `CreateDirectory`.
7. A declared path whose missing ancestor no `CreateDirectory` creates raises
   `ProjectApprovalRefused`; one whose `CreateDirectory` is ordered after a descendant effect does too.
8. A `REGULAR_FILE` or `SYMLINK` ancestor converted by the timeline is admitted; the same ancestor left
   unconverted, and any `OTHER` ancestor, raise `ProjectApprovalRefused`.
9. Two declared paths resolving to the same parent node with equal leaf bytes raise
   `ProjectApprovalRefused` naming both spellings.
10. The complete scratch set is instantiated, bound to concrete parents per §6.3.3, proved pairwise
    distinct, and each leaf is within its parent's `name_max`.
11. The produced `RecoveryTopology` validates through `build_recovery_snapshot` for every specification
    the suite approves.
12. `ApprovedExistingDirectory` covers exactly `ProjectRoot` and the `TopologyDirectory`s;
    `ApprovedPlannedDirectory` covers exactly `WorkRoot` and the parent `PersistentNode`s.
13. `node_id` assignment is identical across repeated approvals of one specification.
14. The §7.4 re-run reaches A2's verdict on every compiled input.
15. Every exception A4b-1 raises reaches the caller as the same object, for every declared type and
    every load-bearing branch.
16. `approval.py` contains no `except` clause enclosing a resolver call.
17. No leaf frontier observation and no mount identifier appears in the retained proof.
18. `resolve.py` and `lookup.py` import none of `atoms.core.compiler`, `atoms.core.spec`, or
    `atoms.core.recovery`, and the guard covering them names exactly those two modules.
19. No production consumer of `ProjectApprovedSpec` exists, asserted rather than assumed.
20. Approval issues no write of any kind to project space.
