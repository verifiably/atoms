# A4b-2 final fix report

Date: 2026-07-31

Reviewed base: `0f64211e0c331e5edcd11c56326f984d70d48cec`

## Outcome

All seven whole-branch review findings are fixed in one final wave. The behavioral
defects were reproduced before production edits, covered by regressions, and verified
both in focused suites and in the complete repository gates. The pure/IO boundary is
unchanged: `topology.py` and `judgment.py` remain pure, `approval.py` still propagates
resolver exceptions without handling them, and approval still performs no project-space
write.

## Reproduction and correction

### 1. Unresolved `NAME_MAX`

Root cause: `PathResolver.resolve()` necessarily stopped at its first absent or blocking
frontier. `_facts_by_prefix()` derived the remaining planned directory facts, but neither
ancestor judgment nor endpoint distinctness checked the UTF-8 byte length of those
unwalked names against their actual or inherited parent constraints.

RED command:

```text
.venv/bin/pytest tests/test_fs_judgment.py -k 'planned_ancestor_over or planned_leaf_over or move_source_can' -vv
```

Captured RED result:

```text
FAILED tests/test_fs_judgment.py::test_a_planned_ancestor_over_its_parents_name_max_is_refused - Failed: DID NOT RAISE <class 'atoms.core.errors.ProjectApprovalRefused'>
FAILED tests/test_fs_judgment.py::test_a_move_source_can_be_converted_from_a_file_to_a_directory - atoms.core.errors.ProjectApprovalRefused: 'p' exists and is not a directory, but no effect removes it before effect 1 creates a directory there
FAILED tests/test_fs_judgment.py::test_a_planned_leaf_over_its_parents_name_max_is_refused - Failed: DID NOT RAISE <class 'atoms.core.errors.ProjectApprovalRefused'>
3 failed
```

Correction: `require_ancestors_legal()` checks each derived ancestor component against
the constraints of its resolved parent node, and `require_endpoints_distinct()` checks
every persistent leaf before keying it. Both use one `_require_name_fits()` helper and
UTF-8 byte counts. Pure planned-ancestor/planned-leaf tests and real-ext4 conformance
tests cover both paths.

### 2. `MoveNoClobber` source removal

Root cause: ancestor conversion indexed `CreateDirectory` creators and `DeletePath`
removers only. It omitted the regular-file source of `MoveNoClobber`, although that
effect also produces absence at its source.

The same RED command above captured the exact refusal:

```text
atoms.core.errors.ProjectApprovalRefused: 'p' exists and is not a directory, but no effect removes it before effect 1 creates a directory there
```

Correction: the effect scan now handles its closed variants explicitly and records a
`MoveNoClobber.source` as a remover. A pure judgment test and a real-filesystem approval
test exercise move-source, create-directory, descendant-create in order.

### 3. Cross-resolution prefix drift

Root cause: `_facts_by_prefix()` assigned facts by lexical prefix without comparing a
new observation with an earlier observation. A later resolution could therefore replace
the identity/constraints of an earlier directory, or combine an earlier directory with
a later absent/blocking frontier.

RED command:

```text
.venv/bin/pytest tests/test_fs_topology.py -k 'replaced_directory_between or directory_that_becomes or live_created_parent or planned_directories_merge' -vv
```

Captured RED result:

```text
FAILED tests/test_fs_topology.py::test_a_replaced_directory_between_resolutions_is_refused - Failed: DID NOT RAISE <class 'atoms.core.errors.PreconditionRefused'>
FAILED tests/test_fs_topology.py::test_a_directory_that_becomes_a_frontier_between_resolutions_is_refused[absent] - Failed: DID NOT RAISE <class 'atoms.core.errors.PreconditionRefused'>
FAILED tests/test_fs_topology.py::test_a_directory_that_becomes_a_frontier_between_resolutions_is_refused[blocking-file] - Failed: DID NOT RAISE <class 'atoms.core.errors.PreconditionRefused'>
4 failed, 1 passed
```

Correction: topology construction records root, hop, and frontier observations for every
lexical prefix before constructing nodes. Exact repetitions and a directory hop paired
with a `DIRECTORY` frontier of the same identity are compatible; every other change
raises `PreconditionRefused`. Tests cover directory replacement and transitions from a
directory to both absence and a blocking regular file.

The new coherence rule exposed an incoherent synthetic test helper for the generated
A2/A3 agreement property. For example, that helper described `a` as absent when resolving
the declared endpoint and as an existing directory when resolving `a/b`. `prefixes_for()`
now models a declared endpoint that is also another declared path's live ancestor as the
same directory at both walks. This keeps the fixture at one filesystem moment; A6 still
owns fingerprint/precondition agreement.

