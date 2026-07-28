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
- fingerprint well-formedness (hash spelling, byte lengths, permission-only modes);
- continuous repeated-path timelines (§5.3);
- initial/final surface agreement and exact effect-surface coverage;
- structural consistency between a declared path and its declared ancestors.

**Deferred to A4, unchanged:**

- root and metadata-directory identity, compared by `st_dev`/`st_ino` rather than spelling (§5.4);
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

Three modules under `python/src/atoms/core/`, rather than one, because twelve rules in a single file is
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

Two ordering constraints are forced rather than chosen. Duplicate effect IDs (phase 5) must be rejected
before dependency endpoints are resolved (phase 6), or an ID would not identify one effect. Exact
coverage (phase 9) must be proven before timeline endpoints are compared against the surfaces (phase 10),
or the endpoint comparison could look up a path that no surface declares.

### Phase 1 — Structure and header

Exact runtime types on every container and scalar. `bool` is refused wherever an integer is required,
mirroring A1's decoder, since `bool` subclasses `int`.

- `spec` is a `TransactionSpec`.
- `schema_version` is an integer equal to `SCHEMA_VERSION`.
- `consumer_tag` satisfies A1's safe-identifier grammar (`require_valid_identifier`).
- `intent_digest` matches `^sha256:[0-9a-f]{64}$`.
- `initial_surface` and `final_surface` are tuples of `SurfaceEntry`, each with a `str` path and a state
  that is one of the four `PathState` classes.
- `effects` is a tuple whose every member is one of the five effect variants.
- `dependencies` is a tuple of `Dependency`, each with `str` endpoints.

### Phase 2 — Fingerprints

Applied to every `PathState` reachable from the surfaces and from the effects.

- `FileState.content_hash` matches `^sha256:[0-9a-f]{64}$` — lowercase hex only.
- `FileState.byte_len` is an integer `>= 0`.
- Every `mode` (`FileState`, `DirectoryState`, `SymlinkState`) is an integer in `0 .. 0o7777`.
  Permission bits only, never type bits. The range admits setuid, setgid, and sticky, because a setgid
  directory is a legitimate declared postcondition.
- `SymlinkState.target` is a non-empty string containing no NUL. It is **not** subject to the
  project-relative path grammar of phase 3: a symlink target may legitimately be absolute or contain
  `..`, and the engine treats it as opaque bytes it fingerprints rather than a path it resolves (§6).
- Empty-file cross-check: `byte_len == 0` if and only if `content_hash` is the SHA-256 of the empty
  string (`sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`).

### Phase 3 — Path grammar

Applied to every effect path (`ReplaceFile.path`, `CreateFileNoClobber.path`, `DeletePath.path`,
`MoveNoClobber.source`, `MoveNoClobber.destination`, `CreateDirectory.path`) and every
`SurfaceEntry.path`.

A path is valid when it is:

- non-empty and free of NUL;
- without a leading `/` (project-relative, never absolute);
- without a trailing `/`;
- free of empty components — `a//b` is refused;
- free of any `.` or `..` component;
- free of any component for which `aliases_scratch_sigil` is true.

The scratch check applies to **every** component, not only the leaf, and uses the equivalence-aware
predicate rather than the plain prefix test. §5.1 requires the case- and NFC/NFD-normalization semantics,
and §13.3 makes that an explicit conformance obligation. Checking every component also refuses
`a/.#~b/c.txt`, where a scratch-shaped directory sits in the ancestor chain; the engine never creates a
scratch directory in a live parent (§9.5 stages directory creation in `work/`), so such a component is
foreign debris and refusing to operate beneath it is the conservative reading.

### Phase 4 — Effect ID and exact variant shape

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

### Phase 5 — Duplicate effect IDs

No effect ID appears twice.

### Phase 6 — Dependencies

- `before` and `after` each name a declared effect ID.
- `before != after` — no self-edge.
- No `(before, after)` pair appears twice.
- `before` precedes `after` in the authoritative effect order.

### Phase 7 — Surface well-formedness

