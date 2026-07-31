"""Direct tests of the lookup-constraint reader (design §5.2-§5.4)."""

from __future__ import annotations

import dataclasses
import errno
import os

import pytest

from atoms.core.errors import CapabilityUnavailable
from atoms.fs.lookup import (
    EXT4_NAME_MAX,
    FS_CASEFOLD_FL,
    FS_IOC_GETFLAGS,
    DirectoryConstraints,
    LookupProof,
    inherited_constraints,
    lookup_equivalence_key,
    read_lookup_constraints,
)

NON_EXT4 = ("xfs", "btrfs", "ext2", "tmpfs", "")


@pytest.mark.parametrize("filesystem_type", NON_EXT4)
def test_dispatch_refuses_every_non_ext4_filesystem(directory_fd, filesystem_type):
    with pytest.raises(CapabilityUnavailable) as caught:
        read_lookup_constraints(directory_fd, filesystem_type)
    assert filesystem_type in str(caught.value) or "ext4" in str(caught.value)


@pytest.mark.parametrize("filesystem_type", NON_EXT4)
def test_dispatch_happens_before_any_ioctl(monkeypatch, directory_fd, filesystem_type):
    """A non-ext4 volume must never have ext4 flag semantics applied to it."""
    calls = []
    monkeypatch.setattr(
        "atoms.fs.lookup.fcntl.ioctl",
        lambda *args, **kwargs: calls.append(args) or 0,
    )
    with pytest.raises(CapabilityUnavailable):
        read_lookup_constraints(directory_fd, filesystem_type)
    assert calls == []


def test_clear_casefold_flag_reports_exact_bytes(monkeypatch, directory_fd):
    monkeypatch.setattr("atoms.fs.lookup._casefold_flag", lambda fd: False)
    constraints = read_lookup_constraints(directory_fd, "ext4")
    assert constraints.lookup_proof is LookupProof.EXACT_BYTES


def test_set_casefold_flag_reports_unreproducible(monkeypatch, directory_fd):
    monkeypatch.setattr("atoms.fs.lookup._casefold_flag", lambda fd: True)
    constraints = read_lookup_constraints(directory_fd, "ext4")
    assert constraints.lookup_proof is LookupProof.UNREPRODUCIBLE_CASEFOLD


def test_the_casefold_flag_is_read_from_the_real_ioctl(directory_fd):
    """The bit tested must be FS_CASEFOLD_FL, not some other flag that happens to be clear."""
    from atoms.fs.lookup import _casefold_flag, _raw_flags

    assert _casefold_flag(directory_fd) is bool(_raw_flags(directory_fd) & FS_CASEFOLD_FL)


def test_enotty_from_the_flag_ioctl_refuses_without_fallback(monkeypatch, directory_fd):
    def refuse(*args, **kwargs):
        raise OSError(errno.ENOTTY, "Inappropriate ioctl for device")

    monkeypatch.setattr("atoms.fs.lookup.fcntl.ioctl", refuse)
    with pytest.raises(CapabilityUnavailable):
        read_lookup_constraints(directory_fd, "ext4")


@pytest.mark.parametrize("code", [errno.EACCES, errno.EIO, errno.EPERM, errno.EMFILE])
def test_unexpected_ioctl_errors_propagate_unwrapped(monkeypatch, directory_fd, code):
    """Assert on the injected object itself.

    `not isinstance(caught, CapabilityUnavailable)` is satisfied by almost every OSError
    and so proves nothing about wrapping. Identity with the raised object does, and the
    call record proves the ioctl was actually reached rather than short-circuited
    earlier — the exact blind spot A4a's review found in a propagation test.
    """
    injected = OSError(code, "injected")
    calls = []

    def refuse(fd, request, *args, **kwargs):
        calls.append((fd, request))
        raise injected

    monkeypatch.setattr("atoms.fs.lookup.fcntl.ioctl", refuse)
    with pytest.raises(OSError) as caught:
        read_lookup_constraints(directory_fd, "ext4")
    assert caught.value is injected
    assert calls == [(directory_fd, FS_IOC_GETFLAGS)]


