---
id: atoms-6a916d
title: Bring test-fast under its 90 s limit by running testmon's selection on xdist workers
status: todo
priority: 2
size: s
complexity: mid
process: direct
created: 2026-10-17T23:27:35Z
updated: 2026-10-17T23:27:35Z
depends: []
tags: [testing]
agent: claude-code
---

Why: test-fast is still serial. On main, warm testmon selections of 4978 and 4995 tests took 152.9 s (2026-10-10) and 134.9 s (2026-10-16) against the 90 s limit in ops latency.toml; the in-process ceiling has grown from 106 s (2026-09-05, atoms-83ae5c) to roughly 160-210 s. tt-latency reads the target as insufficient only because its window holds 3 runs, so the next five on three days are likely to breach and halt atoms again, as atoms-286555 did for test.

Change: once ops-ab03a7 records a fast_cmd form that keeps testmon's selection safe under xdist (workers there can collect different tests), apply it to fast_cmd and run test-fast under host-budget run, as test and hook-pre-push do since a2020d2. If ops-ab03a7 recommends serial testmon, re-scope this task instead of forcing workers.

Check: a warm run after a core-module edit (the atoms-83ae5c probe: one module-level statement in fs/lookup.py) selects about the same tests as serially and finishes under 90 s; a no-change run selects 0; repeated runs never show xdist's 'Different tests were collected'. Related: atoms-4fb428 (cold test-fast runs are not recorded as widened, so they inflate the window).
