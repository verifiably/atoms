"""The persistence-cut model: units, recording, durable state (design §4).

A `RecordingCutBackend` sits beneath the audited facade -- the same seam `KillingBackend`
occupies in the kill matrix -- and delegates every call to the real backend. Recording is
success-only: operands are resolved before the delegated call (so a rename or removal can
still name what it removed), and a failed delegated call appends nothing, because the
exception propagates before any `_emit_mutation`/`_emit_barrier` call is reached.

Durability units are keyed by real filesystem identity (`st_dev`, `st_ino`), captured the
first time any Backend call references an object -- whether that reference creates it or
merely opens something the seed already produced. Only creation (or reference-with-intent,
per §4.1's short list: `create_exclusive`, `create_or_open` when it creates, `mkdir_child`,
`symlink_child`, `link_anchor`, `transfer_noclobber`, `exchange`, `unlink_child`,
`rmdir_child`, `write`, `set_mode`, `set_marker_xattr`, `repair_entry_mode`) appends a
`Mutation`; every other Backend method is a pure delegate plus fd-table bookkeeping.

Store COMMITs share one sequencer with the recording backend (`attach_store_sequencer`):
each successful `Store.transaction()` exit appends a `Commit` event labelled from the
mutating `_StoreTransaction` method actually called inside it -- not from a precomputed,
effect-counted schedule (`tests/test_coordinator_kill_matrix.py`'s `STORE_BARRIERS` can
hardcode nine names because every kill-matrix variant is single-effect; the exerciser's
`corpus-write` and `archive-move` scenarios are not, so this file derives the label from
the write that happened rather than from a position).
"""

from __future__ import annotations

import contextlib
import hashlib
import itertools
import os
import sqlite3
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import NewType

DB_FAMILY = ("atoms.db", "atoms.db-wal", "atoms.db-shm", "atoms.db-journal")

UnitKey = NewType("UnitKey", tuple)

# path (root-relative, e.g. "project/d/f.txt" or "metadata/.#~chain") ->
# ("file", bytes, mode) | ("file", bytes, mode, ("link-group", token)) | ("dir", mode) |
# ("symlink", target) -- design §4.4/§9's WorldState.tree shape.
WorldTree = dict[str, tuple]


@dataclass(frozen=True)
class ModelInode:
    token: int
    kind: str  # "file" | "directory" | "symlink"
    symlink_target: str | None = None


@dataclass(frozen=True)
class Unit:
    key: tuple
    change: str  # "insert" | "remove" | "replace" | "image" | "mode" | "xattr"
    object_token: int | None = None
    payload: bytes | int | None = None


@dataclass(frozen=True)
class Seed:
    world_digest: str
    backup_id: int


@dataclass(frozen=True)
class Mutation:
    units: tuple[Unit, ...]


@dataclass(frozen=True)
class Barrier:
    covered: frozenset


@dataclass(frozen=True)
class Commit:
    label: str
    backup_id: int


Event = Seed | Mutation | Barrier | Commit


class Stream:
    """Recorded events, SQLite backups, and the live model tree that grounds them."""

    def __init__(self) -> None:
        self.events: list[Event] = []
        self.backups: dict[int, bytes | None] = {}
        self.tokens = itertools.count(1)

        # fd tables, maintained by every open/create/close call.
        self.fd_token: dict[int, int] = {}
        self.fd_offset: dict[int, int] = {}

        # The live model tree: real filesystem identity -> token, the model inode each
        # token names, the current (dir token, name) -> token entry map, and the current
        # complete byte image of every file-kind token. `identity_reverse` is the
        # inverse of `identity`, kept so a token whose last live entry is removed can
        # be evicted from `identity` in O(1) -- a real filesystem reuses a freed
        # (st_dev, st_ino) pair for an unrelated later create (capability probing does
        # many rapid create+remove cycles in one small scratch directory), and without
        # eviction the next real object at that pair would collide with the stale
        # token and be misread as the *same* model inode.
        self.identity: dict[tuple[int, int], int] = {}
        self.identity_reverse: dict[int, tuple[int, int]] = {}
        self.inodes: dict[int, ModelInode] = {}
        self.entries: dict[tuple[int, str], int] = {}
        self.images: dict[int, bytearray] = {}

        # Tokens that name a database-family entry (design §4.1: "the database
        # namespace is model-owned"). Once a token is known to be one, every unit
        # naming it -- entry, data, or meta -- is dropped from the recorded stream.
        self.db_tokens: set[int] = set()

        # Keys touched by a Mutation and not yet covered by a Barrier.
        self.pending: dict[tuple, Unit] = {}

        # The seed overlay `durable_state` folds from first (design §4.3): the root
        # token for each labelled root ("project"/"metadata"), the seed-era entry map,
        # every seed-era inode's mode, and every seed-era file's byte image. Populated
        # by `_snapshot_seed`, sharing `tokens`/`identity`/`inodes` above with the
        # recording backend attached afterward, so a seed-era object touched later
        # resolves to the same token.
        self.roots: dict[str, int] = {}
        self.seed_entries: dict[tuple[int, str], int] = {}
        self.seed_modes: dict[int, int] = {}
        self.seed_images: dict[int, bytes] = {}

        # Set by `record_scenario` once the recorded transaction finishes: the canonical
        # digest (`world_digest`) of the real, live final world -- the fidelity
        # self-check's target (design §9).
        self.final_world_digest: str | None = None

    # --- accessors (mechanical) --------------------------------------------------
    def mutations(self) -> list[Mutation]:
        return [event for event in self.events if type(event) is Mutation]

    def barriers(self) -> list[Barrier]:
        return [event for event in self.events if type(event) is Barrier]

    def commits(self) -> list[Commit]:
        return [event for event in self.events if type(event) is Commit]

    def commit_index(self, label: str) -> int:
        for index, event in enumerate(self.events):
            if type(event) is Commit and event.label == label:
                return index
        raise KeyError(label)

    def pending_before(self, index: int) -> dict[tuple, Unit]:
        """Replay events[:index], returning the key -> Unit overlay still pending there."""
        state: dict[tuple, Unit] = {}
        for event in self.events[:index]:
            if type(event) is Mutation:
                for unit in event.units:
                    state[unit.key] = unit
            elif type(event) is Barrier:
                for key in event.covered:
                    state.pop(key, None)
        return state

    def index_after(self, *, change: str) -> int:
        """The index right after the *last* Mutation whose `change`-typed entry unit
        leaves its object token with more than one live name.

        A cut-locating helper: `minimal-move`'s `link_anchor` gives its token a second
        live name (the anchor), and the later `transfer_noclobber` gives it a second
        live name again (the destination, replacing the source) -- both are
        `change="insert"` events leaving the token linked, so the *last* one is the cut
        where the destination and the still-durable anchor share one inode (design
        §9.4).
        """
        entries: dict[tuple[int, str], int] = {}
        reverse: dict[int, set[tuple[int, str]]] = {}
        match: int | None = None
        for index, event in enumerate(self.events):
            if type(event) is not Mutation:
                continue
            touched: list[Unit] = []
            for unit in event.units:
                if unit.key[0] != "entry":
                    continue
                entry_key = (unit.key[1], unit.key[2])
                if unit.change in ("insert", "replace"):
                    old = entries.pop(entry_key, None)
                    if old is not None:
                        reverse[old].discard(entry_key)
                    if unit.object_token is not None:
                        entries[entry_key] = unit.object_token
                        reverse.setdefault(unit.object_token, set()).add(entry_key)
                elif unit.change == "remove":
                    old = entries.pop(entry_key, None)
                    if old is not None:
                        reverse[old].discard(entry_key)
                touched.append(unit)
            for unit in touched:
                if (
                    unit.change == change
                    and unit.object_token is not None
                    and len(reverse.get(unit.object_token, ())) > 1
                ):
                    match = index + 1
        if match is None:
            raise KeyError(change)
        return match


