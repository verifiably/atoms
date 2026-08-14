"""Per-txid scratch namespaces under the engine-owned metadata root (design §8.3)."""

from __future__ import annotations

import errno
import os
import stat
from typing import TYPE_CHECKING, Never, Self

from atoms.core.errors import ProtocolError
from atoms.fs.lock import close_all
from atoms.store.errors import MetadataStoreInvalid
from atoms.store.records import require_identifier

if TYPE_CHECKING:
    from atoms.store.connection import Store

STAGING_PARENT = "staging"
WORK_PARENT = "work"
_WORKSPACE_TOKEN = object()


class Workspace:
    """A live resource: two owned directory descriptors and their spent flags."""

    __slots__ = ("_backend", "_closed", "_staging_fd", "_store", "_txid", "_work_fd")

    def __init__(
        self,
        *,
        store: Store | None = None,
        txid: str = "",
        staging_fd: int | None = None,
        work_fd: int | None = None,
        _construction_token: object | None = None,
    ) -> None:
        if _construction_token is not _WORKSPACE_TOKEN:
            raise TypeError(
                "Workspace values are created only by Store.create_workspace or "
                "Store.reopen_workspace"
            )
        if store is None:
            raise TypeError("a Workspace requires its issuing Store")
        self._store = store
        self._backend = store._binding._backend
        self._txid = txid
        self._staging_fd = staging_fd
        self._work_fd = work_fd
        self._closed = False

    def __copy__(self) -> Never:
        raise ProtocolError("a copied Workspace would duplicate descriptor ownership")

    def __deepcopy__(self, memo: dict[int, object]) -> Never:
        raise ProtocolError("a copied Workspace would duplicate descriptor ownership")

    def __reduce__(self) -> Never:
        raise ProtocolError("a Workspace owns descriptors and cannot be pickled")

    def __reduce_ex__(self, protocol: int) -> Never:
        raise ProtocolError("a Workspace owns descriptors and cannot be pickled")

    @property
    def txid(self) -> str:
        return self._txid

    def _anchor(self, fd: int | None, label: str) -> int:
        if self._closed:
            raise ProtocolError(f"this workspace is closed; {label} is gone")
        if fd is None:
            raise ProtocolError(
                f"{label} is spent for txid {self._txid!r}: that half of the workspace "
                "is not on disk, or promotion has already consumed it"
            )
        store = self._store
        if store is None:
            raise ProtocolError("this workspace has no store")
        store._require_live()
        return fd

    @property
    def staging_fd(self) -> int:
        return self._anchor(self._staging_fd, "staging_fd")

    @property
    def work_fd(self) -> int:
        return self._anchor(self._work_fd, "work_fd")

    def _spend_staging(self) -> None:
        if self._staging_fd is not None:
            try:
                self._backend.close_fd(self._staging_fd)
            finally:
                self._staging_fd = None

    def _spend_work(self) -> None:
        if self._work_fd is not None:
            try:
                self._backend.close_fd(self._work_fd)
            finally:
                self._work_fd = None

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            close_all(
                self._backend,
                (fd for fd in (self._staging_fd, self._work_fd) if fd is not None),
            )
        finally:
            self._staging_fd = None
            self._work_fd = None
            store = self._store
            if store is not None:
                store._workspaces.discard(self)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _issue(store: Store, workspace: Workspace) -> Workspace:
    store._workspaces.add(workspace)
    return workspace


def _parent_fd(store: Store, name: str) -> int:
    return store._binding._backend.open_child_directory(
        store._binding.metadata_root_fd, name
    )


def _parent_fds(store: Store) -> tuple[int, int]:
    backend = store._binding._backend
    staging_parent = _parent_fd(store, STAGING_PARENT)
    try:
        return staging_parent, _parent_fd(store, WORK_PARENT)
    except BaseException:
        backend.close_fd(staging_parent)
        raise


def _open_child(store: Store, parent_fd: int, parent: str, txid: str) -> int | None:
    try:
        return store._binding._backend.open_child_directory(parent_fd, txid)
    except FileNotFoundError:
        return None
    except OSError as caught:
        if caught.errno not in (errno.ENOTDIR, errno.ELOOP):
            raise
        raise MetadataStoreInvalid(
            f"{parent}/{txid} is not a directory; create_workspace is the only "
            "permitted producer here and it makes directories"
        ) from caught


def _is_txid(name: str) -> bool:
    from atoms.core.identifiers import is_valid_identifier

    return is_valid_identifier(name)


def _require_permitted_children(parent_fd: int, parent: str) -> tuple[str, ...]:
    names: list[str] = []
    for name in sorted(os.listdir(parent_fd)):
        info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if not stat.S_ISDIR(info.st_mode):
            raise MetadataStoreInvalid(
                f"{parent}/{name} is not a directory; no permitted producer could have "
                "written it"
            )
        if type(name) is not str or not _is_txid(name):
            raise MetadataStoreInvalid(
                f"{parent}/{name!r} is not a well-formed txid; no permitted producer "
                "could have written it"
            )
        names.append(name)
    return tuple(names)


