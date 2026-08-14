"""A6 tier 2 -- the descriptor table (design §5)."""

from __future__ import annotations

import errno
import os

import pytest

from atoms.core.errors import PreconditionRefused, ProtocolError
from atoms.core.recovery.model import ObservedAbsent, ObservedFile, ObservedSymlink
from atoms.core.recovery.snapshot import ProjectRoot, TopologyDirectory, WorkRoot
from atoms.fs.linux import LinuxBackend
from atoms.fs.observe import Observation
from atoms.fs.topology import ApprovedPlannedDirectory
from tests.capture_support import BEFORE, approved_replace, state_of, write_project_file
from tests.coordinator_support import compiled_creating_a_directory


def _table(lease, approved, workspace, observation):
    from atoms.coordinator.descriptors import _build_descriptor_table

    return _build_descriptor_table(lease, approved, workspace, observation)


def test_the_table_holds_one_descriptor_per_approved_directory(leased):
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        approved = approved_replace(lease)
        with (
            open_workspace(lease, approved) as workspace,
            Observation(LinuxBackend()) as observation,
            _table(lease, approved, workspace, observation) as table,
        ):
            # Measured: this spec's directories are ProjectRoot and
            # TopologyDirectory(0) for `d`.
            assert isinstance(table.fd_for(ProjectRoot()), int)
            assert isinstance(table.fd_for(TopologyDirectory(node_id=0)), int)


def test_the_walk_opens_every_intermediate_directory_even_when_undeclared(leased):
    """A5b's `_parent_paths` is scoped to admission's own job (design §6.4): it maps
    only declared paths and their direct parent. `approved.directories` holds an entry
    for every directory prefix, declared or not, so the walk's own node-to-path table
    must be total over all of them -- borrowing admission's is not enough.

    `a`, `a/b`, and `a/b/c` all exist on disk and none of the three is itself
    declared -- only `a/b/c/f.txt` is -- so each is an undeclared `TopologyDirectory`
    the walk must still open a descriptor for.
    """
    from atoms.coordinator.prepare import open_workspace
    from atoms.core.recovery.snapshot import TopologyDirectory
    from tests.capture_support import approved_deep_replace

    with leased() as lease:
        approved = approved_deep_replace(lease)
        intermediate = [
            entry.node
            for entry in approved.directories
            if type(entry.node) is TopologyDirectory
        ]
        assert len(intermediate) == 3
        with (
            open_workspace(lease, approved) as workspace,
            Observation(LinuxBackend()) as observation,
            _table(lease, approved, workspace, observation) as table,
        ):
            for node in intermediate:
                assert isinstance(table.fd_for(node), int)


def test_the_root_descriptors_are_borrowed_and_survive_close(leased):
    """§5.5: ProjectBinding and Workspace own theirs; the table closes only its own."""
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        approved = approved_replace(lease)
        with open_workspace(lease, approved) as workspace:
            with (
                Observation(LinuxBackend()) as observation,
                _table(lease, approved, workspace, observation) as table,
            ):
                assert table.fd_for(ProjectRoot()) == lease._binding.project_root_fd
            assert os.fstat(lease._binding.project_root_fd).st_ino > 0
            assert os.fstat(workspace.staging_fd).st_ino > 0


def test_closing_the_table_releases_only_what_it_opened(leased):
    from atoms.coordinator.prepare import open_workspace
    from tests.fs_support import descriptor_count

    with leased() as lease:
        approved = approved_replace(lease)
        with open_workspace(lease, approved) as workspace, Observation(LinuxBackend()) as observation:
            before = descriptor_count()
            table = _table(lease, approved, workspace, observation)
            assert descriptor_count() > before
            table.close()
            assert descriptor_count() == before


def test_stops_are_unavailable_after_the_table_closes(leased):
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        approved = approved_replace(lease)
        with (
            open_workspace(lease, approved) as workspace,
            Observation(LinuxBackend()) as observation,
        ):
            table = _table(lease, approved, workspace, observation)
            table.close()
            with pytest.raises(ProtocolError, match="closed"):
                _ = table.stops


