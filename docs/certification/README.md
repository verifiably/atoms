# Certification records

Each JSON file here is one accepted certification run: the durability tuple
that `CERTIFIED_ALLOWLIST` in `python/src/atoms/fs/volume.py` admits, together
with the harness evidence that produced it. The allowlist entry names the
record it rests on via `certification_ref`.

## Why records turn over so quickly

`kernel_identifier` is an exact `uname -r`. Any change to the host kernel —
including a pkgrel-only rebuild such as `7.1.8-arch1-2` to `7.1.8-arch1-3` —
leaves the running kernel outside the allowlist, and the tuple has to be
re-certified by a fresh nine-scenario QEMU sweep before it is admitted again.

Arch ships `linux` roughly weekly, which made recertification a near-continuous
background cost. Two host-side measures bound it. Neither is repo content; both
are machine configuration.

- **The sweep is unattended.** A user timer runs the recertification after boot
  whenever the running kernel is uncertified, then pushes a
  `chore/recertify-<kernel>` branch for review. Nothing blocks on it.
- **The kernel is gated.** `linux` and `linux-headers` are held in pacman's
  `IgnorePkg`, so an ordinary system upgrade leaves the kernel alone. They are
  released together, deliberately, with `kernel-gate unlock`; `kernel-gate
  status` reports what is held, what is available, and whether the allowlist
  still matches the running kernel.

The two packages are always released as a pair. DKMS modules build against the
headers, so a kernel and headers at different versions break every module build
until they match again; a pacman hook refuses to let the pair diverge silently.

## Reading a stale allowlist

If `kernel_identifier` does not match the running kernel, the tuple is simply
not certified on this host right now — that is the designed refusal, not a bug.
Either a release window is in progress and the sweep has not finished, or its
review branch is still open.

## Running the sweep

From `python/` in a clean checkout with the certification prerequisites built:

```sh
uv run --frozen python -m tools.certify run --all --accel kvm --jobs 3 --record docs/certification
```

`--jobs` bounds simultaneous guests; each guest reserves 2 GiB of memory plus
host image and replay overhead. Without the flag, the runner uses half the CPUs
available to its process, capped at the scenario count and with a minimum of
one. Use `--jobs 1` for serial execution or choose a smaller bound on a busy
host. Acceleration remains explicit: omitting `--accel` selects TCG.

Each guest owns separate writable images and private guest scratch. Every guest
must report identical mkfs/mount commands, log format, and drive cache mode.
Rows stay in scenario order, and the retained QEMU command belongs to the final
scenario in that order. A failure cancels queued guests and waits for running
guests to exit before cleaning their images; no certification record is written.

The manual `uv run --frozen python -m tools.certify self-check` exercises the
scheduler, evidence refusals, and serial/parallel record equivalence without
booting guests. `self-test` remains the guest boot/device-safety check.
