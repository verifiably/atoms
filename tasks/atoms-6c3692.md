---
id: atoms-6c3692
title: Memoize identical sabotaged persistence-cut sweeps
status: done
priority: 1
size: s
created: 2026-09-02T03:41:26Z
updated: 2026-09-05T11:07:27Z
depends: []
tags: [performance, testing, certification]
---

Why: test_each_sabotaged_run_fails_only_its_own_designated_check repeats the same full minimal-create sweeps already run by test_sabotage_1, test_sabotage_2, and test_sabotage_5 for blob-flush, pre-done-flush, and committed-decision. The aggregate equality assertion is stronger, but the three reconstructed sweeps are identical.

Change: memoize sabotaged Sweeper.__call__ results across tests by (scenario, sabotage), preserving immutable SweepReport evidence. Keep unsabotaged, caught, drift, and subprocess-placement runs out of this cache. A fresh N2 process must start cold and perform its own sweep.

Done when: each of the three (minimal-create, sabotage) pairs executes once per module process; the individual and aggregate assertions remain; record three before/after module timings; run the Python repository gates.

## Notes

- 2026-09-02T10:38:53Z (perf/revise-test-tasks): Narrowed to the three proven duplicate minimal-create sabotage sweeps; placement and recovery work are unchanged.
- 2026-09-05T10:37:05Z (perf/memoize-sabotage-sweeps): Cache is a ClassVar dict on Sweeper keyed (scenario, arm.name), read/written only in _sabotaged; directed test proves the second request returns the same report without recording.
- 2026-09-05T11:07:27Z (perf/memoize-sabotage-sweeps): Sweeper memoizes sabotaged runs per process by (scenario, arm); the three minimal-create pairs record once. Module timings before 229.6/219.9/282.7s, after 215.7/227.9/224.4s (within noise; the removed work is ~2s). Gates: ruff, pyright, 6235 passed.