def test_close_attempts_every_owned_descriptor_and_preserves_the_failure(
    leased, monkeypatch
):
    from atoms.coordinator.prepare import open_workspace
    from atoms.core.recovery.snapshot import TopologyDirectory
    from atoms.fs.audit import AuditedBackend
    from tests.capture_support import approved_deep_replace

    with leased() as lease:
        approved = approved_deep_replace(lease)
        nodes = [
            entry.node
            for entry in approved.directories
            if type(entry.node) is TopologyDirectory
        ]
        with open_workspace(lease, approved) as workspace, Observation(LinuxBackend()) as observation:
            table = _table(lease, approved, workspace, observation)
            owned = [table.fd_for(node) for node in nodes]
            assert len(owned) == 3
            backend = lease._binding.backend
            assert isinstance(backend, AuditedBackend)
            attempted = []
            real_close = LinuxBackend.close_fd

            def failing_close(self, fd):
                attempted.append(fd)
                real_close(self, fd)
                if fd == owned[1]:
                    raise OSError(errno.EIO, "injected descriptor close failure")

            with monkeypatch.context() as patched:
                patched.setattr(LinuxBackend, "close_fd", failing_close)
                with pytest.raises(OSError) as caught:
                    table.close()
            try:
                assert caught.value.errno == errno.EIO
                assert attempted == owned
                assert table._owned == ()
                assert table._fds == {}
                for fd in owned:
                    with pytest.raises(ProtocolError, match="unregistered"):
                        backend.provenance_of(fd)
            finally:
                for fd in owned:
                    try:
                        backend.provenance_of(fd)
                    except ProtocolError:
                        continue
                    backend.close_fd(fd)


def test_construction_unwind_attempts_every_owned_descriptor_and_preserves_the_failure(
    leased, monkeypatch
):
    from atoms.coordinator import descriptors
    from atoms.coordinator.prepare import open_workspace
    from atoms.fs.audit import AuditedBackend
    from tests.capture_support import approved_deep_replace

    with leased() as lease:
        approved = approved_deep_replace(lease)
        backend = lease._binding.backend
        assert isinstance(backend, AuditedBackend)
        with open_workspace(lease, approved) as workspace, Observation(LinuxBackend()) as observation:
            validated: list[int] = []
            attempted: list[int] = []
            real_constraints = descriptors.read_lookup_constraints
            real_close = LinuxBackend.close_fd

            def fail_after_the_third_owned_descriptor(fd, filesystem_type):
                constraints = real_constraints(fd, filesystem_type)
                validated.append(fd)
                if len(validated) == 4:
                    raise PreconditionRefused("injected construction failure")
                return constraints

            def failing_close(self, fd):
                attempted.append(fd)
                real_close(self, fd)
                if len(attempted) == 1:
                    raise OSError(errno.EIO, "first injected close failure")
                if len(attempted) == 2:
                    raise OSError(errno.ENOSPC, "second injected close failure")

            with monkeypatch.context() as patched:
                patched.setattr(
                    descriptors,
                    "read_lookup_constraints",
                    fail_after_the_third_owned_descriptor,
                )
                patched.setattr(LinuxBackend, "close_fd", failing_close)
                with pytest.raises(OSError) as caught:
                    _table(lease, approved, workspace, observation)
            owned = validated[1:]
            try:
                assert len(owned) == 3
                assert caught.value.errno == errno.EIO
                assert attempted == owned
                for fd in owned:
                    with pytest.raises(ProtocolError, match="unregistered"):
                        backend.provenance_of(fd)
            finally:
                for fd in owned:
                    try:
                        backend.provenance_of(fd)
                    except ProtocolError:
                        continue
                    backend.close_fd(fd)


