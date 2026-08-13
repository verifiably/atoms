import os
from pathlib import Path
from typing import Any, cast

import pytest

from atoms.core.errors import ProtocolError
from atoms.core.scratch import CHAIN_LEAF
from atoms.fs.audit import (
    AuditedBackend,
    AuditRecord,
    Provenance,
    RootKind,
    TargetClass,
)
from atoms.fs.backend import Backend
from atoms.fs.linux import LinuxBackend


class RecordingBackend:
    def __init__(self, *, fail: frozenset[str] = frozenset()) -> None:
        self.inner = LinuxBackend()
        self.calls: list[str] = []
        self.fail = fail

    def __getattr__(self, name: str) -> Any:
        method = getattr(self.inner, name)

        def invoke(*args: object, **kwargs: object) -> object:
            self.calls.append(name)
            if name in self.fail:
                raise OSError(5, "injected")
            return method(*args, **kwargs)

        return invoke


def _roots(tmp_path: Path, *, create_metadata: bool = True) -> tuple[Path, Path]:
    project = tmp_path / "project"
    metadata = tmp_path / "metadata"
    project.mkdir()
    if create_metadata:
        metadata.mkdir()
    return project, metadata


def _facade(
    project: Path, metadata: Path, inner: Backend | None = None
) -> AuditedBackend:
    return AuditedBackend(
        inner or LinuxBackend(),
        project_root=str(project),
        metadata_root=str(metadata),
    )


def test_unregistered_descriptor_refuses_before_the_syscall(tmp_path):
    project, metadata = _roots(tmp_path)
    recording = RecordingBackend()
    backend = _facade(project, metadata, cast(Backend, recording))

    with pytest.raises(ProtocolError, match="unregistered"):
        backend.mkdir_child(2**30, "data.txt", 0o700)

    assert recording.calls == []
    assert backend.records == ()


def test_descriptor_identity_requires_an_exact_integer(tmp_path):
    project, metadata = _roots(tmp_path)
    backend = _facade(project, metadata)
    backend.register(1, Provenance(RootKind.METADATA, ""))

    with pytest.raises(ProtocolError, match="non-negative integer"):
        backend.provenance_of(True)


@pytest.mark.parametrize(
    "invoke",
    [
        lambda backend: backend.open_child_directory(2**30, "child"),
        lambda backend: backend.open_regular_nofollow(2**30, "file"),
        lambda backend: backend.symlink_fingerprint(2**30, "link"),
        lambda backend: backend.flush_file(2**30),
        lambda backend: backend.flush_directory(2**30),
        lambda backend: backend.lock_exclusive(2**30),
        lambda backend: backend.try_lock_exclusive(2**30),
    ],
)
def test_reads_flushes_and_locks_require_registered_descriptors(tmp_path, invoke):
    project, metadata = _roots(tmp_path)
    recording = RecordingBackend()
    backend = _facade(project, metadata, cast(Backend, recording))

    with pytest.raises(ProtocolError, match="unregistered"):
        invoke(backend)

    assert recording.calls == []
    assert backend.records == ()


def test_declared_scope_cannot_be_supplied_per_call(tmp_path):
    project, metadata = _roots(tmp_path)
    recording = RecordingBackend()
    backend = _facade(project, metadata, cast(Backend, recording))
    root_fd = backend.open_root(str(project))
    try:
        calls_before = list(recording.calls)
        with pytest.raises(ProtocolError, match="authorized target"):
            backend.mkdir_child(root_fd, "data.txt", 0o700)
        assert recording.calls == calls_before

        backend.set_declared_paths(frozenset({"data.txt"}))
        backend.mkdir_child(root_fd, "data.txt", 0o700)
        assert backend.records == (
            AuditRecord(
                "mkdir_child",
                ((TargetClass.DECLARED_EFFECT, "data.txt"),),
            ),
        )

        backend.clear_declared_paths()
        calls_before = list(recording.calls)
        with pytest.raises(ProtocolError, match="authorized target"):
            backend.mkdir_child(root_fd, "data.txt", 0o700)
        assert recording.calls == calls_before
    finally:
        backend.close_fd(root_fd)


