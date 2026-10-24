---
id: atoms-c0220c
title: "Authority design §12.2 cites §15 for the out-of-process seam's shape, but §15 never mentions it"
status: done
priority: 3
size: xs
complexity: low
process: direct
owner: docs/section15-execution-seam
created: 2026-10-24T19:01:37Z
updated: 2026-10-24T19:13:40Z
started: 2026-10-24T19:05:14Z
completed: 2026-10-24T19:13:40Z
closed: 2026-10-24T19:13:40Z
depends: []
tags: [docs]
source: vdocs-e4318e
agent: claude-code
---

Found by the second guided tour (vdocs-e4318e, 2026-10-24). docs/plans/2026-07-23-recoverable-fs-effect-engine-design.md §12.2 says 'a serialized spec plus out-of-process executor is the plausible shape, §15', and §15 (out of scope and future extension) covers a commit participant, recursive effects, one-root transactions, the publish/computation split and the tamper-evident log, but no execution seam. Either add the seam to §15 as a future extension or drop the cross-reference.

## Notes

- 2026-10-24T19:05:14Z (main): started
  provenance: {"harness_session":"claude-code:0d5ee316-6a93-4242-8ffb-3b30103c5067","harness_session_source":"CLAUDE_CODE_SESSION_ID"}
- 2026-10-24T19:05:43Z (docs/section15-execution-seam): resumed
  provenance: {"harness_session":"claude-code:0d5ee316-6a93-4242-8ffb-3b30103c5067","harness_session_source":"CLAUDE_CODE_SESSION_ID"}
- 2026-10-24T19:13:40Z (docs/section15-execution-seam): done
  provenance: {"harness_session":"claude-code:0d5ee316-6a93-4242-8ffb-3b30103c5067","harness_session_source":"CLAUDE_CODE_SESSION_ID"}
- 2026-10-24T19:13:40Z (docs/section15-execution-seam): Design §15 now names the language-neutral execution seam as a future extension (serialized TransactionSpec, out-of-process executor that compiles and approves it itself), so §12.2's cross-reference resolves; whether to build it stays with atoms-495445.
  provenance: {"harness_session":"claude-code:0d5ee316-6a93-4242-8ffb-3b30103c5067","harness_session_source":"CLAUDE_CODE_SESSION_ID"}