class RecordingCutBackend:
    """Success-only recorder beneath the audited facade (design §4.1).

    Every Backend method is implemented explicitly. Pure opens and probes delegate and
    perform fd-table/identity bookkeeping only; the methods design §4.1 lists as
    mutating additionally resolve operands before the delegated call, and append a
    `Mutation` after it succeeds.
    """

    def __init__(self, inner, stream: Stream) -> None:
        self._inner = inner
        self._stream = stream

    # --- identity and bookkeeping -------------------------------------------------
    def _register_identity(
        self, info: os.stat_result, *, parent_fd: int | None = None, name: str | None = None
    ) -> int:
        stream = self._stream
        identity_key = (info.st_dev, info.st_ino)
        token = stream.identity.get(identity_key)
        if token is not None:
            return token
        token = next(stream.tokens)
        stream.identity[identity_key] = token
        stream.identity_reverse[token] = identity_key
        if stat.S_ISDIR(info.st_mode):
            kind = "directory"
        elif stat.S_ISLNK(info.st_mode):
            kind = "symlink"
        else:
            kind = "file"
        target = None
        if kind == "symlink" and parent_fd is not None and name is not None:
            target = os.readlink(name, dir_fd=parent_fd)
        stream.inodes[token] = ModelInode(token, kind, target)
        if kind == "file":
            stream.images.setdefault(token, bytearray())
        return token

    def _forget_if_orphaned(self, token: int | None) -> None:
        """Evict `token`'s (st_dev, st_ino) mapping once no live entry names it.

        Called after every entry removal. Does *not* touch `stream.inodes` (token
        values are never reused) or any open fd's `fd_token` entry (unaffected,
        since `write`/`set_mode`/etc. read the fd table directly) -- only the
        forward `identity` lookup a *future* create/open would otherwise collide
        with if the real filesystem reuses the freed (st_dev, st_ino) pair.
        """
        stream = self._stream
        if token is None or any(value == token for value in stream.entries.values()):
            return
        identity_key = stream.identity_reverse.pop(token, None)
        if identity_key is not None:
            stream.identity.pop(identity_key, None)

    def _resolve_entry_token(self, parent_token: int, parent_fd: int, name: str) -> int:
        """The token at (parent_token, name), minting one from a pre-call lstat if unseen.

        Covers a name whose entry was never created through this recorder -- a seed-era
        survivor -- so a rename or removal of it still names the object it displaced.
        """
        stream = self._stream
        token = stream.entries.get((parent_token, name))
        if token is not None:
            return token
        info = os.lstat(name, dir_fd=parent_fd)
        token = self._register_identity(info, parent_fd=parent_fd, name=name)
        stream.entries[(parent_token, name)] = token
        return token

    def _track_open(self, fd: int) -> int:
        info = os.fstat(fd)
        token = self._register_identity(info)
        self._stream.fd_token[fd] = token
        return token

    # --- mutation / barrier emission ------------------------------------------------
    def _is_db_unit(self, unit: Unit) -> bool:
        stream = self._stream
        key = unit.key
        if key[0] == "entry":
            name = key[2]
            if name in DB_FAMILY:
                if unit.object_token is not None:
                    stream.db_tokens.add(unit.object_token)
                return True
            return False
        return key[1] in stream.db_tokens

    def _emit_mutation(self, *units: Unit) -> None:
        stream = self._stream
        kept = tuple(unit for unit in units if not self._is_db_unit(unit))
        if not kept:
            return
        for unit in kept:
            stream.pending[unit.key] = unit
        stream.events.append(Mutation(kept))

    def _pending_keys(self) -> frozenset:
        return frozenset(self._stream.pending)

    def _emit_barrier(self, covered: frozenset) -> None:
        stream = self._stream
        for key in covered:
            stream.pending.pop(key, None)
        stream.events.append(Barrier(covered))

    # --- pure opens: delegate, then identity/fd bookkeeping only -------------------
    def open_root(self, path: str) -> int:
        fd = self._inner.open_root(path)
        self._track_open(fd)
        return fd

    def open_child_directory(self, parent_fd: int, name: str) -> int:
        fd = self._inner.open_child_directory(parent_fd, name)
        self._track_open(fd)
        return fd

    def open_directory_handle(self, parent_fd: int, name: str) -> int:
        fd = self._inner.open_directory_handle(parent_fd, name)
        self._track_open(fd)
        return fd

    def open_existing(
        self, parent_fd: int, name: str, *, read_write: bool = False, nofollow: bool = False
    ) -> int:
        fd = self._inner.open_existing(
            parent_fd, name, read_write=read_write, nofollow=nofollow
        )
        self._track_open(fd)
        self._stream.fd_offset[fd] = 0
        return fd

    def open_regular_nofollow(self, parent_fd: int, name: str) -> int:
        fd = self._inner.open_regular_nofollow(parent_fd, name)
        self._track_open(fd)
        return fd

    def symlink_fingerprint(self, parent_fd: int, name: str):
        return self._inner.symlink_fingerprint(parent_fd, name)

    def lock_exclusive(self, fd: int) -> None:
        self._inner.lock_exclusive(fd)

    def try_lock_exclusive(self, fd: int) -> bool:
        return self._inner.try_lock_exclusive(fd)

    def close_fd(self, fd: int) -> None:
        self._inner.close_fd(fd)
        stream = self._stream
        stream.fd_token.pop(fd, None)
        stream.fd_offset.pop(fd, None)

    def detach_fd(self, fd: int) -> None:
        self._inner.detach_fd(fd)
        stream = self._stream
        stream.fd_token.pop(fd, None)
        stream.fd_offset.pop(fd, None)

    # --- creation: mint a token, register the entry, emit insert(+mode) ------------
    def create_exclusive(self, parent_fd: int, name: str, mode: int) -> int:
        stream = self._stream
        parent = stream.fd_token[parent_fd]
        fd = self._inner.create_exclusive(parent_fd, name, mode)
        token = self._register_identity(os.fstat(fd))
        stream.entries[(parent, name)] = token
        stream.fd_token[fd] = token
        stream.fd_offset[fd] = 0
        self._emit_mutation(
            Unit(("entry", parent, name), "insert", token),
            Unit(("meta", token, "mode"), "mode", token, mode),
        )
        return fd

    def create_or_open(self, parent_fd: int, name: str, mode: int) -> int:
        stream = self._stream
        parent = stream.fd_token[parent_fd]
        try:
            os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            existed = True
        except FileNotFoundError:
            existed = False
        fd = self._inner.create_or_open(parent_fd, name, mode)
        token = self._register_identity(os.fstat(fd))
        stream.fd_token[fd] = token
        stream.fd_offset[fd] = 0
        if not existed:
            stream.entries[(parent, name)] = token
            self._emit_mutation(
                Unit(("entry", parent, name), "insert", token),
                Unit(("meta", token, "mode"), "mode", token, mode),
            )
        return fd

    def mkdir_child(self, parent_fd: int, name: str, mode: int) -> None:
        stream = self._stream
        parent = stream.fd_token[parent_fd]
        self._inner.mkdir_child(parent_fd, name, mode)
        token = self._register_identity(os.lstat(name, dir_fd=parent_fd))
        stream.entries[(parent, name)] = token
        self._emit_mutation(
            Unit(("entry", parent, name), "insert", token),
            Unit(("meta", token, "mode"), "mode", token, mode),
        )

    def symlink_child(self, parent_fd: int, name: str, target: str) -> None:
        stream = self._stream
        parent = stream.fd_token[parent_fd]
        self._inner.symlink_child(parent_fd, name, target)
        info = os.lstat(name, dir_fd=parent_fd)
        token = self._register_identity(info, parent_fd=parent_fd, name=name)
        stream.entries[(parent, name)] = token
        self._emit_mutation(Unit(("entry", parent, name), "insert", token))

    # --- removal ---------------------------------------------------------------
    def unlink_child(self, parent_fd: int, name: str) -> None:
        stream = self._stream
        parent = stream.fd_token[parent_fd]
        token = self._resolve_entry_token(parent, parent_fd, name)
        self._inner.unlink_child(parent_fd, name)
        stream.entries.pop((parent, name), None)
        self._forget_if_orphaned(token)
        self._emit_mutation(Unit(("entry", parent, name), "remove", token))

    def rmdir_child(self, parent_fd: int, name: str) -> None:
        stream = self._stream
        parent = stream.fd_token[parent_fd]
        token = self._resolve_entry_token(parent, parent_fd, name)
        self._inner.rmdir_child(parent_fd, name)
        stream.entries.pop((parent, name), None)
        self._forget_if_orphaned(token)
        self._emit_mutation(Unit(("entry", parent, name), "remove", token))

    # --- rename / exchange / hard link -----------------------------------------
    def transfer_noclobber(self, src_fd: int, src: str, dst_fd: int, dst: str) -> None:
        stream = self._stream
        src_dir = stream.fd_token[src_fd]
        dst_dir = stream.fd_token[dst_fd]
        token = self._resolve_entry_token(src_dir, src_fd, src)
        self._inner.transfer_noclobber(src_fd, src, dst_fd, dst)
        stream.entries.pop((src_dir, src), None)
        stream.entries[(dst_dir, dst)] = token
        self._emit_mutation(
            Unit(("entry", src_dir, src), "remove", token),
            Unit(("entry", dst_dir, dst), "insert", token),
        )

    def exchange(self, parent_fd: int, left: str, right: str) -> None:
        stream = self._stream
        parent = stream.fd_token[parent_fd]
        left_token = self._resolve_entry_token(parent, parent_fd, left)
        right_token = self._resolve_entry_token(parent, parent_fd, right)
        self._inner.exchange(parent_fd, left, right)
        stream.entries[(parent, left)] = right_token
        stream.entries[(parent, right)] = left_token
        self._emit_mutation(
            Unit(("entry", parent, left), "replace", right_token),
            Unit(("entry", parent, right), "replace", left_token),
        )

    def link_anchor(self, src_fd: int, src: str, dst_fd: int, dst: str) -> None:
        stream = self._stream
        src_dir = stream.fd_token[src_fd]
        dst_dir = stream.fd_token[dst_fd]
        token = self._resolve_entry_token(src_dir, src_fd, src)
        self._inner.link_anchor(src_fd, src, dst_fd, dst)
        stream.entries[(dst_dir, dst)] = token
        self._emit_mutation(Unit(("entry", dst_dir, dst), "insert", token))

    # --- content and metadata ----------------------------------------------------
    def write(self, fd: int, data: bytes) -> int:
        written = self._inner.write(fd, data)
        stream = self._stream
        token = stream.fd_token[fd]
        offset = stream.fd_offset.get(fd, 0)
        image = stream.images.setdefault(token, bytearray())
        image[offset : offset + written] = data[:written]
        stream.fd_offset[fd] = offset + written
        self._emit_mutation(Unit(("data", token), "image", token, bytes(image)))
        return written

    def set_mode(self, fd: int, mode: int) -> None:
        self._inner.set_mode(fd, mode)
        token = self._stream.fd_token.get(fd)
        if token is not None:
            self._emit_mutation(Unit(("meta", token, "mode"), "mode", token, mode))

    def set_marker_xattr(self, fd: int, name: str, value: bytes) -> None:
        self._inner.set_marker_xattr(fd, name, value)
        token = self._stream.fd_token.get(fd)
        if token is not None:
            self._emit_mutation(
                Unit(("meta", token, f"xattr:{name}"), "xattr", token, value)
            )

    def repair_entry_mode(self, parent_fd, name, mode, *, before_change) -> None:
        self._inner.repair_entry_mode(parent_fd, name, mode, before_change=before_change)
        stream = self._stream
        parent = stream.fd_token.get(parent_fd)
        if parent is None:
            return
        token = self._resolve_entry_token(parent, parent_fd, name)
        self._emit_mutation(Unit(("meta", token, "mode"), "mode", token, mode))

    # --- durable publish: barrier coverage (design §4.2) --------------------------
    def flush_file(self, fd: int) -> None:
        self._inner.flush_file(fd)
        token = self._stream.fd_token.get(fd)
        covered = (
            frozenset(
                key
                for key in self._pending_keys()
                if key[0] in ("data", "meta") and key[1] == token
            )
            if token is not None
            else frozenset()
        )
        self._emit_barrier(covered)

    def flush_directory(self, fd: int) -> None:
        self._inner.flush_directory(fd)
        directory = self._stream.fd_token.get(fd)
        covered = (
            frozenset(
                key
                for key in self._pending_keys()
                if (key[0] == "entry" and key[1] == directory)
                or (key[0] == "meta" and key[1] == directory)
            )
            if directory is not None
            else frozenset()
        )
        self._emit_barrier(covered)