def test_the_work_root_is_absent_when_the_topology_has_none(leased):
    """`coordinator_on` shares one `ext4_project_root` across calls in one test (only
    the metadata root varies), so this case gets its own test function rather than
    sharing a `with leased()` block with the work-base case below -- otherwise the
    directory `approved_replace` makes would leak into the second block's fresh-`d`
    assumption."""
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        without = approved_replace(lease)
        with (
            open_workspace(lease, without) as workspace,
            Observation(LinuxBackend()) as observation,
            _table(lease, without, workspace, observation) as table,
        ):
            assert without.work_base is None
            with pytest.raises(ProtocolError, match="no descriptor"):
                table.fd_for(WorkRoot())


def test_the_work_root_is_present_when_the_topology_has_one(leased):
    from atoms.coordinator.admission import admit
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        with_work = admit(lease, compiled_creating_a_directory(lease))
        with (
            open_workspace(lease, with_work) as workspace,
            Observation(LinuxBackend()) as observation,
            _table(lease, with_work, workspace, observation) as table,
        ):
            assert with_work.work_base is not None
            assert table.fd_for(WorkRoot()) == workspace.work_fd


def test_the_work_root_baseline_is_its_own_record_not_the_work_base(leased):
    """`workspace.work_fd` names work/<txid>; `approved.work_base` describes work/.

    A4b records the child as `ApprovedPlannedDirectory(WorkRoot(),
    inherited_constraints(work_base.constraints, filesystem_type))` (`approval.py:155`,
    `topology.py:180`), so the child's baseline lives in `approved.directories` like every
    other node's, and the parent's retained facts are the wrong thing to compare a child
    descriptor against.

    On ext4 the two carry equal values, so the only way to make the choice observable is
    to make them differ -- which means editing the proof. `ProjectApprovedSpec` is
    token-guarded so that nothing outside A4b constructs one, and `dataclasses.replace`
    refuses for the same reason (`approval.py:69-84`); a sabotage fixture is the one place
    that has to go around it. `object.__setattr__` is what the factory's own `__init__`
    uses, and each doctored proof is discarded with its lease.

    Both directions are asserted, because either alone is satisfiable by the wrong code:
    a wrong parent must NOT refuse, and a wrong child record MUST.
    """
    import dataclasses

    from atoms.coordinator.admission import admit
    from atoms.coordinator.prepare import open_workspace
    from atoms.fs.lookup import inherited_constraints
    from atoms.fs.resolve import filesystem_type_of
    from atoms.fs.topology import ApprovedWorkBase

    # Arm 1: sabotage the PARENT's retained facts. The table must still build.
    with leased() as lease:
        approved = admit(lease, compiled_creating_a_directory(lease))
        assert approved.work_base is not None
        record = next(e for e in approved.directories if e.node == WorkRoot())
        assert type(record) is ApprovedPlannedDirectory
        assert record.constraints == inherited_constraints(
            approved.work_base.constraints, filesystem_type_of(lease._binding)
        )

        wrong = dataclasses.replace(record.constraints, name_max=8)
        assert wrong != record.constraints
        with open_workspace(lease, approved) as workspace:
            object.__setattr__(
                approved,
                "work_base",
                ApprovedWorkBase(
                    identity=approved.work_base.identity, constraints=wrong
                ),
            )
            with (
                Observation(LinuxBackend()) as observation,
                _table(lease, approved, workspace, observation) as table,
            ):
                assert table.fd_for(WorkRoot()) == workspace.work_fd

    # Arm 2: sabotage the CHILD's own record. The table must refuse -- proving the record
    # is read, and not skipped merely because a planned directory has no identity.
    with leased() as lease:
        approved = admit(lease, compiled_creating_a_directory(lease))
        record = next(e for e in approved.directories if e.node == WorkRoot())
        wrong = dataclasses.replace(record.constraints, name_max=8)
        with open_workspace(lease, approved) as workspace:
            object.__setattr__(
                approved,
                "directories",
                tuple(
                    ApprovedPlannedDirectory(node=WorkRoot(), constraints=wrong)
                    if entry.node == WorkRoot()
                    else entry
                    for entry in approved.directories
                ),
            )
            with (
                Observation(LinuxBackend()) as observation,
                pytest.raises(PreconditionRefused, match="name_max=8"),
            ):
                _table(lease, approved, workspace, observation)


