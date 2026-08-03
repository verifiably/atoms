"""A5b tier 2 -- admission: generation, the loop, re-resolution, and translation."""

from __future__ import annotations

import pytest

from atoms.core.errors import PreconditionRefused, ProtocolError
from atoms.core.identifiers import is_valid_identifier
from tests.coordinator_support import compiled_for
from tests.store_support import one_effect_spec


def test_new_txid_is_a_valid_identifier():
    from atoms.coordinator.admission import new_txid

    for _ in range(64):
        assert is_valid_identifier(new_txid())


def test_new_txid_does_not_repeat():
    from atoms.coordinator.admission import new_txid

    assert len({new_txid() for _ in range(512)}) == 512


def test_a_candidate_a_durable_record_owns_is_discarded(leased, monkeypatch):
    from atoms.coordinator import admission

    with leased() as lease:
        with lease._store.transaction() as txn:
            txn.insert_record("taken", one_effect_spec())

        issued = iter(["taken", "free"])
        monkeypatch.setattr(admission, "new_txid", lambda: next(issued))

        approved = admission.admit(lease, compiled_for(lease))

        assert approved.txid == "free"


def test_every_candidate_owned_by_a_record_exhausts_the_bound(leased, monkeypatch):
    from atoms.coordinator import admission

    with leased() as lease:
        for txid in ("a", "b", "c"):
            with lease._store.transaction() as txn:
                txn.insert_record(txid, one_effect_spec())

        issued = iter(["a", "b", "c"])
        monkeypatch.setattr(admission, "new_txid", lambda: next(issued))

        with pytest.raises(PreconditionRefused) as caught:
            admission.admit(lease, compiled_for(lease))

        message = str(caught.value)
        assert "no usable txid after 3 attempts" in message
        # It must NOT claim scratch occupancy, which is not why it refused.
        assert "scratch occupied" not in message


def test_the_proof_binds_to_the_lease_that_issued_it(leased):
    from atoms.coordinator.admission import _require_admitted, admit

    with leased() as lease:
        approved = admit(lease, compiled_for(lease))

        assert _require_admitted(lease, approved) is None


def test_a_proof_from_another_binding_is_refused(leased):
    from atoms.coordinator.admission import _require_admitted, admit

    with leased() as first, leased() as second:
        approved = admit(first, compiled_for(first))

        with pytest.raises(ProtocolError) as caught:
            _require_admitted(second, approved)

        assert "binding" in str(caught.value)


def test_a_raw_compiled_spec_is_refused(leased):
    from typing import cast

    from atoms.coordinator.admission import _require_admitted
    from atoms.fs.approval import ProjectApprovedSpec

    with leased() as lease:
        raw = cast(ProjectApprovedSpec, compiled_for(lease))

        with pytest.raises(ProtocolError) as caught:
            _require_admitted(lease, raw)

        assert "ProjectApprovedSpec" in str(caught.value)


def test_an_occupied_scratch_leaf_regenerates_then_succeeds(leased, monkeypatch):
    from atoms.coordinator import admission
    from tests.coordinator_support import occupy_the_scratch_leaf

    with leased() as lease:
        first = admission.admit(lease, compiled_for(lease))
        occupied_path = occupy_the_scratch_leaf(lease, first)

        issued = iter([first.txid, "second"])
        monkeypatch.setattr(admission, "new_txid", lambda: next(issued))

        approved = admission.admit(lease, compiled_for(lease))

        assert approved.txid == "second"
        assert occupied_path.endswith(f".#~{first.txid}.e1.staging")


def test_persistent_occupancy_exhausts_and_names_the_leaves(leased, monkeypatch):
    from atoms.coordinator import admission

    with leased() as lease:
        monkeypatch.setattr(
            admission,
            "_occupied_scratch",
            lambda lease_, approved: (f"d/.#~{approved.txid}.e1.staging",),
        )

        with pytest.raises(PreconditionRefused) as caught:
            admission.admit(lease, compiled_for(lease))

        message = str(caught.value)
        assert "no usable txid after 3 attempts" in message
        assert "scratch occupied at d/.#~" in message


def test_a_record_collision_clears_an_earlier_candidates_occupancy(leased, monkeypatch):
    """The `occupied = ()` reset in `admit`'s record-collision branch.

    Without it, a leaf found occupied on an early attempt is still named in the final
    refusal even though the attempt that exhausted the bound was a record collision --
    reporting external occupancy for a candidate whose scratch was never examined.

    Neither existing exhaustion test can arm this: the record-collision one never
    reaches `_occupied_scratch`, and the occupancy one has no record collisions. Only a
    mixed sequence distinguishes the two versions, so this one occupies the first
    candidate's scratch and gives the remaining two candidates durable records.

    The real `_occupied_scratch` runs. The brief allowed patching it, but the leaf a
    given txid produces is derivable: approving the same compiled spec against the same
    binding and the txid `new_txid` is about to issue names exactly the leaf `admit`
    will go on to consult.
    """
    from atoms.coordinator import admission
    from atoms.fs.approval import ProjectContext, approve_for_project
    from tests.coordinator_support import occupy_the_scratch_leaf

    with leased() as lease:
        compiled = compiled_for(lease)
        # "a" has no record, so its scratch is consulted; "b" and "c" collide first.
        for txid in ("b", "c"):
            with lease._store.transaction() as txn:
                txn.insert_record(txid, one_effect_spec())
        occupied_leaf = occupy_the_scratch_leaf(
            lease, approve_for_project(compiled, ProjectContext(lease._binding, "a"))
        )
        issued = iter(["a", "b", "c"])
        monkeypatch.setattr(admission, "new_txid", lambda: next(issued))

        with pytest.raises(PreconditionRefused) as caught:
            admission.admit(lease, compiled)

        message = str(caught.value)
        assert occupied_leaf == "d/.#~a.e1.staging"
        assert "no usable txid after 3 attempts" in message
        assert "scratch occupied" not in message


