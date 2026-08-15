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
        # complete byte image of every file-kind token.
        self.identity: dict[tuple[int, int], int] = {}
        self.inodes: dict[int, ModelInode] = {}
        self.entries: dict[tuple[int, str], int] = {}
        self.images: dict[int, bytearray] = {}

        # Tokens that name a database-family entry (design §4.1: "the database
        # namespace is model-owned"). Once a token is known to be one, every unit
        # naming it -- entry, data, or meta -- is dropped from the recorded stream.
        self.db_tokens: set[int] = set()

        # Keys touched by a Mutation and not yet covered by a Barrier.
        self.pending: dict[tuple, Unit] = {}

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
        self._emit_mutation(Unit(("entry", parent, name), "remove", token))

    def rmdir_child(self, parent_fd: int, name: str) -> None:
        stream = self._stream
        parent = stream.fd_token[parent_fd]
        token = self._resolve_entry_token(parent, parent_fd, name)
        self._inner.rmdir_child(parent_fd, name)
        stream.entries.pop((parent, name), None)
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
    """A serialized in-memory copy of the SQLite database at `db_path` (design §4.1)."""
    source = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        dest = sqlite3.connect(":memory:")
        try:
            source.backup(dest)
            return dest.serialize()
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


# --- seeding and top-level recording ---------------------------------------------


def _walk_world(project_root: str, metadata_root: str) -> str:
    """A digest of the seeded project and metadata trees, DB_FAMILY entries excluded."""
    digest = hashlib.sha256()
    for label, root in (("project", project_root), ("metadata", metadata_root)):
        base = Path(root)
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames.sort()
            here = Path(dirpath)
            rel_dir = here.relative_to(base).as_posix()
            for name in sorted(filenames):
                if here == base and name in DB_FAMILY:
                    continue
                full = here / name
                if full.is_symlink():
                    payload = os.readlink(full).encode()
                else:
                    payload = full.read_bytes()
                digest.update(label.encode())
                digest.update(rel_dir.encode())
                digest.update(b"\0")
                digest.update(name.encode())
                digest.update(b"\0")
                digest.update(payload)
    return digest.hexdigest()


def record_scenario(entry, ingredients, monkeypatch, *, caught: bool = False) -> Stream:
    """Seed, snapshot event 0, and record only the transaction (controller ruling).

    Bootstrap (`setup_clean`: seed the world, register the root) is seed-owned per
    design §4.1 and runs unrecorded, through the plain backend. Event 0 then captures
    the tree that bootstrap produced -- including the store `register_root` just
    created -- so the recorded stream never enumerates it as entry-unit mutation. Only
    afterwards is the backend wrapped and the transaction alone run through it.
    """
    from tests.exerciser import setup_clean, transact, transact_caught

    backend, project_root, metadata_root, storage = ingredients
    setup_clean(entry, ingredients, monkeypatch)

    stream = Stream()
    world_digest = _walk_world(project_root, metadata_root)
    db_path = Path(metadata_root) / "atoms.db"
    stream.backups[0] = _backup_db(db_path) if db_path.exists() else None
    stream.events.append(Seed(world_digest, 0))

    recorder = RecordingCutBackend(backend, stream)
    recorded_ingredients = (recorder, project_root, metadata_root, storage)
    attach_store_sequencer(stream, monkeypatch, metadata_root)

    if caught:
        transact_caught(entry, recorded_ingredients, monkeypatch)
    else:
        transact(entry, recorded_ingredients)

    return stream
