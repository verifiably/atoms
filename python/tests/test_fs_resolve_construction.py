"""PathResolver construction: liveness, dispatch, and root refusals (design §6.1)."""

from __future__ import annotations

import dataclasses
import errno
import os

import pytest

from atoms.core.errors import (
    CapabilityUnavailable,
    ProjectApprovalRefused,
    ProtocolError,
)
from atoms.fs.lookup import DirectoryConstraints, LookupProof
from atoms.fs.resolve import (
    AbsentFrontier,
    DirectoryFacts,
    EntryKind,
    FilesystemIdentity,
    PathResolver,
    PresentFrontier,
    ResolvedHop,
    ResolvedPrefix,
)


def test_injected_lookup_runs_resolver_contracts_with_non_ext4_evidence(
    monkeypatch, bound_volume, injected_lookup
):
    with bound_volume() as binding:
        configuration = dataclasses.replace(
            binding.evidence.configuration, filesystem_type="xfs"
        )
        evidence = _evidence_with(binding.evidence, configuration=configuration)
        monkeypatch.setattr(type(binding), "evidence", property(lambda self: evidence))
        resolver = PathResolver(binding)
        assert resolver.resolve("missing").root.constraints is injected_lookup


def test_a_closed_binding_refuses_construction(bound_volume, injected_lookup):
    with bound_volume() as binding:
        pass
    with pytest.raises(ProtocolError):
        PathResolver(binding)


def test_liveness_is_checked_before_any_detached_evidence_is_read(
    monkeypatch, bound_volume, injected_lookup
):
    """A closed binding must fail on liveness, never on an evidence-derived refusal.

    `evidence` is a detached value whose property performs no liveness check, so
    reading it first would let a closed binding produce a CapabilityUnavailable.
    """
    with bound_volume() as binding:
        pass
    monkeypatch.setattr(
        type(binding),
        "evidence",
        property(lambda self: pytest.fail("evidence read before the liveness gate")),
    )
    with pytest.raises(ProtocolError):
        PathResolver(binding)


def test_live_resources_are_read_before_detached_evidence(
    monkeypatch, bound_volume, injected_lookup
):
    """Both liveness-bearing properties precede the detached evidence value."""
    with bound_volume() as binding:
        backend = binding.backend
        root_fd = binding.project_root_fd
        evidence = binding.evidence
        reads = []
        monkeypatch.setattr(
            type(binding),
            "backend",
            property(lambda self: reads.append("backend") or backend),
        )
        monkeypatch.setattr(
            type(binding),
            "project_root_fd",
            property(lambda self: reads.append("project_root_fd") or root_fd),
        )

        def detached(self):
            assert reads == ["backend", "project_root_fd"]
            return evidence

        monkeypatch.setattr(type(binding), "evidence", property(detached))
        PathResolver(binding)


def test_a_non_linux_backend_id_refuses(monkeypatch, bound_volume):
    with bound_volume() as binding:
        configuration = dataclasses.replace(
            binding.evidence.configuration, backend_id="darwin"
        )
        evidence = _evidence_with(binding.evidence, configuration=configuration)
        monkeypatch.setattr(type(binding), "evidence", property(lambda self: evidence))
        with pytest.raises(CapabilityUnavailable) as caught:
            PathResolver(binding)
        assert "darwin" in str(caught.value)


@pytest.mark.parametrize("filesystem_type", ["xfs", "btrfs", "ext2"])
def test_a_non_ext4_filesystem_refuses(monkeypatch, bound_volume, filesystem_type):
    with bound_volume() as binding:
        configuration = dataclasses.replace(
            binding.evidence.configuration, filesystem_type=filesystem_type
        )
        evidence = _evidence_with(binding.evidence, configuration=configuration)
        monkeypatch.setattr(type(binding), "evidence", property(lambda self: evidence))
        with pytest.raises(CapabilityUnavailable) as caught:
            PathResolver(binding)
        assert filesystem_type in str(caught.value)


def test_a_casefold_project_root_refuses(monkeypatch, bound_volume):
    with bound_volume() as binding:
        monkeypatch.setattr(
            "atoms.fs.resolve.read_lookup_constraints",
            lambda fd, filesystem_type: DirectoryConstraints(
                lookup_proof=LookupProof.UNREPRODUCIBLE_CASEFOLD, name_max=255
            ),
        )
        with pytest.raises(ProjectApprovalRefused) as caught:
            PathResolver(binding)
        assert "casefold" in str(caught.value).lower()


