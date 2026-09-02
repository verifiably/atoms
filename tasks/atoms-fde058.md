---
id: atoms-fde058
title: Memoize equivalent kill-matrix rehearsals
status: todo
priority: 1
size: m
created: 2026-09-02T03:41:26Z
updated: 2026-09-02T10:38:53Z
depends: []
tags: [performance, testing, certification]
---

Why: the two _recover subprocesses in _assert_terminal are the convergence property and stay. The reducible work is rehearsal recording repeated across parametrized cases: 17 redundant store-barrier rehearsals, 11 forward-flush rehearsals, 11 backend/chain rehearsals, 2 mkdir rehearsals, and 1 chain-append rehearsal, about 42 avoidable child launches plus repeated _roots, _prepare, and register_root work.

Change: memoize rehearsal events by the configuration values that actually affect them, at session scope. A session fixture must own its patching with pytest.MonkeyPatch.context() rather than accepting the function-scoped monkeypatch fixture. Keep every killed child and both fresh-process recovery/convergence launches.

Done when: tests prove distinct rehearsal configurations remain distinct; subprocess instrumentation shows about 42 fewer rehearsal launches without changing cut coverage; record three before/after kill-matrix timings; run the Python repository gates.

## Notes

- 2026-09-02T10:38:53Z (perf/revise-test-tasks): Rescoped from removing load-bearing recovery launches to memoizing only the 42 equivalent rehearsals.