# --- the store commit sequencer ------------------------------------------------


def _backup_db(db_path: Path) -> bytes:
    """A serialized in-memory copy of the SQLite database at `db_path` (design §4.1).

    The live database runs WAL (design's own pragma probe), and `Connection.backup`
    copies page 1 verbatim -- including the file-format-version bytes at offsets 18/19,
    which read 2 ("may use WAL") on a WAL-mode source even though the `:memory:`
    destination itself only ever runs in-memory journalling (`PRAGMA journal_mode`
    reports `memory`, never `wal`, on `dest`). A later `Connection.deserialize` of
    those bytes then reads a WAL-format header on a fresh connection and fails
    (`OperationalError: unable to open database file`) trying to open a WAL sidecar no
    memory-VFS database can have. The backup is already a complete, page-consistent
    snapshot with no separate WAL to reconcile, so forcing both bytes back to 1 (legacy
    rollback format -- reconstruction never needs WAL, only a plain readable file) is
    safe and restores a cleanly deserializable image.
    """
    source = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        dest = sqlite3.connect(":memory:")
        try:
            source.backup(dest)
            data = bytearray(dest.serialize())
            assert len(data) >= 20 and data[18] in (1, 2) and data[19] in (1, 2), (
                "not a SQLite file image at the expected file-format-version offsets "
                f"(len={len(data)}, [18]={data[18:19]!r}, [19]={data[19:20]!r})"
            )
            data[18] = 1
            data[19] = 1
            return bytes(data)
        finally:
            dest.close()
    finally:
        source.close()


