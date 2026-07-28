# A2 — Filesystem-independent compilation validation and repeated-path timelines

**Date:** 2026-07-28
**Status:** Implemented, including final-review corrections (2026-07-28).
**Refines:** [`2026-07-23-recoverable-fs-effect-engine-design.md`](2026-07-23-recoverable-fs-effect-engine-design.md) §5.3, §5.4, §13.3
**Depends on:** [`2026-07-23-plan-a1-core-model.md`](2026-07-23-plan-a1-core-model.md) (implemented)

Where this document and the authority design disagree, the authority design wins.

## 1. Decision

A2 adds the first stage of the engine's specification trust boundary as a pure function:

```python
compile_spec(spec: TransactionSpec) -> CompiledSpec
```

`CompiledSpec` is a factory-controlled, frozen proof of A2's filesystem-independent lexical/model
rules. A3 may consume it for the pure recovery reference model. A4 consumes it and, after actual
project/root checks, constructs the distinct `ProjectApprovedSpec` proof defined by authority §5.4.
A5–A8 accept that A4 proof, not raw `TransactionSpec` and not raw `CompiledSpec`. No compatibility
adapter, union-typed entry point, or implicit downstream recompilation bridges the two proof stages.

A2 implements exactly the subset of design §5.4 that is decidable without touching a filesystem. It has
no filesystem, SQLite, or platform dependency, and — like A1 — depends on nothing outside the standard
library.

## 2. Scope

**A2 establishes the trust boundary for:**

- effect-ID uniqueness and dependency validity;
- effect-ID distinctness under
  `portability_equivalence_key(effect_id)`;
- exact per-variant shape validation;
- safe identifiers and the project-relative path grammar;
- reserved `.#~` scratch-name rejection, under case- and NFC/NFD-equivalence semantics;
- lexical distinctness of declared paths under
  `portability_equivalence_key(path)`, so two spellings of one entry cannot compile as two timelines;
- UTF-8 encodability of every string that reaches the durable canonical form;
- fingerprint well-formedness (hash spelling, signed-64-bit byte lengths, permission-only modes);
- continuous repeated-path timelines (§5.3);
- initial/final surface agreement and exact effect-surface coverage;
- structural consistency between a declared path and its declared ancestors.

Phases 12–13 establish their lexical topology rules in time linear in the total characters/components
they consume. They use iterative component tries or an equivalent single-pass traversal, not repeated
materialization of every prefix string, and impose no lexical path-depth or path-length limit.

**Deferred to A4:**

- root and metadata-directory identity, compared by `st_dev`/`st_ino` rather than spelling (§5.4);
- **filesystem-aware path aliasing** — whether two declared paths that A2's lexical key treats as
  distinct nonetheless name one entry. A2 refuses the lexically detectable cases (phase 4); A4 owes the
  remainder against each **parent directory's** actual lookup policy, not the mount's, including for
  paths declared `ABSENT`, where there is no inode to compare and the mutation-time no-clobber guard does
  not contain the case. Now an explicit §5.4 obligation;
- the resolved per-directory equivalence topology. A4 must re-run surface-tree consistency and
  created-directory-before-descendant ordering over that topology; pairwise endpoint distinctness alone
  does not discharge this;
- pairwise distinctness of every instantiated effect/role scratch leaf in its concrete parent under
  that parent's actual lookup policy. Intrinsic name collisions refuse approval; transaction-ID
  regeneration is reserved for external occupancy and cannot repair equivalent effect IDs;
- ancestor symlink and mount traversal (`anchored_traversal`, §6);
- platform capability availability and the per-mount probe (§5.5);
- durability-allowlist membership;
- live filesystem preconditions;
- `NAME_MAX` / `PATH_MAX` enforcement, which is genuinely per-filesystem and therefore not a lexical
  rule A2 can state.

## 3. The compiled value

```
CompiledSpec
  spec:      TransactionSpec          # canonicalized
  timelines: tuple[PathTimeline, ...] # sorted by path

PathTimeline
  path:        RelPath
  occurrences: tuple[TimelineOccurrence, ...]   # in authoritative order

TimelineOccurrence
  effect_id:    str
  effect_index: int
  role:         str          # "target" | "source" | "destination"
  pre:          PathState
  post:         PathState
```

All three are frozen dataclasses, consistent with A1's model. `PathTimeline` and
`TimelineOccurrence` are ordinary data values. `CompiledSpec` is different because its type is proof:
its dataclass uses a guarded, non-generated constructor, and `compile_spec` is its sole public
construction authority.

Ordinary `CompiledSpec(spec=..., timelines=...)` construction raises `TypeError`, even when the supplied
fields came from a valid compiled value. `dataclasses.replace(compiled, ...)` also raises `TypeError`,
because `replace` calls that guarded constructor and does not possess the module-private construction
authority. Assignment to a field raises exactly `dataclasses.FrozenInstanceError`.

