---
id: atoms-aff620
title: Benchmark complete serial and parallel certification sweeps
status: todo
priority: 3
size: s
created: 2026-09-11T12:35:07Z
updated: 2026-09-11T12:35:07Z
depends: [atoms-eabd89]
tags: [certification, tooling]
source: atoms-eabd89
---

Follow-up to atoms-eabd89, explicitly deferred to a dedicated run by the user on 2026-09-11. Run the complete nine-scenario certification matrix with --jobs 1 and --jobs 3 on the same clean commit, host boot, target tuple, and acceleration mode. Use separate record destinations and tools/tt; reserve enough time for both sweeps to finish. Require zero violations and exhaustive observed-prefix coverage in each run, compare target and harness evidence while accounting for ephemeral host paths, and preserve canonical scenario ordering. Physical mark/prefix counts may differ; do not assert equality of independent bio traces. Record elapsed times, speedup or slowdown, relevant host load, and a practical jobs recommendation in docs/plans/2026-08-30-parallel-certification-guests-note.md. If per-prefix work dominates, record a bounded profiling recommendation before proposing optimizations. Do not change the record schema, scenario coverage, or production allowlist as part of this measurement.
