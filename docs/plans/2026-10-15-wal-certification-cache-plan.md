# SQLite-WAL certification cache Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A process certifies SQLite-WAL hostability once per volume key, so repeated binds of one volume stop spawning two child interpreters each.

**Architecture:** `atoms.fs.probe` gains a frozen `CertifiedVolume` key, a module-level set of keys that certified successfully, and `certify_volume`, which runs the unchanged `certify_sqlite_wal` choreography only on a miss. `bind_project_volume` builds the key at step 7 from values steps 2–3 already hold. Admission (step 5), the capability probe and both reclamations run on every bind.

**Tech Stack:** Python 3.13, pytest (+ testmon, xdist), ruff, pyright; `just` recipes through `tools/tt`.

**Spec:** `docs/plans/2026-10-15-wal-certification-cache-design.md` (approved, review round 2).

## Global Constraints

- Only the SQLite-WAL choreography is skipped on a hit; steps 1–6, `probe_backend`, and both `reclaim_probe_survivors` calls run on every bind.
- Success only: any exception from certification propagates before the key is added.
- No persistence, no eviction, no lock: the cache is a module-level `set`, in memory, per process.
- Key = `CertifiedVolume(mount_id, device, entry, configuration)`; `entry` is the parsed `MountEntry`, all option tuples.
- `certify_sqlite_wal` keeps its signature and behaviour.
- `atoms.fs.__all__` is unchanged: `CertifiedVolume` and `certify_volume` are not exported (`test_public_surface_is_exactly_the_documented_names`).
- Test arguments in `test_fs_*.py` must be fixtures defined in `tests/conftest.py` (`test_fs_fixture_registry_covers_every_test_argument`); use helper functions, not module-local fixtures.
- Gates: `just test-one <args>` (paths relative to `python/`) while working, `just test-fast` before each commit, `just check` runs at pre-commit. Never call `pytest` directly.
- No machine names or absolute home/Dropbox paths in committed text (the pre-commit check refuses them).

## Review Focus

- A certification that raises something other than `CapabilityUnavailable` (an `OSError` from spawning the child, a cleanup failure after a successful choreography, `KeyboardInterrupt`): nothing is cached, and the next bind certifies again. Pinned in Task 1 by parametrizing the not-cached test over all three.
- A bind whose allowlist refuses after another bind warmed the cache: refused at step 5 with no child spawned. Pinned in Task 2.
- A second mount that reports the same configuration: certified afresh. Pinned in Task 2.
- An option change outside the barrier table: certified afresh with the allowlist match unchanged. Pinned in Task 2.
- A cached bind that skipped reclamation would strand debris in `probe/`. Pinned in Task 1.

---

### Task 1: Certify once per volume key

**Files:**
- Modify: `python/src/atoms/fs/probe.py` (imports at top; new code after `certify_sqlite_wal`, end of file)
- Modify: `python/src/atoms/fs/binding.py:20` (import) and `:244-249` (step 7 call)
- Modify: `python/tests/conftest.py` (autouse fixture)
- Modify: `python/tests/test_fs_binding.py` (four existing injection tests at `:376-510`; new tests)

**Interfaces:**
- Produces, in `atoms.fs.probe`:
  - `CertifiedVolume` — `@dataclass(frozen=True, slots=True)` with fields `mount_id: int`, `device: int`, `entry: MountEntry`, `configuration: VolumeConfiguration`.
  - `_CERTIFIED: set[CertifiedVolume]`
  - `certify_volume(volume: CertifiedVolume, database_path: str, *, backend: Backend, parent_fd: int) -> None`
- Produces, in `python/tests/test_fs_binding.py`: helper `_count_children(monkeypatch) -> list[str]` (phases of every `_run_child` call), used again by Task 2.

- [ ] **Step 1: Add the key, an empty set, and a pass-through `certify_volume`**

This step changes no behaviour: `certify_volume` still certifies on every call, so the tests written in Step 5 can be seen failing on their assertions before the cache exists.

In `python/src/atoms/fs/probe.py`, add to the imports:

```python
from dataclasses import dataclass
```

and, beside the existing `atoms.fs` imports:

```python
from atoms.fs.volume import MountEntry, VolumeConfiguration
```