This is not cryptographic or hostile-process unforgeability. Python permits deliberate bypass with
private module state, `object.__new__`, and `object.__setattr__`. The contract prevents ordinary
construction and accidental in-repository laundering; module privacy, static typing, and architecture
tests enforce that convention. It must not be described as proof against arbitrary Python code already
executing in the engine process.

The next proof is composition, not inheritance:

```
ProjectApprovedSpec
  compiled:        CompiledSpec
  project_binding: A4-owned rooted approval evidence
  topology:        A4-owned resolved per-directory equivalence topology
```

A4's construction authority is:

```python
approve_for_project(
    compiled: CompiledSpec,
    context: ProjectContext,
) -> ProjectApprovedSpec
```

It is the sole public construction authority for that frozen, factory-controlled type. Its ordinary
constructor and `dataclasses.replace` obey the same refusal contract. The A4 plan owns the concrete
`ProjectContext`, binding, and topology field types; it may refine their internal shape but may not
collapse `ProjectApprovedSpec` into `CompiledSpec`.

`CompiledSpec` deliberately holds nothing else. The required-capability set stays derivable through the
existing `TransactionSpec.required_capabilities()`, which A4 calls; freezing a denormalized copy into the
compiled value would buy nothing and add a field to keep in sync.

**Canonicalization.** `compile_spec` canonicalizes only the set-like fields — `initial_surface` and
`final_surface` sorted by path, `dependencies` sorted by `(before, after)` — using the same ordering
A1's `canonical_obj` already applies. It **never** reorders `effects`: that sequence is the authoritative
effect order (§5.2).

**Occurrence order.** The authoritative order over all occurrences is `(effect_index, index within the
effect)`. A1's `occurrences()` already returns a variant's occurrences in a fixed order — for
`MoveNoClobber`, source before destination — so this is total and deterministic.

## 4. Module layout

Three modules under `python/src/atoms/core/`, rather than one, because thirteen rules in a single file is
more than one unit's worth of responsibility:

| Module | Holds | Consumes |
| --- | --- | --- |
| `paths.py` | The project-relative path grammar and shared `portability_equivalence_key`. Purely lexical. | `errors`, `scratch` |
| `timeline.py` | `TimelineOccurrence`, `PathTimeline`, timeline construction and the continuity rule. | `errors`, `effects`, `fingerprint` |
| `compiler.py` | `CompiledSpec`, `compile_spec`, and the phase sequence. | all of the above, plus `spec`, `identifiers` |

`compiler.py`, not `compile.py`, so `from atoms.core import compile` cannot shadow the builtin.

## 5. Validation phases

Compilation is described as thirteen fail-early phases because that is the clearest responsibility map.
The numbers do **not** make every adjacent pair a public first-error contract. Independent rules may be
reordered without a compatibility promise, and tests must not freeze such changes merely to detect them.

Only four precedence constraints are load-bearing:

1. Phase 1 exact structural typing precedes every field interpretation, so a malformed direct dataclass
   cannot leak `AttributeError`, `TypeError`, or subclass-controlled behavior.
2. Phase 6 duplicate effect-ID refusal precedes phase 7 endpoint resolution, because an ID must name
   exactly one effect before `{effect_id: index}` is meaningful.
3. Phase 8 duplicate-surface refusal precedes construction of the surface maps consumed by phases
   10–12, because dict construction would silently collapse a duplicate.
4. Phase 10 exact coverage precedes phase 11 endpoint lookup, because endpoint comparison indexes both
   surface maps by timeline path.

Each stable edge carries a multi-violation test asserting the earlier refusal: malformed structure plus
a later-rule violation, duplicate IDs plus an invalid dependency, duplicate surface entries plus an
endpoint/tree violation, and a missing surface path that also makes endpoint lookup impossible.
Per-rule tests establish all other rules independently. There is deliberately no twelve-test
adjacent-phase change detector.

### Phase 1 — Exhaustive structural typing

Every value reachable from the specification is checked against its declared type, **recursively and
exhaustively, before any later phase reads it**, and against its **exact runtime type** rather than by
`isinstance`. The model is closed: the four path states, the five effect variants, and the scalars they
hold are the entire vocabulary, so a subclass is not a member. This is what refuses `bool` where an
integer is required — mirroring A1's decoder, since `bool` subclasses `int` — but the rule is general,
not a `bool` special case.

A subclass would pass an `isinstance` gate and then break a later phase in one of three ways, each of
which violates §6's error contract:

- **By overriding a method a phase calls.** A `str` subclass may define `startswith`, `__eq__`, or
  `__hash__` however it likes; the path grammar, the alias key, and every set membership test in phases 3
  through 13 would then be operating on values that answer questions differently than `str` does. A
  `Dependency` subclass may override the comparison that canonical ordering sorts on.
- **By not being stable across passes.** A `tuple` subclass may yield different members each time it is
  iterated, so the pass phase 1 validated would not be the pass a later phase reads.
