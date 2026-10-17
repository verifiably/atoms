---
id: atoms-6bfc93
title: Record per-test durations with tt version 6
status: doing
priority: 3
size: xs
complexity: low
process: direct
owner: crew
created: 2026-10-08T15:55:23Z
updated: 2026-10-17T21:47:56Z
started: 2026-10-17T21:31:46Z
depends: []
tags: []
source: ops-ee4abc
agent: claude-code/claude-opus-5-5
---

tt version 6 (ops docs/specs/2026-10-08-tt-content-and-slowest-design.md) records each run's five slowest tests when the runner writes a JUnit file into $TT_JUNIT_DIR, or, for unittest, prints --durations.
Change: add the runner's form from spec §4.3, quoted guard included, to one_cmd and fast_cmd in the justfile (test_cmd too if the full suite should be measured); in a justfile string the quotes are escaped (\"). Form: pytest: `--junitxml="${TT_JUNIT_DIR:?run through tt}/pytest.xml"` (works under xdist).
Check: one `just test-one <a known test>` run; the last line of the timing log has a non-null "slowest" naming that test. Without tt the command must stop with "TT_JUNIT_DIR: run through tt" before the runner starts.

## Notes

- 2026-10-17T21:31:46Z (crew): started
- 2026-10-17T21:38:36Z (crew): crew: attempt 01M55WEAE4Q6CP72KNEBQH8N64 started — kind code-xs, worker openrouter/deepseek/deepseek-v4-flash-0731, worktree .worktrees/crew-ebqh8n64
- 2026-10-17T21:47:56Z (crew): crew: attempt 01M55WEAE4Q6CP72KNEBQH8N64 — verdict not-passed (worker openrouter/deepseek/deepseek-v4-flash-0731, reviewer openrouter/openai/gpt-6.1-sol); report /home/keith/.local/state/crew/attempts/01M55WEAE4Q6CP72KNEBQH8N64/attempt-report.md
