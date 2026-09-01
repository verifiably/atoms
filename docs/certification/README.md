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