def test_a_project_root_identical_to_the_metadata_root_refuses(
    monkeypatch, bound_volume, injected_lookup
):
    with bound_volume() as binding:
        info = os.fstat(binding.project_root_fd)
        evidence = _evidence_with(
            binding.evidence,
            metadata_root_device=info.st_dev,
            metadata_root_inode=info.st_ino,
        )
        monkeypatch.setattr(type(binding), "evidence", property(lambda self: evidence))
        with pytest.raises(ProjectApprovalRefused) as caught:
            PathResolver(binding)
        assert "metadata root" in str(caught.value)


def test_lookup_unavailability_precedes_path_limit_and_root_identity(
    monkeypatch, bound_volume
):
    """Root facts are observed before PATH_MAX and metadata exclusion (§6.1)."""
    with bound_volume() as binding:
        info = os.fstat(binding.project_root_fd)
        configuration = dataclasses.replace(
            binding.evidence.configuration, filesystem_type="xfs"
        )
        evidence = _evidence_with(
            binding.evidence,
            configuration=configuration,
            metadata_root_device=info.st_dev,
            metadata_root_inode=info.st_ino,
        )
        monkeypatch.setattr(type(binding), "evidence", property(lambda self: evidence))
        monkeypatch.setattr(
            "atoms.fs.resolve.os.fpathconf",
            lambda fd, name: pytest.fail("PATH_MAX read before root lookup constraints"),
        )
        with pytest.raises(CapabilityUnavailable) as caught:
            PathResolver(binding)
        assert "xfs" in str(caught.value)


def test_unexpected_lookup_error_precedes_path_limit_and_root_identity(
    monkeypatch, bound_volume
):
    injected = OSError(errno.EIO, "injected lookup failure")
    calls = []
    with bound_volume() as binding:
        info = os.fstat(binding.project_root_fd)
        evidence = _evidence_with(
            binding.evidence,
            metadata_root_device=info.st_dev,
            metadata_root_inode=info.st_ino,
        )
        monkeypatch.setattr(type(binding), "evidence", property(lambda self: evidence))

        def refuse(fd, filesystem_type):
            calls.append((fd, filesystem_type))
            raise injected

        monkeypatch.setattr("atoms.fs.resolve.read_lookup_constraints", refuse)
        monkeypatch.setattr(
            "atoms.fs.resolve.os.fpathconf",
            lambda fd, name: pytest.fail("PATH_MAX read before root lookup constraints"),
        )
        with pytest.raises(OSError) as caught:
            PathResolver(binding)
        assert caught.value is injected
        assert calls == [
            (binding.project_root_fd, binding.evidence.configuration.filesystem_type)
        ]


@pytest.mark.parametrize("value", [-1, 0, -17])
def test_nonpositive_path_max_refuses(
    monkeypatch, bound_volume, injected_lookup, value
):
    with bound_volume() as binding:
        monkeypatch.setattr(
            "atoms.fs.resolve.os.fpathconf",
            lambda fd, name: value if name == "PC_PATH_MAX" else 255,
        )
        with pytest.raises(CapabilityUnavailable) as caught:
            PathResolver(binding)
        assert "PC_PATH_MAX" in str(caught.value)


@pytest.mark.parametrize("code", [errno.EACCES, errno.EIO, errno.EPERM, errno.EMFILE])
def test_unexpected_path_max_errors_propagate_unwrapped(
    monkeypatch, bound_volume, injected_lookup, code
):
    with bound_volume() as binding:
        injected = OSError(code, "injected")
        calls = []

        def refuse(fd, name):
            calls.append((fd, name))
            raise injected

        monkeypatch.setattr("atoms.fs.resolve.os.fpathconf", refuse)
        with pytest.raises(OSError) as caught:
            PathResolver(binding)
        assert caught.value is injected
        assert calls == [(binding.project_root_fd, "PC_PATH_MAX")]


def test_identity_ignores_declared_spelling():
    left = FilesystemIdentity(device=1, inode=2)
    right = FilesystemIdentity(device=1, inode=2)
    assert left == right and hash(left) == hash(right)