- **By being absent from the variant tables.** Phases 1, 3, and 5 dispatch on the variant by exact type
  to find its path-valued and state-valued fields. Requiring the exact type is what keeps those tables
  total; under `isinstance` an admitted subclass reaches a lookup that has no entry for it.

Top level:

- `spec` is a `TransactionSpec`.
- `schema_version` is an integer equal to `SCHEMA_VERSION`. An unknown integer is refused with a fixed
  expected-version diagnostic that never interpolates or formats the caller value.
- `consumer_tag` satisfies A1's safe-identifier grammar (`require_valid_identifier`).
- `intent_digest` matches `^sha256:[0-9a-f]{64}$`.
- `initial_surface` and `final_surface` are tuples of `SurfaceEntry`.
- `effects` is a **non-empty** tuple whose every member is one of the five effect variants.
- `dependencies` is a tuple of `Dependency`.

The non-empty requirement is why an empty specification does not slip through: with no effects, phases 9
through 13 are all vacuously satisfied, so refusing here is the only place it can be caught. See §7.

Nested, for every element of those tuples:

- `SurfaceEntry.path` is a `str`; `SurfaceEntry.state` is one of the four `PathState` classes.
- `Dependency.before` and `.after` are `str`.
- Each effect's `effect_id` is a `str`, and each of its path-valued fields (`path`, or `source` and
  `destination` for `MoveNoClobber`) is a `str`.
- Each effect's state-valued fields (`pre`, `post`, `source_pre`) hold one of the four `PathState`
  classes. **Which** of the four each field may legally hold is phase 5's question, not this one; phase 1
  establishes only that the value is a path state at all.
- Within every `PathState` encountered: `content_hash` and `target` are `str`; `mode` and `byte_len` are
  integers and not `bool`.

This phase is exhaustive by construction rather than by inspection: `TransactionSpec` and its members are
plain frozen dataclasses that perform no runtime type checking, so a caller who bypasses `build_spec` can
place any object in any field. Making phase 1 total is what lets phases 2 onward read `effect.path` or
`state.mode` directly without malformed model data reaching an incidental operation.

Exact dataclass type does not imply initialized slots: `object.__new__(TransactionSpec)` and the
corresponding nested A1 dataclasses have the expected type but no field values. Phase 1 therefore reads
each required field as `getattr(obj, field_name, _MISSING)` and raises an explicit
`SpecValidationError` when the sentinel is returned. It does not rely on the pipeline-wide exception
normalization prohibited by §6.

Diagnostics that name a runtime type use one `_type_name(value)` helper. The helper evaluates
`value_type = type(value)` outside its `try`; the `try` covers only `value_type.__name__` and returns a
fixed fallback if a hostile metaclass raises during that lookup. No later validation work shares that
`try`, so an unrelated internal exception cannot be mistaken for a hostile type-name refusal.

The split between phase 1 and phase 5 is deliberate: phase 1 asks "is this a well-typed value at all",
phase 5 asks "is this variant's choice of state legal".

### Phase 2 — Fingerprints

Applied to every `PathState` reachable from the surfaces and from the effects.

- `FileState.content_hash` matches `^sha256:[0-9a-f]{64}$` — lowercase hex only.
- `FileState.byte_len` is in SQLite's signed `INTEGER` domain:
  `0 <= byte_len <= 2**63 - 1`. The bound check runs before the empty-file cross-check and its refusal
  message contains the fixed bounds, not `repr(byte_len)` or any interpolation of the rejected value.
  This makes refusal total for an integer with thousands of digits under both the default and disabled
  `int_max_str_digits` setting and avoids allocating a diagnostic proportional to attacker-controlled
  integer size.
- Every `mode` (`FileState`, `DirectoryState`, `SymlinkState`) is an integer in `0 .. 0o7777`.
  Permission bits only, never type bits. The range admits setuid, setgid, and sticky, because a setgid
  directory is a legitimate declared postcondition. Its refusal diagnostic contains only those fixed
  bounds; it never formats the rejected value as decimal, octal, hexadecimal, or binary.
- `SymlinkState.target` is non-empty, contains no NUL, and is encodable as UTF-8. It is **not** subject
  to the project-relative path grammar of phase 3: a symlink target may legitimately be absolute or
  contain `..`, and the engine treats it as opaque bytes it fingerprints rather than a path it resolves
  (§6).
- Empty-file cross-check: `byte_len == 0` if and only if `content_hash` is the SHA-256 of the empty
  string (`sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`).

### Phase 3 — Path grammar

Applied to every effect path (`ReplaceFile.path`, `CreateFileNoClobber.path`, `DeletePath.path`,
`MoveNoClobber.source`, `MoveNoClobber.destination`, `CreateDirectory.path`) and every
`SurfaceEntry.path`.

A path is valid when it is:

- non-empty and free of NUL;
- **encodable as UTF-8**;
- without a leading `/` (project-relative, never absolute);
- without a trailing `/`;
- free of empty components — `a//b` is refused;
- free of any `.` or `..` component;
- free of any component for which `aliases_scratch_sigil` is true.