Append at the end of the module:

```python
@dataclass(frozen=True, slots=True)
class CertifiedVolume:
    """What makes two binds the same volume for WAL hostability (2026-10-15 design §3).

    `entry` carries every mount and superblock option, not only the barrier subset
    `configuration` keeps: locking- and mapping-relevant options (`nolock`, `dax`,
    `ro`) are not barrier options. Any remount that changes an option is a new key.
    """

    mount_id: int
    device: int
    entry: MountEntry
    configuration: VolumeConfiguration


# Keys whose certification succeeded in this process. In memory only: nothing here
# outlives the kernel, SQLite library, and mount that justified it.
_CERTIFIED: set[CertifiedVolume] = set()


def certify_volume(
    volume: CertifiedVolume,
    database_path: str,
    *,
    backend: Backend,
    parent_fd: int,
) -> None:
    """Certify `volume` (A4a §8.3, §9.1 step 7). Unconditional until Step 7."""
    certify_sqlite_wal(
        database_path, cleanup=True, backend=backend, parent_fd=parent_fd
    )
```

- [ ] **Step 2: Call it from binding step 7**

In `python/src/atoms/fs/binding.py`, change the import

```python
from atoms.fs.probe import certify_sqlite_wal, probe_backend
```

to

```python
from atoms.fs.probe import CertifiedVolume, certify_volume, probe_backend
```

and replace the step 7 certification call

```python
            certify_sqlite_wal(
                os.path.join(probe_dir, "certify.db"),
                cleanup=True,
                backend=backend,
                parent_fd=probe_fd,
            )
```

with

```python
            # Once per process per volume key (2026-10-15 design): admission at
            # step 5 and the probe above still run on every bind.
            certify_volume(
                CertifiedVolume(
                    mount_id=metadata_mount,
                    device=metadata_info.st_dev,
                    entry=entry,
                    configuration=configuration,
                ),
                os.path.join(probe_dir, "certify.db"),
                backend=backend,
                parent_fd=probe_fd,
            )
```

- [ ] **Step 3: Clear the cache before every test**

Add to `python/tests/conftest.py`, after the imports:

```python
from atoms.fs import probe as _probe


@pytest.fixture(autouse=True)
def _uncached_certification():
    """Each test certifies its volume afresh, so no test depends on what an
    earlier test in the same worker certified (design 2026-10-15 §6)."""
    _probe._CERTIFIED.clear()
```

- [ ] **Step 4: Move the four existing injection tests to the new seam**

In `python/tests/test_fs_binding.py`, the tests
`test_a_certification_failure_still_reclaims_probe_debris`,
`test_a_descriptor_release_failure_still_reclaims`,
`test_release_and_reclamation_failures_attempt_both_and_raise_first`, and
`test_a_lone_reclamation_failure_surfaces_after_layout_release` patch
`atoms.fs.binding.certify_sqlite_wal`. Since Step 2 binding no longer imports that name,
so `monkeypatch.setattr` would raise `AttributeError`. In each:

- change the target string `"atoms.fs.binding.certify_sqlite_wal"` to `"atoms.fs.binding.certify_volume"`;
- change the fake's signature from `(database_path, cleanup=False, **_kwargs)` to `(volume, database_path, **_kwargs)`. The bodies stay as they are; they already use `database_path`.

For example, `refuse` becomes:

```python
    def refuse(volume, database_path, **_kwargs):
        os.close(os.open(database_path, os.O_CREAT | os.O_WRONLY | os.O_CLOEXEC, 0o600))
        raise CapabilityUnavailable("injected certification failure")

    monkeypatch.setattr("atoms.fs.binding.certify_volume", refuse)
```

- [ ] **Step 5: Write the new tests**

Add to the imports of `python/tests/test_fs_binding.py`:

```python
from atoms.fs import probe as probe_module
from tests.fs_support import (
    make_bound_volume,
    make_fake_backend,
    make_metadata_root,
    make_project_root,
)
```

Append to `python/tests/test_fs_binding.py`:

```python
def _count_children(monkeypatch) -> list[str]:
    """Record the phase of every certification child spawned from here on."""
    phases: list[str] = []
    real = probe_module._run_child

    def counting(script, database_path, phase):
        phases.append(phase)
        return real(script, database_path, phase)

    monkeypatch.setattr("atoms.fs.probe._run_child", counting)
    return phases


def test_a_second_bind_of_the_same_volume_spawns_no_certification_child(
    bound_volume, monkeypatch
):
    children = _count_children(monkeypatch)
    with bound_volume():
        pass
    assert children == ["contention", "write"]
    with bound_volume():
        pass
    assert children == ["contention", "write"], "a cached volume must not certify again"


def test_a_second_project_root_on_the_same_mount_shares_the_result(
    bound_volume, test_volume, test_storage_profile, monkeypatch
):
    children = _count_children(monkeypatch)
    with bound_volume():
        pass
    second = test_volume / "second"
    second.mkdir()
    bind_second = make_bound_volume(
        make_fake_backend(),
        make_project_root(second),
        make_metadata_root(second),
        test_storage_profile,
    )
    with bind_second():
        pass
    assert children == ["contention", "write"]


@pytest.mark.parametrize(
    "failure",
    [
        CapabilityUnavailable("injected certification failure"),
        OSError(errno.EIO, "injected spawn or cleanup failure"),
        KeyboardInterrupt(),
    ],
    ids=["refusal", "oserror", "interrupt"],
)
def test_a_failed_certification_is_not_cached(
    project_root,
    metadata_root,
    linux_backend,
    test_allowlist,
    test_storage_profile,
    monkeypatch,
    failure,
):
    calls = []

    def fail(database_path, cleanup=False, **_kwargs):
        calls.append(database_path)
        raise failure

    monkeypatch.setattr("atoms.fs.probe.certify_sqlite_wal", fail)
    for _ in range(2):
        with acquire_project_lock(linux_backend, str(metadata_root)) as lock:
            allowlist = test_allowlist(lock, project_root, test_storage_profile)
            with pytest.raises(type(failure)):
                bind_project_volume(
                    str(project_root), lock, allowlist=allowlist, storage=test_storage_profile
                )
    assert len(calls) == 2, "a failure must leave the volume uncertified"


def test_a_cached_bind_still_reclaims_and_probes(
    bound_volume, metadata_root, monkeypatch
):
    with bound_volume():
        pass
    survivor = metadata_root / "probe" / "survivor"
    survivor.write_bytes(b"debris")
    probed = []
    real_probe = binding_module.probe_backend

    def recording_probe(*args, **kwargs):
        probed.append(True)
        return real_probe(*args, **kwargs)

    monkeypatch.setattr("atoms.fs.binding.probe_backend", recording_probe)
    children = _count_children(monkeypatch)
    with bound_volume():
        pass
    assert children == [], "the second bind must be a cache hit"
    assert probed == [True], "a hit still probes capabilities"
    assert not survivor.exists(), "a hit still reclaims probe/"
```

Add `from atoms.fs import binding as binding_module` to the same import block.

- [ ] **Step 6: Run them against the unconditional path (RED)**

Run: `just test-one tests/test_fs_binding.py -q`
Expected: exactly three failures, each on its assertion, not in setup:

- `test_a_second_bind_of_the_same_volume_spawns_no_certification_child`: "a cached volume must not certify again", with four phases recorded.
- `test_a_second_project_root_on_the_same_mount_shares_the_result`: the phase list has four entries.
- `test_a_cached_bind_still_reclaims_and_probes`: "the second bind must be a cache hit".

Everything else passes, including the four moved injection tests and the three `test_a_failed_certification_is_not_cached` cases. Those hold on an uncached path too; Step 8 shows they can fail. Any error in setup, or a failure elsewhere, means the tests are not measuring repeated certification: fix that before Step 7.

- [ ] **Step 7: Add the lookup and the record (GREEN)**

Replace the body of `certify_volume` in `python/src/atoms/fs/probe.py`:

```python
    """Certify `volume` unless this process already has (A4a §8.3, §9.1 step 7).

    Only success is recorded: a refusal, a timeout, or any other exception leaves
    the key out, so a failing volume refuses on every bind. Two threads binding the
    same new volume may both certify; each runs the full choreography under its own
    held lock and the add is idempotent, so no lock is taken here.
    """
    if volume in _CERTIFIED:
        return
    certify_sqlite_wal(
        database_path, cleanup=True, backend=backend, parent_fd=parent_fd
    )
    _CERTIFIED.add(volume)
```

