"""A6 tier 1 -- the coherent observation mechanism (design §6)."""

from __future__ import annotations

import errno
import hashlib
import os

import pytest

from atoms.core.errors import CapabilityUnavailable, PreconditionRefused, ProtocolError
from atoms.core.recovery.model import (
    FileBuildRelation,
    ObservedAbsent,
    ObservedDirectory,
    ObservedFile,
    ObservedSymlink,
)
from atoms.fs.linux import LinuxBackend
from atoms.fs.observe import Observation, translated_lookup


def digest_of(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def open_descriptors() -> int:
    return len(os.listdir("/proc/self/fd"))


def test_a_regular_file_is_observed_from_one_descriptor(project):
    root, fd = project
    (root / "f.txt").write_bytes(b"payload")
    os.chmod(root / "f.txt", 0o644)

    with Observation(LinuxBackend()) as observation:
        entry = observation.observe(fd, "f.txt")

    assert type(entry) is ObservedFile
    assert entry.state.content_hash == digest_of(b"payload")
    assert entry.state.byte_len == 7
    assert entry.state.mode == 0o644


def test_one_entry_reached_twice_yields_one_token(project):
    root, fd = project
    (root / "f.txt").write_bytes(b"payload")
    os.link(root / "f.txt", root / "same.txt")

    with Observation(LinuxBackend()) as observation:
        first = observation.observe(fd, "f.txt")
        second = observation.observe(fd, "same.txt")

    assert type(first) is ObservedFile
    assert type(second) is ObservedFile
    assert first.identity == second.identity
    assert first.state == second.state


def test_distinct_entries_yield_distinct_tokens(project):
    root, fd = project
    (root / "a.txt").write_bytes(b"a")
    (root / "b.txt").write_bytes(b"b")

    with Observation(LinuxBackend()) as observation:
        first = observation.observe(fd, "a.txt")
        second = observation.observe(fd, "b.txt")

    assert type(first) is ObservedFile
    assert type(second) is ObservedFile
    assert first.identity != second.identity


def test_each_pass_mints_a_fresh_token_universe(project):
    root, fd = project
    (root / "f.txt").write_bytes(b"payload")

    with Observation(LinuxBackend()) as first_pass:
        first = first_pass.observe(fd, "f.txt")
    with Observation(LinuxBackend()) as second_pass:
        second = second_pass.observe(fd, "f.txt")

    # A3's identity equality means "same entry, same pass". A token that survived the
    # pass would let A3 conclude more than was observed.
    assert type(first) is ObservedFile
    assert type(second) is ObservedFile
    assert first.identity != second.identity


def test_a_symlink_carries_a_fingerprint_and_no_identity(project):
    root, fd = project
    os.symlink("target", root / "link")

    with Observation(LinuxBackend()) as observation:
        entry = observation.observe(fd, "link")

    assert type(entry) is ObservedSymlink
    assert entry.state.target == "target"
    # Structural, not conventional: the type has no field for one.
    assert not hasattr(entry, "identity")


def test_a_directory_requires_its_modeled_children(project):
    """Failure to look is not a finding of absence.

    `has_unmodeled_child` is evidence. Producing `False` without enumerating would put a
    fabricated fact into A3's input, so the route refuses rather than guessing.
    """
    root, fd = project
    (root / "sub").mkdir()

    with Observation(LinuxBackend()) as observation, pytest.raises(ProtocolError, match="modeled"):
        observation.observe(fd, "sub")


def test_a_directory_observed_with_modeled_children_reports_occupancy(project):
    root, fd = project
    (root / "sub").mkdir(mode=0o750)
    (root / "sub" / "modeled").write_bytes(b"")
    (root / "sub" / "stranger").write_bytes(b"")

    with Observation(LinuxBackend()) as observation:
        entry = observation.observe(fd, "sub", modeled=frozenset({"modeled"}))

    assert type(entry) is ObservedDirectory
    assert entry.state.mode == 0o750
    assert entry.has_unmodeled_child is True


def test_a_fully_modeled_directory_reports_no_unmodeled_child(project):
    root, fd = project
    (root / "sub").mkdir()
    (root / "sub" / "modeled").write_bytes(b"")

    with Observation(LinuxBackend()) as observation:
        entry = observation.observe(fd, "sub", modeled=frozenset({"modeled", "not-present-yet"}))

    assert type(entry) is ObservedDirectory
    assert entry.has_unmodeled_child is False


def test_an_empty_directory_reports_no_unmodeled_child(project):
    root, fd = project
    (root / "sub").mkdir()

    with Observation(LinuxBackend()) as observation:
        entry = observation.observe(fd, "sub", modeled=frozenset())

    assert type(entry) is ObservedDirectory
    assert entry.has_unmodeled_child is False


def test_an_absent_name_is_observed_as_absent(project):
    _, fd = project

    with Observation(LinuxBackend()) as observation:
        assert type(observation.observe(fd, "missing")) is ObservedAbsent


def test_a_kind_with_no_declarable_state_refuses(project):
    """A socket, FIFO, or device node. No declared state can describe one."""
    root, fd = project
    os.mkfifo(root / "pipe")

    with Observation(LinuxBackend()) as observation, pytest.raises(PreconditionRefused, match="neither"):
        observation.observe(fd, "pipe")


def test_a_sink_receives_the_bytes_from_the_same_read(project):
    root, fd = project
    (root / "f.txt").write_bytes(b"payload")
    sink_path = root / "sink"
    sink_fd = os.open(str(sink_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with Observation(LinuxBackend()) as observation:
            entry = observation.observe(fd, "f.txt", sink_fd=sink_fd)
    finally:
        os.close(sink_fd)

    assert type(entry) is ObservedFile
    assert sink_path.read_bytes() == b"payload"
    assert entry.state.content_hash == digest_of(b"payload")


def test_a_failing_sink_leaks_no_descriptor(project):
    """Ownership is transferred or the descriptor is closed -- never neither."""
    root, fd = project
    (root / "f.txt").write_bytes(b"payload")
    readonly = os.open(str(root / "readonly"), os.O_RDONLY | os.O_CREAT, 0o400)
    try:
        before = open_descriptors()
        observation = Observation(LinuxBackend())
        with pytest.raises(OSError):
            observation.observe(fd, "f.txt", sink_fd=readonly)
        observation.close()
        assert open_descriptors() == before
    finally:
        os.close(readonly)


def test_the_retained_descriptor_pins_the_inode(project):
    """§11.1: assert the pin, not the reuse.

    Unlinking and recreating does not *force* the kernel to reallocate the inode, so a
    test that asserted distinct tokens after a recreate would pass just as readily with
    no pin at all -- it would be testing the allocator's mood. What is assertable is the
    mechanism: while the pass lives the observation still holds the entry open, so the
    inode cannot be reallocated and the kernel's own no-live-reuse invariant applies.
    """
    root, fd = project
    (root / "f.txt").write_bytes(b"original")

    with Observation(LinuxBackend()) as observation:
        entry = observation.observe(fd, "f.txt")
        assert type(entry) is ObservedFile
        os.unlink(root / "f.txt")
        pinned = observation.pinned_descriptor(entry.identity)
        os.lseek(pinned, 0, os.SEEK_SET)
        assert os.read(pinned, 64) == b"original"


def test_closing_the_pass_releases_every_pinned_descriptor(project):
    root, fd = project
    (root / "a.txt").write_bytes(b"a")
    (root / "b.txt").write_bytes(b"b")

    before = open_descriptors()
    observation = Observation(LinuxBackend())
    entry = observation.observe(fd, "a.txt")
    assert type(entry) is ObservedFile
    observation.observe(fd, "b.txt")
    observation.close()

    assert open_descriptors() == before
    with pytest.raises(ProtocolError, match="closed"):
        observation.pinned_descriptor(entry.identity)


def _relation(root, staged: bytes, planned: bytes) -> FileBuildRelation:
    (root / "staged").write_bytes(staged)
    (root / "planned").write_bytes(planned)
    staged_fd = os.open(str(root / "staged"), os.O_RDONLY)
    planned_fd = os.open(str(root / "planned"), os.O_RDONLY)
    try:
        with Observation(LinuxBackend()) as observation:
            return observation.build_relation(staged_fd, planned_fd)
    finally:
        os.close(staged_fd)
        os.close(planned_fd)


def test_identical_bytes_are_exact(project):
    root, _ = project
    assert _relation(root, b"payload", b"payload") is FileBuildRelation.EXACT


def test_a_truncated_staging_object_is_a_strict_prefix(project):
    root, _ = project
    assert _relation(root, b"pay", b"payload") is FileBuildRelation.STRICT_PREFIX


def test_an_empty_staging_object_is_a_strict_prefix(project):
    root, _ = project
    assert _relation(root, b"", b"payload") is FileBuildRelation.STRICT_PREFIX


def test_differing_bytes_are_diverged(project):
    root, _ = project
    assert _relation(root, b"paZload", b"payload") is FileBuildRelation.DIVERGED


def test_a_staging_object_longer_than_the_plan_is_diverged(project):
    root, _ = project
    assert _relation(root, b"payload+", b"payload") is FileBuildRelation.DIVERGED


def test_a_namespace_contradiction_refuses():
    with pytest.raises(PreconditionRefused, match="while probing"), translated_lookup("probing"):
        raise OSError(errno.ELOOP, "symlink")


def test_an_unsupported_semantic_is_a_capability_refusal():
    with pytest.raises(CapabilityUnavailable), translated_lookup("probing"):
        raise OSError(errno.EOPNOTSUPP, "no")


def test_an_undefined_errno_propagates_as_itself():
    """Design §9.1: A5a's rule, applied to errno.

    ENOSPC is not external state contradicting the frozen spec -- it is a full disk.
    Reporting it as PreconditionRefused would tell a consumer its intent had drifted
    when the hardware had failed.
    """
    with pytest.raises(OSError) as caught, translated_lookup("probing"):
        raise OSError(errno.ENOSPC, "full")
    assert caught.value.errno == errno.ENOSPC