**UTF-8 encodability is a durability requirement, not a stylistic one.** A Python `str` may hold unpaired
surrogates — from `surrogateescape` decoding of an undecodable OS pathname, or from a JSON document
containing a lone `\ud800`. Such a string survives every other rule here, but A1's `canonical_bytes`
raises `UnicodeEncodeError` on it, so a specification that compiled successfully could not be durably
serialized. That would break both the explicit invalid-specification refusal contract of §6 and the
canonical-output guarantee of §7.
Verified against the shipped A1 encoder: a path or symlink target containing `\ud800` raises
`UnicodeEncodeError: 'utf-8' codec can't encode character '\ud800'`. Compilation must refuse it with
`SpecValidationError` instead.

The scratch check applies to **every** component, not only the leaf, and uses the equivalence-aware
predicate rather than the plain prefix test. §5.1 requires the case- and NFC/NFD-normalization semantics,
and §13.3 makes that an explicit conformance obligation. Checking every component also refuses
`a/.#~b/c.txt`, where a scratch-shaped directory sits in the ancestor chain; the engine never creates a
scratch directory in a live parent (§9.5 stages directory creation in `work/`), so such a component is
foreign debris and refusing to operate beneath it is the conservative reading.

### Phase 4 — Path alias distinctness

Across the union of all declared paths — every effect path and every `SurfaceEntry.path` — no two
*distinct spellings* may share `portability_equivalence_key(path)`. The shared key is Unicode caseless
matching applied to the whole value:

```
portability_equivalence_key(value) = NFC( casefold( NFC(value) ) )
```

`paths.py` exposes that one helper for both whole paths and effect IDs. The former
`path_equivalence_key` name is removed, not retained as an alias or compatibility wrapper.

Two declared paths that are byte-identical are of course one path; two that differ but share a key —
`docs/a.md` and `docs/A.md`, or an NFC and an NFD spelling of `café.txt` — are refused.

**Why this is A2's problem.** Without it, a specification declaring effects on both `a` and `A` compiles
as two independent timelines. On a case-insensitive or normalization-insensitive volume — the macOS
default — those timelines address one directory entry, so each one's preconditions and its recovery
frontier are computed against a state the other effect is concurrently changing. Every downstream
guarantee is derived per-timeline, so this corrupts the model itself, not merely the outcome.

**Why refuse rather than resolve.** Whether two spellings actually alias is a property of the volume, so
A2 cannot decide it. Refusing the whole equivalence class is the conservative direction: it costs only
specifications that declare two case- or normalization-variant spellings of one path in a single
transaction, which has no legitimate use even on a case-sensitive volume, and it buys a portability
property worth having — **a specification that compiles contains none of this fixed NFC/casefold alias
class on either kind of volume.** It still requires A4's actual-policy proof before execution. Resolving
instead of refusing would mean silently merging two declared timelines, which is precisely the silent
fallback this codebase forbids.

**What remains A4's.** This stronger whole-path portability refusal remains intentional; it is not an
attempt to model the target filesystem. The key above is a fixed approximation of name folding. A filesystem may
alias two paths this key treats as distinct — a locale-sensitive fold, or HFS+'s particular
normalization — so phase 4 is a conservative first filter, never a proof of distinctness. A4 owes the
real per-directory check and resolved topology against actual name equivalence. Successful phase 4 is
never sufficient evidence for A4 approval.

That check cannot be identity-by-`st_dev`/`st_ino` alone, because a path declared `ABSENT` has no inode
to compare. **Nor is the mutation-time no-clobber guard a sufficient backstop** — an earlier draft of
this section claimed it was, and that was wrong. The guard contains an aliased pair only while the entry
one timeline created is still present when the other tries to create it. This sequence defeats it:

```
CreateFileNoClobber("x", post=F)   # x: ABSENT -> F
DeletePath("x", pre=F)             # x: F      -> ABSENT
CreateFileNoClobber("y", post=G)   # y: ABSENT -> G      (y aliases x)
```

Both paths are declared absent initially, both lexical timelines are continuous, and every `O_EXCL` and
no-clobber transfer succeeds, because the shared entry is genuinely absent at the moment each one runs.
Yet the declared final states — `x` absent, `y` present — are not jointly satisfiable by one entry, and a
crash mid-sequence hands the per-path recovery classifier contradictory observations of that entry. The
guard never fires, so nothing refuses.

A4 therefore needs a **positive** equivalence determination for absent names during project approval,
before any capture or mutation. §5.4 now carries it, and carries one constraint worth repeating here
because it is easy to get wrong: the determination is **per parent directory, not per mount**. ext4
enables case-insensitive lookup through the per-directory `+F` (`FS_CASEFOLD_FL`) attribute, so a single
filesystem can hold case-sensitive and case-insensitive directories, and a result probed in the metadata
root establishes nothing about a target parent. Since folding governs name lookup *within* a directory,
the obligation decomposes: the leaf names declared beneath each parent must be distinct under that
parent's policy, applied along the tree, with a transaction-created directory inheriting the policy of
the deepest existing ancestor.

