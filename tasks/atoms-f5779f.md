---
id: atoms-f5779f
title: Certify the Beliefs publication path under persistence cuts
status: todo
priority: 2
size: xl
created: 2026-08-30T18:19:08Z
updated: 2026-08-30T20:28:33Z
depends: []
tags: [migration, beliefs, certification]
---

Outcome: The Atoms persistence-cut and certification machinery exercises the adopted Beliefs publication path end to end, so Beliefs X2 no longer relies only on engine-interior certification.

Acceptance evidence: Approve the Atoms-local cross-repository test design; extend the existing record-reconstruct-recover or physical certification harness through the real Beliefs composition-root publication path without creating a second transaction authority; cover every consumer-side durability boundary named by X2; record reproducible zero-violation evidence or an explicit fail-closed result; and run the complete Atoms and affected Beliefs gates.

Sources: docs/plans/2026-08-14-a8-persistence-cut-and-certification-design.md; docs/plans/2026-08-13-a7-effect-recovery-execution-design.md §16; Beliefs' cut-7 X2 accounting; ~/d/beliefs/docs/designs/2026-08-03-redesign-adoption-ledger.md; and ~/d/beliefs/docs/plans/2026-08-29-implementation-roadmap.md persistence-cut row.

Uncertainty: Beliefs' publication path is implemented and its roadmap assigns the prerequisite to an Atoms design gate, but the cross-repository harness boundary, hardware matrix, and verified owner are not yet designed.