class _LabelingTransaction:
    """Wraps one `_StoreTransaction`, naming the mutating method actually called.

    Not a positional schedule: `test_coordinator_kill_matrix.STORE_BARRIERS` can afford
    to hardcode nine names because every kill-matrix variant is single-effect, but the
    exerciser also runs multi-effect scenarios (`corpus-write`'s five effects,
    `archive-move`'s two), so the label is read off the write instead of counted.
    """

    __slots__ = ("_inner", "_label")

    def __init__(self, inner) -> None:
        self._inner = inner
        self._label: str | None = None

    def __getattr__(self, name: str):
        return getattr(self._inner, name)

    def label(self) -> str | None:
        return self._label

    def promote_staging(self, *args, **kwargs):
        result = self._inner.promote_staging(*args, **kwargs)
        self._label = "prepared"
        return result

    def insert_record(self, *args, **kwargs):
        result = self._inner.insert_record(*args, **kwargs)
        self._label = "prepared"
        return result

    def set_active(self, txid):
        result = self._inner.set_active(txid)
        self._label = "prepared" if txid is not None else "detach"
        return result

    def set_registration_digest(self, *args, **kwargs):
        result = self._inner.set_registration_digest(*args, **kwargs)
        self._label = "registration-binding"
        return result

    def set_settlement_digest(self, *args, **kwargs):
        result = self._inner.set_settlement_digest(*args, **kwargs)
        self._label = "settlement-binding"
        return result

    def set_transaction_state(self, txid, state):
        result = self._inner.set_transaction_state(txid, state)
        self._label = state.value
        return result

    def set_commit_decision(self, txid, decision):
        result = self._inner.set_commit_decision(txid, decision)
        self._label = decision.value
        return result

    def set_journal_state(self, txid, effect_id, state):
        result = self._inner.set_journal_state(txid, effect_id, state)
        self._label = f"{effect_id}-{state.value}"
        return result

    def set_assembly_halt(self, *args, **kwargs):
        result = self._inner.set_assembly_halt(*args, **kwargs)
        self._label = "assembly-halt"
        return result


