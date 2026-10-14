---
id: atoms-f5779f
title: Certify the Beliefs publication path under persistence cuts
status: todo
priority: 2
size: xl
complexity: high
process: planned
created: 2026-08-30T18:19:08Z
updated: 2026-10-14T10:23:50Z
depends: []
tags: [migration, beliefs, certification]
---

Outcome: The Atoms persistence-cut and certification machinery exercises the adopted Beliefs publication path end to end, so Beliefs X2 no longer relies only on engine-interior certification.

First milestone: Resolve the current registered consumer checkout and locate the historical X2/adoption-ledger/roadmap references below. Verify which publication command is now adopted, its consumer-side durability boundaries, and any existing X2 research or design work. Record the inspected consumer commit, paths and findings here before designing the harness; an unavailable source is an explicit unknown. The local A8 synthetic scenarios do not establish those consumer facts.

Acceptance evidence: Approve the Atoms-local cross-repository test design and implementation plan; extend the existing record-reconstruct-recover or physical certification harness through the real Beliefs composition-root publication path without creating a second transaction authority; cover every consumer-side durability boundary named by X2; record reproducible zero-violation evidence or an explicit fail-closed result; and run the complete Atoms and affected Beliefs gates.

Sources: docs/plans/2026-08-14-a8-persistence-cut-and-certification-design.md; docs/plans/2026-08-13-a7-effect-recovery-execution-design.md §16; Beliefs' cut-7 X2 accounting; ~/d/beliefs/docs/designs/2026-08-03-redesign-adoption-ledger.md; and ~/d/beliefs/docs/plans/2026-08-29-implementation-roadmap.md persistence-cut row.

Uncertainty: The 2026-09-12 assessment reported that Beliefs' publication path is implemented and its roadmap assigns the prerequisite to an Atoms design gate. Those consumer claims and historical document locations have not been freshly verified in this Atoms-local pass. The cross-repository harness boundary, hardware matrix, and verified owner are not yet designed.

Established at Atoms HEAD 9391c72: A8's design deliberately imports no real consumer; python/tests/exerciser.py and python/tests/persistence_model.py provide the synthetic workload and record-reconstruct-recover machinery, while python/tools/certify/ supplies the physical harness. The deferred-obligation ledger has no open engine obligations. Extend this machinery through the verified consumer composition path; distinguish model evidence, real-process recovery and physical crash certification in the reviewed design.

## Notes

- 2026-09-12T16:35:05Z (main): Complexity high: A8 provides engine scenario/cut machinery, while the Beliefs adoption ledger and roadmap still assign X2 consumer-path persistence cuts to an Atoms design gate. Defining the cross-repository publication boundaries, oracle coverage and certification matrix without a second transaction authority remains unresolved design work.
