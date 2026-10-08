---
id: atoms-6bfc93
title: Record per-test durations with tt version 6
status: todo
priority: 3
size: xs
complexity: low
process: direct
created: 2026-10-08T15:55:23Z
updated: 2026-10-08T15:55:23Z
depends: []
tags: []
source: ops-ee4abc
agent: claude-code/claude-opus-5-5
---

tt version 6 (ops docs/specs/2026-10-08-tt-content-and-slowest-design.md) records each run's five slowest tests when the runner writes a JUnit file into $TT_JUNIT_DIR, or, for unittest, prints --durations.
Change: add the runner's form from spec §4.3, quoted guard included, to one_cmd and fast_cmd in the justfile (test_cmd too if the full suite should be measured); in a justfile string the quotes are escaped (\"). Form: pytest: `--junitxml="${TT_JUNIT_DIR:?run through tt}/pytest.xml"` (works under xdist).
Check: one `just test-one <a known test>` run; the last line of the timing log has a non-null "slowest" naming that test. Without tt the command must stop with "TT_JUNIT_DIR: run through tt" before the runner starts.
