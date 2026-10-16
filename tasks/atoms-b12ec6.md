---
id: atoms-b12ec6
title: Rewrite t@t commit authorship in this repository
status: doing
priority: 2
size: s
complexity: low
process: direct
owner: main
created: 2026-10-16T16:31:48Z
updated: 2026-10-16T18:26:37Z
started: 2026-10-16T18:23:21Z
depends: [ops-e5b39c]
tags: []
source: git-identity-leak-2026-10-07
agent: claude-code/claude-opus-5-5
---

Rewrite this repository's t <t@t> commits (from the 2026-10-07 global-config leak; see ops-5749d9) to Keith Hughitt <keith.hughitt@gmail.com>, following the runbook ops-e5b39c. Inventory on titan 2026-10-16: 72 t@t commits on all refs, 1 unpushed on main, 2 worktree(s); remote: verifiably/atoms.

## Notes

- 2026-10-16T18:23:21Z (main): started
  provenance: {"harness_session":"codex:01a145f3-539b-7c11-8300-e8ce409af6ff","harness_session_source":"CODEX_THREAD_ID"}
- 2026-10-16T18:24:43Z (main): Direct history maintenance under ops-e5b39c pilot notes: user confirmed Europa idle; Titan has main plus merged docs/certification-readme, tag v0.1.0, no stashes or other worktrees, 72 t@t commits. Rewrite all refs in one isolated mirror outside Dropbox, bundle backup first, preserve trees/topology/messages, swap refs with old-value guards, attach changed-entry SHA map to this task so append-only historical references remain resolvable, run pre-push gate, push main with explicit lease. No repository code changes or code worktree needed. Europa index is stale; verifying actual file hashes and preserving index before any swap.
- 2026-10-16T18:26:37Z (main): attached: 2026-10-16-identity-commit-map.txt (6195 bytes): Authorship rewrite map: 75 changed commit IDs; resolves historical SHA references without changing append-only notes.