def attach_store_sequencer(stream: Stream, monkeypatch, metadata_root: str) -> None:
    """Wrap `Store.transaction` so each successful exit appends a labelled `Commit`.

    Follows the wrapper shape `tests/execute_child.py:48-66`'s `_configure_store_cut`
    uses for its "after" cut -- run the original contextmanager to completion -- except
    on success it also backs up `atoms.db` and appends the event, rather than counting
    towards a kill countdown.
    """
    from atoms.store.connection import Store

    original = Store.transaction
    db_path = Path(metadata_root) / "atoms.db"

    @contextlib.contextmanager
    def sequenced(self):
        with original(self) as txn:
            labeling = _LabelingTransaction(txn)
            yield labeling
        label = labeling.label()
        if label is None:
            raise AssertionError(
                "a store transaction committed without a recognized mutating call"
            )
        backup_id = len(stream.backups)
        stream.backups[backup_id] = _backup_db(db_path)
        stream.events.append(Commit(label, backup_id))

    monkeypatch.setattr(Store, "transaction", sequenced)


# --- seeding -----------------------------------------------------------------------


def _snapshot_seed(project_root: str, metadata_root: str, stream: Stream) -> None:
    """Register every seed-era object's identity/token, and populate the seed overlay
    `durable_state` folds from -- the root token for each labelled root, the seed-era
    entry map, every seed-era inode's mode, and every seed-era file's byte image -- by
    walking the real filesystem directly, before the recording backend is attached.

    Shares `stream.tokens`/`stream.identity`/`stream.inodes` with `RecordingCutBackend`,
    so a later Backend call touching one of these objects (a rename, a write) resolves
    the *same* token via its (st_dev, st_ino) identity -- unifying seed-era and recorded
    objects in one namespace (module docstring; design §4.1's inode-identity ruling).
    """

    def register(info: os.stat_result, *, symlink_target: str | None = None) -> int:
        identity_key = (info.st_dev, info.st_ino)
        token = stream.identity.get(identity_key)
        if token is not None:
            return token
        token = next(stream.tokens)
        stream.identity[identity_key] = token
        stream.identity_reverse[token] = identity_key
        if stat.S_ISDIR(info.st_mode):
            kind = "directory"
        elif stat.S_ISLNK(info.st_mode):
            kind = "symlink"
        else:
            kind = "file"
        stream.inodes[token] = ModelInode(token, kind, symlink_target)
        return token

    def walk(directory: Path, parent_token: int, *, at_root: bool) -> None:
        for child in sorted(directory.iterdir()):
            name = child.name
            if at_root and name in DB_FAMILY:
                continue
            info = child.lstat()
            if stat.S_ISLNK(info.st_mode):
                token = register(info, symlink_target=os.readlink(child))
            else:
                token = register(info)
                stream.seed_modes[token] = stat.S_IMODE(info.st_mode)
                if stat.S_ISREG(info.st_mode):
                    stream.seed_images[token] = child.read_bytes()
            stream.seed_entries[(parent_token, name)] = token
            if stat.S_ISDIR(info.st_mode):
                walk(child, token, at_root=False)

    for label, root in (("project", project_root), ("metadata", metadata_root)):
        base = Path(root)
        root_token = register(base.lstat())
        stream.roots[label] = root_token
        walk(base, root_token, at_root=True)


def _walk_lstat(base: Path):
    """Yield every (path, lstat) under `base`, recursively, never following symlinks."""
    for child in sorted(base.iterdir()):
        info = child.lstat()
        yield child, info
        if stat.S_ISDIR(info.st_mode):
            yield from _walk_lstat(child)


def world_digest(project_root: str | Path, metadata_root: str | Path) -> str:
    """Canonical digest of both real trees (design §9's fidelity self-check): path,
    kind, mode, content hash, symlink target, and hard-link-group structure.

    `.#~chain` is **included** -- unlike `_snapshot_seed`'s bootstrap scope, chain
    reconciliation is under test here. The DB family is **excluded**: the store's
    durable state is compared by backup identity (design §4.4), never by file bytes,
    and no `-wal`/`-shm`/`-journal` file is ever replayed.
    """
    entries: list[tuple[str, str, int | None, str | None, str | None, tuple[int, int] | None]] = []
    groups: dict[tuple[int, int], list[str]] = {}
    for label, root in (("project", Path(project_root)), ("metadata", Path(metadata_root))):
        for path, info in _walk_lstat(root):
            if path.parent == root and path.name in DB_FAMILY:
                continue
            rel = f"{label}/{path.relative_to(root).as_posix()}"
            if stat.S_ISLNK(info.st_mode):
                entries.append((rel, "symlink", None, None, os.readlink(path), None))
            elif stat.S_ISDIR(info.st_mode):
                entries.append((rel, "dir", stat.S_IMODE(info.st_mode), None, None, None))
            else:
                content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
                inode_key = (info.st_dev, info.st_ino)
                entries.append(
                    (rel, "file", stat.S_IMODE(info.st_mode), content_hash, None, inode_key)
                )
                groups.setdefault(inode_key, []).append(rel)

    digest = hashlib.sha256()
    for rel, kind, mode, content_hash, target, inode_key in sorted(entries, key=lambda e: e[0]):
        digest.update(rel.encode())
        digest.update(b"\0")
        digest.update(kind.encode())
        digest.update(b"\0")
        digest.update(str(mode).encode() if mode is not None else b"-")
        digest.update(b"\0")
        digest.update((content_hash or "").encode())
        digest.update(b"\0")
        digest.update((target or "").encode())
        digest.update(b"\0")
        if kind == "file":
            assert inode_key is not None
            for member in sorted(groups[inode_key]):
                digest.update(member.encode())
                digest.update(b"\0")
        digest.update(b"\0")
    return digest.hexdigest()


