---
id: atoms-8be2dc
title: Ship the A9 macOS backend and certification suite
status: todo
priority: 2
size: xl
complexity: high
created: 2026-08-30T18:18:43Z
updated: 2026-09-12T16:35:04Z
depends: []
tags: [migration, macos, plan-a]
---

Outcome: Plan A gains a macOS backend satisfying the existing semantic capability vocabulary and recovery tables, with unsupported volumes or capabilities continuing to fail closed.

Acceptance evidence: Approve an Atoms-local A9 design and implementation plan; implement the macOS anchored traversal, atomic rename, no-clobber transfer, full-durability barrier, volume probe, and configuration binding; run the model, real-filesystem, subprocess-recovery, and persistence-cut suites on macOS; add canonical crash-certification evidence for every admitted tuple; and move the guarded roadmap boundary past A9 only when the complete Python gate passes.

Sources: docs/plans/2026-07-23-recoverable-fs-effect-engine-design.md §5.5, §13, §14, and §16; AGENTS.md; README.md; and python/tests/test_docs_status.py.

Uncertainty: No A9 design, backend, certification record, active branch, or verified owner exists, and capability availability remains volume-specific.

## Notes

- 2026-09-12T16:35:04Z (main): Complexity high: authority sections 5.5, 13, 14 and 16 define guarantees, but the tree still has only the Linux backend and FIRST_UNIMPLEMENTED=A9, with no A9 design. Anchored traversal, volume-specific durability resolution and physical certification on macOS require substantial platform discovery and architectural judgment.
