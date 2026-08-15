"""Project volume binding: the live resource and its frozen evidence (design §9)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Never, Self

from atoms.core.capabilities import Capability
from atoms.core.errors import CapabilityUnavailable, ProtocolError
from atoms.fs.backend import Backend
from atoms.fs.bootstrap import (
    PROBE_DIRECTORY,
    close_layout,
    ensure_metadata_layout,
    reclaim_probe_survivors,
    verified_child_path,
)
from atoms.fs.lock import HeldProjectLock, establish_root
from atoms.fs.probe import certify_sqlite_wal, probe_backend
from atoms.fs.volume import (
    AllowlistEntry,
    DurabilityAllowlist,
    StorageProfile,
    VolumeConfiguration,
    build_configuration,
    kernel_identifier,
    read_mount_id,
    read_mountinfo,
    resolve_mount_entry,
)

_TOKEN = object()

_BOOTSTRAP_PREREQUISITES = frozenset(
    {Capability.ANCHORED_TRAVERSAL, Capability.ADVISORY_PROJECT_LOCK}
)


@dataclass(frozen=True, slots=True, init=False)
class VolumeEvidence:
    """Diagnostic only. Describes a volume; authorizes no access to one.

    Guarded exactly like CompiledSpec and RecoveryPlan: an explicit __init__ that
    demands the construction token, so ordinary construction AND dataclasses.replace
    both refuse — replace() calls __init__ without the token.
    """

    configuration: VolumeConfiguration
    declared_storage_profile: StorageProfile
    matched_entry: AllowlistEntry
    supplied_capabilities: frozenset[Capability]
    metadata_root_device: int
    metadata_root_inode: int
    mount_id: int

    def __init__(
        self,
        *,
        configuration: VolumeConfiguration,
        declared_storage_profile: StorageProfile,
        matched_entry: AllowlistEntry,
        supplied_capabilities: frozenset[Capability],
        metadata_root_device: int,
        metadata_root_inode: int,
        mount_id: int,
        _construction_token: object | None = None,
    ) -> None:
        if _construction_token is not _TOKEN:
            raise TypeError("VolumeEvidence values are created only by bind_project_volume")
        object.__setattr__(self, "configuration", configuration)
        object.__setattr__(self, "declared_storage_profile", declared_storage_profile)
        object.__setattr__(self, "matched_entry", matched_entry)
        object.__setattr__(self, "supplied_capabilities", supplied_capabilities)
        object.__setattr__(self, "metadata_root_device", metadata_root_device)
        object.__setattr__(self, "metadata_root_inode", metadata_root_inode)
        object.__setattr__(self, "mount_id", mount_id)


class ProjectBinding:
    """A live resource: descriptors, backend, and the lock it borrows from.

    Not frozen, because it owns descriptors and a spent flag. VolumeEvidence is a
    value; this is a resource. Every descriptor it exposes is BORROWED — a consumer
    must never close one.
    """

    __slots__ = ("_active", "_backend", "_evidence", "_lock", "_project_root_fd")

    def __init__(self, *, _construction_token: object | None = None, **kwargs) -> None:
        if _construction_token is not _TOKEN:
            raise TypeError("ProjectBinding values are created only by bind_project_volume")
        self._lock = kwargs["lock"]
        self._backend = kwargs["backend"]
        self._project_root_fd = kwargs["project_root_fd"]
        self._evidence = kwargs["evidence"]
        self._active = True

    def __copy__(self) -> Never:
        raise TypeError("ProjectBinding cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> Never:
        raise TypeError("ProjectBinding cannot be deep-copied")

    def __reduce__(self) -> Never:
        raise TypeError("ProjectBinding cannot be pickled")

    def __reduce_ex__(self, protocol: int) -> Never:
        raise TypeError("ProjectBinding cannot be pickled")

    def _require_active(self) -> None:
        if not self._active:
            raise ProtocolError("the project binding has been closed")
        if not self._lock.held:
            raise ProtocolError("the project lock was released before the binding")

    @property
    def backend(self) -> Backend:
        self._require_active()
        return self._backend

    @property
    def project_root_fd(self) -> int:
        self._require_active()
        return self._project_root_fd

    @property
    def metadata_root_fd(self) -> int:
        self._require_active()
        return self._lock.metadata_root_fd

    @property
    def evidence(self) -> VolumeEvidence:
        return self._evidence

    @property
    def active(self) -> bool:
        return self._active

    def verified_metadata_path(self, name: str) -> str:
        self._require_active()
        return verified_child_path(
            self._lock.metadata_root_fd,
            self._lock.metadata_root_path,
            self._evidence.metadata_root_device,
            self._evidence.metadata_root_inode,
            name,
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc) -> None:
        if not self._active:
            return
        self._active = False
        # Closes only what it opened; the lock owns the metadata-root descriptor.
        self._backend.close_fd(self._project_root_fd)


def bind_project_volume(
    project_root: str,
    lock: HeldProjectLock,
    *,
    allowlist: DurabilityAllowlist,
    storage: StorageProfile,
) -> ProjectBinding:
    """Bind one project volume. The allowlist parameter is required and keyword-only.

    Steps 1-5 are read-only beyond the bootstrap design §5.5 permits, so a
    non-allowlisted volume is refused before the probe writes anything. Reclamation
    at step 4 nevertheless precedes that refusal, so pre-existing attributable
    debris is still removed from a configuration that is no longer certified.
    """
    backend = lock.backend
    metadata_root_fd = lock.metadata_root_fd

    project_root_fd, _, _ = establish_root(backend, project_root, create=False)
    try:
        metadata_info = os.fstat(metadata_root_fd)
        project_info = os.fstat(project_root_fd)
        metadata_mount = read_mount_id(metadata_root_fd)
        project_mount = read_mount_id(project_root_fd)
        if (metadata_mount, metadata_info.st_dev) != (project_mount, project_info.st_dev):
            raise CapabilityUnavailable(
                "project root and metadata root are not on the same volume: "
                f"mount {project_mount} vs {metadata_mount}"
            )

        entry = resolve_mount_entry(metadata_root_fd, read_mountinfo())
        configuration = build_configuration(
            entry, kernel_identifier(), directory_fd=metadata_root_fd
        )

        reclaim_probe_survivors(lock)

        matched = allowlist.match(configuration, storage)
        if matched is None:
            raise CapabilityUnavailable(
                "volume configuration is not on the supplied durability allowlist: "
                f"{configuration.filesystem_type} {configuration.barrier_options} "
                f"profile={storage.profile_id!r}"
            )

        retained = ensure_metadata_layout(lock)
        try:
            probe_fd = retained[PROBE_DIRECTORY]
            supplied = probe_backend(backend, probe_fd, lock)
            missing = _BOOTSTRAP_PREREQUISITES - supplied
            if missing:
                raise CapabilityUnavailable(
                    "bootstrap prerequisites unavailable: "
                    + ", ".join(sorted(item.value for item in missing))
                )
            probe_dir = verified_child_path(
                metadata_root_fd,
                lock.metadata_root_path,
                metadata_info.st_dev,
                metadata_info.st_ino,
                PROBE_DIRECTORY,
            )
            certify_sqlite_wal(
                os.path.join(probe_dir, "certify.db"),
                cleanup=True,
                backend=backend,
                parent_fd=probe_fd,
            )
        finally:
            # Design §9.1 step 8 runs in a finally, not on the success path. A SQLite
            # refusal, a subprocess timeout, or an unexpected errno are exactly the
            # paths that skip a success-only cleanup, and each would strand debris in
            # engine-owned space. If reclamation itself fails while another exception
            # is unwinding, that failure surfaces with the original as its __context__:
            # a broken metadata_root is worth reporting, and the next lease entry
            # (ledger #17) reclaims again under the same held lock.
            #
            # Reclamation sits in the OUTER finally so a failing descriptor release
            # cannot skip it — releasing and reclaiming are independent obligations,
            # and the one that leaves state on disk is the one that must not be
            # conditional on the other. close_layout likewise attempts every
            # descriptor rather than abandoning the rest after the first failure, and
            # releases them in reverse opening order per design §9.3.
            try:
                close_layout(backend, retained)
            except OSError as first:
                try:
                    reclaim_probe_survivors(lock)
                except (OSError, ProtocolError):
                    raise first
                raise
            reclaim_probe_survivors(lock)

        evidence = VolumeEvidence(
            configuration=configuration,
            declared_storage_profile=storage,
            matched_entry=matched,
            supplied_capabilities=supplied,
            metadata_root_device=metadata_info.st_dev,
            metadata_root_inode=metadata_info.st_ino,
            mount_id=metadata_mount,
            _construction_token=_TOKEN,
        )
    except BaseException:
        backend.close_fd(project_root_fd)
        raise

    return ProjectBinding(
        _construction_token=_TOKEN,
        lock=lock,
        backend=backend,
        project_root_fd=project_root_fd,
        evidence=evidence,
    )