Run: `just test-one tests/test_fs_binding.py -q`
Expected: all pass.

- [ ] **Step 8: Prove the not-cached tests can fail**

Temporarily move `_CERTIFIED.add(volume)` above the `certify_sqlite_wal(...)` call.

Run: `just test-one tests/test_fs_binding.py -k not_cached -q`
Expected: 3 failed on "a failure must leave the volume uncertified".

Restore the order, so that `git diff python/src/atoms/fs/probe.py` against Step 7's version is empty, and rerun the same command. Expected: 3 passed.

- [ ] **Step 9: Run the binding and probe tests**

Run: `just test-one tests/test_fs_binding.py tests/test_fs_probe.py tests/test_fs_architecture.py -q`
Expected: all pass (tests that need a certified volume skip only on a declared uncertified host).

- [ ] **Step 10: Run the fast suite**

Run: `just test-fast`
Expected: pass. A fresh worktree has no testmon DB, so this runs the whole fast set once.

- [ ] **Step 11: Commit**

```bash
git add python/src/atoms/fs/probe.py python/src/atoms/fs/binding.py python/tests/conftest.py python/tests/test_fs_binding.py
git commit -m "perf(fs): certify SQLite-WAL hostability once per volume per process"
```

The pre-commit hook runs `just check` (ruff, pyright, ops checks, `tasks check`); fix anything it reports before retrying.

---

### Task 2: Safety boundaries with a warm cache

**Files:**
- Modify: `python/tests/test_fs_binding.py` (append)

**Interfaces:**
- Consumes: `_count_children(monkeypatch) -> list[str]` and the `probe_module`/`binding_module` imports from Task 1; `atoms.fs.volume.parse_mountinfo`, `read_mountinfo`.

- [ ] **Step 1: Write the boundary tests**

Add `from atoms.fs import volume as volume_module` to the imports, then append:

```python
def _rewrite_entry(text: str, mount_id: int, *, new_id: int | None = None, extra_option: str | None = None) -> str:
    """The real mountinfo text with one entry's ID or mount options changed."""
    lines = []
    for line in text.splitlines():
        fields = line.split()
        if fields and fields[0] == str(mount_id):
            if new_id is not None:
                fields[0] = str(new_id)
            if extra_option is not None:
                fields[5] = f"{fields[5]},{extra_option}"
            line = " ".join(fields)
        lines.append(line)
    return "\n".join(lines) + "\n"


def _bind_once(linux_backend, project_root, metadata_root, allowlist, storage):
    with acquire_project_lock(linux_backend, str(metadata_root)) as lock:
        with bind_project_volume(
            str(project_root), lock, allowlist=allowlist, storage=storage
        ) as binding:
            return binding.evidence.mount_id


def _allowlist_for(linux_backend, project_root, metadata_root, test_allowlist, storage):
    with acquire_project_lock(linux_backend, str(metadata_root)) as lock:
        return test_allowlist(lock, project_root, storage)


def test_another_mount_with_the_same_configuration_certifies_afresh(
    project_root,
    metadata_root,
    linux_backend,
    test_allowlist,
    test_storage_profile,
    monkeypatch,
):
    # A real second mount needs privileges the suite lacks; this changes the same key
    # field one would. The allowlist is built before patching, from the real mount.
    allowlist = _allowlist_for(
        linux_backend, project_root, metadata_root, test_allowlist, test_storage_profile
    )
    children = _count_children(monkeypatch)
    real_id = _bind_once(
        linux_backend, project_root, metadata_root, allowlist, test_storage_profile
    )
    assert children == ["contention", "write"]

    text = volume_module.read_mountinfo()
    other = max(entry.mount_id for entry in volume_module.parse_mountinfo(text)) + 1
    rewritten = _rewrite_entry(text, real_id, new_id=other)
    monkeypatch.setattr("atoms.fs.binding.read_mount_id", lambda fd: other)
    monkeypatch.setattr("atoms.fs.volume.read_mount_id", lambda fd: other)
    monkeypatch.setattr("atoms.fs.binding.read_mountinfo", lambda: rewritten)

    assert _bind_once(
        linux_backend, project_root, metadata_root, allowlist, test_storage_profile
    ) == other
    assert children == ["contention", "write"] * 2, "a different mount must certify"


def test_a_changed_mount_option_certifies_afresh(
    project_root,
    metadata_root,
    linux_backend,
    test_allowlist,
    test_storage_profile,
    monkeypatch,
):
    # `x-` options are userspace-only and never reach the kernel's mountinfo, so the
    # added option is certainly new, and it is in no barrier table, so the
    # configuration and the allowlist match are unchanged.
    allowlist = _allowlist_for(
        linux_backend, project_root, metadata_root, test_allowlist, test_storage_profile
    )
    children = _count_children(monkeypatch)
    real_id = _bind_once(
        linux_backend, project_root, metadata_root, allowlist, test_storage_profile
    )
    rewritten = _rewrite_entry(
        volume_module.read_mountinfo(), real_id, extra_option="x-atoms-test"
    )
    monkeypatch.setattr("atoms.fs.binding.read_mountinfo", lambda: rewritten)

    _bind_once(linux_backend, project_root, metadata_root, allowlist, test_storage_profile)
    assert children == ["contention", "write"] * 2, "a changed option must certify"


def test_a_warm_cache_never_bypasses_the_allowlist(
    project_root,
    metadata_root,
    linux_backend,
    test_allowlist,
    test_storage_profile,
    monkeypatch,
):
    allowlist = _allowlist_for(
        linux_backend, project_root, metadata_root, test_allowlist, test_storage_profile
    )
    _bind_once(linux_backend, project_root, metadata_root, allowlist, test_storage_profile)
    assert probe_module._CERTIFIED, "the first bind must have warmed the cache"

    probed = []
    real_probe = binding_module.probe_backend

    def recording_probe(*args, **kwargs):
        probed.append(True)
        return real_probe(*args, **kwargs)

    monkeypatch.setattr("atoms.fs.binding.probe_backend", recording_probe)
    children = _count_children(monkeypatch)
    with acquire_project_lock(linux_backend, str(metadata_root)) as lock:
        with pytest.raises(CapabilityUnavailable, match="durability allowlist"):
            bind_project_volume(
                str(project_root),
                lock,
                allowlist=DurabilityAllowlist(),
                storage=test_storage_profile,
            )
        probe_fd = lock.backend.open_child_directory(lock.metadata_root_fd, "probe")
        try:
            assert os.listdir(probe_fd) == []
        finally:
            os.close(probe_fd)
    assert children == [], "a refused bind must spawn no certification child"
    assert probed == [], "admission must refuse before the probe and the cache are reached"
```

- [ ] **Step 2: Run them**

Run: `just test-one tests/test_fs_binding.py -k "afresh or bypasses" -q`
Expected: 3 passed.

- [ ] **Step 3: Prove the mount and option tests can fail**

Temporarily narrow the key in `python/src/atoms/fs/binding.py` step 7 to configuration alone, replacing `mount_id=metadata_mount,` with `mount_id=0,`, `device=metadata_info.st_dev,` with `device=0,`, and `entry=entry,` with `entry=None,  # type: ignore[arg-type]`.

Run: `just test-one tests/test_fs_binding.py -k "afresh" -q`
Expected: 2 failed, each on its "must certify" assertion.

Then move the allowlist check after the cache. In `bind_project_volume`, cut the `matched = allowlist.match(...)` block and its `if matched is None: raise ...`, and paste it directly after the `certify_volume(...)` call. Restore the key first, so only this change is in effect.

Run: `just test-one tests/test_fs_binding.py -k "bypasses" -q`
Expected: FAIL on "admission must refuse before the probe and the cache are reached". The cache hit spawns no child and `probe/` is reclaimed either way, so the probe assertion is the one that catches a reordering.

Restore `binding.py` exactly (`git diff python/src/atoms/fs/binding.py` must be empty relative to the Task 1 commit), then rerun `just test-one tests/test_fs_binding.py -q`. Expected: all pass.