@pytest.mark.parametrize("value", [-1, 0, -17])
def test_nonpositive_name_max_refuses(monkeypatch, directory_fd, value):
    monkeypatch.setattr("atoms.fs.lookup._casefold_flag", lambda fd: False)
    monkeypatch.setattr("atoms.fs.lookup.os.fpathconf", lambda fd, name: value)
    with pytest.raises(CapabilityUnavailable) as caught:
        read_lookup_constraints(directory_fd, "ext4")
    assert "PC_NAME_MAX" in str(caught.value)


@pytest.mark.parametrize("code", [errno.EACCES, errno.EIO, errno.EPERM, errno.EMFILE])
def test_unexpected_fpathconf_errors_propagate_unwrapped(monkeypatch, directory_fd, code):
    monkeypatch.setattr("atoms.fs.lookup._casefold_flag", lambda fd: False)
    injected = OSError(code, "injected")
    calls = []

    def refuse(fd, name):
        calls.append((fd, name))
        raise injected

    monkeypatch.setattr("atoms.fs.lookup.os.fpathconf", refuse)
    with pytest.raises(OSError) as caught:
        read_lookup_constraints(directory_fd, "ext4")
    assert caught.value is injected
    assert calls == [(directory_fd, "PC_NAME_MAX")]


def test_read_constraints_reports_the_real_name_max(directory_fd):
    constraints = read_lookup_constraints(directory_fd, "ext4")
    assert constraints.name_max == os.fpathconf(directory_fd, "PC_NAME_MAX")


def test_inheritance_keeps_the_parent_proof_and_bounds_names_to_ext4_max():
    parent = DirectoryConstraints(lookup_proof=LookupProof.EXACT_BYTES, name_max=17)
    derived = inherited_constraints(parent, "ext4")
    assert derived == DirectoryConstraints(
        lookup_proof=LookupProof.EXACT_BYTES, name_max=EXT4_NAME_MAX
    )


def test_inheritance_does_not_launder_an_unreproducible_parent():
    parent = DirectoryConstraints(
        lookup_proof=LookupProof.UNREPRODUCIBLE_CASEFOLD, name_max=255
    )
    assert inherited_constraints(parent, "ext4").lookup_proof is (
        LookupProof.UNREPRODUCIBLE_CASEFOLD
    )


@pytest.mark.parametrize("filesystem_type", NON_EXT4)
def test_inheritance_refuses_every_non_ext4_filesystem(filesystem_type):
    parent = DirectoryConstraints(lookup_proof=LookupProof.EXACT_BYTES, name_max=255)
    with pytest.raises(CapabilityUnavailable):
        inherited_constraints(parent, filesystem_type)


@pytest.mark.parametrize("field", ["lookup_proof", "name_max"])
def test_constraints_are_frozen(field):
    """FrozenInstanceError specifically: `pytest.raises(Exception)` trips B017 and
    would also pass against an unrelated AttributeError.

    Parametrized on the field name, not written as a direct assignment, because the
    two gates disagree: pyright rejects assigning to a frozen field, and ruff's B010
    rejects `setattr` with a *constant* name. A variable name satisfies both, and
    covering every field is better coverage than covering one.
    """
    constraints = DirectoryConstraints(lookup_proof=LookupProof.EXACT_BYTES, name_max=255)
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(constraints, field, object())


def test_the_equivalence_key_is_the_identity_under_exact_bytes():
    constraints = DirectoryConstraints(
        lookup_proof=LookupProof.EXACT_BYTES, name_max=255
    )
    for name in ("a", "A", "é", ".hidden", "x" * 255):
        assert lookup_equivalence_key(constraints, name) == name


@pytest.mark.parametrize(
    "proof", [proof for proof in LookupProof if proof is not LookupProof.EXACT_BYTES]
)
def test_the_equivalence_key_refuses_every_unreproducible_proof(proof):
    """Parametrized over the enum, not over the one member that exists today, so a
    future LookupProof fails this suite until someone decides what its key is."""
    constraints = DirectoryConstraints(lookup_proof=proof, name_max=255)
    with pytest.raises(CapabilityUnavailable) as caught:
        lookup_equivalence_key(constraints, "a")
    assert proof.value in str(caught.value)


def test_the_vocabulary_still_has_exactly_one_reproducible_proof():
    """Guards the design's claim that the folding path has no production route: if a
    second reproducible proof lands, the test that asserts one must be revisited."""
    reproducible = [
        proof for proof in LookupProof if proof is LookupProof.EXACT_BYTES
    ]
    assert len(reproducible) == 1
