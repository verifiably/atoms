---
id: atoms-f8cb72
title: Scrub machine layout from tracked files and commit ops-check 5
status: done
priority: 2
size: s
complexity: low
process: direct
owner: main
created: 2026-09-21T13:05:40Z
updated: 2026-09-21T21:03:48Z
started: 2026-09-21T21:03:11Z
completed: 2026-09-21T21:03:48Z
depends: []
tags: []
source: ops-0c42b9
model: "claude-opus-5[1m]"
agent: "claude-code/claude-opus-5[1m]"
---

ops-check 5 (ops ops-0c42b9) fails on this machine's layout in any tracked file: the home directory, the hostname, WORK_ROOT, a registered checkout or its parent, the banned tracker host. tools/ops-check is already updated in the working tree but uncommitted, because the pre-commit hook refuses every commit here until these findings are gone. Per file: rewrite the path or hostname neutrally; drop the file when it is captured scratch that does not belong in the repository; or, for evidence that must stay verbatim, list its path prefix under layout_allowed in a root .ops-check.toml. Commit tools/ops-check in the same change.

Findings (8 lines in 7 files):
- docs/certification/2026-08-16-ext4-linux-7.1.8-arch1-3.json: a registered checkout
- docs/certification/2026-08-27-ext4-linux-7.1.9-arch1-2.json: a registered checkout
- docs/certification/2026-08-28-ext4-linux-7.1.10-arch1-1.json: a registered checkout
- docs/certification/2026-08-30-ext4-linux-7.1.11-arch1-1.json: a registered checkout
- docs/certification/2026-09-03-ext4-linux-7.2.2-arch1-1.json: a registered checkout
- docs/plans/2026-07-23-plan-a1-core-model.md: the home directory, the parent of a registered checkout
- docs/plans/2026-07-28-plan-a2-compilation-validation.md: the home directory

## Notes

- 2026-09-21T21:03:11Z (main): started
  provenance: {"harness_session":"claude-code:6819244b-2358-447b-8b92-1c2457c77557","harness_session_source":"CLAUDE_CODE_SESSION_ID"}
- 2026-09-21T21:03:48Z (main): done
  provenance: {"harness_session":"claude-code:6819244b-2358-447b-8b92-1c2457c77557","harness_session_source":"CLAUDE_CODE_SESSION_ID"}
- 2026-09-21T21:03:48Z (main): ops-check 5 committed; docs/certification/ allowed via root .ops-check.toml (verbatim harness evidence), two plan-doc convention lines rewritten neutrally
  provenance: {"harness_session":"claude-code:6819244b-2358-447b-8b92-1c2457c77557","harness_session_source":"CLAUDE_CODE_SESSION_ID"}
