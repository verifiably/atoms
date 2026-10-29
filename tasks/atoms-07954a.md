---
id: atoms-07954a
title: Resolve existing tilde-layout findings before checker rollout
status: done
priority: 2
size: m
complexity: mid
process: direct
owner: fix/atoms-07954a
created: 2026-10-22T19:06:10Z
updated: 2026-10-29T20:51:10Z
started: 2026-10-29T20:26:35Z
completed: 2026-10-29T20:51:10Z
closed: 2026-10-29T20:51:10Z
depends: []
tags: []
source: ops-295f8d
model: claude-opus-5-5
agent: codex
---

Why: the final candidate from ops-295f8d reports existing home-relative machine-layout paths that the current checker misses. Resolve these before the stricter vendored copy arrives, so unrelated commits are not blocked. These are scanner findings to classify, not an instruction to discard historical documents.

Done: inspect each listed file in context. Replace accidental checkout/storage paths in shared docs, examples or fixtures with repository-relative paths, project-role references or portable URLs as appropriate. For intentional host configuration, use only the narrow existing layout_allowed mechanism where this repository is host configuration by design; do not exempt ordinary docs wholesale. Preserve historical evidence, attachments and every spec/plan review ledger; follow the task attachment CLI and retention rules instead of editing attached artifacts by hand. If a runtime-path correction needs an unsettled public contract, change that piece to planned before implementation. Run the project-declared checks and the reviewed candidate checker by explicit path, then record every listed file disposition and the clean verdict on this task. Do not repoint shared launchers, vendor the checker, publish, push or activate global guidance in this task.

Where: the current project guide/profile, listed tracked files, existing layout_allowed, and ops-295f8d for candidate branch/path and canonical report. The full sweep reached 36 registered repositories with no unavailable roots; counts here are capped scanner records, not an estimate of all occurrences. No source snippets are copied.

The known python/README.md defect stays with atoms-4368ba and is excluded from this list.

Reported files:
- docs/2026-08-23-root-lifecycle-commands-design.md: first reported line 7; 4 reported finding(s)
- docs/2026-08-24-holdings-read-and-evidence-commands-design.md: first reported line 15; 1 reported finding(s)
- docs/plans/2026-07-23-plan-a1-core-model.md: first reported line 35; 6 reported finding(s)
- docs/plans/2026-07-23-recoverable-fs-effect-engine-design.md: first reported line 10; 1 reported finding(s)
- docs/plans/2026-07-28-plan-a2-compilation-validation.md: first reported line 29; 1 reported finding(s)
- docs/plans/2026-07-28-plan-a3-recovery-reference-model.md: first reported line 52; 1 reported finding(s)
- docs/plans/2026-07-29-plan-a4a-capability-backend.md: first reported line 59; 1 reported finding(s)
- docs/plans/2026-07-30-a4b1-path-resolution-design.md: first reported line 108; 8 reported finding(s)
- docs/plans/2026-07-30-plan-a4b1-path-resolution.md: first reported line 28; 3 reported finding(s)
- docs/plans/2026-07-31-a4b2-project-approval-design.md: first reported line 141; 1 reported finding(s)
- docs/plans/2026-07-31-plan-a4b2-project-approval.md: first reported line 51; 2 reported finding(s)
- docs/plans/2026-08-01-plan-a5a-metadata-store.md: first reported line 33; 2 reported finding(s)
- docs/plans/2026-08-02-plan-a5b-recovery-lease.md: first reported line 73; 2 reported finding(s)
- docs/plans/2026-08-07-plan-a6-coherent-capture.md: first reported line 85; 1 reported finding(s)
- docs/plans/2026-08-30-atoms-tasks-migration.md: first reported line 0; 12 reported finding(s)

## Notes

- 2026-10-29T20:26:35Z (main): started
  provenance: {"harness_session":"claude-code:0abf3f61-62e6-4a09-a944-9063cc887bf3","harness_session_source":"CLAUDE_CODE_SESSION_ID"}
- 2026-10-29T20:27:36Z (fix/atoms-07954a): resumed
  provenance: {"harness_session":"claude-code:0abf3f61-62e6-4a09-a944-9063cc887bf3","harness_session_source":"CLAUDE_CODE_SESSION_ID"}