# --- durable state, survivor application, reconstruction (design §4.3-4.4, §9) -----


@dataclass(frozen=True)
class WorldState:
    """The surviving world at a cut. `reconstruct` reads exactly two fields: `tree`
    (design §4's public shape) and `backup_bytes` (the resolved backup content -- a
    bare, stream-independent `WorldState` must carry the bytes itself, since
    `reconstruct`'s signature takes no `Stream` to resolve `backup_id` through).
    `backup_id` is carried for identity/debugging only. `entries`/`modes`/`images` are
    the token-keyed working model `durable_state` folds into and `apply_survivors`
    extends before re-deriving `tree` -- `reconstruct` never reads them; they exist so
    `apply_survivors` can build on `durable_state`'s output without re-deriving it from
    `tree` (which has already lost per-token identity for non-linked files).

    All six fields are required and the dataclass is frozen: every constructor is
    `durable_state`/`apply_survivors` themselves (never a bare literal elsewhere), and
    an unfilled field silently defaulting to `{}` was exactly what let a stale,
    caller-mismatched `WorldState` slip past *without* the caller ever supplying the
    working model apply_survivors needed.
    """

    tree: WorldTree
    backup_id: int | None
    backup_bytes: bytes | None
    entries: dict[tuple[int, str], int]
    modes: dict[int, int]
    images: dict[int, bytes]


@dataclass(frozen=True)
class Skip:
    """An unrepresentable survivor subset (design §4.3): `reason` is one of
    `{"remove-without-target", "replace-without-target", "orphan-object-state"}`."""

    reason: str


def _reverse_index(entries: dict[tuple[int, str], int]) -> dict[int, set[tuple[int, str]]]:
    reverse: dict[int, set[tuple[int, str]]] = {}
    for key, token in entries.items():
        reverse.setdefault(token, set()).add(key)
    return reverse


def _relink(
    entry_key: tuple[int, str],
    token: int | None,
    entries: dict[tuple[int, str], int],
    reverse: dict[int, set[tuple[int, str]]],
) -> None:
    old = entries.pop(entry_key, None)
    if old is not None:
        reverse[old].discard(entry_key)
    if token is not None:
        entries[entry_key] = token
        reverse.setdefault(token, set()).add(entry_key)


def _apply_unit(
    unit: Unit,
    entries: dict[tuple[int, str], int],
    modes: dict[int, int],
    images: dict[int, bytes],
    reverse: dict[int, set[tuple[int, str]]],
    *,
    strict: bool,
) -> str | None:
    """Apply one unit's effect in place; return a Skip reason, or `None` on success.

    `strict=True` (`apply_survivors`) enforces design §4.3's structural-applicability
    rules -- a `remove`/`replace` needs a durable-or-included target, an `image`/`mode`
    needs a durable-or-included name -- because it is judging a *hypothetical* survivor
    subset for representability. `strict=False` (`durable_state`) is folding *real*
    history: every barrier-covered unit really happened, so it always applies --
    including a `remove`/`replace` whose target was itself never durable (a net no-op:
    `_relink` already treats a missing prior entry as "nothing to unlink") and an
    `image`/`mode` for a token with no durable name yet (stored regardless; it simply
    stays unreachable, and therefore absent from `_materialize_tree`'s output, until a
    later durable insert gives the token a name).
    """
    key = unit.key
    if key[0] == "entry":
        entry_key = (key[1], key[2])
        if unit.change == "insert":
            _relink(entry_key, unit.object_token, entries, reverse)
            return None
        if unit.change == "replace":
            if strict and entry_key not in entries:
                return "replace-without-target"
            _relink(entry_key, unit.object_token, entries, reverse)
            return None
        if unit.change == "remove":
            if strict and entry_key not in entries:
                return "remove-without-target"
            _relink(entry_key, None, entries, reverse)
            return None
        raise AssertionError(f"unknown entry change: {unit.change!r}")
    if key[0] == "data":
        token = key[1]
        if strict and not reverse.get(token):
            return "orphan-object-state"
        images[token] = unit.payload  # type: ignore[assignment]
        return None
    if key[0] == "meta":
        token, field_name = key[1], key[2]
        if field_name != "mode":
            # xattr fields carry no reconstructed/digested state (design §4.4/§9's
            # tree shapes and `world_digest` name path/kind/mode/content/symlink/
            # link-group only) -- applying one is a structural no-op, not a skip.
            return None
        if strict and not reverse.get(token):
            return "orphan-object-state"
        modes[token] = unit.payload  # type: ignore[assignment]
        return None
    raise AssertionError(f"unknown unit key kind: {key[0]!r}")


def _materialize_tree(
    stream: Stream,
    entries: dict[tuple[int, str], int],
    modes: dict[int, int],
    images: dict[int, bytes],
) -> WorldTree:
    by_parent: dict[int, list[tuple[str, int]]] = {}
    for (parent_token, name), token in entries.items():
        by_parent.setdefault(parent_token, []).append((name, token))

    tree: WorldTree = {}
    token_paths: dict[int, list[str]] = {}

    def walk(token: int, path: str) -> None:
        inode = stream.inodes[token]
        if inode.kind == "directory":
            tree[path] = ("dir", modes.get(token, 0))
            for name, child in sorted(by_parent.get(token, ())):
                walk(child, f"{path}/{name}")
        elif inode.kind == "symlink":
            tree[path] = ("symlink", inode.symlink_target)
        else:
            token_paths.setdefault(token, []).append(path)

    for label, root_token in stream.roots.items():
        for name, child in sorted(by_parent.get(root_token, ())):
            walk(child, f"{label}/{name}")

    for token, paths in token_paths.items():
        content = images.get(token, b"")
        mode = modes.get(token, 0)
        if len(paths) > 1:
            for path in paths:
                tree[path] = ("file", content, mode, ("link-group", token))
        else:
            tree[paths[0]] = ("file", content, mode)

    return tree