A2's lexical rule shrinks the input to that check; it does not substitute for it. Note also that phase 4's
whole-path key is deliberately **coarser** than the true per-directory question: it refuses `a/x` alongside
`A/x` even where `a` and `A` are genuinely distinct directories. That is the same conservative direction
the rule takes everywhere here, and it is what buys the portability property above.

Actual topology is a separate problem from endpoint aliasing. On an insensitive parent, declared `A`
and declared `a/x` are different endpoints, but the first is the actual ancestor of the second. A4 must
map both component spellings through one resolved parent node, then re-run phase 12's surface rule and
phase 13's creation-order rule over that topology. Pairwise endpoint distinctness and successful A2
lexical checks do not imply either result.

### Phase 5 — Effect ID and exact variant shape

- `effect_id` satisfies `require_valid_identifier`, the same predicate A1's `scratch_leaf` applies before
  interpolating an ID into a pathname component.
- `ReplaceFile.pre` and `.post` are both `FileState`.
- `CreateFileNoClobber.post` is a `FileState`.
- `DeletePath.pre` is a `FileState` or a `SymlinkState`.
- `MoveNoClobber.source_pre` is a `FileState`, and `source != destination`.
- `CreateDirectory.post` is a `DirectoryState`.

`MoveNoClobber` with equal source and destination is refused here rather than left to the timeline
builder, which would otherwise have to invent an order between two occurrences inside one effect.

A `ReplaceFile` whose `pre` equals its `post` is well-formed and permitted; it keeps the timeline
continuous and A2 adds no rule the design does not call for.

### Phase 6 — Effect-ID uniqueness

No effect ID appears twice by exact string, and no two distinct IDs share
`portability_equivalence_key(effect_id)`. This is the same helper phase 4 applies to whole paths, now
applied to the effect-ID field embedded in `.#~<txid>.<effect-id>.<role>`. With exact-only uniqueness,
`e1` and `E1` would compile yet generate same-parent scratch leaves that alias on an insensitive
filesystem.

Transaction-ID regeneration does not repair that intrinsic collision: both leaves receive the same new
transaction-ID prefix and remain aliases. A4 still proves the fully instantiated scratch-leaf set under
each actual parent policy, because a target filesystem can have equivalences beyond A2's fixed key.

### Phase 7 — Dependencies

- `before` and `after` each name a declared effect ID.
- `before != after` — no self-edge.
- No `(before, after)` pair appears twice.
- `before` precedes `after` in the authoritative effect order.

### Phase 8 — Surface well-formedness

Within each surface independently, no path appears twice. Duplicates are **rejected**, never silently
deduplicated: two entries for one path with conflicting states are a consumer error, and collapsing them
would be exactly the silent fallback this codebase forbids.

### Phase 9 — Timelines

Occurrences are grouped by path in the authoritative order. For each path, every consecutive pair must
satisfy `occurrence[i].post == occurrence[i + 1].pre` — the continuity requirement of §5.3. Equality is
structural, over A1's frozen state dataclasses.

A path touched by exactly one effect forms a one-occurrence timeline, which is trivially continuous and
still subject to phase 11.

### Phase 10 — Exact coverage

The set of paths appearing in any effect occurrence, the set of `initial_surface` paths, and the set of
`final_surface` paths are all equal. This is §5.4's "the effect surface equals the declared transition
surface exactly; no persistent path is omitted or undeclared".

### Phase 11 — Timeline endpoints

For every timeline: its first occurrence's `pre` equals the declared `initial_surface` state for that
path, and its last occurrence's `post` equals the declared `final_surface` state.

### Phase 12 — Surface tree consistency

Applied to the initial and final surfaces independently. For declared paths `p` and `q` where `q` is
strictly beneath `p` (that is, `q` starts with `p + "/"`):

- if `p` is a `DIRECTORY`, `q` is unconstrained;
- otherwise — `p` is `ABSENT`, a `FILE`, or a `SYMLINK` — `q` must be `ABSENT`, because nothing can exist
  beneath a path that is not a directory.

This is a property of the two declared surfaces alone and makes no reference to effects. It catches
contradictory specifications that the per-effect rules accept — for instance `CreateDirectory("a/b")`
together with `DeletePath("a/b/c", pre=FileState(...))`, where `a/b` is declared absent initially and so
`a/b/c` cannot be a file.

The compiler constructs one component trie per surface. Each declared path is split once and its
terminal node stores the original full spelling plus state. An iterative stack walk carries the nearest
declared non-directory ancestor, refusing a present terminal beneath it. Trie insertion and traversal
touch each input component a constant number of times and never join prefixes, so this phase is linear
in the total surface characters/components. The walk is iterative: recursive descent would turn
Python's recursion limit into an undeclared path-depth limit.