def test_a_present_planned_parent_refuses_without_comparing_its_identity(leased):
    """Design §6.4 branch two: a planned parent has no approved identity, so nothing
    can be compared -- and a fresh observation would be authorizing itself."""
    from atoms.coordinator import admission
    from tests.coordinator_support import (
        compiled_creating_a_directory,
        create_the_planned_directory,
    )

    with leased() as lease:
        approved = admission.admit(lease, compiled_creating_a_directory(lease))
        create_the_planned_directory(lease, approved)

        with pytest.raises(PreconditionRefused) as caught:
            admission._occupied_scratch(lease, approved)

        message = str(caught.value)
        assert "planned parent directory 'd'" in message
        assert "no approved identity" in message


def test_a_moved_scratch_parent_refuses_naming_identity(leased):
    from atoms.coordinator import admission
    from tests.coordinator_support import replace_the_parent_directory

    with leased() as lease:
        approved = admission.admit(lease, compiled_for(lease))
        replace_the_parent_directory(lease, approved)

        with pytest.raises(PreconditionRefused) as caught:
            admission._occupied_scratch(lease, approved)

        assert "changed identity since approval" in str(caught.value)


def test_a_vanished_scratch_parent_is_translated_to_a_precondition_refusal(leased):
    """`resolve.py` raises PreconditionRefused here already; the translation covers the
    approval-time refusal types, which are wrong once a proof exists."""
    import os

    from atoms.coordinator import admission

    with leased() as lease:
        approved = admission.admit(lease, compiled_for(lease))
        os.rmdir("d", dir_fd=lease._binding.project_root_fd)

        with pytest.raises(PreconditionRefused) as caught:
            admission._occupied_scratch(lease, approved)

        assert "no longer resolves" in str(caught.value)


def test_an_approval_refusal_during_re_resolution_becomes_drift(leased, monkeypatch):
    from atoms.coordinator import admission
    from atoms.core.errors import ProjectApprovalRefused

    with leased() as lease:
        approved = admission.admit(lease, compiled_for(lease))

        def refuse(*_args, **_kwargs):
            raise ProjectApprovalRefused("synthetic approval-time refusal")

        monkeypatch.setattr(admission, "observe_child", refuse)

        with pytest.raises(PreconditionRefused) as caught:
            admission._occupied_scratch(lease, approved)

        message = str(caught.value)
        assert "post-approval drift during re-resolution" in message
        assert "synthetic approval-time refusal" in message


def test_the_work_slot_is_reported_as_occupancy_not_a_refusal(leased):
    from atoms.coordinator import admission
    from tests.coordinator_support import compiled_creating_a_directory

    with leased() as lease:
        approved = admission.admit(lease, compiled_creating_a_directory(lease))
        lease._store.create_workspace(approved.txid).close()

        assert admission._occupied_scratch(lease, approved) == (
            f"work/{approved.txid}",
        )


def test_a_moved_work_base_refuses_rather_than_regenerating(leased, monkeypatch):
    from atoms.coordinator import admission
    from atoms.fs.resolve import ChildObservation, FilesystemIdentity
    from tests.coordinator_support import compiled_creating_a_directory

    with leased() as lease:
        approved = admission.admit(lease, compiled_creating_a_directory(lease))
        assert approved.work_base is not None
        moved = ChildObservation(
            parent_identity=FilesystemIdentity(device=1, inode=1),
            parent_constraints=approved.work_base.constraints,
            present=False,
        )
        monkeypatch.setattr(
            admission, "observe_work_child", lambda binding, leaf: moved
        )

        with pytest.raises(PreconditionRefused) as caught:
            admission._occupied_scratch(lease, approved)

        assert "metadata_root/work changed identity" in str(caught.value)


def test_an_unmapped_parent_node_is_a_protocol_error(leased):
    from atoms.coordinator.admission import _parent_path, _parent_paths, admit

    with leased() as lease:
        approved = admit(lease, compiled_for(lease))

        with pytest.raises(ProtocolError) as caught:
            _parent_path(_parent_paths(approved), object())

        assert "no parent path" in str(caught.value)


