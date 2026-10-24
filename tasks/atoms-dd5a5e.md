---
id: atoms-dd5a5e
title: "Explain why atoms exists against what a newcomer knows, not against proto-science's code history"
status: done
priority: 2
size: s
complexity: mid
process: direct
owner: docs/dd5a5e-newcomer-problem
created: 2026-10-24T17:35:06Z
updated: 2026-10-24T18:46:05Z
started: 2026-10-24T18:06:00Z
completed: 2026-10-24T18:46:05Z
closed: 2026-10-24T18:46:05Z
depends: []
tags: [docs]
source: vdocs-e4318e
agent: claude-code
---

Found by the second guided tour (vdocs-e4318e, 2026-10-24). The authority design's §2 states the problem as 'each consumer re-implements ordering, ownership tracking, and rollback … That split is the defect this engine removes'. Those terms only make sense against proto-science's code, which only the superseded 2026-07-20 design names ('two execution dialects': PathTransition and plan_common.py for archive and supersede, a private snapshot for import). A reader new to the project could not say what a consumer is (beliefs' write families, not science or autonomy), what ordering means (durability order within one transaction), what ownership means (which changes on disk are this transaction's, not who may write), or what the split was (an in-memory tracker beside a durable journal). The README states the contract but not the problem a user would see. Rewrite the README's lead and the design's §2 so the problem is stated against the status quo: a half-written corpus after a crash or kill, a sync client or another tool changing a file mid-write, and no way to tell afterwards which changes were whose. Keep the project's history in the superseded design. Also state the scope the tour's learner got wrong: one host and cooperating processes only; Dropbox across hosts is a non-guarantee (§3.2).

## Notes

- 2026-10-24T18:06:00Z (main): started
  provenance: {"harness_session":"claude-code:0e2eed92-cf40-40a1-aac6-b06ac9f3c1ce","harness_session_source":"CLAUDE_CODE_SESSION_ID"}
- 2026-10-24T18:09:27Z (docs/dd5a5e-newcomer-problem): resumed
  provenance: {"harness_session":"claude-code:0e2eed92-cf40-40a1-aac6-b06ac9f3c1ce","harness_session_source":"CLAUDE_CODE_SESSION_ID"}
- 2026-10-24T18:13:15Z (docs/dd5a5e-newcomer-problem): baseline 2026-10-24 (just test, worktree .worktrees/dd5a5e-newcomer-problem at c177e8a): 6310 passed, 7 skipped, 149.54 s
- 2026-10-24T18:34:26Z (docs/dd5a5e-newcomer-problem): review: impl round 1 — verdict: revise; findings: Important 2, Minor 5; reviewer: claude-code/claude-fable-5-1
- 2026-10-24T18:39:07Z (docs/dd5a5e-newcomer-problem): minor (deferred): the abstract no longer says atoms sits conceptually below nodes (nodes does not yet adopt it); the 400-char bound left no room, the family nav shows the layer order, and design §1/§12.2 keep the claim
- 2026-10-24T18:42:35Z (docs/dd5a5e-newcomer-problem): review: impl round 2 — verdict: revise; findings: Important 1, Minor 1; reviewer: claude-code/claude-fable-5-1
- 2026-10-24T18:45:50Z (docs/dd5a5e-newcomer-problem): review: impl round 3 — verdict: accept; findings: none; reviewer: claude-code/claude-fable-5-1
- 2026-10-24T18:46:05Z (docs/dd5a5e-newcomer-problem): done
  provenance: {"harness_session":"claude-code:0e2eed92-cf40-40a1-aac6-b06ac9f3c1ce","harness_session_source":"CLAUDE_CODE_SESSION_ID"}
- 2026-10-24T18:46:05Z (docs/dd5a5e-newcomer-problem): README abstract (identity.toml) and design §2 state the problem against what a program writing many files meets, not the predecessor's code; both name the scope: one host, cooperating processes, Dropbox across hosts a non-guarantee (§3.2); three review rounds (revise, revise, accept); the dropped nodes clause is the deferred minor
  provenance: {"harness_session":"claude-code:0e2eed92-cf40-40a1-aac6-b06ac9f3c1ce","harness_session_source":"CLAUDE_CODE_SESSION_ID"}