**A non-directory ancestor constrains its descendants; it does not forbid them.** An earlier draft of
this rule refused any declared descendant beneath a declared file or symlink outright, which wrongly
rejected a structurally valid transition — an ancestor whose *type changes* during the transaction:

```
DeletePath("p", pre=FileState(...))          # p: FILE      -> ABSENT
CreateDirectory("p", post=DirectoryState(…)) # p: ABSENT    -> DIRECTORY
CreateFileNoClobber("p/q", post=FileState(…))# p/q: ABSENT  -> FILE
```

Both timelines are continuous, and the surfaces `{p: FILE, p/q: ABSENT}` → `{p: DIRECTORY, p/q: FILE}`
are consistent: `p/q` is absent initially precisely *because* `p` is a file then. Ancestor type change is
expressible in the closed effect set of §5.2 and is not a case the design excludes, so A2 must not
exclude it either. The single clause above admits it while still refusing the contradiction, and phase 13
supplies the ordering that makes it executable.

**Admitting it obliges A4 to capture it, and §6 has been amended accordingly.** §6's absence-capture
procedure originally covered only a *missing* ancestor — open the deepest existing ancestor, confirm the
first missing component is absent. That does not reach this case: `p` exists as a regular file, so
guarded traversal toward `p/q`'s parent fails at `p` with `ENOTDIR`, and no component of `p/q` is missing
where traversal stops. §6 now carries a second case in which the descendant's absence is **inferred from
the ancestor's verified fingerprint** rather than probed, with §9.5's published-directory descriptor
handed down to the descendant exactly as in the missing-ancestor case.

The inference's strength differs by ancestor kind, and §6 states the two branches separately rather than
under one justification. A regular-file ancestor is descriptor-coherent — opened `O_RDONLY | O_NOFOLLOW`,
with type, mode, and content hash all taken from that one descriptor. A **symlink ancestor is not**:
`symlink_fingerprint` is `lstat` plus `readlink`, which §5.5 defines as explicitly not
descriptor-coherent, since `O_NOFOLLOW` fails by design on a symlink leaf. The absence inference itself
holds for both, because neither kind can contain directory entries, but for the symlink branch the
identity guarantee comes from validating the atomically transferred entry against the frozen fingerprint,
never from a descriptor.

A2 must not admit a transition the capture contract cannot express, so the two land together.

The rule constrains only pairs where **both** paths are declared in that surface. An ancestor the
specification never mentions carries no constraint here: whether it exists and is a directory is a live
filesystem question, and answering it is A4's guarded traversal, not A2's.

### Phase 13 — Created-directory ancestor ordering

For every `CreateDirectory` effect with path `P`, every effect with an occurrence on a path strictly
beneath `P` must appear later in the authoritative effect order. Outer creation precedes every affected
descendant, as §6 and §9.5 require, so that each descendant executes relative to a parent descriptor the
engine itself created and verified.

This stays separate from phase 12: phase 12 constrains the declared *states*, phase 13 constrains the
effect *sequence*, and neither implies the other.

The compiler inserts each `CreateDirectory` path into a component trie whose terminal stores the
creator's path and effect index. It then walks each occurrence's proper-ancestor components once,
checking creator indices encountered along that route. It does not expose or call an `ancestors()`
helper and does not materialize joined prefixes; the now-dead helper and its tests are removed rather
than kept for compatibility. The phase is linear in the components across created-directory paths and
effect occurrences, with no component-count or lexical path-length ceiling.

## 6. Error contract

Every specification invalid under the thirteen declared phases raises `SpecValidationError` — A1's
existing exception, unchanged. Caller/model defects are recognized by explicit checks: this includes
wrong scalar or member types, exact A1 dataclass instances with uninitialized slots, hostile runtime
type-name lookup, and every semantic rule in phases 2–13.

`compile_spec` does **not** promise to convert every arbitrary runtime failure into
`SpecValidationError`. The phase pipeline has no blanket `try/except Exception`. An unexpected internal
exception — a programming fault, a failed invariant in compiler code, or a deliberately injected test
fault — propagates unchanged so it cannot be misreported as a caller's specification error.

The only broad catch used for diagnostics is inside `_type_name`, and its `try` surrounds only
`value_type.__name__` after `value_type = type(value)` has completed. Missing slots are not handled by a
catch at all: phase 1 uses `getattr(obj, field_name, _MISSING)` and raises `SpecValidationError`
explicitly when the sentinel is returned. After phase 1 succeeds, the later phases may rely on the
closed, initialized model shape it established.

This explicit boundary is load-bearing in two directions:

- **Malformed model values are refused deliberately.** A `path` field holding an `int` is rejected in
  phase 1 rather than reaching `startswith`; an uninitialized field is rejected as missing rather than
  leaking incidental `AttributeError`.
