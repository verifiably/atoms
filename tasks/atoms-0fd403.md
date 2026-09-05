---
id: atoms-0fd403
title: Test + CI iteration cost audit
status: doing
priority: 2
size: m
owner: test/front-door
created: 2026-09-04T21:44:54Z
updated: 2026-09-05T13:08:58Z
depends: [ops-31f038]
tags: [testing]
---

Piece of ops-65837b (the cross-project audit in the ops hub). 1. Measure: full-suite wall time, and roughly how often agent full-suite runs fail here. 2. Add a fast or affected-only test target for the inner loop and point AGENTS.md at it; keep the full suite for commit and CI. 3. Use a quiet reporter so test output does not flood agent context. 4. Fix suite hygiene: sleeps, real network, unshared fixtures. Record the before and after numbers in a note on this task.

## Notes

- 2026-09-05T02:38:40Z (main): design: ops docs/specs/2026-09-04-test-ci-audit-design.md; follow §5: (1) justfile + vendored tools/tt, route existing hooks, CI, and documented test commands through it, verify a line lands under each agent; (2) after a week of runs, add a note reading 'baseline <date>: <tt-report --project numbers>'; (3) gates to §4.6, AGENTS.md line, hygiene; (4) close with before/after numbers
- 2026-09-05T13:08:58Z (test/front-door): Step 1 landed 2026-09-05: justfile (test-fast=testmon, test, check=ruff+pyright+tasks check, gate, hook-*), tools/tt v2, .githooks + core.hooksPath (local), .gitignore (.tt/, .testmondata), AGENTS.md points at just test/check/gate. Verified in the shared log: check claude 11.9s; test-fast cold claude 6236 tests 637.9s; test-fast warm by hand agent=null tests=0 0.6s. Codex not installed here. Bypass hook counted 14 direct pytest runs today before the front door. Steps 2-4 pending.