def test_a_replaced_directory_refuses_on_identity(leased):
    """Ledger #19: compare against the approved baseline, never reapprove."""
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        approved = approved_replace(lease)
        root_fd = lease._binding.project_root_fd
        os.rename("d", "d-moved", src_dir_fd=root_fd, dst_dir_fd=root_fd)
        os.mkdir("d", dir_fd=root_fd)

        with (
            open_workspace(lease, approved) as workspace,
            Observation(LinuxBackend()) as observation,
            pytest.raises(PreconditionRefused, match="identity"),
        ):
            _table(lease, approved, workspace, observation)


def test_the_project_root_is_revalidated_too(leased, monkeypatch):
    """§5.3: retention is not discharge.

    The binding holds the root descriptor across approval so its identity cannot drift,
    but lookup_proof and name_max are mutable directory properties and ledger #19's rule
    is that a resolved fact is compared against its approved baseline before being
    relied on. A held descriptor is not an exception the ledger grants.
    """
    from atoms.coordinator import descriptors
    from atoms.coordinator.prepare import open_workspace
    from atoms.fs.lookup import DirectoryConstraints, LookupProof

    with leased() as lease:
        approved = approved_replace(lease)
        drifted = DirectoryConstraints(
            lookup_proof=LookupProof.EXACT_BYTES, name_max=64
        )
        monkeypatch.setattr(
            descriptors, "read_lookup_constraints", lambda fd, kind: drifted
        )
        with (
            open_workspace(lease, approved) as workspace,
            Observation(LinuxBackend()) as observation,
            pytest.raises(PreconditionRefused, match="constraints"),
        ):
            _table(lease, approved, workspace, observation)


def test_an_absent_planned_directory_is_a_stop_with_an_absent_observation(leased):
    from atoms.coordinator.admission import admit
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        approved = admit(lease, compiled_creating_a_directory(lease))
        with (
            open_workspace(lease, approved) as workspace,
            Observation(LinuxBackend()) as observation,
            _table(lease, approved, workspace, observation) as table,
        ):
            (stop,) = [s for s in table.stops if s.component == "d"]
            assert stop.path == "d"
            assert type(stop.observed) is ObservedAbsent


def test_an_occupied_planned_directory_reports_what_occupies_it(leased):
    """A planned directory is LOOKED UP, not assumed absent.

    Recording it as missing without a lookup would mean a matching file or symlink
    blocker is never reported, and §8.2's whole branch would be unreachable.
    """
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        write_project_file(lease, "p", BEFORE)
        approved = approved_blocked_file(lease)
        with (
            open_workspace(lease, approved) as workspace,
            Observation(LinuxBackend()) as observation,
            _table(lease, approved, workspace, observation) as table,
        ):
            (stop,) = [s for s in table.stops if s.component == "p"]
            assert type(stop.observed) is ObservedFile
            assert stop.observed.state == state_of(BEFORE)


def test_a_fifo_blocker_is_not_reported_as_a_regular_file(leased):
    """ENOTDIR does not distinguish a regular file from a socket, FIFO, or device node.

    An errno-to-kind table would have called this one REGULAR_FILE, and §8.2's
    verification against the declared FileState would then compare a fabricated kind.
    """
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        write_project_file(lease, "p", BEFORE)
        approved = approved_blocked_file(lease)
        root_fd = lease._binding.project_root_fd
        os.unlink("p", dir_fd=root_fd)
        os.mkfifo("p", 0o644, dir_fd=root_fd)

        # The observer refuses a kind no declared state can describe, rather
        # than inventing one from the errno.
        with (
            open_workspace(lease, approved) as workspace,
            Observation(LinuxBackend()) as observation,
            pytest.raises(PreconditionRefused, match="neither"),
        ):
            _table(lease, approved, workspace, observation)