- [ ] **Step 4: Run the fast suite**

Run: `just test-fast`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add python/tests/test_fs_binding.py
git commit -m "test(fs): pin WAL certification cache safety boundaries"
```

---

### Task 3: Record the change and measure it

**Files:**
- Modify: `docs/plans/2026-07-29-a4a-capability-backend-design.md` (§9.1 step 7)
- Modify: `docs/plans/2026-10-15-wal-certification-cache-design.md` (status line)

**Interfaces:**
- Consumes: the Task 1 behaviour, through Science's suite.

- [ ] **Step 1: Amend A4a §9.1 step 7**

In `docs/plans/2026-07-29-a4a-capability-backend-design.md`, replace

```markdown
7. Probe capabilities, then certify SQLite-WAL hostability.
```

with

```markdown
7. Probe capabilities, then certify SQLite-WAL hostability, once per process per
   volume key ([2026-10-15 design](2026-10-15-wal-certification-cache-design.md)).
```

- [ ] **Step 2: Mark the design implemented**

In `docs/plans/2026-10-15-wal-certification-cache-design.md`, replace the status line with

```markdown
**Status:** Implemented, 2026-10-15. Approved in review round 2; measurement in §6 recorded on `atoms-257797`.
```

- [ ] **Step 3: Measure in Science's suite, before and after, at a fixed worker count**

The cost being measured is interpreter contention, which moves with concurrency. Science's `test-one` runs under `host-budget`, which re-grants workers on every invocation, so `-n auto` could give the two runs different worker counts. Both runs therefore pass `-n 8` (the count Science's justfile names as its sweet spot). Each worker records the count it ran under, its cgroup CPU limit, and the atoms module it actually imported. The summary refuses a pair that differs in any of these.

Make a scratch directory outside both repositories and set `PLUGIN_DIR` to it (for example `PLUGIN_DIR=$(mktemp -d)`). Write `$PLUGIN_DIR/certtime.py`:

```python
"""pytest plugin: worker time spent in atoms' WAL certification, and the run's shape."""
import json, os, time
from atoms.fs import binding, probe

_acc = {"cert": 0.0, "n": 0}
_t0 = time.perf_counter()
_orig = probe.certify_sqlite_wal

def _timed(*a, **k):
    t = time.perf_counter()
    try:
        return _orig(*a, **k)
    finally:
        _acc["cert"] += time.perf_counter() - t
        _acc["n"] += 1

probe.certify_sqlite_wal = _timed
if hasattr(binding, "certify_sqlite_wal"):
    binding.certify_sqlite_wal = _timed

def _cpu_max():
    try:
        path = open("/proc/self/cgroup").read().strip().split("::", 1)[1]
        return open(f"/sys/fs/cgroup{path}/cpu.max").read().strip()
    except (OSError, IndexError):
        return "unavailable"

def pytest_sessionfinish(session):
    record = {
        "worker": os.environ.get("PYTEST_XDIST_WORKER", "controller"),
        "workers": int(os.environ.get("PYTEST_XDIST_WORKER_COUNT", "0")),
        "probe_file": probe.__file__,
        "cpu_max": _cpu_max(),
        "wall": round(time.perf_counter() - _t0, 2),
        "cert": round(_acc["cert"], 2),
        "n": _acc["n"],
    }
    with open(os.environ["CERTTIME_OUT"], "a") as f:
        f.write(json.dumps(record) + "\n")
```

Write `$PLUGIN_DIR/summarize.py`:

```python
"""Compare a before/after pair; exit 1 if the runs are not comparable."""
import json, sys

def load(path, expect_prefix):
    rows = [json.loads(line) for line in open(path)]
    workers = [r for r in rows if r["worker"] != "controller"]
    problems = []
    if not workers:
        problems.append(f"{path}: no worker rows")
    counts = {r["workers"] for r in workers}
    if counts != {len(workers)}:
        problems.append(f"{path}: {len(workers)} worker rows but reported counts {sorted(counts)}")
    wrong = sorted({r["probe_file"] for r in workers if not r["probe_file"].startswith(expect_prefix)})
    if wrong:
        problems.append(f"{path}: atoms imported from {wrong}, expected under {expect_prefix}")
    summary = {
        "workers": len(workers),
        "cpu_max": sorted({r["cpu_max"] for r in workers}),
        "worker_s": round(sum(r["wall"] for r in workers), 1),
        "cert_s": round(sum(r["cert"] for r in workers), 1),
        "n": sum(r["n"] for r in workers),
    }
    return summary, problems