def _stat_or_none(parent_fd: int, name: str) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _open_both(store: Store, txid: str, staging_parent: int, work_parent: int) -> Workspace:
    backend = store._binding._backend
    staging_fd = _open_child(store, staging_parent, STAGING_PARENT, txid)
    try:
        work_fd = _open_child(store, work_parent, WORK_PARENT, txid)
    except BaseException:
        if staging_fd is not None:
            backend.close_fd(staging_fd)
        raise
    return Workspace(
        store=store,
        txid=txid,
        staging_fd=staging_fd,
        work_fd=work_fd,
        _construction_token=_WORKSPACE_TOKEN,
    )


def create_workspace(store: Store, txid: str) -> Workspace:
    store._require_live()
    backend = store._binding._backend
    require_identifier("txid", txid)
    staging_parent, work_parent = _parent_fds(store)
    try:
        for parent_fd, parent in ((staging_parent, STAGING_PARENT), (work_parent, WORK_PARENT)):
            if _stat_or_none(parent_fd, txid) is not None:
                raise ProtocolError(
                    f"{parent}/{txid} already exists; a survivor is evidence about a "
                    "previous attempt and adoption would bypass the occupancy question"
                )
        from atoms.store.connection import gate

        gate(store._binding)
        backend.mkdir_child(staging_parent, txid, 0o700)
        gate(store._binding)
        backend.mkdir_child(work_parent, txid, 0o700)
        backend.flush_directory(staging_parent)
        backend.flush_directory(work_parent)
        return _issue(store, _open_both(store, txid, staging_parent, work_parent))
    finally:
        close_all(backend, (staging_parent, work_parent))


def reopen_workspace(store: Store, txid: str) -> Workspace:
    store._require_live()
    backend = store._binding._backend
    require_identifier("txid", txid)
    staging_parent, work_parent = _parent_fds(store)
    try:
        workspace = _open_both(store, txid, staging_parent, work_parent)
        if workspace._staging_fd is None and workspace._work_fd is None:
            raise ProtocolError(f"no workspace on disk for txid {txid!r}")
        return _issue(store, workspace)
    finally:
        close_all(backend, (staging_parent, work_parent))


def list_workspaces(store: Store) -> tuple[str, ...]:
    store._require_live()
    backend = store._binding._backend
    found: set[str] = set()
    for parent in (STAGING_PARENT, WORK_PARENT):
        parent_fd = _parent_fd(store, parent)
        try:
            found.update(_require_permitted_children(parent_fd, parent))
        finally:
            backend.close_fd(parent_fd)
    return tuple(sorted(found))


def remove_workspace(store: Store, workspace: Workspace) -> None:
    store._require_live()
    backend = store._binding._backend
    if type(workspace) is not Workspace:
        raise ProtocolError(f"expected exactly Workspace, got {type(workspace).__name__}")
    if workspace._closed:
        raise ProtocolError("this workspace is closed")
    if workspace._store is not store:
        raise ProtocolError(
            "this workspace belongs to a different Store over the same binding; its "
            "issuer may close its descriptors underneath"
        )

    from atoms.store.connection import gate

    txid = workspace._txid
    staging_parent, work_parent = _parent_fds(store)
    try:
        staging_names: tuple[str, ...] = ()
        if workspace._staging_fd is not None:
            staging_fd = workspace._staging_fd
            staging_names = tuple(sorted(os.listdir(staging_fd)))
            for name in staging_names:
                info = os.stat(name, dir_fd=staging_fd, follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode):
                    raise MetadataStoreInvalid(
                        f"staging/{txid}/{name} is not a regular file; A6's capture "
                        "writes regular files and nothing else"
                    )
        if workspace._work_fd is not None and os.listdir(workspace._work_fd):
            raise ProtocolError(
                f"work/{txid}/ is not empty; authority §9.5 classifies its contents by "
                "inode against the live filesystem, and that judgment is A7's"
            )

        if workspace._staging_fd is not None:
            for name in staging_names:
                gate(store._binding)
                backend.unlink_child(workspace._staging_fd, name)
            backend.flush_directory(workspace._staging_fd)
            gate(store._binding)
            backend.rmdir_child(staging_parent, txid)
            workspace._spend_staging()
            backend.flush_directory(staging_parent)
        if workspace._work_fd is not None:
            gate(store._binding)
            backend.rmdir_child(work_parent, txid)
            workspace._spend_work()
            backend.flush_directory(work_parent)
    finally:
        close_all(backend, (staging_parent, work_parent))
    workspace.close()