def test_a_symlink_blocker_is_reported_as_a_symlink(leased):
    from atoms.coordinator.prepare import open_workspace
    from atoms.core.fingerprint import SymlinkState
    from tests.capture_support import approved_blocked

    with leased() as lease:
        root_fd = lease._binding.project_root_fd
        os.symlink("elsewhere", "p", dir_fd=root_fd)
        approved = approved_blocked(lease, SymlinkState(target="elsewhere", mode=0o777))
        with (
            open_workspace(lease, approved) as workspace,
            Observation(LinuxBackend()) as observation,
            _table(lease, approved, workspace, observation) as table,
        ):
            (stop,) = [s for s in table.stops if s.component == "p"]
            assert type(stop.observed) is ObservedSymlink


def test_a_vanished_existing_directory_refuses_rather_than_becoming_a_stop(leased):
    """Only PLANNED directories produce stops.

    A `TopologyDirectory` has no declared state, so §8's branches could never rule on
    one. Approval said it exists; if it no longer opens, that is drift and it refuses.
    """
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        approved = approved_replace(lease)
        root_fd = lease._binding.project_root_fd
        os.unlink("d/f.txt", dir_fd=root_fd)
        os.rmdir("d", dir_fd=root_fd)

        with (
            open_workspace(lease, approved) as workspace,
            Observation(LinuxBackend()) as observation,
            pytest.raises(PreconditionRefused, match="namespace"),
        ):
            _table(lease, approved, workspace, observation)


def test_an_undefined_errno_from_the_walk_propagates(leased, monkeypatch):
    """§9.1: only errnos with a defined domain meaning translate.

    Swallowing every OSError would report a failing disk as drift.
    """
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        approved = approved_replace(lease)
        real_open = LinuxBackend.open_child_directory

        def flaky(self, parent_fd, name):
            if name == "d":
                raise OSError(errno.EIO, "I/O error")
            return real_open(self, parent_fd, name)

        monkeypatch.setattr(LinuxBackend, "open_child_directory", flaky)
        with (
            open_workspace(lease, approved) as workspace,
            Observation(LinuxBackend()) as observation,
            pytest.raises(OSError) as caught,
        ):
            _table(lease, approved, workspace, observation)
        assert caught.value.errno == errno.EIO


def test_an_occupied_planned_directory_stops_the_walk_for_its_descendants(leased):
    """The occupied branch must stop too.

    A file holds no directory entries, so `p/q` is absent -- which is exactly §8.2's
    inference. Leaving the node unstopped would make `p/q` neither resolvable nor
    unreachable, and capture would raise ProtocolError on the case §8.2 accepts.
    """
    from atoms.coordinator.prepare import open_workspace
    from atoms.core.recovery.snapshot import PersistentNode

    with leased() as lease:
        write_project_file(lease, "p", BEFORE)
        approved = approved_blocked_file(lease)
        with (
            open_workspace(lease, approved) as workspace,
            Observation(LinuxBackend()) as observation,
            _table(lease, approved, workspace, observation) as table,
        ):
            assert table.is_unreachable(PersistentNode(path="p"))
            assert table.is_unreachable(PersistentNode(path="p/q"))


def test_a_node_beneath_a_stop_is_unreachable_not_missing(leased):
    """Two different facts. The table must not conflate them.

    A node proved unreachable beneath a verified stop needs no observation; a node simply
    absent from the table is an internal defect, and returning None for both would let
    the second pass silently as the first.
    """
    from atoms.coordinator.admission import admit
    from atoms.coordinator.prepare import open_workspace
    from atoms.core.recovery.snapshot import PersistentNode

    with leased() as lease:
        approved = admit(lease, compiled_creating_a_directory(lease))
        with (
            open_workspace(lease, approved) as workspace,
            Observation(LinuxBackend()) as observation,
            _table(lease, approved, workspace, observation) as table,
        ):
            assert table.is_unreachable(PersistentNode(path="d"))
            assert not table.is_unreachable(ProjectRoot())


def approved_blocked_file(lease):
    from tests.capture_support import approved_blocked

    return approved_blocked(lease, state_of(BEFORE))
