import copy
import dataclasses
import errno
import os
import pickle
import shutil
import subprocess
import sys

import pytest

from atoms.core.capabilities import Capability
from atoms.core.errors import CapabilityUnavailable, ProtocolError
from atoms.fs.binding import ProjectBinding, VolumeEvidence, bind_project_volume
from atoms.fs.bootstrap import close_layout, reclaim_probe_survivors
from atoms.fs.lock import acquire_project_lock
from atoms.fs.volume import CERTIFIED_ALLOWLIST, DurabilityAllowlist

_OPTIONAL_CAPABILITIES = tuple(
    capability
    for capability in Capability
    if capability
    not in {Capability.ANCHORED_TRAVERSAL, Capability.ADVISORY_PROJECT_LOCK}
)

_BIND_MOUNT_CHILD = r"""
import os
import subprocess
import sys

from atoms.core.errors import CapabilityUnavailable
from atoms.fs.binding import bind_project_volume
from atoms.fs.linux import LinuxBackend
from atoms.fs.lock import acquire_project_lock
from atoms.fs.volume import DurabilityAllowlist, StorageProfile, read_mount_id

source, target, metadata_root, mount_program = sys.argv[1:]
mounted = subprocess.run(
    [mount_program, "--bind", source, target],
    capture_output=True,
    text=True,
)
if mounted.returncode != 0:
    print(mounted.stderr.strip(), file=sys.stderr)
    sys.exit(77)

backend = LinuxBackend()
source_fd = backend.open_root(source)
target_fd = backend.open_root(target)
try:
    if os.fstat(source_fd).st_dev != os.fstat(target_fd).st_dev:
        print("bind mount did not preserve st_dev", file=sys.stderr)
        sys.exit(10)
    if read_mount_id(source_fd) == read_mount_id(target_fd):
        print("bind mount did not create a distinct mount ID", file=sys.stderr)
        sys.exit(11)
finally:
    os.close(target_fd)
    os.close(source_fd)

with acquire_project_lock(backend, metadata_root) as lock:
    try:
        bind_project_volume(
            os.path.join(target, "project"),
            lock,
            allowlist=DurabilityAllowlist(entries=frozenset()),
            storage=StorageProfile(profile_id="bind-mount-test"),
        )
    except CapabilityUnavailable as caught:
        if "same volume" not in str(caught):
            print(f"wrong refusal: {caught}", file=sys.stderr)
            sys.exit(12)
    else:
        print("equal-st_dev roots with distinct mount IDs were admitted", file=sys.stderr)
        sys.exit(13)
"""


def test_binding_succeeds_and_reports_supplied_capabilities(bound_volume):
    with bound_volume() as binding:
        assert binding.active is True
        assert binding.evidence.supplied_capabilities == frozenset(Capability)
        assert binding.evidence.matched_entry.certification_ref


def test_evidence_configuration_equals_the_matched_entry(bound_volume):
    # Deliberate redundancy: what was resolved and what was certified stay
    # separately legible. match() returns an entry only on exact equality of both.
    with bound_volume() as binding:
        evidence = binding.evidence
        assert evidence.configuration == evidence.matched_entry.configuration
        assert evidence.declared_storage_profile == evidence.matched_entry.storage


def test_empty_allowlist_refuses(project_root, held_lock, metadata_root, test_storage_profile):
    with held_lock(metadata_root) as lock, pytest.raises(
        CapabilityUnavailable, match="allowlist"
    ):
        bind_project_volume(
            str(project_root),
            lock,
            allowlist=CERTIFIED_ALLOWLIST,
            storage=test_storage_profile,
        )


def test_certified_allowlist_is_the_production_singleton():
    assert len(CERTIFIED_ALLOWLIST.entries) == 1


def test_certified_allowlist_refuses_mismatched_feature_masks():
    entry = next(iter(CERTIFIED_ALLOWLIST.entries))
    mismatched = dataclasses.replace(
        entry.configuration,
        durability_features=(
            "compat=0x3d",
            *entry.configuration.durability_features[1:],
        ),
    )
    assert CERTIFIED_ALLOWLIST.match(mismatched, entry.storage) is None


def test_refusal_reclaims_existing_debris_but_writes_nothing_new(
    project_root, held_lock, metadata_root, test_storage_profile
):
    # Reclamation precedes the allowlist refusal: if refusal came first, a
    # configuration removed from the allowlist could never have its debris cleared.
    with held_lock(metadata_root) as lock:
        os.mkdir("probe", mode=0o700, dir_fd=lock.metadata_root_fd)
        probe_fd = lock.backend.open_child_directory(lock.metadata_root_fd, "probe")
        try:
            fd = os.open("debris", os.O_CREAT | os.O_WRONLY, 0o600, dir_fd=probe_fd)
            os.close(fd)
            with pytest.raises(CapabilityUnavailable):
                bind_project_volume(
                    str(project_root),
                    lock,
                    allowlist=CERTIFIED_ALLOWLIST,
                    storage=test_storage_profile,
                )
            assert os.listdir(probe_fd) == []
        finally:
            os.close(probe_fd)
        assert "staging" not in os.listdir(lock.metadata_root_fd)