def _observation_like(observed, **changes):
    from atoms.fs.resolve import ChildObservation

    fields = {
        "parent_identity": observed.parent_identity,
        "parent_constraints": observed.parent_constraints,
        "present": observed.present,
    }
    return ChildObservation(**{**fields, **changes})


def test_a_changed_name_max_refuses_naming_constraints(leased, monkeypatch):
    """NAME_MAX is half of DirectoryConstraints, and it cannot be changed by any
    syscall a test may issue -- so the comparison is driven directly."""
    from atoms.coordinator import admission
    from atoms.fs.lookup import DirectoryConstraints

    with leased() as lease:
        approved = admission.admit(lease, compiled_for(lease))
        entry = next(
            item
            for item in approved.directories
            if type(item).__name__ == "ApprovedExistingDirectory"
            and item.node == approved.scratch[0].parent_node
        )
        shrunk = DirectoryConstraints(
            lookup_proof=entry.constraints.lookup_proof,
            name_max=entry.constraints.name_max - 1,
        )
        real = admission.observe_child
        monkeypatch.setattr(
            admission,
            "observe_child",
            lambda binding, parent, leaf: _observation_like(
                real(binding, parent, leaf), parent_constraints=shrunk
            ),
        )

        with pytest.raises(PreconditionRefused) as caught:
            admission._occupied_scratch(lease, approved)

        assert "changed lookup constraints since approval" in str(caught.value)


def test_a_changed_lookup_proof_refuses_naming_constraints(leased, monkeypatch):
    """The other half. A directory that became casefold has an unreproducible lookup
    relation, so every approved name under it is meaningless -- but the observed
    NAME_MAX is unchanged, so a test that only moved NAME_MAX would not cover it."""
    from atoms.coordinator import admission
    from atoms.fs.lookup import DirectoryConstraints, LookupProof

    with leased() as lease:
        approved = admission.admit(lease, compiled_for(lease))
        real = admission.observe_child

        def folded(binding, parent, leaf):
            observed = real(binding, parent, leaf)
            return _observation_like(
                observed,
                parent_constraints=DirectoryConstraints(
                    lookup_proof=LookupProof.UNREPRODUCIBLE_CASEFOLD,
                    name_max=observed.parent_constraints.name_max,
                ),
            )

        monkeypatch.setattr(admission, "observe_child", folded)

        with pytest.raises(PreconditionRefused) as caught:
            admission._occupied_scratch(lease, approved)

        message = str(caught.value)
        assert "changed lookup constraints since approval" in message
        assert "unreproducible_casefold" in message


def test_a_parent_that_moved_across_mounts_refuses(leased, monkeypatch):
    """Mount membership. `open_child_directory` carries RESOLVE_NO_XDEV, so a parent
    that became a mount point raises EXDEV; A5b's obligation is that the EXDEV reaches
    the caller as drift rather than as a bare OSError. Injected because mounting
    requires privileges this suite does not assume."""
    import errno

    from atoms.coordinator import admission
    from atoms.fs.linux import LinuxBackend

    with leased() as lease:
        approved = admission.admit(lease, compiled_for(lease))
        real = LinuxBackend.open_child_directory

        def crosses(self, parent_fd, name):
            if name == "d":
                raise OSError(errno.EXDEV, "Invalid cross-device link")
            return real(self, parent_fd, name)

        monkeypatch.setattr(LinuxBackend, "open_child_directory", crosses)

        with pytest.raises(PreconditionRefused) as caught:
            admission._occupied_scratch(lease, approved)

        message = str(caught.value)
        assert "no longer resolves at component 'd'" in message


def test_a_capability_refusal_during_re_resolution_becomes_drift(leased, monkeypatch):
    """The second declared resolver refusal type. §9 requires both to arrive as
    PreconditionRefused once a proof exists, and one type proves only one branch."""
    from atoms.coordinator import admission
    from atoms.core.errors import CapabilityUnavailable

    with leased() as lease:
        approved = admission.admit(lease, compiled_for(lease))

        def unavailable(*_args, **_kwargs):
            raise CapabilityUnavailable("synthetic capability refusal")

        monkeypatch.setattr(admission, "observe_child", unavailable)

        with pytest.raises(PreconditionRefused) as caught:
            admission._occupied_scratch(lease, approved)

        message = str(caught.value)
        assert "post-approval drift during re-resolution" in message
        assert "synthetic capability refusal" in message


def test_an_approve_for_project_refusal_is_never_translated(leased, monkeypatch):
    """The translation covers re-resolution only. Approval's own refusals are genuine
    approval refusals and must reach the caller with their type intact -- wrapping them
    would tell a caller that external state drifted when the spec was simply refused."""
    from atoms.coordinator import admission
    from atoms.core.errors import ProjectApprovalRefused

    with leased() as lease:
        compiled = compiled_for(lease)

        def refuse(*_args, **_kwargs):
            raise ProjectApprovalRefused("synthetic approval refusal")

        monkeypatch.setattr(admission, "approve_for_project", refuse)

        with pytest.raises(ProjectApprovalRefused) as caught:
            admission.admit(lease, compiled)

        assert "synthetic approval refusal" in str(caught.value)
