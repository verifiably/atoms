---
id: atoms-fde058
title: Memoize equivalent kill-matrix rehearsals
status: done
priority: 1
size: m
owner: perf/memoize-kill-rehearsals
created: 2026-09-02T03:41:26Z
updated: 2026-09-05T12:02:59Z
depends: []
tags: [performance, testing, certification]
---

Why: the two _recover subprocesses in _assert_terminal are the convergence property and stay. The reducible work is rehearsal recording repeated across parametrized cases: 17 redundant store-barrier rehearsals, 11 forward-flush rehearsals, 11 backend/chain rehearsals, 2 mkdir rehearsals, and 1 chain-append rehearsal, about 42 avoidable child launches plus repeated _roots, _prepare, and register_root work.

Change: memoize rehearsal events by the configuration values that actually affect them, at session scope. A session fixture must own its patching with pytest.MonkeyPatch.context() rather than accepting the function-scoped monkeypatch fixture. Keep every killed child and both fresh-process recovery/convergence launches.

Done when: tests prove distinct rehearsal configurations remain distinct; subprocess instrumentation shows about 42 fewer rehearsal launches without changing cut coverage; record three before/after kill-matrix timings; run the Python repository gates.

## Notes

- 2026-09-02T10:38:53Z (perf/revise-test-tasks): Rescoped from removing load-bearing recovery launches to memoizing only the 42 equivalent rehearsals.
- 2026-09-05T11:12:48Z (perf/memoize-kill-rehearsals): Rehearsals memo keyed by json.dumps(config, sort_keys=True); session fixture owns its own tempdir on the test volume and scopes _prepare's patch with MonkeyPatch.context(); 56 rehearsal launches should become 13.
- 2026-09-05T12:02:59Z (perf/memoize-kill-rehearsals): Rehearsals memo (whole-config key) + session fixture in conftest; rehearsal launches 56 -> 13 with killed (80) and recovery (158) launches unchanged. Kill-matrix timings before 110.2/109.3/120.2s, after 78.7/75.4/75.2s. Gates: ruff, pyright, full suite green.
