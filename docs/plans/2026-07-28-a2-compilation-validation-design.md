# A2 — Filesystem-independent compilation validation and repeated-path timelines

**Date:** 2026-07-28
**Status:** Design, approved for planning
**Refines:** [`2026-07-23-recoverable-fs-effect-engine-design.md`](2026-07-23-recoverable-fs-effect-engine-design.md) §5.3, §5.4, §13.3
**Depends on:** [`2026-07-23-plan-a1-core-model.md`](2026-07-23-plan-a1-core-model.md) (implemented)

Where this document and the authority design disagree, the authority design wins.

## 1. Decision

A2 adds the engine's **compilation trust boundary** as a pure function:

```python
compile_spec(spec: TransactionSpec) -> CompiledSpec
```

`CompiledSpec` is a frozen value that A3–A8 accept wherever they require trusted input. Everything
downstream of this call may assume the specification is well-formed; nothing downstream re-derives these
invariants.

A2 implements exactly the subset of design §5.4 that is decidable without touching a filesystem. It has
no filesystem, SQLite, or platform dependency, and — like A1 — depends on nothing outside the standard
library.

## 2. Scope

**A2 establishes the trust boundary for:**

- effect-ID uniqueness and dependency validity;
- exact per-variant shape validation;
- safe identifiers and the project-relative path grammar;
- reserved `.#~` scratch-name rejection, under case- and NFC/NFD-equivalence semantics;
- lexical distinctness of declared paths under Unicode caseless matching, so two spellings of one entry
  cannot compile as two timelines;
- UTF-8 encodability of every string that reaches the durable canonical form;
- fingerprint well-formedness (hash spelling, byte lengths, permission-only modes);
- continuous repeated-path timelines (§5.3);
- initial/final surface agreement and exact effect-surface coverage;
- structural consistency between a declared path and its declared ancestors.

**Deferred to A4, unchanged:**

- root and metadata-directory identity, compared by `st_dev`/`st_ino` rather than spelling (§5.4);
- **filesystem-aware path aliasing** — whether two declared paths that A2's lexical key treats as
  distinct nonetheless name one entry. A2 refuses the lexically detectable cases (phase 4); A4 owes the
  remainder against each **parent directory's** actual lookup policy, not the mount's, including for
  paths declared `ABSENT`, where there is no inode to compare and the mutation-time no-clobber guard does
  not contain the case. Now an explicit §5.4 obligation;
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

All three are frozen dataclasses, consistent with A1's model.

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
| `paths.py` | The project-relative path grammar. Purely lexical. | `errors`, `scratch` |
| `timeline.py` | `TimelineOccurrence`, `PathTimeline`, timeline construction and the continuity rule. | `errors`, `effects`, `fingerprint` |
| `compiler.py` | `CompiledSpec`, `compile_spec`, and the phase sequence. | all of the above, plus `spec`, `identifiers` |

`compiler.py`, not `compile.py`, so `from atoms.core import compile` cannot shadow the builtin.

## 5. Validation phases

Compilation proceeds in fail-early phases. **The phase order is part of the contract**: it determines
which violation surfaces first when a specification breaks several rules at once, which is what makes
error assertions in the test suite stable.

Three ordering constraints are forced rather than chosen. Every field must be type-checked (phase 1)
before any later phase interprets one, or a wrong-typed field would raise the exceptions §6 forbids.
Duplicate effect IDs (phase 6) must be rejected before dependency endpoints are resolved (phase 7), or an
ID would not identify one effect. Exact coverage (phase 10) must be proven before timeline endpoints are
compared against the surfaces (phase 11), or the endpoint comparison could look up a path that no surface
declares.

### Phase 1 — Exhaustive structural typing

Every value reachable from the specification is checked against its declared type, **recursively and
exhaustively, before any later phase reads it**. `bool` is refused wherever an integer is required,
mirroring A1's decoder, since `bool` subclasses `int`.

Top level:

- `spec` is a `TransactionSpec`.
- `schema_version` is an integer equal to `SCHEMA_VERSION`.
- `consumer_tag` satisfies A1's safe-identifier grammar (`require_valid_identifier`).
- `intent_digest` matches `^sha256:[0-9a-f]{64}$`.
- `initial_surface` and `final_surface` are tuples of `SurfaceEntry`.
- `effects` is a **non-empty** tuple whose every member is one of the five effect variants.
- `dependencies` is a tuple of `Dependency`.

The non-empty requirement is why an empty specification does not slip through: with no effects, phases 9
through 13 are all vacuously satisfied, so refusing here is the only place it can be caught. See §7.

Nested, for every element of those tuples:

- `SurfaceEntry.path` is a `str`; `SurfaceEntry.state` is an instance of one of the four `PathState`
  classes.
- `Dependency.before` and `.after` are `str`.
- Each effect's `effect_id` is a `str`, and each of its path-valued fields (`path`, or `source` and
  `destination` for `MoveNoClobber`) is a `str`.
