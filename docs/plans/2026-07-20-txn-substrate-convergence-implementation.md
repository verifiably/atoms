# Recoverable filesystem effect engine — delivery roadmap

**Date:** 2026-07-21
**Status:** Deferred roadmap — gated on design approval; intentionally task-free (do not resurrect the former task sequence)
**Design:** [`2026-07-20-txn-substrate-convergence-design.md`](2026-07-20-txn-substrate-convergence-design.md)

## Why this document changed

The former implementation plan converged archive, import, and supersede on shared filesystem
primitives, callback-driven ownership marking, and family-owned execution loops. Four review rounds
found valid failure-path defects, including partial fixes in successive rounds. Those findings were
incorporated into the replacement design, but the repeated pattern exposed a deeper boundary issue:
the proposed durable journal would eventually become a second execution authority beside the
in-memory tracker.

The approved replacement architecture makes one durable executor responsible for effects,
ownership, rollback, and recovery. Family plans authenticate and compile into that executor; they do
not orchestrate primitives directly.

This file intentionally contains no executable task checklist. Retaining the old task sequence would
make it too easy to implement an architecture that has now been rejected.

## Preserved findings

The redesign retains the validated requirements from the earlier plan:

- coherent single-file-descriptor capture;
- ancestor-resolved, leaf-retaining containment;
- atomic exchange/quarantine semantics that preserve a last-instant concurrent entry;
- no-clobber file, directory, and move publication;
- destination-parent durability before source-parent durability for moves;
- staged, restartable file/symlink restoration and quarantined created-directory rollback;
- authority checks before staging-survivor mutation;
- versioned import transition surfaces and Gate-B authentication;
- actual mutation-surface auditing;
- separation of Python exception tests from true process-kill tests.

It replaces:

- per-family ownership trackers and callback/index plumbing;
- family-specific apply and rollback loops;
- bare-list rollback and temporary signature bridges;
- a live partial import destination and reservation sentinel;
- transition-role dispatch as the execution language;
- the link/unlink move fallback and its destructive final check/use race;
- the assumption that the future journal can be layered over the family executors unchanged.

## Replacement implementation plans

After the replacement design is reviewed and approved, create two detailed plans.

### Plan A — engine core and supersede vertical slice

Plan A covers the pure effect/recovery model, transaction-spec validation, project lock, journal,
blob store, coherent capture, restartable materialization, effect implementations, recovery engine,
and model/real-filesystem/subprocess tests. It ends with a hard-cut supersede adapter so the engine
has a production consumer.

### Plan B — family convergence and hard cut

Plan B covers archive, import, and cohort-import adapters; import’s saved transition schema and
surface authentication; staged no-clobber import publication; cross-family recovery and mutation
surface acceptance; and deletion of every superseded execution dialect.

The README platform-support statement (Linux and macOS supported; unsupported platforms refuse at
preparation) lands with this hard cut — once the engine actually gates the mutating commands — not
before, so the README never documents behavior that has not shipped.

No feature flag, compatibility executor, or runtime fallback between transaction dialects is
permitted.

## Approval gate

Do not write either implementation plan, change production code, or execute tasks from the former
plan until the owner approves the replacement design. After approval, write and review Plan A before
implementation. Plan B may be drafted after Plan A’s interfaces are settled; it must not redefine
the engine protocol.
