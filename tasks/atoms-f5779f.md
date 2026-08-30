---
id: atoms-f5779f
title: Certify the Science publication path under persistence cuts
status: todo
priority: 2
size: xl
created: 2026-08-30T18:19:08Z
updated: 2026-08-30T18:19:08Z
depends: []
tags: [migration, science, certification]
---

Outcome: The Atoms persistence-cut and certification machinery exercises the adopted Science publication path end to end, so Science X2 no longer relies only on engine-interior certification.

Acceptance evidence: Approve the Atoms-local cross-repository test design; extend the existing record-reconstruct-recover or physical certification harness through the real Science composition-root publication path without creating a second transaction authority; cover every consumer-side durability boundary named by X2; record reproducible zero-violation evidence or an explicit fail-closed result; and run the complete Atoms and affected Science gates.

Sources: docs/plans/2026-08-14-a8-persistence-cut-and-certification-design.md; docs/plans/2026-08-13-a7-effect-recovery-execution-design.md §16; Science's cut-7 X2 accounting; ~/d/science/docs/designs/2026-08-03-redesign-adoption-ledger.md; and ~/d/science/docs/plans/2026-08-29-implementation-roadmap.md persistence-cut row.

Uncertainty: Science's publication path is implemented and its roadmap assigns the prerequisite to an Atoms design gate, but the cross-repository harness boundary, hardware matrix, and verified owner are not yet designed.