### 4. Live `CreateDirectory` endpoint classification

Root cause: directory proof classification depended only on whether `_facts_by_prefix()`
had an identity. When a live `CreateDirectory` endpoint was also traversed for a
descendant, its hop supplied an identity and the parent `PersistentNode` was emitted as
`ApprovedExistingDirectory` with observed constraints.

The topology RED command above captured:

```text
FAILED tests/test_fs_topology.py::test_a_live_created_parent_is_still_planned - AssertionError: assert False
```

The independent real-filesystem RED command was:

```text
.venv/bin/pytest tests/test_fs_approval_conformance.py -k 'move_source_converted or live_created_parent or planned_component_over' -vv
```

Captured RED result:

```text
FAILED tests/test_fs_approval_conformance.py::test_a_move_source_converted_to_a_directory_approves_on_disk
FAILED tests/test_fs_approval_conformance.py::test_a_live_created_parent_is_approved_as_planned
FAILED tests/test_fs_approval_conformance.py::test_a_planned_component_over_name_max_is_refused[planned-leaf]
FAILED tests/test_fs_approval_conformance.py::test_a_planned_component_over_name_max_is_refused[planned-ancestor]
4 failed
```

Correction: created candidates always replace observed constraints with constraints
inherited from their approved parent. `_directory_entries()` classifies nodes named by a
transaction `CreateDirectory` as `ApprovedPlannedDirectory` even when a compatible live
identity was observed. Pure and real-ext4 regressions prove the created parent remains
planned; the pure test uses deliberately different observed/inherited `name_max` values
to prove the inherited constraints won. The existing exact-partition test remains green.

### 5. Deferred-obligation ledger #9

Root cause: A4b's factory guard was complete, but the discharged row also claimed future
A5–A8 entry points accepted only `ProjectApprovedSpec`. Those entry points do not exist,
so that half could not have been verified.

Correction: #9 is restored to the open table, narrowed to A5 and explicit A5–A8
entry-point enforcement. `AGENTS.md` and the A4b-2 design record the completed factory
half. The discharged #9 row is removed. The architecture guard parses both ledger
sections and proves the obligation is open, assigned to A5, names A5–A8, and is not also
discharged.

### 6. Folding topology through A3

Root cause: the injected-equivalence `A` / `a/x` regression checked node merging only;
it did not prove that the resulting production topology satisfies A3's snapshot
constructor.

Correction: the test passes the merged topology through the existing
`_prepared_snapshot()` helper and asserts A3 retains the same topology.

### 7. Authority-document status

Root cause: `AGENTS.md` reflected the completed implementation while both the A4b-2
design and implementation plan still said unimplemented.

RED command:

```text
.venv/bin/pytest tests/test_fs_architecture.py::test_a4b_status_is_synchronized_across_authority_documents -vv
```

Captured RED result:

```text
FAILED tests/test_fs_architecture.py::test_a4b_status_is_synchronized_across_authority_documents - AssertionError: assert 'Designed 2026-07-31; unimplemented. A5–A8 remain unimplemented. A4b-2 reads project space and never writes to it.' == 'Implemented on 2026-07-31. A5–A8 remain unimplemented. A4b-2 reads project space and never writes to it.'
1 failed
```

Correction: both documents now use the same implemented status paragraph. The
synchronized-status test reads and compares both status paragraphs in addition to the
`AGENTS.md` status marker and ledger #9 state.

## Files changed

- `python/src/atoms/fs/judgment.py`: derived/final `NAME_MAX` judgment and
  `MoveNoClobber` source removal.
- `python/src/atoms/fs/topology.py`: repeated-prefix observation coherence and planned
  classification/inherited constraints for transaction-created directories.
- `python/tests/fs_support.py`: coherent repeated-prefix data for the exhaustive
  generated-specification fixture.
- `python/tests/test_fs_judgment.py`: pure regressions for planned names and move-source
  conversion.
- `python/tests/test_fs_topology.py`: drift, live-created classification, and A3 folding
  regressions.
- `python/tests/test_fs_approval_conformance.py`: real-ext4 move-source, live-created,
  and planned-name regressions.
- `python/tests/test_fs_architecture.py`: status and ledger #9 synchronization guards,
  plus explicit pure-import allowlist updates.