def durable_state(stream: Stream, cut: int) -> WorldState:
    """Fold events `0..cut` into the durable world (design §4.3): the seed tree, each
    `Barrier`'s covered pending units moved into the durable tree (keyed replacement --
    the last pending `Unit` at a key wins), and the latest `Commit`'s backup. Units
    still pending at `cut` are *not* included here -- `apply_survivors` extends this
    with a chosen survivor subset of them.
    """
    if cut <= 0:
        return WorldState({}, None, None, {}, {}, {})

    entries = dict(stream.seed_entries)
    modes = dict(stream.seed_modes)
    images: dict[int, bytes] = {token: bytes(data) for token, data in stream.seed_images.items()}
    reverse = _reverse_index(entries)
    backup_id: int | None = 0
    backup_bytes = stream.backups.get(0)
    pending: dict[tuple, Unit] = {}

    for event in stream.events[1:cut]:
        if type(event) is Mutation:
            for unit in event.units:
                pending[unit.key] = unit
        elif type(event) is Barrier:
            for key in event.covered:
                unit = pending.pop(key, None)
                if unit is None:
                    continue
                _apply_unit(unit, entries, modes, images, reverse, strict=False)
        elif type(event) is Commit:
            backup_id = event.backup_id
            backup_bytes = stream.backups.get(event.backup_id)

    tree = _materialize_tree(stream, entries, modes, images)
    return WorldState(tree, backup_id, backup_bytes, entries, modes, images)


def _pending_with_positions(stream: Stream, cut: int) -> dict[tuple, tuple[int, Unit]]:
    """Like `Stream.pending_before`, but keeping each surviving key's stream position."""
    state: dict[tuple, tuple[int, Unit]] = {}
    for index, event in enumerate(stream.events[:cut]):
        if type(event) is Mutation:
            for unit in event.units:
                state[unit.key] = (index, unit)
        elif type(event) is Barrier:
            for key in event.covered:
                state.pop(key, None)
    return state


def pending_keys_at(stream: Stream, cut: int) -> frozenset[tuple]:
    """The pending overlay's keys at `cut` (`Stream.pending_before(cut)`), as a set."""
    return frozenset(stream.pending_before(cut))


def apply_survivors(
    state: WorldState, stream: Stream, cut: int, survivors: frozenset[tuple]
) -> WorldState | Skip:
    """Apply the chosen pending keys **in stream order** against `state`'s durable tree
    (design §4.3). Every key must be pending at `cut` -- `pending_keys_at(stream, cut)`
    is the closed universe callers choose subsets from; a key outside it is a caller
    bug, not a representability question, and raises rather than skipping silently.

    The *first* structurally inapplicable unit -- a `remove`/`replace` whose target
    entry is absent, or `image`/`mode` state for a token with no durable-or-included
    name anywhere -- makes the whole subset unrepresentable: returns `Skip(reason)`
    immediately, without partially applying the rest.
    """
    positioned = _pending_with_positions(stream, cut)
    chosen: list[tuple[int, Unit]] = []
    for key in survivors:
        found = positioned.get(key)
        if found is None:
            raise KeyError(f"not a pending key at cut {cut}: {key!r}")
        chosen.append(found)
    # Stable-sort by stream position; within one Mutation event (a tied position --
    # `create_exclusive`'s insert+mode land in the same event), entry units must apply
    # before data/meta units, since a data/meta unit's strict reachability check needs
    # its own entry-insert already applied when both are chosen from the same event.
    # The unit key itself breaks any remaining tie (two entry units, or two data/meta
    # units, from the same event) deterministically -- `survivors` is a `frozenset`, so
    # without this, `chosen`'s relative order among same-priority ties would depend on
    # frozenset iteration order (hash-seed-dependent), and Task 5's skip accounting
    # needs reproducible ordering across runs.
    chosen.sort(key=lambda item: (item[0], 0 if item[1].key[0] == "entry" else 1, item[1].key))

    entries = dict(state.entries)
    modes = dict(state.modes)
    images = dict(state.images)
    reverse = _reverse_index(entries)

    for _, unit in chosen:
        reason = _apply_unit(unit, entries, modes, images, reverse, strict=True)
        if reason is not None:
            return Skip(reason)

    tree = _materialize_tree(stream, entries, modes, images)
    return WorldState(tree, state.backup_id, state.backup_bytes, entries, modes, images)


def _maximal_survivors(stream: Stream, cut: int) -> frozenset[tuple]:
    """The largest subset of `pending_keys_at(stream, cut)` `apply_survivors` accepts
    whole -- design §9's "every pending unit surviving" fidelity check, computed
    correctly in the presence of transient, never-durable churn.

    Keyed replacement forgets a key's earlier pending `Unit` the moment a later one at
    the *same* key supersedes it -- so an object created and fully removed again before
    any barrier ever covers it leaves its `data`/`meta` keys (a different key per
    field, never superseded) permanently orphaned, and can leave a `remove` pending
    whose target was never durable and whose insert is gone. Every such leftover key is
    unconditionally excludable: applying it or not changes nothing observable (a
    `remove` with no target is already a no-op; `data`/`meta` for a nameless token
    never surfaces in `_materialize_tree`), and design §9's own `apply_survivors`
    Step-3 text calls this shape "an unreachable inode carries no observable state" --
    so dropping it, rather than letting one leftover key fail an otherwise-legitimate
    full-survival reconstruction, is what "every pending unit surviving" means when
    some pending units are structurally inert. A greedy pass -- include a key only if
    it applies cleanly against everything already accepted, in stream order -- is
    exactly this: every kept key was truly reachable when it landed, and every dropped
    key was inert by construction, never a discarded *observable* change.
    """
    base = durable_state(stream, cut)
    positioned = _pending_with_positions(stream, cut)
    ordered = sorted(
        positioned.values(), key=lambda item: (item[0], 0 if item[1].key[0] == "entry" else 1)
    )
    entries = dict(base.entries)
    modes = dict(base.modes)
    images = dict(base.images)
    reverse = _reverse_index(entries)
    accepted: set[tuple] = set()
    for _, unit in ordered:
        reason = _apply_unit(unit, entries, modes, images, reverse, strict=True)
        if reason is None:
            accepted.add(unit.key)
    return frozenset(accepted)