before, p1 = load(sys.argv[1], sys.argv[3])
after, p2 = load(sys.argv[2], sys.argv[4])
problems = p1 + p2
for key in ("workers", "cpu_max"):
    if before[key] != after[key]:
        problems.append(f"{key} differs: before {before[key]}, after {after[key]}")
print(json.dumps({"before": before, "after": after}, indent=1))
for problem in problems:
    print("NOT COMPARABLE:", problem)
sys.exit(1 if problems else 0)
```

Set the two expected import roots. `MAIN_SRC` is where Science's editable install points, read without an override. `ATOMS_WT` is this worktree's absolute path (`git rev-parse --show-toplevel` inside it). From Science's `python/`:

```bash
MAIN_SRC=$(uv run --no-sync python -c "import atoms, pathlib; print(pathlib.Path(atoms.__path__[0]).parent)")
```

`$MAIN_SRC` must not be under `$ATOMS_WT`. If it is, the pair cannot distinguish the two and the step stops here.

**Pilot first.** From Science's main checkout, run one world-heavy module through both commands and the summary:

```bash
CERTTIME_OUT=$PLUGIN_DIR/pilot-before.log PYTHONPATH=$PLUGIN_DIR just test-one tests/test_cmd_spec.py -p certtime -n 8 --dist worksteal -q
CERTTIME_OUT=$PLUGIN_DIR/pilot-after.log PYTHONPATH=$ATOMS_WT/python/src:$PLUGIN_DIR just test-one tests/test_cmd_spec.py -p certtime -n 8 --dist worksteal -q
python3 $PLUGIN_DIR/summarize.py $PLUGIN_DIR/pilot-before.log $PLUGIN_DIR/pilot-after.log "$MAIN_SRC" "$ATOMS_WT/python/src"
```

Expected: exit 0, 8 workers in both, the same `cpu_max`, and `n` lower after than before. On a non-zero exit, fix the cause it names before the full pair. The likely causes are a missing `PYTHONPATH` entry or a worker count other than 8.

**Full pair, back to back.** Record `cat /proc/loadavg` before each run.

```bash
CERTTIME_OUT=$PLUGIN_DIR/before.log PYTHONPATH=$PLUGIN_DIR just test-one tests -p certtime -n 8 --dist worksteal -q
CERTTIME_OUT=$PLUGIN_DIR/after.log PYTHONPATH=$ATOMS_WT/python/src:$PLUGIN_DIR just test-one tests -p certtime -n 8 --dist worksteal -q
python3 $PLUGIN_DIR/summarize.py $PLUGIN_DIR/before.log $PLUGIN_DIR/after.log "$MAIN_SRC" "$ATOMS_WT/python/src"
```

Expected: exit 0. `after.n` should be about the worker count times the number of distinct volumes a worker binds (one or two each), against roughly 3,300 before. The `cert_s` share of `worker_s` should fall from about a third to near zero. A non-zero exit means the pair is not evidence; rerun it rather than recording it.

- [ ] **Step 4: Record the measurement on the task**

```bash
tasks note atoms-257797 "measured <date>, science full suite at -n 8 (cpu_max <v>): before <passed> in <s> s, cert <s> of <worker-s> worker-s over <n> certifications (load <x>); after <passed> in <s> s, cert <s> of <worker-s> over <n> (load <x>); summarize exit 0"
```

Fill each `<…>` with the observed values. A missing figure is a failed step, not an omission.

- [ ] **Step 5: Run the fast suite and commit**

Run: `just test-fast`
Expected: pass. Only docs changed, so the selection is empty or small.

```bash
git add docs/plans/2026-07-29-a4a-capability-backend-design.md docs/plans/2026-10-15-wal-certification-cache-design.md
git commit -m "docs(plans): record per-process WAL certification in A4a §9.1"
```
