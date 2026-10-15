# SQLite-WAL certification once per volume per process

**Status:** Implemented, 2026-10-15. Approved in review round 2; measurement in §6 recorded on `atoms-257797`.
**Task:** `atoms-257797`. **Inspected Atoms base:** `8bd158a`.

**Authority:** [engine design](2026-07-23-recoverable-fs-effect-engine-design.md)
§5.5; [A4a capability backend](2026-07-29-a4a-capability-backend-design.md) §8.3,
§9.1. The roadmap remains at A9. This change adds no capability, schema, or public
command.

## 1. Decision and consumer need

`bind_project_volume` runs the §8.3 SQLite-WAL hostability choreography at step 7 of
every bind: one parent connection and two `sys.executable -c` children. A lease entry
binds once, so every command a consumer runs pays two interpreter spawns per root it
opens.

Measured in Science's suite on 2026-10-15 (the development host, 16 xdist workers, 694 tests):
3,298 binds spawned 6,596 certification children, taking 468 of 1,252 worker-seconds
(37%). One Science fixture world binds 50 times; certification is about half of a
one-corpus world's build and 30% of the largest. A child costs about 25 ms on a quiet
host and averaged 71 ms across that loaded run. The cost grows with the number of
concurrent workers. Science's
test-latency halt `sci-5937be` depends on this task.

The measurement recorded on `atoms-257797` after implementation counted differently:
at 8 workers the same 694 tests made 6,912 certifications before the change (one per
bind, each spawning two children, as a second counter confirmed), taking
414.5 of 1,193 worker-seconds, and 8 after, taking 0.5 of 689. The figures above are
the pre-design estimate that motivated this change; the recorded pair is the evidence.

**Decision:** a process that has certified a volume does not certify that same volume
again. "Same volume" is a key built from facts step 2 and step 3 already resolve on
every bind (§3). Steps 1–6, the capability probe in step 7, and both reclamations run
exactly as today. Only the SQLite-WAL choreography is skipped, and only on a hit.

## 2. Why this does not weaken the guarantee

Engine design §5.5 separates two claims:

- **Durability** is carried by the crash-certified allowlist, matched at step 5 on
  every bind. This change does not touch it.
- **WAL hostability** is a functional property: the volume supports POSIX advisory
  locking and shared-memory-backed WAL across processes. It depends on the filesystem
  implementation, its mount and superblock options, the kernel, and the SQLite library.

Within one process, the kernel and the SQLite library are fixed. The filesystem and its
options are fixed as long as the mount is. A remount that changes any option changes
`/proc/self/mountinfo`, which step 2 re-reads on every bind. Because the key holds the
full mount entry, a changed option is a new key, and the new key certifies.

What a cached certification no longer catches is a volume whose WAL behaviour changes
while its mount entry, mount ID, and device stay identical. On the filesystems the
allowlist can admit (local ext4, xfs, btrfs tuples), no such change exists short of
kernel or hardware failure. A per-bind probe would catch such a failure only if it
struck in the moments before a bind. The engine already disclaims runtime probes as
evidence of anything beyond functional availability (engine design §3.2). Network
filesystems, where a lock daemon can fail under an unchanged mount, are refused at
step 5 before certification is reached.

## 3. The key

```python
@dataclass(frozen=True, slots=True)
class CertifiedVolume:
    mount_id: int                      # step 2, from /proc/self/fdinfo
    device: int                        # st_dev of the metadata root
    entry: MountEntry                  # parsed mount identity, complete option tuples
    configuration: VolumeConfiguration # step 3: kernel, backend revision, barrier, features
```

`entry` is the parsed `MountEntry`: mount ID, device (`major:minor`), mount point,
filesystem type, and the complete mount-option and superblock-option tuples. It holds
every option, not only the barrier options the configuration keeps. Options that bear
on locking or memory mapping (for example `nolock`, `local_lock`, `dax`, or `ro` after
`errors=remount-ro`) are not barrier options and would be invisible to a key built
from `VolumeConfiguration` alone.

`parse_mountinfo` discards four mountinfo fields, so a change confined to them does not
change the key:

- **Parent mount ID:** where the mount sits in the tree.
- **Root within the filesystem:** which directory a bind mount exposes.
- **Optional propagation fields:** `shared:`, `master:`, `unbindable`.
- **Mount source:** a label such as `/dev/sda1`.

None of these bears on how the filesystem implements POSIX locks or maps the WAL index.
The device field identifies the backing device, so the source label adds nothing to it.
A new bind mount gets a new mount ID, and a moved mount keeps its ID but changes its
mount point. Both fields are in the key.
The parser is not extended. A future case where one of these fields matters would add
it to `MountEntry`, and so to the key, in the same change.

A mount ID can be reused after an unmount. A reused ID matches only if the new mount
has the same device, the same mount point, the same options, and the same
configuration. That is the same volume configuration on the same device, and
hostability is a property of that configuration. No inode or directory is in the key:
certification is a property of the volume, and two project roots on one mount share
their result.

A process in another mount namespace sees different mount IDs and certifies again. A
forked child inherits the parent's cache. It shares the parent's kernel and, until it
unshares, its mounts. After an unshare, mount IDs change.

## 4. Where the cache lives

`atoms.fs.probe` gains:

```python
_CERTIFIED: set[CertifiedVolume] = set()

def certify_volume(volume: CertifiedVolume, database_path: str, *, backend, parent_fd) -> None:
    """Certify `volume` unless this process already has (A4a §8.3, §9.1 step 7)."""
    if volume in _CERTIFIED:
        return
    certify_sqlite_wal(database_path, cleanup=True, backend=backend, parent_fd=parent_fd)
    _CERTIFIED.add(volume)
```

- **Success only.** A refusal, a timeout, or any other exception propagates before the
  add, so a failing volume refuses on every bind, exactly as today.
- **Concurrency.** Two threads binding the same new volume may both certify. That is
  harmless: each runs the full choreography under its own held project lock, and the
  set add is idempotent. No lock is added.
- **Unbounded but tiny.** A process holds one entry per distinct volume it has bound.
- **No eviction, no persistence.** Nothing is written outside `probe/`, and nothing
  survives the process.

`bind_project_volume` builds the `CertifiedVolume` from `metadata_mount`,
`metadata_info.st_dev`, `entry` and `configuration`, all of which it already holds, and
calls `certify_volume` in place of `certify_sqlite_wal`. `certify_sqlite_wal` keeps its
signature and stays the standalone certification that `test_fs_probe.py` exercises.

Rejected:

- **A marker file persisted in `metadata_root`** would extend the gain across processes.
  It is a new trust surface, though: a file that asserts a capability, outliving the
  kernel, the SQLite library, and the mount that justified it. The measured cost is
  repeated binds within one process, so the in-memory cache captures it.
- **A test-only switch to skip certification** would leave every consumer suite
  exercising a path production never takes, and it does nothing for production
  commands that bind several roots.
- **Caching inside `certify_sqlite_wal`** with an optional key would put the cache
  behind the name the binding tests patch. It would also mix the standalone
  choreography with process state.

## 5. Changes

1. `atoms/fs/probe.py`: `CertifiedVolume`, `_CERTIFIED`, `certify_volume` (§3, §4).
2. `atoms/fs/binding.py`: step 7 calls `certify_volume` with the key (§4).
3. Tests (§6).
4. A4a design §9.1 step 7 gains one sentence and a link to this document: "Certify
   SQLite-WAL hostability, once per process per volume key (2026-10-15 design)."
   §8.3 is unchanged: the choreography is the same whenever it runs.

No change to the deferred-obligation ledger: the change admits no new shape.

## 6. Testing

New, in `test_fs_binding.py`:

- **A second bind of the same volume spawns no certification child.** Count calls to
  `atoms.fs.probe._run_child` across two binds of one project root: 2, then 0.
- **A second project root on the same mount shares the result.** Same count.
- **A changed mount entry certifies again.** Patch `atoms.fs.binding.read_mountinfo`
  to return the same line with one added non-barrier option, `x-atoms-test`. An `x-`
  option is userspace-only and never reaches the kernel's mountinfo, so it is certainly
  new on any host, where `lazytime` might already be set. The configuration, and so
  the allowlist match, is unchanged, but the key differs. Expect two children.
- **Another mount with the same configuration certifies afresh.** Certify one volume
  by binding it. Then bind again while the volume reports a different mount: patch
  `atoms.fs.binding.read_mount_id` and `atoms.fs.volume.read_mount_id` to return an
  unused ID, and `atoms.fs.binding.read_mountinfo` to return the real text with the
  matched entry's ID rewritten to it. The configuration and the allowlist match are
  unchanged. Require two certification children on the second bind. A real second
  mount needs privileges the suite does not have; the simulation exercises the same
  key field a real one would change.
- **A populated cache never bypasses admission.** Warm the cache by binding a volume
  with the test allowlist. Then bind the same project root with an empty
  `DurabilityAllowlist`. Require `CapabilityUnavailable` from step 5, zero
  certification children, and no `probe/` entries afterwards. Admission precedes the
  cache lookup, so a warm cache can skip only certification.
- **A refusal is not cached.** Inject a refusing `certify_sqlite_wal` and bind twice.
  Both binds refuse, and the refusing function runs both times.
- **A hit still reclaims and still probes.** Leave debris in `probe/` before a cached
  bind, and assert it is reclaimed. Assert that `probe_backend` ran.

The existing tests that inject a certification failure patch
`atoms.fs.binding.certify_sqlite_wal`. They move to patching
`atoms.fs.binding.certify_volume`, so an injection always replaces the cached path as a
whole and never depends on what an earlier test in the same worker certified. An
autouse fixture in `tests/conftest.py` clears `_CERTIFIED` before each test. The atoms
suite stays order-independent, and each test certifies its volume once, as every test
does today.

Measurement, recorded on the task: Science's full suite before and after on the same host,
with the certification-time plugin used for the figures in §1. Expect certification
children per worker to fall from roughly 400 to the number of distinct volumes a
worker binds (one or two), and the 37% share to fall to near zero.

## 7. Crash certification

A8's replay harness recovers each cut in a fresh process by default (A8 design §7.1
step 4). A fresh process starts with an empty cache. A same-process recovery over a
remounted clone would hit the cache only if the clone matched on mount ID, device,
options and configuration. In that case its WAL hostability is the property already
certified, and crash certification judges recovery outcomes, not this probe. No A8
change is needed.