def test_cross_volume_roots_refuse(
    held_lock, metadata_root, distinct_volume, test_storage_profile
):
    with held_lock(metadata_root) as lock, pytest.raises(
        CapabilityUnavailable, match="volume"
    ):
        bind_project_volume(
            str(distinct_volume),
            lock,
            allowlist=DurabilityAllowlist(entries=frozenset()),
            storage=test_storage_profile,
        )


def test_real_bind_mount_with_equal_device_and_distinct_mount_id_refuses(test_volume):
    # The captured mountinfo fixture proves the parser distinction everywhere. This
    # Tier 3 test proves the live descriptor path and binding decision when namespace
    # support is available.
    unshare = shutil.which("unshare")
    mount_program = shutil.which("mount")
    if unshare is None or mount_program is None:
        missing = "unshare" if unshare is None else "mount"
        pytest.skip(f"real bind-mount test requires the {missing!r} program")

    namespace_probe = subprocess.run(
        [unshare, "--mount", "--map-root-user", "--", "true"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if namespace_probe.returncode != 0:
        reason = namespace_probe.stderr.strip() or "no diagnostic"
        pytest.skip(f"user/mount namespaces unavailable: {reason}")

    source = test_volume / "bind-source"
    target = test_volume / "bind-target"
    source.mkdir()
    target.mkdir()
    (source / "project").mkdir()
    metadata_root = source / "metadata"

    finished = subprocess.run(
        [
            unshare,
            "--mount",
            "--map-root-user",
            "--",
            sys.executable,
            "-c",
            _BIND_MOUNT_CHILD,
            str(source),
            str(target),
            str(metadata_root),
            mount_program,
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if finished.returncode == 77:
        reason = finished.stderr.strip() or "no diagnostic"
        pytest.skip(f"isolated bind mount unavailable: {reason}")
    assert finished.returncode == 0, finished.stderr


def test_a_lock_that_does_not_exclude_refuses_binding(
    project_root, metadata_root, fake_backend, test_allowlist, test_storage_profile
):
    # The bootstrap-prerequisite check is not dead code. flock can succeed while
    # failing to exclude — the real case on NFS without a working lock daemon — so
    # the probe reports advisory_project_lock absent even though acquisition worked.
    # (The refusals for a prerequisite that is unavailable *at acquisition* live in
    # test_fs_lock.py, since acquire_project_lock is where they are converted.)
    backend = fake_backend(supplied=set(Capability), lock_excludes=False)
    with acquire_project_lock(backend, str(metadata_root)) as lock:
        allowlist = test_allowlist(lock, project_root, test_storage_profile)
        with pytest.raises(CapabilityUnavailable, match="advisory_project_lock"):
            bind_project_volume(
                str(project_root),
                lock,
                allowlist=allowlist,
                storage=test_storage_profile,
            )


@pytest.mark.parametrize("absent", _OPTIONAL_CAPABILITIES)
def test_each_absent_optional_capability_binds_and_reports_exactly(bound_volume, absent):
    with bound_volume(withhold={absent}) as binding:
        assert binding.evidence.supplied_capabilities == frozenset(
            set(Capability) - {absent}
        )
        assert binding.active is True


def test_accessors_refuse_after_exit(bound_volume):
    with bound_volume() as binding:
        pass
    assert binding.active is False
    for accessor in ("backend", "project_root_fd", "metadata_root_fd"):
        with pytest.raises(ProtocolError):
            getattr(binding, accessor)


def test_evidence_survives_exit(bound_volume):
    with bound_volume() as binding:
        expected = binding.evidence
    assert binding.evidence is expected
    assert binding.evidence.supplied_capabilities


def test_exit_is_idempotent(bound_volume):
    with bound_volume() as binding:
        pass
    binding.__exit__(None, None, None)
    assert binding.active is False


def test_descriptors_are_cloexec(bound_volume):
    with bound_volume() as binding:
        assert os.get_inheritable(binding.project_root_fd) is False


def test_accessors_refuse_once_the_lock_is_released(
    project_root, held_lock, metadata_root, test_allowlist, test_storage_profile
):
    # Without this, metadata_root_fd could hand back a descriptor the lock already
    # closed — an out-of-order exit surfacing as EBADF somewhere far away.
    lock = held_lock(metadata_root)
    binding = None
    try:
        binding = bind_project_volume(
            str(project_root),
            lock,
            allowlist=test_allowlist(lock, project_root, test_storage_profile),
            storage=test_storage_profile,
        )
        lock.__exit__(None, None, None)
        with pytest.raises(ProtocolError):
            _ = binding.metadata_root_fd
    finally:
        if binding is not None:
            binding.__exit__(None, None, None)
        lock.__exit__(None, None, None)


def test_verified_metadata_path_delegates_to_the_shared_verifier(bound_volume, metadata_root):
    with bound_volume() as binding:
        resolved = binding.verified_metadata_path("atoms.db")
        assert resolved.endswith("/atoms.db")
        with pytest.raises(ProtocolError):
            binding.verified_metadata_path("nested/child")


def test_verified_metadata_path_refuses_a_nul_component(bound_volume):
    with bound_volume() as binding, pytest.raises(ProtocolError, match="component"):
        binding.verified_metadata_path("atoms.db\x00shadow")


def test_guarded_types_refuse_ordinary_construction(bound_volume):
    with pytest.raises(TypeError, match="bind_project_volume"):
        VolumeEvidence(
            configuration=None,  # pyright: ignore[reportArgumentType]
            declared_storage_profile=None,  # pyright: ignore[reportArgumentType]
            matched_entry=None,  # pyright: ignore[reportArgumentType]
            supplied_capabilities=frozenset(),
            metadata_root_device=0,
            metadata_root_inode=0,
            mount_id=0,
        )
    with pytest.raises(TypeError, match="bind_project_volume"):
        ProjectBinding()
    with bound_volume() as binding, pytest.raises(
        TypeError, match="bind_project_volume"
    ):
        # replace() calls __init__ without the token, so it refuses too.
        dataclasses.replace(binding.evidence, mount_id=1)


def test_binding_refuses_dataclass_replacement(bound_volume):
    # ProjectBinding is deliberately NOT a dataclass — it owns a descriptor and a
    # spent flag, which are not value semantics — so replace() refuses for want of
    # __dataclass_fields__ rather than for want of the token. Either way no copy of a
    # live binding can be fabricated, which is what A4b will rely on (ledger #16).
    with bound_volume() as binding, pytest.raises(TypeError):
        dataclasses.replace(binding)


@pytest.mark.parametrize(
    ("duplicate", "message"),
    [
        pytest.param(copy.copy, "ProjectBinding cannot be copied", id="copy"),
        pytest.param(
            copy.deepcopy, "ProjectBinding cannot be deep-copied", id="deepcopy"
        ),
        pytest.param(
            lambda value: pickle.loads(pickle.dumps(value)),
            "ProjectBinding cannot be pickled",
            id="pickle-round-trip",
        ),
    ],
)
def test_binding_refuses_duplicate_ownership(bound_volume, duplicate, message):
    # A duplicate would be a second active owner of the same descriptor integer.
    # Refusal must leave the sole real owner active and its descriptor unchanged.
    with bound_volume() as binding:
        project_root_fd = binding.project_root_fd
        project_root_identity = os.fstat(project_root_fd)

        with pytest.raises(TypeError, match=message):
            duplicate(binding)

        assert binding.active is True
        assert os.fstat(project_root_fd) == project_root_identity

    with pytest.raises(OSError) as caught:
        os.fstat(project_root_fd)
    assert caught.value.errno == errno.EBADF


def test_evidence_is_frozen(bound_volume):
    with bound_volume() as binding, pytest.raises(dataclasses.FrozenInstanceError):
        binding.evidence.mount_id = 1


def test_a_certification_failure_still_reclaims_probe_debris(
    project_root, metadata_root, linux_backend, test_allowlist, test_storage_profile, monkeypatch
):
    # Reclamation is in a finally, not on the success path. A SQLite refusal is the
    # exact shape that skips a success-only cleanup, and the debris it would strand
    # sits in engine-owned space under the lock — where the next lease entry would
    # find it and have to guess whose it was.
    def refuse(database_path, cleanup=False, **_kwargs):
        os.close(os.open(database_path, os.O_CREAT | os.O_WRONLY | os.O_CLOEXEC, 0o600))
        raise CapabilityUnavailable("injected certification failure")

    monkeypatch.setattr("atoms.fs.binding.certify_sqlite_wal", refuse)
    with acquire_project_lock(linux_backend, str(metadata_root)) as lock:
        allowlist = test_allowlist(lock, project_root, test_storage_profile)
        with pytest.raises(CapabilityUnavailable, match="injected"):
            bind_project_volume(
                str(project_root), lock, allowlist=allowlist, storage=test_storage_profile
            )
        probe_fd = lock.backend.open_child_directory(lock.metadata_root_fd, "probe")
        try:
            assert os.listdir(probe_fd) == []
        finally:
            os.close(probe_fd)


def test_a_descriptor_release_failure_still_reclaims(
    project_root, metadata_root, linux_backend, test_allowlist, test_storage_profile, monkeypatch
):
    # Reclamation sits in the OUTER finally. Releasing the layout descriptors and
    # emptying probe/ are independent obligations, and the one that leaves state on
    # disk must not become conditional on the one that does not: `close_all(...)`
    # followed by `reclaim_probe_survivors(lock)` in a single finally would skip
    # reclamation on exactly this path.
    def leaves_debris(database_path, cleanup=False, **_kwargs):
        os.close(os.open(database_path, os.O_CREAT | os.O_WRONLY | os.O_CLOEXEC, 0o600))

    def failing_release(backend, retained):
        close_layout(backend, retained)
        raise OSError(errno.EIO, "injected release failure")

    monkeypatch.setattr("atoms.fs.binding.certify_sqlite_wal", leaves_debris)
    monkeypatch.setattr("atoms.fs.binding.close_layout", failing_release)
    with acquire_project_lock(linux_backend, str(metadata_root)) as lock:
        allowlist = test_allowlist(lock, project_root, test_storage_profile)
        with pytest.raises(OSError) as caught:
            bind_project_volume(
                str(project_root), lock, allowlist=allowlist, storage=test_storage_profile
            )
        assert caught.value.errno == errno.EIO
        probe_fd = lock.backend.open_child_directory(lock.metadata_root_fd, "probe")
        try:
            assert os.listdir(probe_fd) == [], "reclamation must run despite the release failure"
        finally:
            os.close(probe_fd)


def test_release_and_reclamation_failures_attempt_both_and_raise_first(
    project_root,
    metadata_root,
    linux_backend,
    test_allowlist,
    test_storage_profile,
    monkeypatch,
):
    events = []
    reclaim_calls = 0

    def no_certification(database_path, cleanup=False, **_kwargs):
        pass

    def failing_release(backend, retained):
        events.append("release")
        close_layout(backend, retained)
        raise OSError(errno.EIO, "injected release failure")

    def failing_final_reclamation(lock):
        nonlocal reclaim_calls
        reclaim_calls += 1
        reclaim_probe_survivors(lock)
        if reclaim_calls == 2:
            events.append("reclaim")
            raise OSError(errno.EPERM, "injected reclamation failure")

    monkeypatch.setattr("atoms.fs.binding.certify_sqlite_wal", no_certification)
    monkeypatch.setattr("atoms.fs.binding.close_layout", failing_release)
    monkeypatch.setattr(
        "atoms.fs.binding.reclaim_probe_survivors", failing_final_reclamation
    )
    with acquire_project_lock(linux_backend, str(metadata_root)) as lock:
        allowlist = test_allowlist(lock, project_root, test_storage_profile)
        with pytest.raises(OSError) as caught:
            bind_project_volume(
                str(project_root), lock, allowlist=allowlist, storage=test_storage_profile
            )

    assert caught.value.errno == errno.EIO, "the first cleanup failure must surface"
    assert events == ["release", "reclaim"], "both cleanup obligations must be attempted"


def test_a_lone_reclamation_failure_surfaces_after_layout_release(
    project_root,
    metadata_root,
    linux_backend,
    test_allowlist,
    test_storage_profile,
    monkeypatch,
):
    events = []
    reclaim_calls = 0

    def no_certification(database_path, cleanup=False, **_kwargs):
        pass

    def recording_release(backend, retained):
        events.append("release")
        close_layout(backend, retained)

    def failing_final_reclamation(lock):
        nonlocal reclaim_calls
        reclaim_calls += 1
        reclaim_probe_survivors(lock)
        if reclaim_calls == 2:
            events.append("reclaim")
            raise OSError(errno.EPERM, "injected reclamation failure")

    monkeypatch.setattr("atoms.fs.binding.certify_sqlite_wal", no_certification)
    monkeypatch.setattr("atoms.fs.binding.close_layout", recording_release)
    monkeypatch.setattr(
        "atoms.fs.binding.reclaim_probe_survivors", failing_final_reclamation
    )
    with acquire_project_lock(linux_backend, str(metadata_root)) as lock:
        allowlist = test_allowlist(lock, project_root, test_storage_profile)
        with pytest.raises(OSError) as caught:
            bind_project_volume(
                str(project_root), lock, allowlist=allowlist, storage=test_storage_profile
            )

    assert caught.value.errno == errno.EPERM
    assert events == ["release", "reclaim"]