- Each effect's state-valued fields (`pre`, `post`, `source_pre`) hold an instance of one of the four
  `PathState` classes. **Which** subclass each field may legally hold is phase 5's question, not this
  one; phase 1 establishes only that the value is a path state at all.
- Within every `PathState` encountered: `content_hash` and `target` are `str`; `mode` and `byte_len` are
  integers and not `bool`.

This phase is exhaustive by construction rather than by inspection: `TransactionSpec` and its members are
plain frozen dataclasses that perform no runtime type checking, so a caller who bypasses `build_spec` can
place any object in any field. Making phase 1 total is what lets phases 2 onward read `effect.path` or
`state.mode` directly, with no defensive checks and no risk of the `AttributeError` or `TypeError` that
§6 prohibits.

The split between phase 1 and phase 5 is deliberate: phase 1 asks "is this a well-typed value at all",
phase 5 asks "is this variant's choice of state legal".

### Phase 2 — Fingerprints

Applied to every `PathState` reachable from the surfaces and from the effects.

- `FileState.content_hash` matches `^sha256:[0-9a-f]{64}$` — lowercase hex only.
- `FileState.byte_len` is an integer `>= 0`.
- Every `mode` (`FileState`, `DirectoryState`, `SymlinkState`) is an integer in `0 .. 0o7777`.
  Permission bits only, never type bits. The range admits setuid, setgid, and sticky, because a setgid
  directory is a legitimate declared postcondition.
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
serialized. That would break both the totality claim of §6 and the canonical-output guarantee of §7.
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
*distinct spellings* may share a name-equivalence key. The key is Unicode caseless matching applied to
the whole path:

```
key(p) = NFC( casefold( NFC(p) ) )
```

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
property worth having — **a specification that compiles is safe to execute on either kind of volume.**
Resolving instead of refusing would mean silently merging two declared timelines, which is precisely the
silent fallback this codebase forbids.

**What remains A4's.** The key above is a fixed approximation of one volume's folding. A filesystem may
alias two paths this key treats as distinct — a locale-sensitive fold, or HFS+'s particular
normalization — so phase 4 is a conservative first filter, never a proof of distinctness. A4 owes the
real check against the volume's actual name equivalence, and §5.4 now carries it as an explicit
compilation obligation.

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

A4 therefore needs a **positive** equivalence determination for absent names, established at compilation,
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

### Phase 6 — Duplicate effect IDs

No effect ID appears twice.

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

## 6. Error contract

Every malformed specification raises `SpecValidationError` — A1's existing exception, unchanged. No
`TypeError`, `KeyError`, `AttributeError`, or assertion escapes `compile_spec` on any input, including a
`TransactionSpec` constructed directly with ill-typed fields rather than through `build_spec`.

Totality is load-bearing in two directions, and phases 1 and 3 are what secure it:

- **Nothing leaks out of `compile_spec`.** Phase 1's exhaustive typing is what makes this true; without
  it, a `path` field holding an `int` would reach phase 3 and raise `AttributeError` from `startswith`.
- **Nothing that compiles can fail downstream.** A `CompiledSpec` must be durably serializable, so phase
  3's UTF-8 rule closes the case where compilation succeeds but `canonical_bytes` then raises
  `UnicodeEncodeError`. "Compilation succeeded" has to mean the specification is usable, not merely
  well-shaped.

The exception carries a formatted message naming the violated rule and, where applicable, the offending
effect ID and path. It gains **no** structured attributes. A1's decoder already establishes
message-only refusal, its tests already assert with `pytest.raises(..., match=...)`, and a wider error
surface would be a contract every one of A3–A8 then has to honor.

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

**Compilation is total and pure.** `compile_spec` reads nothing outside its argument. Compiling the same
specification twice yields equal `CompiledSpec` values and byte-identical `canonical_bytes(compiled.spec)`,
satisfying §13.3's "validate a spec twice, require identical canonical output". Compilation is also
idempotent: `compile_spec(compile_spec(s).spec) == compile_spec(s)`.

## 8. Testing

Every phase gets independent tests for both acceptance and refusal, asserting on the message so the
documented phase order is actually pinned. Beyond per-rule coverage:

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
  non-`PathState` in `pre`, a `bool` mode, a non-`SurfaceEntry` in a surface tuple — each asserted to
  raise `SpecValidationError` rather than `AttributeError` or `TypeError`.
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
entries 1 through 6 and 8 originate here. The register exists because every finding across three review
rounds of this document sat at that seam: a shape A2 permitted whose downstream contract was unwritten.
A3 and A4 are reviewed against it before their plans are written.

## 10. Open items

None. The two decisions design §14 defers — the durability-allowlist configuration tuples and the SQLite
I/O layer — belong to A4 and A5 respectively and are untouched here.