def test_policy_classifies_chain_scratch_and_metadata_targets(tmp_path):
    project, metadata = _roots(tmp_path)
    backend = _facade(project, metadata)
    project_fd = backend.open_root(str(project))
    metadata_fd = backend.open_root(str(metadata))
    try:
        backend.mkdir_child(project_fd, CHAIN_LEAF, 0o700)
        backend.mkdir_child(project_fd, ".#~tx.e01.staging", 0o700)
        backend.mkdir_child(metadata_fd, "blobs", 0o700)

        assert backend.records == (
            AuditRecord(
                "mkdir_child",
                ((TargetClass.CHAIN_BOOKKEEPING, CHAIN_LEAF),),
            ),
            AuditRecord(
                "mkdir_child",
                ((TargetClass.ENGINE_SCRATCH, ".#~tx.e01.staging"),),
            ),
            AuditRecord(
                "mkdir_child",
                ((TargetClass.METADATA, "blobs"),),
            ),
        )
    finally:
        backend.close_fd(metadata_fd)
        backend.close_fd(project_fd)


def test_two_target_operations_classify_each_target_in_argument_order(tmp_path):
    project, metadata = _roots(tmp_path)
    (project / CHAIN_LEAF).mkdir()
    (project / ".#~tx.e01.staging").write_bytes(b"scratch")
    (project / "live").write_bytes(b"live")
    backend = _facade(project, metadata)
    backend.set_declared_paths(frozenset({"live"}))
    project_fd = backend.open_root(str(project))
    chain_fd = backend.open_child_directory(project_fd, CHAIN_LEAF)
    try:
        backend.exchange(project_fd, ".#~tx.e01.staging", "live")
        backend.transfer_noclobber(
            project_fd,
            ".#~tx.e01.staging",
            chain_fd,
            "entry",
        )

        assert backend.records == (
            AuditRecord(
                "exchange",
                (
                    (TargetClass.ENGINE_SCRATCH, ".#~tx.e01.staging"),
                    (TargetClass.DECLARED_EFFECT, "live"),
                ),
            ),
            AuditRecord(
                "transfer_noclobber",
                (
                    (TargetClass.ENGINE_SCRATCH, ".#~tx.e01.staging"),
                    (TargetClass.CHAIN_BOOKKEEPING, f"{CHAIN_LEAF}/entry"),
                ),
            ),
        )
    finally:
        backend.close_fd(chain_fd)
        backend.close_fd(project_fd)