def _resolve(rel: str, project_root: Path, metadata_root: Path) -> Path:
    label, _, tail = rel.partition("/")
    return (project_root if label == "project" else metadata_root) / tail


def reconstruct(state: WorldState, project_root: Path, metadata_root: Path) -> None:
    """Write `state.tree` onto fresh `project_root`/`metadata_root` directories (design
    §4.4): model inodes become fresh files, hard-link groups reproduced with `os.link`
    (files only), directory modes applied, symlink targets exact, and the cut's backup
    installed as `atoms.db` **alone** -- never any other `DB_FAMILY` name.

    Modes are applied explicitly with `os.chmod`, never left to `mkdir`'s/`open`'s mode
    argument alone -- the umask would otherwise drift the reconstructed mode away from
    the durable one the fidelity digest checks for.

    Directories are created at a permissive `0o700` first and only chmod'd to their
    *durable* mode in a final, deepest-first pass -- a survivor subset can legitimately
    include an entry `insert` without its paired `meta mode` unit (design §4.2's
    independently-barrier-covered keys), so `_materialize_tree` can hand back a `0`
    (no permission bits at all) directory mode. Applying that mode immediately, before
    the directory is populated or a descendant is even created, would make every path
    through it unresolvable (`os.mkdir`/`os.chmod`/`os.link`/`os.symlink` all need `x`
    on every ancestor). Populating everything first, then locking modes down from the
    leaves toward the roots, means every write that needs to resolve through a
    directory has already happened before that directory's real (possibly `0`) mode is
    ever applied.
    """
    project_root = Path(project_root)
    metadata_root = Path(metadata_root)

    directories = sorted(
        (path for path, value in state.tree.items() if value[0] == "dir"),
        key=lambda path: path.count("/"),
    )
    for rel in directories:
        target = _resolve(rel, project_root, metadata_root)
        target.mkdir()
        os.chmod(target, 0o700)

    groups: dict[tuple, list[str]] = {}
    singles: list[str] = []
    for rel, value in state.tree.items():
        if value[0] != "file":
            continue
        if len(value) == 4:
            groups.setdefault(value[3], []).append(rel)
        else:
            singles.append(rel)

    for rel in singles:
        _, content, mode = state.tree[rel]
        target = _resolve(rel, project_root, metadata_root)
        target.write_bytes(content)
        os.chmod(target, mode)

    for members in groups.values():
        first, *rest = sorted(members)
        _, content, mode, _group = state.tree[first]
        first_target = _resolve(first, project_root, metadata_root)
        first_target.write_bytes(content)
        os.chmod(first_target, mode)
        for rel in rest:
            os.link(first_target, _resolve(rel, project_root, metadata_root))

    for rel, value in state.tree.items():
        if value[0] != "symlink":
            continue
        os.symlink(value[1], _resolve(rel, project_root, metadata_root))

    # Deepest-first: every path that still needs to resolve *through* a directory
    # (every mkdir/write/link/symlink above) has already happened, so locking a
    # directory down to its real -- possibly `0` -- durable mode here can never block
    # a not-yet-done write.
    for rel in sorted(directories, key=lambda path: path.count("/"), reverse=True):
        os.chmod(_resolve(rel, project_root, metadata_root), state.tree[rel][1])

    if state.backup_bytes is not None:
        memory = sqlite3.connect(":memory:")
        try:
            memory.deserialize(state.backup_bytes)
            db_path = metadata_root / "atoms.db"
            connection = sqlite3.connect(db_path)
            try:
                memory.backup(connection)
            finally:
                connection.close()
        finally:
            memory.close()


# --- top-level recording -----------------------------------------------------------


def record_scenario(entry, ingredients, monkeypatch, *, caught: bool = False) -> Stream:
    """Seed, snapshot event 0, and record only the transaction (controller ruling).

    Bootstrap (`setup_clean`: seed the world, register the root) is seed-owned per
    design §4.1 and runs unrecorded, through the plain backend. `_snapshot_seed`
    captures the tree bootstrap produced -- including the store `register_root` just
    created -- as the durable-state fold's starting overlay, so the recorded stream
    never enumerates it as entry-unit mutation. Only afterwards is the backend wrapped
    and the transaction alone run through it. `final_world_digest` is computed last, so
    the fidelity self-check (design §9) has a live-world target to compare against.
    """
    from tests.exerciser import setup_clean, transact, transact_caught

    backend, project_root, metadata_root, storage = ingredients
    setup_clean(entry, ingredients, monkeypatch)

    stream = Stream()
    _snapshot_seed(project_root, metadata_root, stream)
    db_path = Path(metadata_root) / "atoms.db"
    stream.backups[0] = _backup_db(db_path) if db_path.exists() else None
    stream.events.append(Seed(world_digest(project_root, metadata_root), 0))

    recorder = RecordingCutBackend(backend, stream)
    recorded_ingredients = (recorder, project_root, metadata_root, storage)
    attach_store_sequencer(stream, monkeypatch, metadata_root)

    if caught:
        transact_caught(entry, recorded_ingredients, monkeypatch)
    else:
        transact(entry, recorded_ingredients)

    stream.final_world_digest = world_digest(project_root, metadata_root)
    return stream