- **Accepted values satisfy downstream serialization preconditions.** A `CompiledSpec` must be durably
  serializable, so phase 3's UTF-8 rule closes the case where compilation succeeds but
  `canonical_bytes` then raises `UnicodeEncodeError`. "Compilation succeeded" means the specification
  passed every declared A2 rule, not that unrelated internal exceptions have been normalized.

Misusing the proof type's guarded constructor or `dataclasses.replace` raises `TypeError` by design;
mutating a compiled proof raises `dataclasses.FrozenInstanceError`. Those are API-misuse refusals, not
malformed-specification results.

The exception carries a formatted message naming the violated rule and, where applicable, the offending
effect ID and path. Caller integers outside their allowed domains are the exception to value echoing:
`schema_version`, `mode`, and `byte_len` diagnostics contain only fixed expected values or bounds and
never format the rejected integer in any base. `SpecValidationError` gains **no** structured
attributes. A1's decoder already establishes message-only refusal, its tests already assert with
`pytest.raises(..., match=...)`, and a wider error surface would become an unnecessary contract for
compilation callers and the A3/A4 seam.

## 7. Decisions recorded

**The §5.4 payload bullet is reassigned to A6.** Design §5.4 requires that "payload hashes and modes
match the declared postconditions". The A1 model carries no payload field: `ReplaceFile.post` and
`CreateFileNoClobber.post` *are* the declared postcondition, and content bytes arrive out-of-band through
the blob store (§6, §7). There is therefore nothing at A2 for a hash to be checked *against*. The real
check belongs to coherent capture and materialization, which hash the actual stream and compare it to the
frozen `FileState`. What A2 owns under that heading is phase 2 in full, including the empty-file
cross-check. The bullet is reassigned, not dropped.

**The effects sequence is authoritative; dependencies are a redundancy assertion.** §5.1 provides
dependencies "where sequence alone is insufficient", but A2 requires every dependency edge to agree with
the effect sequence (phase 7), so in a compiled spec they carry no scheduling information. They are a
consumer-declared assertion that A2 checks. A7's executor follows the sequence and does not consult
`dependencies` for ordering.

**A zero-effect transaction is refused.** A specification with no effects and empty surfaces satisfies
phases 9 through 13 vacuously — coverage compares three empty sets and there are no timelines to check —
so it would otherwise compile, and the engine would take the project lock, write a durable record, and
commit having mutated nothing.

Design §13.3's "reject missing effects" does not settle this, despite appearances. In context that phrase
sits in a list of coverage-divergence cases — "missing effects, extra effects, invalid ordering, malformed
timelines, payload/mode mismatches, path escapes, and initial/final surface divergence" — where "missing"
and "extra" are the two directions of a declared surface failing to match the effect surface. That is
phase 10, which already implements it. The authority design was simply silent on a transaction that
declares nothing at all.

It is now explicit: §5.4 gains the requirement that a specification declare at least one effect, and A2
enforces it in phase 1, which is the only phase that can. Consumers computing an empty change set skip the
engine rather than transacting over nothing.

**Compilation is pure and deterministic, while refusal is explicit.** `compile_spec` reads nothing
outside its argument. Compiling the same valid specification twice yields equal `CompiledSpec` values
and byte-identical `canonical_bytes(compiled.spec)`, satisfying §13.3's "validate a spec twice, require
identical canonical output". Compilation is also idempotent:
`compile_spec(compile_spec(s).spec) == compile_spec(s)`. Invalid specifications are covered by explicit
phase checks; purity and determinism do not justify catching and relabeling unexpected internal faults.

**Compilation proof is staged, not universal.** `CompiledSpec` means only that A2's pure rules passed.
It deliberately carries no project root, metadata-root identity, lookup policy, resolved topology,
capability result, or live precondition. A4's composed `ProjectApprovedSpec` is the proof accepted by
A5–A8. Keeping two concrete types prevents a caller from treating a portability filter as actual
filesystem approval.

**`byte_len` adopts the durable-store domain early.** Although A2 imports no SQLite module, the value is
destined for §7's SQLite `INTEGER` column. Refusing values outside signed 64-bit range at the pure
boundary prevents a spec from compiling successfully and failing only when metadata is prepared.

## 8. Testing

Every phase gets independent tests for both acceptance and refusal, asserting on the message so the
rule is identified. Only the four load-bearing precedence edges in §5 get multi-violation tests; tests
do not pin error order between independent phases. Beyond per-rule coverage:

- **Proof construction and freezing.** `compile_spec` returns `CompiledSpec`; ordinary direct
  construction and `dataclasses.replace` raise `TypeError`; field assignment raises the exact
  `dataclasses.FrozenInstanceError`.
- **Size-independent caller-integer diagnostics.** `schema_version`, `mode`, and `byte_len` each refuse
  a thousands-of-digits integer under `sys.int_info.default_max_str_digits` and `0`, with the same
  fixed diagnostic in both settings. `byte_len == 2**63 - 1` compiles and survives
  `from_canonical_bytes(canonical_bytes(...))`; `2**63` refuses.