Within each surface independently, no path appears twice. Duplicates are **rejected**, never silently
deduplicated: two entries for one path with conflicting states are a consumer error, and collapsing them
would be exactly the silent fallback this codebase forbids.

### Phase 8 — Timelines

Occurrences are grouped by path in the authoritative order. For each path, every consecutive pair must
satisfy `occurrence[i].post == occurrence[i + 1].pre` — the continuity requirement of §5.3. Equality is
structural, over A1's frozen state dataclasses.

A path touched by exactly one effect forms a one-occurrence timeline, which is trivially continuous and
still subject to phase 10.

### Phase 9 — Exact coverage

The set of paths appearing in any effect occurrence, the set of `initial_surface` paths, and the set of
`final_surface` paths are all equal. This is §5.4's "the effect surface equals the declared transition
surface exactly; no persistent path is omitted or undeclared".

### Phase 10 — Timeline endpoints

For every timeline: its first occurrence's `pre` equals the declared `initial_surface` state for that
path, and its last occurrence's `post` equals the declared `final_surface` state.

### Phase 11 — Surface tree consistency

Applied to the initial and final surfaces independently. For declared paths `p` and `q` where `q` is
strictly beneath `p` (that is, `q` starts with `p + "/"`):

- if `p` is `ABSENT`, then `q` must be `ABSENT` — nothing can exist beneath a directory that does not;
- if `p` is a `FILE` or a `SYMLINK`, the specification is refused outright — nothing can exist beneath a
  non-directory;
- if `p` is a `DIRECTORY`, `q` is unconstrained.

This is a property of the two declared surfaces alone and makes no reference to effects. It catches
contradictory specifications that the per-effect rules accept — for instance `CreateDirectory("a/b")`
together with `DeletePath("a/b/c", pre=FileState(...))`, where `a/b` is declared absent initially and so
`a/b/c` cannot be a file.

The rule constrains only pairs where **both** paths are declared in that surface. An ancestor the
specification never mentions carries no constraint here: whether it exists and is a directory is a live
filesystem question, and answering it is A4's guarded traversal, not A2's.

### Phase 12 — Created-directory ancestor ordering

For every `CreateDirectory` effect with path `P`, every effect with an occurrence on a path strictly
beneath `P` must appear later in the authoritative effect order. Outer creation precedes every affected
descendant, as §6 and §9.5 require, so that each descendant executes relative to a parent descriptor the
engine itself created and verified.

This stays separate from phase 11: phase 11 constrains the declared *states*, phase 12 constrains the
effect *sequence*, and neither implies the other.

## 6. Error contract

Every malformed specification raises `SpecValidationError` — A1's existing exception, unchanged. No
`TypeError`, `KeyError`, `AttributeError`, or assertion escapes `compile_spec` on any input, including a
`TransactionSpec` constructed directly with ill-typed fields rather than through `build_spec`.

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
the effect sequence (phase 6), so in a compiled spec they carry no scheduling information. They are a
consumer-declared assertion that A2 checks. A7's executor follows the sequence and does not consult
`dependencies` for ordering.

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
- **Directly-constructed malformed dataclasses.** Specifications built by calling `TransactionSpec(...)`
  directly rather than through `build_spec`, with ill-typed and unsorted fields, proving `compile_spec`
  is a real boundary and not a checker that trusts its constructor. This is the test that proves the
  error contract of §6.
- **Determinism and idempotence.** Compile twice, require equal compiled values and identical canonical
  bytes; compile a compiled spec, require an equal result.
- **A1 interoperability.** `from_canonical_bytes(canonical_bytes(compiled.spec)) == compiled.spec`, so
  the compiled canonical form survives the durable-format round trip that fresh-process recovery depends
  on (§8.4).
- **§13.3 alias conformance.** A persistent path aliasing the scratch sigil through a case or NFC/NFD
  variant is refused, in leaf position and in ancestor position.

## 9. Open items

None. The two decisions design §14 defers — the durability-allowlist configuration tuples and the SQLite
I/O layer — belong to A4 and A5 respectively and are untouched here.
