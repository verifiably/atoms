# Parallel certification guests

**Status:** Implemented on 2026-09-11 for `atoms-eabd89`, after design approval
on the same day. Code landed at `cafad73`; verification is recorded below.
This refines A8b §7 without changing the authority's certification contract.

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

The repository gate is `just check` plus `just test`; the certification-tool
checks remain manual and outside pytest collection.

### Measured results — 2026-09-11

The following checks ran against the implementation at `cafad73` on
`7.2.2-arch1-1`:

- `just check`: ruff and pyright passed; `tasks check` had zero errors and
  warnings.
- `just test`: **6,238 passed, 7 skipped**, 523.35 seconds.
- The manual `self-check`: **13 checks passed**, including real scheduling,
  deterministic serial/parallel record equality, every middle-guest refusal
  listed above, cleanup, pending-future cancellation, and subprocess console
  output. Removing evidence comparison, concurrency, cancellation, or console
  serialization in memory caused the corresponding regression check to fail.
- Two KVM guests concurrently completed the existing `--self-test` workload
  using one shared initramfs and distinct 8 MiB data/log images. Both reported
  success, all four image digests were unchanged, and all temporary workspaces
  were removed. Total time including the shared build: **13.97 seconds**.
- A real `run --all --accel kvm --jobs 1 --record ...` was deliberately stopped
  after **29 of 309 minimal-create prefixes**. A second guest started before
  failure collection canceled the queue; it completed **1 of 329 minimal-replace
  prefixes** and was also stopped. The driver exited nonzero, created no record
  directory, and left no certification workspaces. This is partial execution
  and failure-cleanup evidence, not a successful certification sweep.

The paired full nine-scenario wall-time benchmark is **deferred**: the sampled
serial replay rate implied several hours for the comparison. No speedup or new
certification is claimed. A dedicated run can use `--jobs 1` and `--jobs 3` on
one clean commit and boot, with separate `--record` destinations. Both must
finish before comparing their tuples, harness evidence, observed coverage,
and elapsed times.

Local raw logs are retained, untracked, under
`python/.certify/parallel-validation/` in the implementation checkout. The
physical checks and manual self-check were timed through `tools/tt`.
