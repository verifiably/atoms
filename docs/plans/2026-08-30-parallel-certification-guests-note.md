# Parallel certification guests

**Status:** Design approved on 2026-09-11 for `atoms-eabd89`; implementation
under verification. This refines A8b §7 without changing the authority's
certification contract.

## Design-gate findings

The review against the authority §13.2, A8b §7, and the current runner at
`2605839` found no new production boundary or deferred obligation. The ledger
has no open obligations. The existing nine scenarios remain independent.

The original note overstated the isolation work: data and log images already
live in unique per-scenario host temporary directories. The common initramfs
and pinned replay-log build finish before any guest starts; guests read them
through the existing read-only root share. Guest `/tmp` and `/run`, device
mappers, and replay clones are private to each VM.

The recorded mount command contains a random guest temporary path today.
Literal equality across guests therefore requires a fixed guest-private
mountpoint. Comparing commands after stripping paths would weaken the evidence;
the approved design instead mounts the workload at `/run/atoms-certify-volume`
in each guest's private tmpfs and retains the actual command unchanged.

## Approved behavior

- Use `ThreadPoolExecutor` around the existing one-scenario guest lifecycle.
  `run --jobs N` requires a positive integer; omit it to use half the CPUs in
  the host process's affinity, rounded down with a minimum of one. Cap workers
  at the selected scenario count. `--jobs 1` selects serial execution.
- Each worker owns its data/log images until QEMU exits. Build shared inputs
  before starting the pool. Serialize console writes by line while retaining
  each guest's independent serial transcript for parsing.
- Validate every guest's scenario row, exact target configuration, storage
  profile, and log format. Require literal equality of mkfs argv, mount argv,
  log format, and QEMU drive cache mode across every guest, including serial
  runs and runs without `--record`.
- Observe completed futures to detect failures without waiting for earlier
  scenarios. Cancel queued work on failure and join running workers before
  removing the outer workspace. Interruption follows the same ownership rule;
  waiting for active guests may take until their current scenario finishes.
- Assemble rows in the declared scenario order, regardless of completion
  order. Retain the last scenario in that order as the record's QEMU command.
  Write a record only after the complete matrix passes and all workers exit.
- Preserve the record schema, exact-tuple matching, nine-scenario matrix,
  prefix coverage, explicit caps, and manual, non-collected certification.

No new Python dependency, scheduler abstraction, record format, or production
engine change is needed.

## Verification

The existing `self-test` checks boot, resolver/replay sanity, and attached-device
safety; it is not a reduced scenario sweep. Extend the manual `self-check`
instead: exercise the real thread pool, temporary-image lifetimes, and record
writer with simulated guests. Check overlap and the jobs bound, canonical
ordering and retained command, serial/parallel record equality after replacing
only ephemeral host image paths, and refusal of an invalid middle guest's
configuration, commands, log/cache mode, fatal/missing row, violations, or
exception. Exercise concurrent console writes with real subprocesses.

Run real nine-scenario sweeps with `--jobs 1` and parallel jobs on the same
clean commit and host boot. Require the same tuple and harness evidence and
zero violations with exhaustive observed-prefix coverage in each run. Record
timing and observed counts; physical bio trace counts can differ between runs,
so equality of those counts is not a certification requirement. The simulated
runs provide deterministic field-for-field aggregation checks.

Run `just gate`; the certification-tool checks remain manual and outside
pytest collection. Bank the measured results here after verification.
