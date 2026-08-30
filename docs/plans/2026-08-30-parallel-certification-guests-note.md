# Parallel certification guests — design note

**Status:** proposed note, 2026-08-30. Not an approved design; the A8b design
gate run is the owning task's first step, and this note is its input, not its
substitute.

## Problem

The A8b certification sweep runs its nine scenarios serially — one QEMU guest
boot per scenario in a single loop (`tools/certify/__main__.py`,
`_run_scenarios`) — and the host kernel that de-certifies the tuple updates
on the order of weekly (7.1.9 → 7.1.10 → 7.1.11 landed 08-27, 08-28, 08-30).
Recertification is "one command" as designed, but the command is slow and its
cost is paid on every kernel bump. A post-boot user unit now runs it in the
background (machine config, `~/.local/bin/atoms-recertify`), which removes
the blocking; this note is about removing most of the wall time.

## Proposal

Run the nine scenarios in N concurrent guests instead of one after another.
Each scenario is already an independent unit: its own guest boot, its own
dm-log-writes log, its own replay and check. The loop's body becomes a worker;
results are collected and validated exactly as today, and `record.write` is
untouched — same per-scenario rows, same totals, same record schema.

What has to be kept honest:

- **Workspace isolation.** The guest image, log image, and any per-run
  scratch under `python/.certify/` must be per-scenario copies; today's
  layout assumes one run at a time. The pinned `replay-log` binary and the
  fetched xfstests checkout are read-only and shared.
- **Harness evidence.** `_guest_harness_evidence` is taken from the last
  scenario's final summary and the record retains one `qemu_command`. Under
  parallelism "last" is nondeterministic; the design should instead require
  the evidence tuple (mkfs command, mount command, log format, cache mode) to
  be **equal across all nine guests** and refuse the record otherwise — a
  strictly stronger claim than today's.
- **Host load.** Nine KVM guests at once may oversubscribe; a `--jobs N`
  bound (default: min(scenarios, cores/2)) rather than unbounded fan-out.
- **Determinism of refusal.** A failure in any scenario must fail the whole
  run before a record is written, exactly as today; parallel collection must
  not turn one guest's fatal row into a swallowed error.

## Non-goals

- No change to the scenario matrix, the record schema, the allowlist match,
  or the "certification stays manual and non-collected" rule.
- No weakening of the exact-tuple match; this is wall time only.

## Verification sketch

The existing `self-test` runs a reduced pass; add a parallel variant and
assert its record-equivalent output matches a serial run on the same boot,
field for field, modulo the retained `qemu_command`'s ordering rule above.