- **Explicit error boundary.** A monkeypatched internal phase raises a test-only exception and that
  exact exception escapes unchanged. Separately, a value whose metaclass raises on `__name__` and exact
  A1 dataclass instances created by `object.__new__` with uninitialized slots are refused with
  `SpecValidationError`. These tests jointly prohibit both incidental caller-error leaks and blanket
  pipeline normalization.
- **Effect-ID portability equivalence.** Distinct safe IDs `e1` and `E1` on otherwise independent effects
  refuse even though exact-string uniqueness passes. Exact duplicates still surface before dependency
  resolution.
- **Linear tree validation.** A path deeper than `sys.getrecursionlimit()` compiles when otherwise
  valid; a standing assertion confirms the compiler and path module have no `ancestors` attribute after
  the iterative component-trie rewrite. The algorithmic proof is the one-split, one-insert, one-walk
  accounting in §5, not a wall-clock threshold. The trie code imports the dataclass field factory under
  a non-colliding name, uses postponed direct `_PathTrieNode` annotations, and passes Ruff and Pyright.
- **One portability-key API.** Path and effect-ID tests import
  `portability_equivalence_key`; `path_equivalence_key` is absent rather than retained as an alias.
- **Stable precedence only.** Multi-violation cases lock phase 1 before interpretation, phase 6 before
  phase 7, phase 8 before surface-map consumers, and phase 10 before phase 11. No test is added solely
  for the other adjacent pairs.
- **Repeated-path timelines.** The `absent → file → absent → file` case from §5.3, proving a path may
  appear in several ordered effects with a continuous timeline, plus a broken variant for each way
  continuity can fail.
- **All five variants.** One specification exercising `ReplaceFile`, `CreateFileNoClobber`, `DeletePath`
  (file and symlink preconditions), `MoveNoClobber`, and `CreateDirectory` together.
- **Ancestor type change.** The `DeletePath("p")` → `CreateDirectory("p")` → `CreateFileNoClobber("p/q")`
  sequence of phase 12 compiles. The contradiction it must stay distinguished from — `p` declared `FILE`
  initially *and* `p/q` declared `FILE` initially — is refused. The reverse direction (a directory
  becoming a file) is not expressible in the closed effect set of §5.2, since `DeletePath` does not
  accept directories, so it is not a case A2 can reach.
- **Zero-effect refusal.** A specification with no effects and empty surfaces is refused, asserted
  explicitly so the vacuous pass through phases 9–13 can never silently become acceptance.
- **Path alias distinctness.** A specification declaring both `docs/a.md` and `docs/A.md`, and one
  declaring NFC and NFD spellings of `café.txt`, are each refused; the byte-identical single-spelling
  case compiles.
- **UTF-8 encodability.** A path and a symlink target each containing an unpaired surrogate are refused
  with `SpecValidationError`. Paired with a regression test asserting that **every** specification
  `compile_spec` accepts can be passed to `canonical_bytes` without raising — the property the rule
  exists to protect.
- **Directly-constructed malformed dataclasses.** Specifications built by calling `TransactionSpec(...)`
  directly rather than through `build_spec`, with ill-typed and unsorted fields, proving `compile_spec`
  is a real boundary and not a checker that trusts its constructor. This is the test that proves the
  error contract of §6. It covers a wrong type at **every** nesting depth — a non-`str` effect path, a
  non-`PathState` in `pre`, a `bool` mode, a non-`SurfaceEntry` in a surface tuple — plus missing slots
  at every model nesting level, each asserted to raise `SpecValidationError` through an explicit phase
  1 check.
- **Determinism and idempotence.** Compile twice, require equal compiled values and identical canonical
  bytes; compile a compiled spec, require an equal result.
- **A1 interoperability.** `from_canonical_bytes(canonical_bytes(compiled.spec)) == compiled.spec`, so
  the compiled canonical form survives the durable-format round trip that fresh-process recovery depends
  on (§8.4).
- **§13.3 alias conformance.** A persistent path aliasing the scratch sigil through a case or NFC/NFD
  variant is refused, in leaf position and in ancestor position.

## 9. Obligations this boundary creates

Compilation admits shapes it does not itself execute, and each admission obliges a later sub-plan.
Those are registered in [`docs/deferred-obligation-ledger.md`](../deferred-obligation-ledger.md) —
entries 1 through 6, 8, and the final-review additions 9 through 11 originate here or at its A4 seam.
The additions lock the staged A4 proof, resolved equivalence topology, and concrete scratch-name
distinctness. The register exists because every finding across review rounds of this document sat at
that seam: a shape A2 permitted whose downstream contract was unwritten. A3 and A4 are reviewed against
it before their plans are written.

## 10. Open items

The final-review production corrections specified in the implementation plan remain unimplemented until
the owner approves this written amendment. The durability-allowlist configuration tuples and SQLite I/O
layer still belong to A4 and A5 respectively and are untouched here.
