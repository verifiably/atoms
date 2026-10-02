---
id: atoms-e067b0
title: Isolate the pre-push gate from Git-local environment
status: done
priority: 2
size: xs
complexity: low
process: direct
owner: fix/pre-push-rollout
created: 2026-10-02T01:32:38Z
updated: 2026-10-02T01:47:33Z
started: 2026-10-02T01:32:38Z
completed: 2026-10-02T01:47:32Z
depends: []
tags: []
source: ops-e6ec4e
agent: codex
---

Adopt the landed ops pre-push isolation block before the existing gate. Preserve keepalive, gate selection, custom steps, exact ref stdin and exit codes; Git LFS runs before isolation with its original environment and refs. Verify the exact hook with the shared behavioral regression, then project test-fast and required checks. Record the adoption commit on ops-e6ec4e.

## Notes

- 2026-10-02T01:32:38Z (main): started
  provenance: {"harness_session":"codex:01a0fa3a-dc49-7fe2-ab41-ffb86990bda9","harness_session_source":"CODEX_SESSION_ID"}
- 2026-10-02T01:36:11Z (fix/pre-push-rollout): resumed
  provenance: {"harness_session":"codex:01a0fa3a-dc49-7fe2-ab41-ffb86990bda9","harness_session_source":"CODEX_SESSION_ID"}
- 2026-10-02T01:42:43Z (fix/pre-push-rollout): review: impl round 1 — verdict: accept; findings: none; reviewer: codex/GPT-6
- 2026-10-02T01:43:47Z (fix/pre-push-rollout): Initial host-budget test-fast reached 98% with no printed failures before its 300-second timeout (the fresh worktree had no testmon history). Analyze the resulting testmon selection before retrying; shared hook red/green tests already passed.
- 2026-10-02T01:47:32Z (fix/pre-push-rollout): After the timeout, testmon collected only 211 remaining tests (26 deselected); the retry passed all 211 in 20.11s. The first run reached 98% without printed failures. Shared adopter regression passes including index/ref isolation, refs/exit codes and early refusal.
- 2026-10-02T01:47:32Z (fix/pre-push-rollout): done
  provenance: {"harness_session":"codex:01a0fa3a-dc49-7fe2-ab41-ffb86990bda9","harness_session_source":"CODEX_SESSION_ID"}
- 2026-10-02T01:47:32Z (fix/pre-push-rollout): Adopted pre-push Git-local environment isolation; shared behavioral regression and completed test-fast selection pass.
  provenance: {"harness_session":"codex:01a0fa3a-dc49-7fe2-ab41-ffb86990bda9","harness_session_source":"CODEX_SESSION_ID"}