@pytest.mark.parametrize(
    ("value", "field"),
    [
        (FilesystemIdentity(1, 2), "device"),
        (
            DirectoryFacts(
                FilesystemIdentity(1, 2),
                DirectoryConstraints(
                    lookup_proof=LookupProof.EXACT_BYTES, name_max=255
                ),
            ),
            "identity",
        ),
        (PresentFrontier(FilesystemIdentity(1, 2), EntryKind.DIRECTORY), "kind"),
        (
            ResolvedHop(
                "a",
                DirectoryFacts(
                    FilesystemIdentity(1, 2),
                    DirectoryConstraints(
                        lookup_proof=LookupProof.EXACT_BYTES, name_max=255
                    ),
                ),
            ),
            "declared_component",
        ),
        (
            ResolvedPrefix(
                root=DirectoryFacts(
                    FilesystemIdentity(1, 2),
                    DirectoryConstraints(
                        lookup_proof=LookupProof.EXACT_BYTES, name_max=255
                    ),
                ),
                hops=(),
                frontier_name="a",
                frontier=AbsentFrontier(),
                remainder=(),
            ),
            "frontier_name",
        ),
    ],
)
def test_every_declared_value_type_is_frozen(value, field):
    """FrozenInstanceError specifically, per design §9.1.

    `pytest.raises(Exception)` trips B017 and would also pass against an unrelated
    AttributeError, which is what a frozen slots dataclass raises for a name that is
    not a declared field. The field name comes through parametrize rather than being
    a literal: pyright rejects a direct assignment to a frozen field, and ruff's B010
    rejects `setattr` with a constant name, so only a variable satisfies both gates.
    """
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(value, field, object())


def test_the_absent_frontier_is_frozen_and_declares_no_field():
    """Checked directly and behaviourally, without an assignment.

    A frozen slots dataclass raises TypeError, not FrozenInstanceError, for a name it
    does not declare — and AbsentFrontier declares none — so an assignment-based test
    would assert the wrong thing. The dataclass parameter is the exact frozen-contract
    assertion; equality and hashability lock the resulting value behaviour too.
    """
    assert dataclasses.fields(AbsentFrontier) == ()
    assert vars(AbsentFrontier)["__dataclass_params__"].frozen
    assert AbsentFrontier() == AbsentFrontier()
    assert hash(AbsentFrontier()) == hash(AbsentFrontier())


def test_deepest_constraints_falls_back_to_the_root_when_there_are_no_hops():
    constraints = DirectoryConstraints(lookup_proof=LookupProof.EXACT_BYTES, name_max=255)
    root = DirectoryFacts(FilesystemIdentity(1, 2), constraints)
    prefix = ResolvedPrefix(
        root=root, hops=(), frontier_name="a", frontier=AbsentFrontier(), remainder=()
    )
    assert prefix.deepest_constraints is constraints


def test_deepest_constraints_uses_the_last_hop_when_there_are_hops():
    root_constraints = DirectoryConstraints(
        lookup_proof=LookupProof.EXACT_BYTES, name_max=255
    )
    deep_constraints = DirectoryConstraints(
        lookup_proof=LookupProof.EXACT_BYTES, name_max=14
    )
    root = DirectoryFacts(FilesystemIdentity(1, 2), root_constraints)
    hop = ResolvedHop("a", DirectoryFacts(FilesystemIdentity(1, 3), deep_constraints))
    prefix = ResolvedPrefix(
        root=root, hops=(hop,), frontier_name="b", frontier=AbsentFrontier(), remainder=()
    )
    assert prefix.deepest_constraints is deep_constraints


def _evidence_with(evidence, **changes):
    """A stand-in VolumeEvidence: the real one refuses construction without its token."""
    fields = {
        "configuration": evidence.configuration,
        "declared_storage_profile": evidence.declared_storage_profile,
        "matched_entry": evidence.matched_entry,
        "supplied_capabilities": evidence.supplied_capabilities,
        "metadata_root_device": evidence.metadata_root_device,
        "metadata_root_inode": evidence.metadata_root_inode,
        "mount_id": evidence.mount_id,
    }
    fields.update(changes)
    return _StandInEvidence(**fields)


@dataclasses.dataclass(frozen=True, slots=True)
class _StandInEvidence:
    configuration: object
    declared_storage_profile: object
    matched_entry: object
    supplied_capabilities: object
    metadata_root_device: int
    metadata_root_inode: int
    mount_id: int
