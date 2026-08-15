# A8b Certification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the ext4 feature-mask resolver, the QEMU + dm-log-writes certification harness, one
real certification run whose canonical JSON record populates `CERTIFIED_ALLOWLIST`, and the A8
landing: `FIRST_UNIMPLEMENTED` → `"A9"`, ledger #9/#15 discharge, and the corpus status sweep.

**Architecture:** The resolver is the one `src/` change: `EXT4_IOC_GET_TUNE_SB_PARAM` on the
bound directory descriptor pins all three superblock feature masks into
`VolumeConfiguration.durability_features`, fail-closed on unsupported kernels. The harness is repo
tooling in `python/tools/certify/`, never collected by pytest: a host driver boots QEMU by direct
kernel boot of the host's own kernel over a read-only 9p root, and the in-guest driver stacks
dm-log-writes over a virtio data device, runs the A8a exerciser scenarios, replays every
completion-ordered bio prefix onto fresh never-mounted clones, and verifies recovery against A3.
Certification of one exact `(VolumeConfiguration, StorageProfile)` pair emits one canonical JSON
record; the allowlist entry names it; CI validates the correspondence without rerunning anything.

**Tech Stack:** Python 3.11+ stdlib (`fcntl`, `struct`, `ctypes`-free), `pytest` for the
collected halves, `qemu-system-x86_64` (host prerequisite, installed via pacman),
`dm-log-writes` + `dmsetup` + e2fsprogs (in-guest, from the host's own tree via 9p), xfstests'
`replay-log` built from a pinned commit.

**Spec:** [`2026-08-14-a8-persistence-cut-and-certification-design.md`](2026-08-14-a8-persistence-cut-and-certification-design.md).
**Authority:** [`2026-07-23-recoverable-fs-effect-engine-design.md`](2026-07-23-recoverable-fs-effect-engine-design.md).
Where this plan and either document disagree, the design wins over this plan and the authority wins
over both. **Prerequisite:** the A8a plan is fully merged; the exerciser and cut model exist.

## Measured facts this plan is built on

Probed 2026-08-14 on this host (`main` at `e116d79`; kernel `7.1.8-arch1-3`,
`linux-api-headers 7.1-1`, `e2fsprogs 1.47.4-1`). **If any turns out false during implementation,
stop and report — do not adapt around it silently.**

| Fact | Where measured |
| --- | --- |
| `EXT4_IOC_GET_TUNE_SB_PARAM = _IOR('f', 45, struct ext4_tune_sb_params)` (`/usr/include/linux/ext4.h:36`); the struct is 232 bytes with `feature_compat`/`feature_incompat`/`feature_ro_compat` at byte offsets 64/68/72 (`:113-144`: 16 bytes of u32/u16 header, three u64 at 16/24/32, four u32 at 40-52, u16×2 + u8×2 + u16 at 56-62, then the three feature words, three set masks, three clear masks, `mount_opts[64]`, `pad[68]`). | header read 2026-08-14 |
| Invoked unprivileged on a directory fd for an ext4 volume on this host, the ioctl returns `compat=0x3c incompat=0x246 ro_compat=0x46b`; kernel handler returns the masks with no capability check; unsupported kernels fail `ENOTTY`/`EOPNOTSUPP`. | run 2026-08-14 (design §7.4) |
| mkfs fixtures: `mkfs.ext4 -O fast_commit,^orphan_file` vs `^fast_commit,orphan_file` vs neither on 128 MiB image files (no root needed) yields compat masks differing from plain by exactly `0x400` (fast_commit) and `0x1000` (orphan_file); superblock magic `0xEF53` at offset `1024+0x38`, masks at `1024+0x5C/0x60/0x64` in the image. e2fsprogs 1.47 enables `orphan_file` by default, so every mkfs in the harness passes an explicit `-O` list. | run 2026-08-14 |
| The live target's incompat mask `0x246` includes ext4's runtime `needs_recovery` bit `0x4`; `mkfs.ext4 -O needs_recovery` is invalid. The builder reproduces the raw target with only `needs_recovery` cleared (`0x242` here), and the mounted in-guest ioctl must then equal the target `0x246` exactly. | corrected by implementation preflight 2026-08-15 |
| `VolumeConfiguration(backend_id, backend_revision, kernel_identifier, filesystem_type, barrier_options, durability_features)` at `fs/volume.py:35`; `build_configuration(entry, kernel_identifier)` at `:256` hard-codes `durability_features=()` at `:275`; production and reusable test-support callers are `fs/binding.py:190-191` (`bind_project_volume`, fd in scope from `:184`'s same-volume check), `tests/fs_support.py:534` (`build_test_allowlist`, fd in scope), and the bind-mount child embedded in `tests/test_fs_resolve_conformance.py`; direct fixture calls in `test_fs_volume.py` must also adopt the keyword-only fd. `DurabilityAllowlist.match` at `:69` is exact equality; `CERTIFIED_ALLOWLIST` empty at `:78`. | corrected by implementation preflight 2026-08-15 |
| The four emptiness assertions to flip: `test_fs_architecture.py:262` (`test_certified_allowlist_is_empty_so_population_is_deliberate`), the closing assertion of `test_fs_architecture.py:453` (`test_the_production_bind_call_passes_the_certified_allowlist`, emptiness asserted at `:470`), `test_fs_volume.py:380` (`test_certified_allowlist_ships_empty`), `test_fs_binding.py:107` (`test_certified_allowlist_is_the_empty_production_constant`). | grep 2026-08-14 |
| Host prerequisites: `dmsetup`, `mkinitcpio`, the `dm-log-writes` module (`/lib/modules/7.1.8-arch1-3/kernel/drivers/md/dm-log-writes.ko.zst`), e2fsprogs, and (installed by the operator on 2026-08-15) `qemu-system-x86_64` present; `replay-log` **absent**. Host kernel image at `/boot/vmlinuz-linux`. Host ext4 mounts: `rw,noatime` (root) and `rw,noatime,data=ordered` (the ssd volume). | run 2026-08-14; corrected by implementation preflight 2026-08-15 |
| `_BARRIER_OPTIONS` (ext4): `barrier`→default `barrier=1`, `data`→`data=ordered`, `journal_async_commit`→absent, `commit`→`commit=5`, `sync`→`async`, `dirsync`→absent (`fs/volume.py:89-100`). `kernel_identifier()` is `os.uname().release` (`volume.py:287`). `BACKEND_REVISION = "linux-4"` (`fs/platform.py:12`). | read 2026-08-14 |
| dm-log-writes requires separate data and log devices; normal writes are logged around flushes in completion order; replay is prefix-based to marks/FLUSH/FUA boundaries (kernel admin-guide device-mapper/log-writes). `replay-log` lives in xfstests `src/log-writes/`, builds standalone with gcc. | kernel docs / design §7.1-§7.2 |
| `test_docs_status.py` after A8a: `STAGES` contains `"A8a", "A8b"`, `FIRST_UNIMPLEMENTED = "A8b"`. Science's adoption ledger row 4 (artifact 4) is the science repo's, updated there after landing. | A8a plan Task 10 |

## Global Constraints

- Stdlib only in `src/atoms` and `python/tests`; `python/tools/certify/` may shell out to qemu,
  dmsetup, mkfs.ext4, and the pinned `replay-log`, but adds no Python dependency.
- **Certification is manual and non-collected** (design §8): nothing under `python/tools/` is
  imported by any test; CI validates the banked record ↔ allowlist correspondence only.
- Fail early on prerequisites with exact names (design §7.1); no silent caps — any replay-prefix
  cap is declared in the record.
- The resolver applies to ext4; xfs/btrfs keep `durability_features=()` (no tuple of theirs is
  being certified; extending them is future work per design §11).
- No new production `bind_project_volume` caller; `CERTIFIED_ALLOWLIST` stays the single
  `ast.Name` the architecture test pins.
- Conventional commits, no AI-attribution trailers.
- After every task: `uv run pytest`, `uv run ruff check`, `uv run pyright` from `python/`, green.
- **Task 7 runs last** and only after the real run: it flips the boundary to `"A9"`.

## File Structure

```
python/src/atoms/fs/
  volume.py                    # modify: FeatureMasks, resolve_ext4_feature_masks, build_configuration (Task 1)
  binding.py                   # modify: thread the directory fd into build_configuration (Task 1)
python/tests/
  fs_support.py                # modify: build_test_allowlist threads the fd (Task 1)
  test_fs_volume.py            # modify/extend: resolver unit + fixture-pair tests (Task 1)
  test_fs_architecture.py      # modify: allowlist population assertions (Task 6)
  test_fs_binding.py           # modify: same (Task 6)
  test_certification_record.py # create: record ↔ allowlist correspondence (Task 5)
python/tools/certify/
  __init__.py                  # create (empty; the package is invoked as python -m tools.certify)
  __main__.py                  # create: host driver CLI (Tasks 2, 6)
  prerequisites.py             # create: fail-early checks (Task 2)
  images.py                    # create: data/log image + explicit-feature mkfs + clones (Task 2)
  guest.py                     # create: initramfs build, qemu invocation, 9p root (Task 2)
  guest_init.py                # create: in-guest driver — identity, dm stack, workload, replay loop (Task 3)
  replay.py                    # create: pinned replay-log build + prefix replay (Task 3)
  record.py                    # create: canonical JSON record schema + writer (Task 5)
.gitignore                     # modify: ignore the exact-pin replay-log build workspace (Task 3)
docs/certification/            # create: the banked record lands here (Task 6)
```

---

## Task 1: The ext4 feature-mask resolver

**Files:**
- Modify: `python/src/atoms/fs/volume.py`
- Modify: `python/src/atoms/fs/binding.py:190-191`
- Modify: `python/tests/fs_support.py:534-556`
- Test: `python/tests/test_fs_volume.py`

**Interfaces:**
- Produces: `FeatureMasks(compat: int, incompat: int, ro_compat: int)` frozen dataclass;
  `resolve_ext4_feature_masks(directory_fd: int) -> FeatureMasks` — the ioctl, raising
  `CapabilityUnavailable("ext4 feature masks unresolvable: ...")` on `ENOTTY`/`EOPNOTSUPP`/
  `EINVAL`; `feature_mask_options(masks) -> tuple[str, str, str]` returning
  `("compat=0x3c", "incompat=0x246", "ro_compat=0x46b")`-shaped strings (lowercase hex,
  zero-padded to none — exact `f"compat={masks.compat:#x}"` form).
- Produces: `build_configuration(entry, kernel_identifier, *, directory_fd: int)` — the new
  keyword-only fd; for `filesystem_type == "ext4"` it sets
  `durability_features=feature_mask_options(resolve_ext4_feature_masks(directory_fd))`; for
  xfs/btrfs it keeps `()`.
- Consumers updated in the same task: `bind_project_volume` passes the fd it already holds;
  `build_test_allowlist` passes the fd it opens at `fs_support.py:541`.

- [ ] **Step 1: Write the failing tests**

```python
# extend python/tests/test_fs_volume.py
import struct

from atoms.fs.volume import (
    FeatureMasks, build_configuration, feature_mask_options, resolve_ext4_feature_masks,
)

FAST_COMMIT = 0x400
ORPHAN_FILE = 0x1000


def test_the_ioctl_resolves_masks_on_a_live_ext4_directory(ext4_probe_fd):
    masks = resolve_ext4_feature_masks(ext4_probe_fd)
    assert masks.compat >= 0 and masks.incompat > 0  # ext4 always sets incompat bits


def test_mask_options_are_canonical_hex(ext4_probe_fd):
    masks = FeatureMasks(compat=0x43C, incompat=0x2C2, ro_compat=0x46B)
    assert feature_mask_options(masks) == ("compat=0x43c", "incompat=0x2c2", "ro_compat=0x46b")


def test_the_mkfs_fixture_pair_is_two_sided(tmp_path):
    """Design §7.4: fast_commit is compat 0x400, orphan_file compat 0x1000.

    The images are built without root and parsed at the superblock offsets the
    first spike pinned; the in-guest half re-reads them through the ioctl under
    the certification kernel (tools/certify/guest_init.py).
    """
    import subprocess

    def image_compat(features: str) -> int:
        image = tmp_path / f"{features.replace(',', '_').replace('^', 'no-')}.img"
        image.write_bytes(b"")
        subprocess.run(
            ["mkfs.ext4", "-q", "-F", "-O", features, str(image), "32768"],
            check=True,
        )
        raw = image.read_bytes()[1024 : 1024 + 1024]
        magic = struct.unpack_from("<H", raw, 0x38)[0]
        assert magic == 0xEF53
        return struct.unpack_from("<I", raw, 0x5C)[0]

    plain = image_compat("^fast_commit,^orphan_file")
    assert image_compat("fast_commit,^orphan_file") ^ plain == FAST_COMMIT
    assert image_compat("^fast_commit,orphan_file") ^ plain == ORPHAN_FILE


def test_build_configuration_pins_masks_for_ext4(ext4_probe_fd):
    from atoms.fs.volume import parse_mountinfo, read_mount_id  # probe exact imports
    # Reuse the existing live-mount resolution path the current tests use
    # (see build_test_allowlist) to obtain the MountEntry, then:
    # configuration = build_configuration(entry, "test-kernel", directory_fd=ext4_probe_fd)
    # assert any(option.startswith("compat=0x") for option in configuration.durability_features)
    ...


def test_a_non_ext4_entry_keeps_empty_features():
    # Build a MountEntry for xfs from fixture mountinfo text (existing fixtures in
    # this file already construct MountEntry values — reuse one) and assert
    # build_configuration(..., directory_fd=-1).durability_features == ().
    ...
```

(The two `...` bodies reuse this file's existing `MountEntry` fixture construction — probe
`test_fs_volume.py`'s existing tests for the established pattern and follow it.)

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_fs_volume.py -q` fails on
imports.

- [ ] **Step 3: Implement**

```python
# in python/src/atoms/fs/volume.py
import fcntl
import struct

_EXT4_TUNE_SB_PARAMS_SIZE = 232
_EXT4_IOC_GET_TUNE_SB_PARAM = (
    (2 << 30)  # _IOC_READ
    | (_EXT4_TUNE_SB_PARAMS_SIZE << 16)
    | (ord("f") << 8)
    | 45
)
_FEATURE_OFFSETS = (64, 68, 72)  # feature_compat, feature_incompat, feature_ro_compat


@dataclass(frozen=True, slots=True)
class FeatureMasks:
    compat: int
    incompat: int
    ro_compat: int


def resolve_ext4_feature_masks(directory_fd: int) -> FeatureMasks:
    """All three superblock feature masks via EXT4_IOC_GET_TUNE_SB_PARAM (design §7.4).

    The descriptor is the already-bound directory, so filesystem identity is
    intrinsic; the kernel returns the coherent mounted superblock's masks without
    a capability check. Kernels without the ioctl fail closed.
    """
    buffer = bytearray(_EXT4_TUNE_SB_PARAMS_SIZE)
    try:
        fcntl.ioctl(directory_fd, _EXT4_IOC_GET_TUNE_SB_PARAM, buffer)
    except OSError as error:
        if error.errno in (errno.ENOTTY, errno.EOPNOTSUPP, errno.EINVAL):
            raise CapabilityUnavailable(
                "ext4 feature masks unresolvable: the kernel does not support "
                "EXT4_IOC_GET_TUNE_SB_PARAM"
            ) from error
        raise
    compat, incompat, ro_compat = (
        struct.unpack_from("<I", buffer, offset)[0] for offset in _FEATURE_OFFSETS
    )
    return FeatureMasks(compat=compat, incompat=incompat, ro_compat=ro_compat)


def feature_mask_options(masks: FeatureMasks) -> tuple[str, str, str]:
    return (
        f"compat={masks.compat:#x}",
        f"incompat={masks.incompat:#x}",
        f"ro_compat={masks.ro_compat:#x}",
    )
```

`build_configuration` gains the keyword-only `directory_fd` and the ext4 branch; both call sites
thread the fd they already hold. Update the `durability_features=()` comment at `:275` to name
the resolver rule instead of the placeholder.

- [ ] **Step 4: Run the full gates** — the signature change touches `bind_project_volume` and
`build_test_allowlist`; the whole suite must stay green with masks now resolved live everywhere.

- [ ] **Step 5: Commit**

```bash
git add python/src/atoms/fs/volume.py python/src/atoms/fs/binding.py python/tests/fs_support.py python/tests/test_fs_volume.py
git commit -m "feat(volume): pin ext4 superblock feature masks via EXT4_IOC_GET_TUNE_SB_PARAM"
```

## Task 2: Harness skeleton — prerequisites, images, guest boot

**Files:**
- Create: `python/tools/certify/__init__.py`, `__main__.py`, `prerequisites.py`, `images.py`,
  `guest.py`

**Interfaces:**
- Produces: `python -m tools.certify check` — prints each prerequisite as `ok:`/`MISSING:` and
  exits nonzero listing exactly what is absent: `qemu-system-x86_64`, `/boot/vmlinuz-linux`
  readable, `dm-log-writes` module for the *host* kernel release, `mkinitcpio`, `dmsetup`,
  `mkfs.ext4`, `debugfs`, the pinned `replay-log` binary under `python/.certify/` (built by Task 3;
  reported missing until then), and a **clean atoms checkout** (`git status --porcelain` empty).
- Produces: `images.build_data_image(path, *, size_mib, feature_masks: FeatureMasks,
  mount_options: str)` — raw image mkfs'd with an **explicit `-O` list derived from the target
  masks** (translate each known bit to its e2fsprogs name; raise `UnreproducibleFeatureSet`
  naming any unknown set bit — design §7.4's refusal). Ext4 incompat `needs_recovery` (`0x4`) is
  the sole lifecycle bit: require it in the mounted target, omit it from `mkfs.ext4 -O`, require
  the raw image to equal the requested masks with only that bit cleared, and require the mounted
  in-guest ioctl to equal the requested masks exactly. Produces
  `images.build_log_image(path, size_mib)`; `images.clone(path) -> Path` — a fresh never-mounted
  copy per replay prefix (design §7.1).
- Produces: `guest.build_initramfs(work: Path) -> Path` — a cpio.gz built with the host's installed
  `mkinitcpio` `base` hook (the measured `/usr/lib/initcpio/busybox` alone has no mount or module
  loader applets), the host kernel's `9p`, `9pnet_virtio`, `virtio_pci`, `virtio_blk`,
  `dm-log-writes`, `dm-mod`, and `loop` modules (from `/lib/modules/$(uname -r)`), and an `init`
  shell script that loads them, mounts the 9p root read-only at `/root9p`, bind-mounts a tmpfs over
  its `/tmp` and `/run`, and execs `chroot /root9p python -m tools.certify.guest_init` with
  `data_device=/dev/vda` and `log_device=/dev/vdb` passed through the kernel cmdline.
- Produces: `guest.run(kernel: Path, initramfs: Path, data_image: Path, log_image: Path,
  *, shared_root: Path, memory_mib: int = 2048) -> GuestResult` — invokes
  `qemu-system-x86_64 -nographic -no-reboot -m {memory} -kernel {kernel} -initrd {initramfs}
  -append "console=ttyS0 rootfstype=9p ..." -fsdev local,id=root9p,path=/,security_model=none,readonly=on
  -device virtio-9p-pci,fsdev=root9p,mount_tag=root9p
  -drive file={data},format=raw,if=virtio,cache=writeback
  -drive file={log},format=raw,if=virtio,cache=writeback
  -serial mon:stdio`, captures the serial stream, and parses `CERTIFY-JSON:{...}` lines the guest
  emits; the exact qemu command line is retained verbatim in `GuestResult.command` for the
  record (design §7.5).

- [ ] **Step 1:** Write `prerequisites.py` with a pure `check() -> list[str]` returning missing
items, and `__main__.py` dispatching `check`. No pytest test — the tools tree is non-collected;
verification is running it.
- [ ] **Step 2:** Run `uv run python -m tools.certify check` from `python/`. Expected after the
operator's 2026-08-15 QEMU installation: `MISSING: replay-log`. Everything else `ok:`.
- [ ] **Step 3:** Implement `images.py` and `guest.py` per the interfaces. The known-bit →
mkfs-name table covers exactly the bits observed on this host's volumes plus the fixture pair
(`has_journal`, `ext_attr`, `resize_inode`, `dir_index`, `fast_commit`, `orphan_file`,
`filetype`, the runtime-only `needs_recovery`, `extent`, `64bit`, `flex_bg`,
`metadata_csum_seed`, `sparse_super`, `large_file`,
`huge_file`, `dir_nlink`, `extra_isize`, `metadata_csum` — from the spike's dumpe2fs output);
any other set bit raises `UnreproducibleFeatureSet` with the bit position.
- [ ] **Step 4:** Smoke-run image building: `uv run python -c "from tools.certify.images import
build_data_image; ..."` building a 64 MiB image from this host's live masks and verifying the
superblock masks in the raw image equal the requested masks with only incompat
`needs_recovery` cleared (read at `1024+0x5C..` as in Task 1's fixture test); Task 3's in-guest
mount verifies the live ioctl restores exact equality.
- [ ] **Step 5: Commit**

```bash
git add python/tools/certify/
git commit -m "feat(certify): harness skeleton - prerequisites, images, guest boot"
```

## Task 3: The in-guest driver and the replay loop

**Files:**
- Create: `python/tools/certify/guest_init.py`, `python/tools/certify/replay.py`
- Modify: `.gitignore`

**Interfaces:**
- Produces (`replay.py`): `ensure_replay_log(work: Path) -> Path` — fetches exactly xfstests commit
  `acb6d4cb84205a8e3f19ca470cfcf7bf6d93a509` into the git-ignored `python/.certify/` workspace
  (`git init` + `git fetch --depth 1 origin <commit>` + detached checkout, so recertification does
  not depend on that commit remaining upstream HEAD; the constant lives at the top of `replay.py`
  and is recorded in the certification record), `gcc -O2 -o
  replay-log src/log-writes/replay-log.c ...` (probe the actual source layout at build time —
  if it needs xfstests' headers, build via `make -C src/log-writes`), returns the binary path.
  `replay_prefix(log_device, clone_image, *, end_mark: str | None, end_entry: int | None)` —
  shells `replay-log --log {log} --replay {clone-as-loop} [--end-mark {mark} | --limit
  {entries}]`.
- Produces (`guest_init.py`), run as root inside the guest:
  1. **Identity verification** (design §7.1): `uname -r` equals the host release baked into the
     kernel cmdline by the driver; `BACKEND_REVISION` imported from the 9p checkout equals the
     value the host driver recorded; `sys.executable` and `sys.version` match; `git -C <checkout>
     rev-parse HEAD` matches and the tree is clean. Any mismatch: emit
     `CERTIFY-JSON:{"fatal": "identity", ...}` and exit.
  2. **Resolver cross-check** (design §7.4): mount each Task 1 mkfs fixture image loopback,
     `resolve_ext4_feature_masks` on it, assert `fast_commit`/`orphan_file` bits match the
     formatted-in features, unmount. This is the ioctl's two-sided proof under the certification
     kernel.
  3. **Replay self-verification** (design §9): on a scratch dm-log-writes stack, write a known
     byte pattern with an explicit fsync between two marks, replay to each mark on fresh clones,
     assert the pattern's presence/absence matches the mark — proving the pinned `replay-log`
     and the clone discipline before any certification claim.
  4. **The workload loop**, per exerciser scenario (design §7.1): create the dm-log-writes
     target over the data device (`dmsetup create certify --table "0 <sectors> log-writes
     <data_device> <log_device>"`, resolving the kernel-command-line values `/dev/vda` and
     `/dev/vdb` rather than assuming a root disk occupies `vda`), mkfs with the target masks and
     mount with the production-equivalent
     options at a fresh mountpoint, run the scenario through the real composition path
     (`run_clean` — the same `tests.exerciser` entry the matrix uses) with
     `CERTIFIED_ALLOWLIST` patched by `build_test_allowlist` (the guest is certifying, not yet
     certified), unmount, `dmsetup remove`, then **replay**: for every FLUSH/FUA mark and every
     intermediate entry index in the recorded log, `images.clone` the pre-workload data image,
     replay the prefix, attach the clone loopback, mount it (no fsck — mount-time journal
     replay only), enter a fresh recovery lease against it, and verify with the A8a cell
     assertions (A3 agreement via the captured spy, second pass, side assertions). Emit
     `CERTIFY-JSON` progress per scenario: `{"scenario": ..., "marks": N, "prefixes": M,
     "violations": 0}`.
  5. Resolve the guest-side `VolumeConfiguration` with `build_configuration` over the mounted
     workload volume (design §7.3: the recorded tuple is produced in-guest, never hand-typed)
     and emit it in the final `CERTIFY-JSON:{"configuration": {...}, "storage":
     "flush-honoring-disk.v1", ...}` summary.
- A replay that surfaces the §9.5 same-inode tuple is logged and verified as a bonus, never
  required (design §4.5): `guest_init` tags such cells `{"bonus": "same-inode-9.5"}`.

- [ ] **Step 1:** Implement `replay.py`; build it on the host (`uv run python -m tools.certify
build-replay`) and re-run `check` — `replay-log` flips to `ok:`.
- [ ] **Step 2:** Implement `guest_init.py` steps 1-3 and a `--self-test` mode that stops after
step 3. Run the full guest boot with `--self-test` (requires qemu installed — coordinate with the
operator; `sudo pacman -S qemu-system-x86` is the one host install this plan needs).
Expected serial output ends `CERTIFY-JSON:{"self_test": "ok"}`.
- [ ] **Step 3:** Implement the workload/replay loop (step 4-5).
- [ ] **Step 4:** Dry-run one scenario end-to-end (`python -m tools.certify run --scenario
minimal-create --trials 1`) and inspect the emitted counts.
- [ ] **Step 5: Commit**

```bash
git add python/tools/certify/
git commit -m "feat(certify): in-guest driver, pinned replay-log, prefix replay loop"
```

## Task 4: §9.5 bonus logging and full-matrix guest sweep

**Files:**
- Modify: `python/tools/certify/guest_init.py`, `__main__.py`

**Steps:**

- [ ] **Step 1:** Extend the loop to the full A8a scenario list (minimal five + `corpus-write` +
`archive-move` + `caught-rollback` + `caught-rollback-move`; refusal scenarios excluded — no
recovery row). Add `run --all`.
- [ ] **Step 2:** Run `python -m tools.certify run --all` end-to-end. Every scenario must report
`"violations": 0`; the run prints total marks/prefixes. If a scenario's prefix count exceeds
2,000, the driver refuses unless `--declare-cap N` is passed, and any cap is carried into the
record verbatim (design §7.2 — declared, never silent).
- [ ] **Step 3: Commit**

```bash
git add python/tools/certify/
git commit -m "feat(certify): full-matrix guest sweep with declared-cap discipline"
```

## Task 5: The canonical record and the correspondence test

**Files:**
- Create: `python/tools/certify/record.py`
- Create: `python/tests/test_certification_record.py`
- Create: `docs/certification/` (lands with Task 6's record)

**Interfaces:**
- Produces: `record.write(path: Path, *, configuration, storage_id, qemu_command, cache_mode,
  mkfs_command, mount_command, versions, replay_log_commit, log_format, kernel, atoms_commit,
  scenarios, date) -> None` — one canonical JSON document (sorted keys, no floats), schema:

```json
{
  "record_version": 1,
  "date": "YYYY-MM-DD",
  "configuration": {
    "backend_id": "...", "backend_revision": "linux-4",
    "kernel_identifier": "...", "filesystem_type": "ext4",
    "barrier_options": ["..."],
    "durability_features": ["compat=0x...", "incompat=0x...", "ro_compat=0x..."]
  },
  "storage": {"profile_id": "flush-honoring-disk.v1",
              "contract": "completed FLUSH makes all previously completed writes durable; a completed FUA write is durable at completion"},
  "harness": {"qemu_command": ["..."], "cache_mode": "writeback",
              "mkfs_command": ["..."], "mount_command": ["..."],
              "versions": {"qemu": "...", "e2fsprogs": "...", "kernel": "..."},
              "replay_log_commit": "...", "log_format_version": "..."},
  "atoms_commit": "...",
  "scenarios": [{"name": "...", "marks": 0, "prefixes": 0, "violations": 0,
                 "declared_cap": null}],
  "zero_violations": true
}
```

- Produces (collected test): `test_certification_record.py` — **skips cleanly when
  `docs/certification/` is empty** (pre-run state), and once a record exists asserts: the JSON
  parses and round-trips canonically; `zero_violations` is true and every scenario row has
  `violations == 0`; `CERTIFIED_ALLOWLIST` contains exactly one entry; that entry's
  `certification_ref` names this record file; the entry's `VolumeConfiguration` equals the
  record's `configuration` field-for-field; and the entry's `storage.profile_id` equals the
  record's. This is the CI half of design §7.5/§8.

- [ ] **Step 1:** Write the test (with the empty-directory skip), run — skips.
- [ ] **Step 2:** Implement `record.py`; unit-run its writer against a synthetic result and
`json.loads` round-trip.
- [ ] **Step 3: Commit**

```bash
git add python/tools/certify/record.py python/tests/test_certification_record.py
git commit -m "feat(certify): canonical certification record and CI correspondence test"
```

## Task 6: The certification run and the allowlist population

**This task performs the real run (design §7): operator-present, manual, not CI.**

**Files:**
- Create: `docs/certification/<date>-ext4-linux-<kernel>.json` (emitted)
- Modify: `python/src/atoms/fs/volume.py:78`
- Modify: `python/tests/test_fs_architecture.py:262,470`, `python/tests/test_fs_volume.py:380`,
  `python/tests/test_fs_binding.py:107`

**Steps:**

- [ ] **Step 1:** Confirm qemu installed (`python -m tools.certify check` fully `ok:`), tree
clean at the intended commit.
- [ ] **Step 2:** Target selection: resolve the production volume's masks and mount options
(`python -m tools.certify target /mnt/ssd` prints the tuple the run will certify — it must equal
what `bind_project_volume` resolves there).
- [ ] **Step 3:** `python -m tools.certify run --all --record docs/certification/` — the full
sweep; on `zero_violations`, the record file lands.
- [ ] **Step 4:** Populate:

```python
CERTIFIED_ALLOWLIST = DurabilityAllowlist(
    entries=frozenset(
        {
            AllowlistEntry(
                configuration=VolumeConfiguration(
                    # every field copied verbatim from the record's "configuration"
                ),
                storage=StorageProfile(profile_id="flush-honoring-disk.v1"),
                certification_ref="docs/certification/<the-record-file>.json",
            )
        }
    )
)
"""Populated by the A8 certification run; see the named record. Recertification
after a kernel or backend-revision change: python -m tools.certify run --all."""
```

- [ ] **Step 5:** Flip the four emptiness assertions to deliberate-population assertions: exactly
one entry; `certification_ref` resolves to an existing file under `docs/certification/`; the
record's embedded configuration equals the code entry (import both and compare); the binding
test's constant check becomes "the production constant is the certified singleton".
- [ ] **Step 6:** Full gates; `test_certification_record.py` now runs un-skipped and green.
Production binding on the certified volume accepts; every other volume still refuses
(add one assertion to `test_fs_binding.py`: a mismatched-mask configuration finds no entry).
- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat(certify): certify the ext4 tuple and populate CERTIFIED_ALLOWLIST"
```

## Task 7: Landing — the boundary, the ledger, the corpus sweep

**Files:**
- Modify: `python/tests/test_docs_status.py:34` (`FIRST_UNIMPLEMENTED = "A9"`)
- Modify: `AGENTS.md`, `README.md`, the authority design's status header, the A7 design's
  "A8 remains unimplemented" sentences, this design's header
- Modify: `docs/deferred-obligation-ledger.md` (#15 removed; #9's A8 clause noted closed)

**Steps:**

- [ ] **Step 1:** Verify the named suites pass — the #15 discharge evidence is A8a's agreement
matrix (three legs) plus this plan's Task 6; #9 closes because A8 added no production entry
point (assert: no new function joined `_TRANSACTION_STAGE_ENTRY_POINTS`; `git diff` of the A8
branch touches no coordinator entry surface).
- [ ] **Step 2:** `FIRST_UNIMPLEMENTED = "A9"`; run `uv run pytest tests/test_docs_status.py -q`
and fix every document it names — the corpus test is the sweep's authority (design §10). Update
the ledger: remove #15's row with a landing note; annotate #9.
- [ ] **Step 3:** Full gates green. Commit:

```bash
git add -A
git commit -m "docs(a8b): move the roadmap boundary to A9 and discharge ledger #15"
```

- [ ] **Step 4:** After merge to `main`: update science's adoption ledger row 4 in the science
repository as its own commit (A8 landed; production volume binding accepts the certified tuple;
Plan B adoption is the remaining gate) — outside this repo, per design §10.
