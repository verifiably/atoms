---
id: atoms-aff620
title: Benchmark complete serial and parallel certification sweeps
status: todo
priority: 3
size: s
complexity: mid
process: direct
created: 2026-09-11T12:35:07Z
updated: 2026-10-14T10:24:17Z
depends: [atoms-eabd89]
tags: [certification, tooling]
source: atoms-eabd89
---

Why: Complete the physical wall-time comparison deferred from atoms-eabd89. The runner and record-validation logic are implemented; successful serial-versus-parallel full-sweep performance has not been measured.

Done:
- First run the manual certification self-check through scheduler, canonical-record and refusal verdicts, then the smallest physical scenario through its final verdict. Use that pilot to estimate both full nine-scenario sweeps and record the estimate and host prerequisites here. The boot/device self-test alone does not cover the workload or final record gates.
- Measure jobs 1 and jobs 3 on the same clean code commit, host boot, resolved target tuple and explicit acceleration mode, with separate record destinations and tools/tt timings. Respect the host-budget grant: proceed with jobs 3 only when at least three workers are granted, and record the actual guest concurrency. The current just certify recipe injects OPS_WORKERS; an explicit jobs argument must not exceed that grant.
- Keep long runs harness-tracked through completion. Current tools.certify exposes no resume/checkpoint option and retains temporary replay work only for the invocation. If the pilot predicts more than the harness tracking limit, obtain agreement on a tracked long-running process before the full sweeps; do not pretend restarting temporary runs is resumable slicing. Record pids and cleanup for any process that must outlive a turn.
- Both full sweeps must finish with zero violations and exhaustive observed-prefix coverage before reporting comparative speedup. Compare target/harness evidence and canonical scenario ordering, allowing ephemeral host image paths and different independent physical trace counts.
- Record elapsed times, host load and the practical jobs recommendation in docs/plans/2026-08-30-parallel-certification-guests-note.md. Preserve the raw summaries and record destinations. Analyze any completed portion of a failed/aborted run before retrying. If per-prefix replay dominates, record a bounded profiling recommendation.
- Keep the record schema, scenario coverage and production allowlist unchanged. Run tasks check and just check for the resulting record/document update.

Where to look: docs/plans/2026-08-30-parallel-certification-guests-note.md; python/tools/certify/__main__.py (_run_scenarios and CLI); python/tools/certify/self_check.py; python/tools/certify/guest.py; python/tools/certify/record.py; justfile (certify); tools/tt.

Established at HEAD 9391c72: cafad73 is an ancestor, the bounded thread pool and exact nine-scenario record gate remain implemented, and the later 1deaefd recipe adds host-budget with jobs from OPS_WORKERS. The predecessor task is done. The existing note records only partial physical replay and explicitly defers this paired benchmark. No fresh timing, host availability or granted worker budget is established by this scope pass.

Original scope and captured procedure (preserved):
Follow-up to atoms-eabd89, explicitly deferred to a dedicated run by the user on 2026-09-11. Run the complete nine-scenario certification matrix with --jobs 1 and --jobs 3 on the same clean commit, host boot, target tuple, and acceleration mode. Use separate record destinations and tools/tt; reserve enough time for both sweeps to finish. Require zero violations and exhaustive observed-prefix coverage in each run, compare target and harness evidence while accounting for ephemeral host paths, and preserve canonical scenario ordering. Physical mark/prefix counts may differ; do not assert equality of independent bio traces. Record elapsed times, speedup or slowdown, relevant host load, and a practical jobs recommendation in docs/plans/2026-08-30-parallel-certification-guests-note.md. If per-prefix work dominates, record a bounded profiling recommendation before proposing optimizations. Do not change the record schema, scenario coverage, or production allowlist as part of this measurement.

## Notes

- 2026-09-12T16:35:06Z (main): Complexity low: cafad73 is an ancestor of HEAD and the runner implements bounded parallel guests and evidence validation. The linked note and task specify jobs 1/3, controlled commit/boot/tuple, exhaustive coverage, separate records, trace-count caveat and timing comparison. Remaining work follows an established measurement procedure with explicit checks; hours of runtime do not raise reasoning complexity.