- 2026-10-29T20:43:37Z (fix/atoms-07954a): dispositions: docs/2026-08-23-root-lifecycle-commands-design.md:7,103,1024,1055 — reworded (beliefs authority to <beliefs checkout>/..., own paths repository-relative); docs/2026-08-24-holdings-read-and-evidence-commands-design.md:15 — reworded (<beliefs checkout>/...); docs/plans/2026-07-23-plan-a1-core-model.md:35,39,50,189,196,1967 — reworded (nodes paths to <nodes checkout>/..., docs convention to <checkout>/, LICENSE repository-relative, quoted README line to <atoms checkout>/README.md); docs/plans/2026-07-28-plan-a2-compilation-validation.md:29 — reworded (docs convention to <checkout>/); docs/plans/2026-07-28-plan-a3-recovery-reference-model.md:52, docs/plans/2026-07-29-plan-a4a-capability-backend.md:59, docs/plans/2026-08-07-plan-a6-coherent-capture.md:85 — reworded (docs-path convention to <atoms checkout>/...); docs/plans/2026-07-30-a4b1-path-resolution-design.md:108,110,113,750,751,761,764,765 — reworded (module layout repository-relative with column realignment of 109,111,112; casefold setup snippet and prose to <atoms checkout> and its sibling <atoms checkout>/../atoms-test-volumes/, 764-765 rewrapped); docs/plans/2026-07-30-plan-a4b1-path-resolution.md:28,56,3261, docs/plans/2026-07-31-plan-a4b2-project-approval.md:51,82, docs/plans/2026-08-01-plan-a5a-metadata-store.md:33,100, docs/plans/2026-08-02-plan-a5b-recovery-lease.md:73,138 — reworded (Work from python/; docs-path convention to <atoms checkout>/...; saved-plan path repository-relative); docs/plans/2026-07-31-a4b2-project-approval-design.md:141 — reworded (python/src/atoms/fs/); docs/plans/2026-08-30-atoms-tasks-migration.md:34-39,102,158,159,181,182,204,213,214,216,217,220-222,224,225,230 — allowed: exact-file layout_allowed entry, historical evidence ledger (worktree inventory, reviewed task bodies, recorded verification commands name the audited checkouts)
- 2026-10-29T20:49:02Z (fix/atoms-07954a): verification: candidate rc=0 at 76a36f1 (ops-295f8d bin/ops-check, run by explicit path from the worktree top level; vendored tools/ops-check also rc=0); just check passed (ops-check, ops-docs check, ruff, pyright 0 errors, tasks check); just test-one tests/test_docs_status.py 8 passed; git diff --check main...HEAD clean; baseline just test at worktree creation (no .tt.toml, so policy absent): 6310 passed, 7 skipped in 596.67s under a 1-worker host budget; the first attempt was stopped by my own 580s timeout, not a failure. Doc edits landed while the baseline ran; they touch no file the suite reads for status
- 2026-10-29T20:49:28Z (fix/atoms-07954a): parked (waiting on agent): controller: review fix/atoms-07954a at 76a36f1 in .worktrees/atoms-07954a, then integrate
  provenance: {"harness_session":"claude-code:0abf3f61-62e6-4a09-a944-9063cc887bf3","harness_session_source":"CLAUDE_CODE_SESSION_ID"}
- 2026-10-29T20:50:40Z (fix/atoms-07954a): resumed
  provenance: {"harness_session":"claude-code:0abf3f61-62e6-4a09-a944-9063cc887bf3","harness_session_source":"CLAUDE_CODE_SESSION_ID"}
- 2026-10-29T20:50:56Z (fix/atoms-07954a): review: impl round 1 — verdict: accept; findings: none; reviewer: claude-code/claude-opus-5-5
- 2026-10-29T20:51:10Z (fix/atoms-07954a): done
  provenance: {"harness_session":"claude-code:0abf3f61-62e6-4a09-a944-9063cc887bf3","harness_session_source":"CLAUDE_CODE_SESSION_ID"}
- 2026-10-29T20:51:10Z (fix/atoms-07954a): Reworded 33 checkout aliases across two designs and 11 plans to repository-relative paths and checkout placeholders; allowed the tasks-migration ledger (recorded audit evidence) by exact-file entry; fast-forwarded into main; candidate exits 0 on main; just check passed; baseline just test 6310 passed
  provenance: {"harness_session":"claude-code:0abf3f61-62e6-4a09-a944-9063cc887bf3","harness_session_source":"CLAUDE_CODE_SESSION_ID"}
