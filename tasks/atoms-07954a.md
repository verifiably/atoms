---
id: atoms-07954a
title: Resolve existing tilde-layout findings before checker rollout
status: todo
priority: 2
size: m
complexity: mid
process: direct
created: 2026-10-22T19:06:10Z
updated: 2026-10-22T19:06:10Z
depends: []
tags: []
source: ops-295f8d
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