- `AGENTS.md`: completed A4b status with only the factory half of #9 complete.
- `docs/deferred-obligation-ledger.md`: move-source wording for #3 and reopened/narrowed
  #9.
- `docs/plans/2026-07-23-recoverable-fs-effect-engine-design.md`: unwalked-name,
  repeated-observation, and move-source authority clarifications.
- `docs/plans/2026-07-31-a4b2-project-approval-design.md`: implemented status and the
  corrected contracts, verification matrix, and acceptance criteria.
- `docs/plans/2026-07-31-plan-a4b2-project-approval.md`: implemented status and a final
  correction record that supersedes stale historical task snippets.

## GREEN evidence

The three behavioral RED selections passed after their respective fixes:

```text
.venv/bin/pytest tests/test_fs_judgment.py -k 'planned_ancestor_over or planned_leaf_over or move_source_can' -vv
============================== 3 passed ===============================

.venv/bin/pytest tests/test_fs_topology.py -k 'replaced_directory_between or directory_that_becomes or live_created_parent or planned_directories_merge' -vv
============================== 5 passed ===============================

.venv/bin/pytest tests/test_fs_approval_conformance.py -k 'move_source_converted or live_created_parent or planned_component_over' -vv
============================== 4 passed ===============================
```

Focused suite evidence after the final fixture adjustment:

```text
.venv/bin/pytest tests/test_fs_judgment.py tests/test_fs_topology.py -q
.................................................................        [100%]

.venv/bin/pytest tests/test_fs_approval.py tests/test_fs_approval_conformance.py -q
.............................................                            [100%]

.venv/bin/pytest tests/test_fs_architecture.py -q
...............................................................          [100%]

.venv/bin/ruff check src/atoms/fs/judgment.py src/atoms/fs/topology.py tests/fs_support.py tests/test_fs_judgment.py tests/test_fs_topology.py tests/test_fs_approval_conformance.py tests/test_fs_architecture.py
All checks passed!

.venv/bin/pyright
0 errors, 0 warnings, 0 informations
```

The complete repository gates were then run once from `python/`, exactly as required:

```text
uv run pytest
4778 passed, 7 skipped in 21.31s

uv run ruff check
All checks passed!

uv run pyright
0 errors, 0 warnings, 0 informations
```

The seven skips are the repository's existing environment-dependent skips; there were
no failures or unexpected errors.

## Design, plan, and ledger amendments

- The authority design now states that `NAME_MAX` applies to unwalked suffixes using
  actual/inherited parent constraints, repeated prefix observations must agree, and a
  regular-file move source is a valid remover in the conversion timeline.
- The A4b-2 design corrects phase ownership, construction/judgment ordering, exception
  semantics, created-directory classification, verification cases, acceptance criteria,
  and the partial status of #9.
- The implementation plan retains its historical tasks but adds a dated final correction
  record and corrects its file-responsibility and ledger instructions.
- Ledger #3 names both deletion mechanisms. Ledger #9 follows the existing partial-
  discharge convention: its factory half is documented as complete while future
  consumer enforcement remains open under A5.
- All three status surfaces now say A4b-2 was implemented on 2026-07-31 while A5–A8
  remain unimplemented.

## Self-review

- Traced every new production branch from `approve_for_project()` through topology and
  judgment and back into `ProjectApprovedSpec`; `approval.py` required no change.
- Confirmed the new imports in `judgment.py` and `topology.py` are value types and pure
  helpers only. Neither module imports `os`, a backend, or resolver I/O.
- Confirmed repeated-prefix comparison runs before node/key construction and never
  overwrites incompatible observations.
- Confirmed planned-name checks use the parent node's retained actual/inherited
  constraints, count UTF-8 bytes, and cover both derived ancestors and final leaves.
- Confirmed the move-source rule is limited by A2's closed `MoveNoClobber.source_pre`
  type (`FileState`) and does not admit directory or unknown removals.
- Confirmed a transaction-created parent uses inherited constraints and is planned even
  if resolution observed a compatible live directory; A6 retains ownership of the live
  precondition mismatch.
- Confirmed no exception-catching or silent fallback was added, no approval write path
  changed, no compatibility layer or dependency was introduced, and no unrelated
  optimization was taken.
- Confirmed `final-review-findings.md` and the SDD progress ledger were not modified.

## Concerns

No unresolved concern blocks A4b-2. Ledger #9 intentionally remains open until the
future A5–A8 transaction entry points exist and can be guarded. A5–A8 remain outside
this fix wave, as recorded in every synchronized status surface.