def test_open_root_registers_only_the_three_configured_roots(tmp_path):
    project, metadata = _roots(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    recording = RecordingBackend()
    backend = _facade(project, metadata, cast(Backend, recording))
    fds = [
        backend.open_root(str(project)),
        backend.open_root(str(metadata)),
        backend.open_root(str(tmp_path)),
    ]
    try:
        assert [backend.provenance_of(fd) for fd in fds] == [
            Provenance(RootKind.PROJECT, ""),
            Provenance(RootKind.METADATA, ""),
            Provenance(RootKind.METADATA_PARENT, ""),
        ]
        calls_before = list(recording.calls)
        with pytest.raises(ProtocolError, match="configured root"):
            backend.open_root(str(other))
        assert recording.calls == calls_before
    finally:
        for fd in reversed(fds):
            backend.close_fd(fd)


def test_metadata_parent_can_create_and_reopen_only_the_metadata_root(tmp_path):
    project, metadata = _roots(tmp_path, create_metadata=False)
    backend = _facade(project, metadata)
    parent_fd = backend.open_root(str(tmp_path))
    try:
        backend.mkdir_child(parent_fd, metadata.name, 0o700)
        metadata_fd = backend.open_child_directory(parent_fd, metadata.name)
        try:
            assert backend.provenance_of(metadata_fd) == Provenance(RootKind.METADATA, "")
        finally:
            backend.close_fd(metadata_fd)

        with pytest.raises(ProtocolError, match="authorized target"):
            backend.mkdir_child(parent_fd, "other", 0o700)
        assert backend.records == (
            AuditRecord("mkdir_child", ((TargetClass.METADATA, ""),)),
        )
    finally:
        backend.close_fd(parent_fd)


def test_project_root_can_bootstrap_its_direct_metadata_child_only(tmp_path):
    project = tmp_path / "project"
    metadata = project / "metadata"
    project.mkdir()
    backend = _facade(project, metadata)
    project_fd = backend.open_root(str(project))
    try:
        assert backend.provenance_of(project_fd) == Provenance(RootKind.PROJECT, "")
        backend.mkdir_child(project_fd, metadata.name, 0o700)
        metadata_fd = backend.open_child_directory(project_fd, metadata.name)
        try:
            assert backend.provenance_of(metadata_fd) == Provenance(RootKind.METADATA, "")
        finally:
            backend.close_fd(metadata_fd)

        with pytest.raises(ProtocolError, match="authorized target"):
            backend.mkdir_child(project_fd, "sibling", 0o700)
        assert backend.records == (
            AuditRecord("mkdir_child", ((TargetClass.METADATA, ""),)),
        )
    finally:
        backend.close_fd(project_fd)


def test_record_is_appended_only_after_the_syscall_succeeds(tmp_path):
    project, metadata = _roots(tmp_path)
    recording = RecordingBackend(fail=frozenset({"mkdir_child"}))
    backend = _facade(project, metadata, cast(Backend, recording))
    metadata_fd = backend.open_root(str(metadata))
    try:
        with pytest.raises(OSError, match="injected"):
            backend.mkdir_child(metadata_fd, "blobs", 0o700)
        assert recording.calls[-1] == "mkdir_child"
        assert backend.records == ()
    finally:
        backend.close_fd(metadata_fd)


def test_create_exclusive_registers_the_returned_descriptor(tmp_path):
    project, metadata = _roots(tmp_path)
    backend = _facade(project, metadata)
    backend.set_declared_paths(frozenset({"data.txt"}))
    project_fd = backend.open_root(str(project))
    try:
        file_fd = backend.create_exclusive(project_fd, "data.txt", 0o600)
        try:
            backend.set_mode(file_fd, 0o640)
            assert backend.provenance_of(file_fd) == Provenance(
                RootKind.PROJECT, "data.txt"
            )
            assert backend.records[-1] == AuditRecord(
                "set_mode",
                ((TargetClass.DECLARED_EFFECT, "data.txt"),),
            )
        finally:
            backend.close_fd(file_fd)
    finally:
        backend.close_fd(project_fd)


def test_rebind_changes_subsequent_descendant_classification(tmp_path):
    project, metadata = _roots(tmp_path)
    scratch = ".#~tx.e01.work"
    (project / scratch).mkdir()
    backend = _facade(project, metadata)
    backend.set_declared_paths(frozenset({"live/child"}))
    project_fd = backend.open_root(str(project))
    scratch_fd = backend.open_child_directory(project_fd, scratch)
    try:
        backend.rebind(scratch_fd, Provenance(RootKind.PROJECT, "live"))
        backend.mkdir_child(scratch_fd, "child", 0o700)
        assert backend.records == (
            AuditRecord(
                "mkdir_child",
                ((TargetClass.DECLARED_EFFECT, "live/child"),),
            ),
        )
    finally:
        backend.close_fd(scratch_fd)
        backend.close_fd(project_fd)


def test_close_unregisters_once_and_refuses_a_second_close(tmp_path):
    project, metadata = _roots(tmp_path)
    recording = RecordingBackend()
    backend = _facade(project, metadata, cast(Backend, recording))
    fd = backend.open_root(str(project))

    backend.close_fd(fd)
    assert recording.calls.count("close_fd") == 1
    with pytest.raises(ProtocolError, match="unregistered"):
        backend.provenance_of(fd)
    with pytest.raises(ProtocolError, match="unregistered"):
        backend.close_fd(fd)
    assert recording.calls.count("close_fd") == 1


def test_close_unregisters_even_when_the_inner_close_raises(tmp_path, monkeypatch):
    project, metadata = _roots(tmp_path)
    backend = _facade(project, metadata)
    fd = os.open(metadata, os.O_RDONLY | os.O_DIRECTORY)
    backend.register(fd, Provenance(RootKind.METADATA, ""))
    real_close = os.close

    def fail_close(closing_fd: int) -> None:
        assert closing_fd == fd
        raise OSError(5, "injected close")

    try:
        with monkeypatch.context() as patch:
            patch.setattr("atoms.fs.linux.os.close", fail_close)
            with pytest.raises(OSError, match="injected close"):
                backend.close_fd(fd)
        with pytest.raises(ProtocolError, match="unregistered"):
            backend.provenance_of(fd)
    finally:
        real_close(fd)
