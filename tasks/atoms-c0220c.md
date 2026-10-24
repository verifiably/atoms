---
id: atoms-c0220c
title: "Authority design §12.2 cites §15 for the out-of-process seam's shape, but §15 never mentions it"
status: todo
priority: 3
size: xs
complexity: low
process: direct
created: 2026-10-24T19:01:37Z
updated: 2026-10-24T19:01:37Z
depends: []
tags: [docs]
source: vdocs-e4318e
agent: claude-code
---

Found by the second guided tour (vdocs-e4318e, 2026-10-24). docs/plans/2026-07-23-recoverable-fs-effect-engine-design.md §12.2 says 'a serialized spec plus out-of-process executor is the plausible shape, §15', and §15 (out of scope and future extension) covers a commit participant, recursive effects, one-root transactions, the publish/computation split and the tamper-evident log, but no execution seam. Either add the seam to §15 as a future extension or drop the cross-reference.
