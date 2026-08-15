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

Two known bounds of the Task-6 runner in this module, both deliberate and both cheap to
lift if a later task needs them:

- `realign_durable_identities` counts an `lstat` hit as a rewritten identity even when
  the reconstructed object is not a directory, so its return value is an upper bound on
  "directories realigned", not an exact count. It is used as evidence the step ran, never
  as an assertion target.
- `_inspect` projects the **last** `transaction_record` row only. Every exerciser
  scenario records exactly one transaction, so the last row is the transaction; a future
  multi-transaction scenario would need the projection widened to a per-txid mapping.
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import itertools
import json
import os
import shutil
import sqlite3
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import NewType, cast

DB_FAMILY = ("atoms.db", "atoms.db-wal", "atoms.db-shm", "atoms.db-journal")
LOCK_LEAF = "lock"  # `acquire_project_lock`'s metadata_root child (`fs/lock.py:217`)

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
    """One durability unit. `payload` carries the data image for an `image` unit and the
    mode/xattr value for a `mode`/`xattr` unit. A *creation's* mode is not a unit at all
    (design §4.1 as amended 2026-08-15) -- it rides with the typed model inode, in
    `Stream.creation_modes`.
    """

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
        # The mode each recorded object was CREATED with (design §4.1 as amended
        # 2026-08-15: creation-time mode is atomic with inode creation). It is a
        # property of the model inode, not a durability unit, so keyed replacement --
        # a rename's `remove` superseding the pending creating `insert` at the same
        # entry key -- cannot lose it, and a survivor subset can never reconstruct an
        # object at a mode it was never created with. Only a later `set_mode`/
        # `repair_entry_mode` *change* emits a `("meta", token, "mode")` unit, which
        # tears independently and overrides this fallback wherever it applies.
        self.creation_modes: dict[int, int] = {}
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

    # --- creation: mint a token, register the entry, emit ONE insert carrying the
    # creation mode (design §4.1 as amended 2026-08-15 -- a create journals its inode
    # with its entry, so the mode cannot tear away beneath a durable entry; only a
    # later `set_mode`/`repair_entry_mode` change emits an independently tearing
    # `meta` unit) ----------------------------------------------------------------
    def create_exclusive(self, parent_fd: int, name: str, mode: int) -> int:
        stream = self._stream
        parent = stream.fd_token[parent_fd]
        fd = self._inner.create_exclusive(parent_fd, name, mode)
        token = self._register_identity(os.fstat(fd))
        stream.entries[(parent, name)] = token
        stream.fd_token[fd] = token
        stream.fd_offset[fd] = 0
        stream.creation_modes.setdefault(token, mode)
        self._emit_mutation(Unit(("entry", parent, name), "insert", token))
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
            stream.creation_modes.setdefault(token, mode)
            self._emit_mutation(Unit(("entry", parent, name), "insert", token))
        return fd

    def mkdir_child(self, parent_fd: int, name: str, mode: int) -> None:
        stream = self._stream
        parent = stream.fd_token[parent_fd]
        self._inner.mkdir_child(parent_fd, name, mode)
        token = self._register_identity(os.lstat(name, dir_fd=parent_fd))
        stream.entries[(parent, name)] = token
        stream.creation_modes.setdefault(token, mode)
        self._emit_mutation(Unit(("entry", parent, name), "insert", token))

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
    """Yield every (path, lstat) under `base`, recursively, never following symlinks.

    A directory whose *durable* mode denies the owner read or search permission is
    yielded, never descended into, and never chmod'd: design §4.3's survivor product
    branches an entry unit independently of its paired `meta mode` unit, so a
    reconstructed world legitimately contains a mode-`0` directory (Task 5's directed
    zero-mode test pins exactly that shape). Its contents are unobservable -- to the
    engine as much as to this walk -- so "an unreadable directory at this mode" is the
    whole canonical fact about it, and the mode is already part of every digest entry.
    Restoring permission to look inside would mutate the very world the second pass is
    about to compare.
    """
    try:
        children = sorted(base.iterdir())
    except PermissionError:
        return
    for child in children:
        try:
            info = child.lstat()
        except PermissionError:
            continue
        yield child, info
        if stat.S_ISDIR(info.st_mode):
            yield from _walk_lstat(child)


_UNREADABLE = "unreadable"


def _content_hash(path: Path) -> str:
    """The file's content digest, or the `_UNREADABLE` marker for a mode that denies it.

    Same ruling as `_walk_lstat`: a survivor subset may drop a file's `meta mode` unit
    while keeping its entry, and design §4.4 reconstructs that faithfully as a mode-`0`
    file. Its bytes are unobservable -- to the engine as much as to this digest -- and
    the mode itself is already a digested field, so the marker records exactly what is
    knowable instead of raising.
    """
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except PermissionError:
        return _UNREADABLE


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
                content_hash = _content_hash(path)
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

    def mode_of(token: int) -> int:
        return modes.get(token, stream.creation_modes.get(token, 0))

    def walk(token: int, path: str) -> None:
        inode = stream.inodes[token]
        if inode.kind == "directory":
            tree[path] = ("dir", mode_of(token))
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
        mode = mode_of(token)
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


def _chosen_order(key: tuple) -> tuple:
    """The `entry`-before-`data`/`meta` tie-break `apply_survivors` and every one of
    `enumerate_cells`'s applicability passes share -- one engine, one ordering rule
    (CRITICAL 2): within one stream position (`create_exclusive`'s insert+mode land in
    the same event), an entry unit must apply before a data/meta unit, since a
    data/meta unit's strict reachability check needs its own entry-insert already
    applied when both are chosen from the same event; the unit key itself breaks any
    remaining tie deterministically, since a `frozenset`'s iteration order is
    hash-seed-dependent and skip accounting needs reproducible ordering across runs.
    """
    return (0 if key[0] == "entry" else 1, key)


def _apply_chosen(
    state: WorldState,
    stream: Stream,
    cut: int,
    positioned: dict[tuple, tuple[int, Unit]],
    survivors: frozenset[tuple],
) -> WorldState | Skip:
    """`apply_survivors`'s engine, taking an already-computed `positioned` (Important
    4: `enumerate_cells` computes `_pending_with_positions` once per cut and shares it
    across every combo's applicability pass and its final `WorldState`, rather than
    recomputing an O(events) replay per combo)."""
    chosen: list[tuple[int, Unit]] = []
    for key in survivors:
        found = positioned.get(key)
        if found is None:
            raise KeyError(f"not a pending key at cut {cut}: {key!r}")
        chosen.append(found)
    chosen.sort(key=lambda item: (item[0], _chosen_order(item[1].key)))

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
    return _apply_chosen(state, stream, cut, _pending_with_positions(stream, cut), survivors)


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


def _probe_root_token(stream: Stream) -> int | None:
    """The token naming `metadata/probe` (`atoms.fs.bootstrap.PROBE_DIRECTORY`) --
    created at seed-owned bootstrap (design §4.1: bootstrap publication is seed's, not
    recorded), so it lives in `seed_entries`, never as a recorded `Mutation`."""
    metadata_root = stream.roots.get("metadata")
    if metadata_root is None:
        return None
    return stream.seed_entries.get((metadata_root, "probe"))


def _probe_classification(stream: Stream) -> tuple[frozenset[int], dict[int, frozenset[int]]]:
    """`(probe_tokens, token_parents)` for the whole stream, computed once (it is
    cut-independent) and shared by every cut's classification:

    `probe_tokens` is every token that *is* `metadata/probe`, or is transitively
    contained by it -- `src/atoms/fs/probe.py`'s capability probing nests freely
    (`src`/`dst` link-anchor probing creates and populates its own child
    directories), so membership is discovered by a single forward pass: an object
    inserted under an already-known probe parent is itself a probe token from then on
    (a child cannot be created before its parent).

    `token_parents` is, for every token that was ever named by an `entry` unit
    (`insert`/`replace`, anywhere in the stream) or by a seed-era entry, the set of
    every *parent* token it was ever bound under -- the CONTROLLER ruling's
    `data`/`meta` classification ("every name its token ever held lives there") reads
    directly off this.
    """
    probe_root = _probe_root_token(stream)
    probe_tokens: set[int] = {probe_root} if probe_root is not None else set()
    token_parents: dict[int, set[int]] = {}
    for (parent, _name), token in stream.seed_entries.items():
        token_parents.setdefault(token, set()).add(parent)
    for event in stream.events:
        if type(event) is not Mutation:
            continue
        for unit in event.units:
            if unit.key[0] != "entry" or unit.change not in ("insert", "replace"):
                continue
            if unit.object_token is None:
                continue
            parent = unit.key[1]
            token_parents.setdefault(unit.object_token, set()).add(parent)
            if parent in probe_tokens:
                probe_tokens.add(unit.object_token)
    return frozenset(probe_tokens), {
        token: frozenset(parents) for token, parents in token_parents.items()
    }


def _is_probe_noise(
    key: tuple,
    probe_tokens: frozenset[int],
    token_parents: dict[int, frozenset[int]],
) -> bool:
    """CONTROLLER ruling #1: an `entry` key is probe-noise iff its parent lies in the
    `probe/` subtree; a `data`/`meta` key is probe-noise iff *every* parent its token
    was ever bound under does -- a token with no recorded binding at all (should not
    occur; every `data`/`meta` unit's token was named by some `entry` insert) is
    conservatively TRANSACTIONAL, never silently folded away.
    """
    if key[0] == "entry":
        return key[1] in probe_tokens
    parents = token_parents.get(key[1])
    return bool(parents) and parents <= probe_tokens


def _tree_digest(tree: WorldTree) -> str:
    """A deterministic dedupe key for a `WorldState.tree` (design's `(state digest,
    backup_id)` dedupe pair) -- not a security digest, only stable ordering."""
    digest = hashlib.sha256()
    for path, value in sorted(tree.items()):
        digest.update(path.encode())
        digest.update(b"\0")
        digest.update(repr(value).encode())
        digest.update(b"\0")
    return digest.hexdigest()


class PendingCapExceeded(Exception):
    """A cut's pending *transactional* key count exceeded `enumerate_cells`'s
    `pending_cap` -- loud, never sampled (design §4.3): "a cap breach means the model
    or the engine changed, and the matrix must say so.\""""


@dataclass(frozen=True)
class Cell:
    """One reconstructible world at a cut: the chosen pending-key subset (the
    transactional combo plus whichever probe-noise keys folded in) and the
    `WorldState` `apply_survivors`' own engine built from it."""

    cut: int
    survivors: frozenset[UnitKey]
    state: WorldState


@dataclass(frozen=True)
class SweepAccounting:
    """The sweep's bookkeeping: how many cells were kept, how many worlds deduped away
    (identical `(tree digest, backup_id)`), how many survivor subsets were skipped by
    reason, and `probe_folded` -- the total count of probe-noise keys folded into a
    *kept* cell across the whole sweep (summed, not deduplicated: a probe-noise key
    folded into ten different cells counts ten times), evidence that probe-noise
    reclamation actually ran rather than merely being declared."""

    cells: int
    deduped: int
    skips: dict[str, int]
    probe_folded: int


def _fold(
    stream: Stream,
    base: WorldState,
    positioned: dict[tuple, tuple[int, Unit]],
    mandatory: frozenset[tuple],
    optional: frozenset[tuple],
) -> tuple[str | None, frozenset[tuple]]:
    """One applicability pass, in true stream order, over `mandatory | optional` --
    CRITICAL 2: the *same* engine (`_chosen_order`'s tie-break, `_apply_unit`'s strict
    rules) that judges the final `Cell`, not a second, differently-ordered one. A
    `mandatory` (chosen transactional survivor) unit that fails aborts the whole combo
    immediately, returning its `Skip` reason and no accepted optionals -- matching
    `apply_survivors`' own all-or-nothing contract for a chosen key. An `optional`
    (probe-noise) unit that fails is simply excluded and the pass continues: the
    maximal-applicable probe-noise fold (`_maximal_survivors`'s own greedy philosophy,
    design §9's "an unreachable inode carries no observable state"), computed by
    exactly the ordering that will later re-apply it, so a fold decision can never
    diverge from what `apply_survivors` itself would say about the same merged set --
    `_apply_unit` never mutates on failure, so an excluded optional key leaves no trace
    for the units still to come.
    """
    ordered = sorted(
        ((key, positioned[key]) for key in mandatory | optional),
        key=lambda item: (item[1][0], _chosen_order(item[1][1].key)),
    )
    entries = dict(base.entries)
    modes = dict(base.modes)
    images = dict(base.images)
    reverse = _reverse_index(entries)
    accepted: set[tuple] = set()
    for key, (_, unit) in ordered:
        reason = _apply_unit(unit, entries, modes, images, reverse, strict=True)
        if key in mandatory:
            if reason is not None:
                return reason, frozenset()
        elif reason is None:
            accepted.add(key)
    return None, frozenset(accepted)


def enumerate_cells(
    stream: Stream, *, pending_cap: int = 12
) -> tuple[tuple[Cell, ...], SweepAccounting]:
    """Every reconstructible cell of `stream`, materialized (erratum 2 -- a pair, never
    an iterator): walk every cut index (design §4.3's "an index into the recorded
    stream"), split that cut's pending keys into TRANSACTIONAL and probe-noise
    (`_is_probe_noise`; CONTROLLER ruling #1), and take the full powerset over the
    transactional keys **of every kind** (`entry`, `data`, `meta`) -- design §4.3's
    full-independence adversary, restored: a scenario's own mode/content axis is
    genuinely combinatorial (a create's entry landing without its paired mode unit is
    §4.4's own reconstructible shape), so it is swept, not folded away.

    Every transactional combo is completed by the maximal-applicable probe-noise fold
    (`_fold`) -- capability probing's own churn is unconditionally reclaimed at lease
    entry (`atoms.fs.bootstrap.reclaim_probe_survivors`) regardless of which
    transactional units happen to survive a given cut, so it contributes at most one
    deterministic outcome per combo, never an independent branch (ruling #3).

    `pending_cap` bounds the cut's transactional pending-key count (every kind, not
    just `entry`) before the walk raises `PendingCapExceeded` -- loud, never sampled.
    Measured across every `SCENARIOS` entry, the transactional dimension never exceeds
    6 simultaneously pending keys (probe-noise separately reaches 24-51) -- comfortably
    under the default cap, and matching design §4.3's "the engine's barrier discipline
    keeps pending sets small" read as being about the engine's *own* transactional
    surface, not capability-probing scratch.

    Identical `(tree digest, backup_id)` worlds dedupe -- expected and common, since
    many adjacent cuts share the same durable base and the same transactional
    powerset.
    """
    probe_tokens, token_parents = _probe_classification(stream)

    cells: list[Cell] = []
    seen: set[tuple[str, int | None]] = set()
    deduped = 0
    skips: dict[str, int] = {}
    probe_folded = 0

    for cut in range(len(stream.events) + 1):
        pending = pending_keys_at(stream, cut)
        transactional = tuple(
            sorted(
                key for key in pending
                if not _is_probe_noise(key, probe_tokens, token_parents)
            )
        )
        probe_keys = frozenset(
            key for key in pending if _is_probe_noise(key, probe_tokens, token_parents)
        )
        if len(transactional) > pending_cap:
            raise PendingCapExceeded(
                f"cut {cut}: {len(transactional)} pending transactional keys exceeds "
                f"pending_cap={pending_cap}"
            )
        base = durable_state(stream, cut)
        positioned = _pending_with_positions(stream, cut)
        for size in range(len(transactional) + 1):
            for combo in itertools.combinations(transactional, size):
                mandatory = frozenset(combo)
                reason, accepted = _fold(stream, base, positioned, mandatory, probe_keys)
                if reason is not None:
                    skips[reason] = skips.get(reason, 0) + 1
                    continue
                survivors = mandatory | accepted
                result = _apply_chosen(base, stream, cut, positioned, survivors)
                if isinstance(result, Skip):
                    skips[result.reason] = skips.get(result.reason, 0) + 1
                    continue
                dedupe_key = (_tree_digest(result.tree), result.backup_id)
                if dedupe_key in seen:
                    deduped += 1
                    continue
                seen.add(dedupe_key)
                probe_folded += len(accepted)
                cells.append(Cell(cut, cast("frozenset[UnitKey]", survivors), result))

    return tuple(cells), SweepAccounting(
        cells=len(cells), deduped=deduped, skips=skips, probe_folded=probe_folded
    )


def named_tuples(stream: Stream) -> dict[str, Cell]:
    """`{"dual-name-forward", "anchor-only-forward"}` and, in a caught-rollback move
    stream, `{"dual-name-reverse", "anchor-only-reverse"}` (design §9.4).

    A move's transfer is a two-unit `remove`+`insert` `Mutation` sharing an object
    token that also carries a `link_anchor`-shaped insert elsewhere in the stream (a
    single-unit entry `insert` Mutation) -- rollback re-moves a landed move by emitting
    a *second*, structurally identical transfer of the same token, so a caught-rollback
    stream carries two matches, not one.

    Minor 8: `src/atoms/fs/probe.py`'s own hard-link capability probing performs the
    identical shape (a rename under `src`/`dst`, and its own `link_anchor`) on
    different tokens -- unenforced by token identity alone, since nothing stops two
    *different* object tokens from each independently matching the shape. Candidates
    whose remove/insert parent lies in the `probe/` subtree (`_is_probe_noise`) are
    excluded before shape-matching, so only the scenario's own move can ever match.

    Direction: `TransactionState.ROLLING_BACK`'s `"rolling_back"` commit is the
    earliest point any effect's undo can begin (`classify_recovery` always transitions
    to `ROLLING_BACK` before any per-effect `UNDO_STARTED` step) -- a transfer at or
    after it is the reverse (UNDO-era) traffic; before it (or when the stream never
    rolls back at all) is forward.

    For each match, the cut immediately after its `Mutation` names two single-survivor
    cells -- `{insert}` (both names momentarily alive: dual-name) and `{remove}` (the
    anchor is the only surviving name: anchor-only) -- design §4.3's "a remove of an
    entry that is durably present applies" is exactly how anchor-only arises. Design
    §4.3/§9.4 calls both **generated, never skipped**: either resolving to `Skip`
    raises `KeyError` naming the tuple, rather than silently omitting it.
    """
    probe_tokens, _ = _probe_classification(stream)

    try:
        boundary = stream.commit_index("rolling_back")
    except KeyError:
        boundary = len(stream.events)

    # An anchor insert is a single-unit entry `insert` that leaves its token with a
    # SECOND live name -- `index_after`'s discriminator, applied here too. Shape alone
    # is not enough since design §4.1's creation-mode ruling made a creation a
    # single-unit entry insert as well (it used to carry a paired `meta mode` unit, and
    # a two-unit event was excluded from this scan for free): without the second-name
    # test, every `create_exclusive`/`mkdir_child` token would register as an anchor,
    # and every work->live publication transfer would then match the tuple shape --
    # measured, `minimal-create` grew a spurious `anchor-only-forward`.
    anchor_tokens: set[int] = set()
    live: dict[tuple[int, str], int] = dict(stream.seed_entries)
    names: dict[int, set[tuple[int, str]]] = {}
    for entry_key, token in live.items():
        names.setdefault(token, set()).add(entry_key)
    for event in stream.events:
        if type(event) is not Mutation:
            continue
        for unit in event.units:
            if unit.key[0] != "entry":
                continue
            entry_key = (unit.key[1], unit.key[2])
            displaced = live.pop(entry_key, None)
            if displaced is not None:
                names[displaced].discard(entry_key)
            if unit.change in ("insert", "replace") and unit.object_token is not None:
                live[entry_key] = unit.object_token
                names.setdefault(unit.object_token, set()).add(entry_key)
        if len(event.units) != 1:
            continue
        unit = event.units[0]
        if unit.key[0] != "entry" or unit.change != "insert" or unit.object_token is None:
            continue
        if unit.key[1] in probe_tokens:
            continue
        if len(names.get(unit.object_token, ())) > 1:
            anchor_tokens.add(unit.object_token)

    result: dict[str, Cell] = {}
    for index, event in enumerate(stream.events):
        if type(event) is not Mutation or len(event.units) != 2:
            continue
        by_change = {unit.change: unit for unit in event.units}
        if set(by_change) != {"remove", "insert"}:
            continue
        remove_unit, insert_unit = by_change["remove"], by_change["insert"]
        if remove_unit.key[0] != "entry" or insert_unit.key[0] != "entry":
            continue
        if remove_unit.key[1] in probe_tokens or insert_unit.key[1] in probe_tokens:
            continue
        if remove_unit.object_token != insert_unit.object_token:
            continue
        if remove_unit.object_token not in anchor_tokens:
            continue

        direction = "forward" if index < boundary else "reverse"
        cut = index + 1
        base = durable_state(stream, cut)

        dual_survivors = frozenset({insert_unit.key})
        dual = apply_survivors(base, stream, cut, dual_survivors)
        if isinstance(dual, Skip):
            raise KeyError(f"dual-name-{direction}")
        result[f"dual-name-{direction}"] = Cell(
            cut, cast("frozenset[UnitKey]", dual_survivors), dual
        )

        anchor_survivors = frozenset({remove_unit.key})
        anchor = apply_survivors(base, stream, cut, anchor_survivors)
        if isinstance(anchor, Skip):
            raise KeyError(f"anchor-only-{direction}")
        result[f"anchor-only-{direction}"] = Cell(
            cut, cast("frozenset[UnitKey]", anchor_survivors), anchor
        )

    return result


def complete_named_cell(cell: Cell, stream: Stream) -> Cell:
    """Complete a bare `apply_survivors`-built cell into a physically runnable one.

    `named_tuples` builds its four cells through `apply_survivors` with a single chosen
    *entry* key, so every other pending key at that cut is dropped. Design §9.4 defines
    those tuples purely by which **names** are live -- dual-name keeps the transfer's
    insert, anchor-only keeps its remove -- and says nothing about the modes and byte
    images of unrelated objects, which the bare cell would silently drop along with
    everything else: unwritten file content, and every pending mode *change*.

    So the entry topology is left exactly as the tuple names it -- no other `entry` key
    is ever folded in, since that is precisely what distinguishes the two tuples -- and
    every non-`entry` pending key, plus the probe-noise keys `enumerate_cells` folds
    anyway, is offered to the same `_fold` pass the sweep uses. Two survivor
    vocabularies, one physical cell, one applicability engine.

    History worth keeping: before design §4.1's creation-mode ruling (2026-08-15) a
    dropped *creation* mode unit reconstructed its object at mode `0`, and both
    `minimal-move` tuples then refused the lease on an inaccessible
    `metadata/work/<txid>` instead of performing the repair their directed tests exist
    to observe. The ruling removed that failure mode at the source; this completion is
    still required for the tears that remain.
    """
    probe_tokens, token_parents = _probe_classification(stream)
    positioned = _pending_with_positions(stream, cell.cut)
    optional = frozenset(
        key
        for key in pending_keys_at(stream, cell.cut)
        if key not in cell.survivors
        and (key[0] != "entry" or _is_probe_noise(key, probe_tokens, token_parents))
    )
    base = durable_state(stream, cell.cut)
    mandatory = frozenset(cell.survivors)
    reason, accepted = _fold(stream, base, positioned, mandatory, optional)
    if reason is not None:
        raise KeyError(f"a named cell at cut {cell.cut} is unrepresentable: {reason}")
    survivors = mandatory | accepted
    state = _apply_chosen(base, stream, cell.cut, positioned, survivors)
    if isinstance(state, Skip):
        raise KeyError(f"a named cell at cut {cell.cut} skipped: {state.reason}")
    return Cell(cell.cut, cast("frozenset[UnitKey]", survivors), state)


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
                # `_backup_db` forced the file-format-version bytes back to 1 so the
                # image could be deserialized at all; a *reconstructed* store must be
                # what the live one was -- WAL. `open_database` refuses a completed
                # store in any other journal mode ("converting it would rewrite a
                # database on a guess"), so restoring the recorded mode here is part of
                # reconstruction fidelity, not a repair the engine is being spared.
                mode = connection.execute("PRAGMA journal_mode=wal").fetchone()[0]
                assert mode == "wal", f"reconstructed store would not take WAL: {mode!r}"
            finally:
                connection.close()
        finally:
            memory.close()


def realign_durable_identities(project_root: Path, metadata_root: Path) -> int:
    """Rewrite every durable `approval_evidence` identity onto the reconstructed inodes.

    Design §4.4 claims reconstruction is "exact up to inode renaming, ... safe because
    nothing durable stores inode numbers". That claim is **wrong about one durable
    document**: `transaction_record.approval_evidence`
    (`atoms.fs.approval.encode_approval_evidence`) pins `(st_dev, st_ino)` for every
    *existing* approved directory and for the work base. Recovery's first act on a live
    record is `_diff_approved_topology` (`coordinator/recover.py:402`), which compares
    those numbers against the live world and, on any difference, persists an
    `AssemblyHalt(APPROVAL_EVIDENCE_MISMATCH)` and raises -- **before**
    `classify_recovery` is ever reached. Reconstruction mints fresh inodes by
    construction, so without this step every record-bearing cell halts on an artifact of
    reconstruction and the A3 agreement matrix asserts nothing at all (measured: 42 of
    `minimal-create`'s 103 cells, and every cell that ever reached a durable record).

    So the renaming §4.4 assumes is performed here, at the one place identity is durable:
    each identity-bearing directory's `(st_dev, st_ino)` is replaced by the reconstructed
    object's real one. A declared directory that is *absent* from the reconstructed world
    keeps its recorded identity -- an absent approved directory is a genuine finding the
    matrix must keep seeing, not a renaming. Planned directories (`identity: null`) and
    `mount_id` are untouched: the former carry no identity, and the latter is a real fact
    about the volume, identical because reconstruction stays on it.

    Returns the number of identities rewritten, so a caller can assert the step was not
    a silent no-op.
    """
    from atoms.fs.bootstrap import WORK_DIRECTORY

    db_path = Path(metadata_root) / "atoms.db"
    if not db_path.exists():
        return 0

    def identity_of(path: Path) -> dict[str, int] | None:
        try:
            info = path.lstat()
        except OSError:
            return None
        return {"st_dev": info.st_dev, "st_ino": info.st_ino}

    # `trg_evidence_write_once` (`atoms.store.schema`) aborts any UPDATE of the column,
    # and `classify` compares the whole catalog -- including this trigger's exact SQL
    # text -- against `EXPECTED_CATALOG` before the store opens. So the trigger is
    # dropped and recreated from the production statement itself, never from a
    # hand-copied spelling that could drift from it.
    from atoms.store.schema import SCHEMA_STATEMENTS

    trigger = next(
        statement
        for statement in SCHEMA_STATEMENTS
        if "trg_evidence_write_once" in statement
    )

    rewritten = 0
    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute(
            "SELECT txid, approval_evidence, assembly_halt FROM transaction_record"
        ).fetchall()
        for txid, evidence, stored_halt in rows:
            # `require_assembly_halt_binding` (`store/records.py:456`) demands
            # `halt.expected == record.approval_evidence`, so rewriting the evidence
            # under a stored halt would break that binding and surface as a
            # `ProtocolError` from deep inside recovery -- read as an engine bug, not as
            # the model's doing. Failing loud here is preferred over silently rewriting
            # `halt.expected` too: no exerciser scenario records a durable assembly halt
            # (measured: none, before or after the creation-mode ruling), and a future
            # sabotage arm that deliberately plants one must be *seen* by the model
            # rather than quietly repaired by it.
            assert stored_halt is None, (
                f"transaction_record {txid} carries a durable assembly halt; realigning "
                "its approval evidence would break the halt binding"
            )
            document = json.loads(evidence)
            work_base = Path(metadata_root) / WORK_DIRECTORY
            work_root = document["work_root"]
            if work_root is not None:
                found = identity_of(work_base)
                if found is not None and found != work_root["identity"]:
                    work_root["identity"] = found
                    rewritten += 1
            for item in document["directories"]:
                if item["identity"] is None:
                    continue
                relative = item["path"]
                target = (
                    work_base / txid
                    if relative is None
                    else Path(project_root) / relative
                    if relative
                    else Path(project_root)
                )
                found = identity_of(target)
                if found is None or found == item["identity"]:
                    continue
                item["identity"] = found
                rewritten += 1
            replacement = json.dumps(
                document, ensure_ascii=False, separators=(",", ":"), sort_keys=True
            )
            if replacement == evidence:
                continue
            connection.execute("DROP TRIGGER trg_evidence_write_once")
            try:
                connection.execute(
                    "UPDATE transaction_record SET approval_evidence = ? WHERE txid = ?",
                    (replacement, txid),
                )
            finally:
                connection.execute(trigger)
        connection.commit()
    finally:
        connection.close()
    return rewritten


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


# --- per-cell recovery, the A3 agreement, and the side assertions (design §5) -------


@dataclass(frozen=True)
class CellResult:
    """One cell's verdict.

    `agrees` is the A3 comparison's answer, and is `True` when the comparison did not
    apply (no durable record, or a refusal that never reached the classifier) -- read
    `counts["classified"]` to tell "agreed" from "not compared". `halted` is true when
    the lease entry ended in `TransactionHalted`, which is *not* a disagreement: design
    §5's "cells where A3 halts assert the halt agrees and is preserved".

    `projection`/`model_projection` are the durable and reduced projections (design §5's
    widened shape: state, commit decision, rollback result, halt diagnostic, journals,
    active). `world` is the post-recovery tree in `WorldState.tree`'s shape, consumed by
    Task 8's directed repair assertions. `counts` carries the per-cell taxonomy.

    The three failure fields exist because the sweep *reports* rather than stopping at
    the first bad cell: a single failing cell says almost nothing about whether the
    disagreement is systematic, and the parametrized sweep test asserts the aggregated
    tuples are empty, which is the same assertion with a far better failure message.
    """

    agrees: bool
    halted: bool
    projection: tuple | None
    model_projection: tuple | None
    world: WorldTree
    counts: dict[str, int]
    disagreement: str | None = None
    second_pass_violation: str | None = None
    side_assertion_failures: tuple[str, ...] = ()


def world_tree(project_root: str | Path, metadata_root: str | Path) -> WorldTree:
    """The live world in `WorldState.tree`'s shape (design §4.4/§9), for comparison
    against a modelled tree and for Task 8's directed assertions.

    Two deliberate differences from `_materialize_tree`'s modelled trees, both so a live
    tree and a modelled one can be compared after `_normalized_tree`:

    - a hard-link group is keyed by its lexicographically first member path rather than
      by a model token, because inode numbers are exactly what reconstruction does not
      preserve (design §4.4) while the *relation* is;
    - a file whose durable mode denies reading carries `None` for its content, matching
      `world_digest`'s `_UNREADABLE` marker rather than raising.
    """
    tree: WorldTree = {}
    groups: dict[tuple[int, int], list[str]] = {}
    for label, root in (("project", Path(project_root)), ("metadata", Path(metadata_root))):
        for path, info in _walk_lstat(root):
            if path.parent == root and path.name in DB_FAMILY:
                continue
            rel = f"{label}/{path.relative_to(root).as_posix()}"
            if stat.S_ISLNK(info.st_mode):
                tree[rel] = ("symlink", os.readlink(path))
            elif stat.S_ISDIR(info.st_mode):
                tree[rel] = ("dir", stat.S_IMODE(info.st_mode))
            else:
                try:
                    content: bytes | None = path.read_bytes()
                except PermissionError:
                    content = None
                tree[rel] = ("file", content, stat.S_IMODE(info.st_mode))
                groups.setdefault((info.st_dev, info.st_ino), []).append(rel)

    for members in groups.values():
        if len(members) < 2:
            continue
        key = ("link-group", min(members))
        for rel in members:
            kind, content, mode = tree[rel]
            tree[rel] = (kind, content, mode, key)
    return tree


def _normalized_tree(tree: WorldTree) -> WorldTree:
    """Rewrite every hard-link-group key to the group's first member path.

    `_materialize_tree` keys a group by its model token and `world_tree` by a real
    inode pair's first member; neither number survives comparison across a
    reconstruction, and the relation both encode -- "these paths are one inode" -- does.
    """
    members: dict[tuple, list[str]] = {}
    for rel, value in tree.items():
        if value[0] == "file" and len(value) == 4:
            members.setdefault(value[3], []).append(rel)
    renamed = {key: ("link-group", min(paths)) for key, paths in members.items()}
    return {
        rel: (value if value[0] != "file" or len(value) != 4 else (*value[:3], renamed[value[3]]))
        for rel, value in tree.items()
    }


def inaccessible_paths(tree: WorldTree) -> tuple[str, ...]:
    """Every path in `tree` whose durable mode denies the owner the access the engine
    needs: read+search on a directory, read on a file (design §4.3's independent
    `meta mode` axis is what puts them there)."""
    def denied(value: tuple) -> bool:
        if value[0] == "dir":
            return value[1] & 0o500 != 0o500
        return value[0] == "file" and not value[2] & 0o400

    return tuple(rel for rel, value in sorted(tree.items()) if denied(value))


def _record_txids(metadata_root: Path) -> tuple[str, ...]:
    """Every durable `transaction_record` txid, read outside the lock.

    `Store` exposes `read_record(txid)`/`read_active()` but no listing, and a cell whose
    recovery detached the record still needs its txid to project. Read directly, exactly
    as `tests/exerciser.py`'s `_latest_txid` does.
    """
    db_path = Path(metadata_root) / "atoms.db"
    if not db_path.exists():
        return ()
    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute(
            "SELECT txid FROM transaction_record ORDER BY rowid"
        ).fetchall()
    finally:
        connection.close()
    return tuple(row[0] for row in rows)


def _chain_facts(backend, binding) -> dict:
    """Validate the chain and pair its registrations with its settlements (design §5's
    "chain registration/settlement ... asserted by separate end-to-end checks", the
    thing A3 deliberately does not model). Reuses `atoms.chain.read.validate_chain`, so
    a linearization, digest-name, or foreign-leaf fault raises from production code."""
    from atoms.chain.model import RegisteredEntry, SettledEntry
    from atoms.chain.read import validate_chain
    from atoms.core.scratch import CHAIN_LEAF

    try:
        chain_fd = backend.open_child_directory(binding.project_root_fd, CHAIN_LEAF)
    except FileNotFoundError:
        return {"present": False, "failures": (), "entries": 0, "survivors": 0}
    try:
        validated = validate_chain(backend, chain_fd)
    finally:
        backend.close_fd(chain_fd)

    registrations = {
        digest: entry.txid
        for digest, entry in validated.entries
        if type(entry) is RegisteredEntry
    }
    failures: list[str] = []
    settlements: dict[str, str] = {}
    for digest, entry in validated.entries:
        if type(entry) is not SettledEntry:
            continue
        settlements[digest] = entry.txid
        if entry.registration not in registrations:
            failures.append(
                f"settlement {digest} names registration {entry.registration}, "
                "which is not a durable chain entry"
            )
        elif registrations[entry.registration] != entry.txid:
            failures.append(
                f"settlement {digest} for txid {entry.txid} pairs with a registration "
                f"for txid {registrations[entry.registration]}"
            )
    return {
        "present": True,
        "failures": tuple(failures),
        "entries": len(validated.entries),
        "survivors": len(validated.survivors),
        "registrations": registrations,
        "settlements": settlements,
    }


def _inspect(project_root: Path, metadata_root: Path, storage, allowlist) -> dict:
    """The canonical durable projection plus the facts A3 does not model, read through
    ONE fresh binding: `test_coordinator_conformance.py`'s `_durable_projection` idiom
    widened with `halt_diagnostic` (design §5), the chain, the workspace slots, and the
    unindexed blobs.

    One binding, not three: the project lock is exclusive, so a chain read taken under
    its own lock could not describe the same instant as the projection, and three lock
    acquisitions per cell would triple the sweep's cost for no added truth.
    """
    from atoms.fs.audit import AuditedBackend
    from atoms.fs.binding import bind_project_volume
    from atoms.fs.linux import LinuxBackend
    from atoms.fs.lock import acquire_project_lock
    from atoms.store.connection import open_store

    txids = _record_txids(metadata_root)
    backend = AuditedBackend(
        LinuxBackend(), project_root=str(project_root), metadata_root=str(metadata_root)
    )
    with acquire_project_lock(backend, str(metadata_root)) as lock, bind_project_volume(
        str(project_root), lock, allowlist=allowlist, storage=storage
    ) as binding, open_store(binding) as store:
        records = {txid: store.read_record(txid) for txid in txids}
        active = store.read_active()
        workspaces = store.list_workspaces()
        unindexed = store.list_unindexed_blobs()
        chain = _chain_facts(binding.backend, binding)

    record = records[txids[-1]] if txids else None
    projection = (
        None
        if record is None
        else (
            record.state,
            record.committed,
            record.rollback_result,
            record.halt_diagnostic,
            record.journals,
            active is not None,
        )
    )
    return {
        "txid": None if record is None else record.txid,
        "record": record,
        "projection": projection,
        "active": active is not None,
        "workspaces": workspaces,
        "unindexed_blobs": unindexed,
        "chain": chain,
    }


def durable_projection(project_root, metadata_root, storage, allowlist) -> dict | None:
    """`_inspect`'s canonical projection, serialized -- the one reader both children use.

    `tests/coordinator_child.py` (the recovery placement) and `tests/execute_child.py`
    (the whole-cell placement) both call this rather than each growing its own projection
    reader, and the serializer is `tests/exerciser.py`'s, shared with the in-process
    `_durable_projection` -- so "the projection" means one thing in every process the
    matrix runs code in, and the dependency runs one way (this module already builds on
    the exerciser).
    """
    from tests.exerciser import serialize_projection

    return serialize_projection(
        _inspect(Path(project_root), Path(metadata_root), storage, allowlist)["projection"]
    )


def _model_projection(snapshot, plan) -> tuple:
    """A3's fixed point in the durable projection's shape (design §5)."""
    from atoms.core.recovery import apply_recovery_plan

    reduced = apply_recovery_plan(snapshot, plan)
    return (
        reduced.transaction_state,
        reduced.commit_decision,
        reduced.rollback_result,
        reduced.halt_diagnostic,
        reduced.journals,
        reduced.active,
    )


class _Refused(Exception):
    """A lease entry the reconstructed world's own durable modes made impossible.

    A reconstructed world can hold an object whose durable mode denies the engine the
    access it needs -- a mode-`0` workspace, chain-staging file, or effect-scratch file.
    The engine meets those with an access refusal (`PermissionError`, or a typed
    `ChainStateInvalid`/`MetadataStoreInvalid` naming the unreadable leaf) rather than a
    recovery plan, and never reaches `classify_recovery`.

    **Post-ruling scope (2026-08-15).** This used to be ~48% of every sweep, because a
    creation's mode was an independently tearing unit and half the product dropped it.
    Design §4.1's creation-mode ruling deleted that class outright, and the bucket now
    measures **0 on every minimal scenario**. What can still reach it is a mode *change*
    to a denying mode (`set_mode`/`repair_entry_mode` are the only remaining sources) --
    which no current scenario performs. The machinery is kept rather than deleted
    because it is the guard that turns such a world into an explicit, explained outcome
    instead of a raw traceback out of a sweep; if `refused_cells` starts counting again,
    a mode-changing scenario (or a regression) is what put it there.
    Such a cell still carries a real obligation -- the refusal must be deterministic and
    must not mutate the world further -- which is what `run_cell` asserts for it. A
    refusal that NO inaccessible object in the reconstructed world explains is a
    side-assertion failure, not an accepted outcome: `run_cell` checks the explanation
    rather than trusting the exception type.
    """


def _enter_lease(
    project_root: Path, metadata_root: Path, storage, monkeypatch, captured, allowlist
):
    """One fresh lease entry -- the real composition root, recovery running at entry.

    The spy is installed on `atoms.coordinator.recover.classify_recovery`
    (`recover.py:59` binds the name at import from `atoms.core.recovery`, and
    `recover.resolve` calls it once per resolution at `recover.py:712`), which is the
    lease-entry recovery's classification point -- `commit.py`/`execute.py` hold their
    own bindings for the originating-command routes, and patching those would capture
    nothing here.

    `CERTIFIED_ALLOWLIST` is patched here too, not left to whatever an earlier recording
    happened to leave installed: `root.py` is the single production bind call site
    (ledger #18) and it ships empty, so a lease entry that did not patch it would refuse
    every volume -- and one that relied on a *leaked* patch would silently depend on the
    recording's roots still being the same mount.
    """
    from atoms.chain.errors import ChainStateInvalid
    from atoms.coordinator import recover, root
    from atoms.core.errors import TransactionHalted
    from atoms.fs.linux import LinuxBackend
    from atoms.store.errors import MetadataStoreInvalid

    with monkeypatch.context() as patched:
        classify = recover.classify_recovery

        def capture(snapshot):
            plan = classify(snapshot)
            captured.append((snapshot, plan))
            return plan

        patched.setattr(recover, "classify_recovery", capture)
        patched.setattr(root, "CERTIFIED_ALLOWLIST", allowlist)
        try:
            with root._recovery_lease(
                LinuxBackend(), str(project_root), str(metadata_root), storage
            ):
                pass
        except TransactionHalted as halted:
            return "halted", halted
        except PermissionError as refused:
            raise _Refused(f"{type(refused).__name__}: {refused}") from refused
        except OSError as refused:
            if refused.errno not in {errno.EACCES, errno.EPERM}:
                raise
            raise _Refused(f"{type(refused).__name__}: {refused}") from refused
        except (ChainStateInvalid, MetadataStoreInvalid) as refused:
            raise _Refused(f"{type(refused).__name__}: {refused}") from refused
    return "resolved", None


def _engine_owned(rel: str) -> bool:
    """True for a path the engine owns and may reclaim at any lease entry: capability
    probing's scratch, the workspace slots, the blob store (`_reclaim_orphans` removes
    every unindexed blob at every entry), and any reserved scratch leaf. What is left is
    the *external* world -- the state design §5 requires recovery to preserve."""
    from atoms.core.scratch import is_engine_reserved_leaf
    from atoms.fs.bootstrap import PROBE_DIRECTORY
    from atoms.store.blobs import BLOBS_DIRECTORY
    from atoms.store.workspace import STAGING_PARENT, WORK_PARENT

    # The metadata root's own furniture: the layout directories every lease entry
    # (re)creates and verifies (`ensure_metadata_layout`), and the lock file
    # `acquire_project_lock` opens or creates (`fs/lock.py:217`). A lease entered over a
    # world that predates them creates them -- correctly -- so they are engine-owned in
    # both directions of the external-state comparison, never "spurious external state".
    owned = (PROBE_DIRECTORY, STAGING_PARENT, WORK_PARENT, BLOBS_DIRECTORY, LOCK_LEAF)
    if rel.startswith(tuple(f"metadata/{name}" for name in owned)):
        return True
    return any(is_engine_reserved_leaf(part) for part in rel.split("/")[1:])


def _scratch_survivors(tree: WorldTree) -> tuple[str, ...]:
    """Every surviving generated scratch leaf `.#~<txid>.<effect>.<role>` (the closed
    grammar of `atoms.core.scratch.is_scratch_leaf`) -- what a terminal state must have
    reclaimed."""
    from atoms.core.scratch import is_scratch_leaf

    return tuple(
        rel
        for rel in sorted(tree)
        if any(is_scratch_leaf(part) for part in rel.split("/")[1:])
    )


def _occupied_slots(tree: WorldTree) -> tuple[str, ...]:
    """Every surviving entry *inside* a workspace slot (`metadata/staging/<txid>/...`,
    `metadata/work/<txid>/...`).

    The slot directories themselves legitimately outlive a terminal recovery: the only
    reclamation rule is `_reclaim_orphans`' "no `transaction_record` row names this
    txid" (`coordinator/lease.py:24`, ledger #23), and a settled transaction keeps its
    row forever. What design §5 requires is that the slots are *empty* -- every staged
    blob promoted or dropped, every work-slot scratch published or removed.
    """
    from atoms.store.workspace import STAGING_PARENT, WORK_PARENT

    slots = (f"metadata/{STAGING_PARENT}/", f"metadata/{WORK_PARENT}/")
    return tuple(
        rel for rel in sorted(tree) if rel.startswith(slots) and rel.count("/") > 2
    )


def _external_differences(before: WorldTree, after: WorldTree) -> tuple[str, ...]:
    """Every way `after`'s external (non-engine-owned) state differs from `before`'s --
    in **both** directions.

    Used for the no-record cells, whose whole obligation is that recovery reclaims
    engine scratch and touches nothing else. One direction is not enough: a recovery
    that faithfully preserved every prior path while *creating* a spurious one (an
    un-reclaimed staged file promoted into the project, a restored tombstone) would pass
    a survivors-only check while having invented external state out of a world with no
    durable record to authorize it.
    """
    left, right = _normalized_tree(before), _normalized_tree(after)
    lost = [
        f"lost {rel}"
        for rel, value in sorted(left.items())
        if not _engine_owned(rel) and right.get(rel) != value
    ]
    created = [
        f"created {rel}"
        for rel in sorted(right)
        if not _engine_owned(rel) and rel not in left
    ]
    return tuple(lost + created)


def cell_roots(ext4_volume: Path, slot: str) -> tuple[Path, Path]:
    """The roots `run_cell(slot=...)` reconstructs into, named here rather than spelled
    twice: a caller that passes `retain=True` needs to find them afterwards."""
    return Path(ext4_volume) / f"{slot}-project", Path(ext4_volume) / f"{slot}-metadata"


def _discard_roots(*roots: Path) -> None:
    """Remove a finished cell's reconstructed trees.

    Task 7's compound sweeps run ~700 cells; keeping every cell's two trees would leave
    ~1,400 of them (each with its own `atoms.db`) in one temporary volume for the whole
    session. Everything the sweep asserts on is already in memory by the time this runs:
    the projections, the `world` tree, and the digests.

    A durable mode can still deny the walk `rmtree` needs -- rarer since design §4.1's
    creation-mode ruling, but a `set_mode` change to `0` is still representable -- so a
    permission failure retries once with every directory opened up. Cleanup never fails
    a cell: the assertions are complete before it is called.
    """
    for root in roots:
        try:
            shutil.rmtree(root)
        except PermissionError:
            for parent, directories, _files in os.walk(root):
                for name in directories:
                    with contextlib.suppress(OSError):
                        os.chmod(os.path.join(parent, name), 0o700)
            shutil.rmtree(root, ignore_errors=True)


def run_cell(
    cell: Cell,
    stream: Stream,
    ext4_volume: Path,
    storage,
    monkeypatch,
    *,
    slot: str,
    allowlist,
    drift=None,
    retain: bool = False,
) -> CellResult:
    """Reconstruct one cell, recover it through the real composition path, and judge it
    against A3 (design §5).

    The steps, in order:

    1. reconstruct the cell's world into fresh roots under `ext4_volume`, then realign
       the durable approval-evidence identities onto it (`realign_durable_identities` --
       without which every record-bearing cell halts on an artifact of reconstruction);
    2. plant the scenario's external drift, if this family asked for it;
    3. enter a fresh `root._recovery_lease` under the test allowlist with a capturing
       spy on `atoms.coordinator.recover.classify_recovery` -- recovery runs at entry;
    4. read the canonical durable projection, widened with `halt_diagnostic`;
    5. compute A3's own fixed point over the captured `(snapshot, plan)` and compare;
    6. enter a **second** fresh lease and require the canonical projection and the world
       digest to be unchanged -- canonical projection equality, never SQLite byte
       equality (design §5: "not identical physical SQLite bytes");
    7. run the side assertions A3 does not model: the chain parses with its
       registration/settlement pairing intact, no generated scratch leaf survives a
       terminal state, the workspace slots are empty, and no unindexed blob remains.

    A cell with **no durable record** (a cut before the `prepared` commit) skips the A3
    comparison -- there is nothing to classify -- and instead asserts the store is empty,
    every external path survives untouched, and the engine's own scratch is reclaimed.

    A cell whose reconstructed world denies the engine access to one of its own leaves
    (`_Refused`) asserts determinism only: the second pass must refuse identically and
    leave the world byte-identical, and an unexplained refusal is a side-assertion
    failure.

    The reconstructed roots are removed on the way out unless `retain=True`, which a
    caller that wants to inspect the finished world through the store itself (the
    directed absent-directory test) passes.

    `named_tuples`' cells arrive here too, from the other survivor vocabulary
    (`apply_survivors` on a bare chosen key, rather than `enumerate_cells`' folded set).
    Nothing below reads `cell.survivors` -- a `Cell` is a `Cell` -- so both run
    identically; `complete_named_cell` is what makes the bare one physically runnable.
    """
    from atoms.core.recovery import HaltPlan, TransactionState

    project_root, metadata_root = cell_roots(ext4_volume, slot)
    project_root.mkdir()
    metadata_root.mkdir()
    reconstruct(cell.state, project_root, metadata_root)
    counts = {
        "realigned_identities": realign_durable_identities(project_root, metadata_root),
        "classified": 0,
        "halted": 0,
        "plan_halted": 0,
        "assembly_halted": 0,
        "refused": 0,
        "no_record": 0,
        "settled": 0,
        "drifted": 0,
        "drift_preserved": 0,
    }

    reconstructed = world_tree(project_root, metadata_root)
    if drift is not None:
        # A cut early enough to predate the drift target's own directory cannot be
        # drifted at all -- `_drift_delete_target` writes into `d/`, which several cells
        # legitimately do not have. That is a property of the cell, not a failure: the
        # drift family's obligation ("the external blocker survives recovery") is over
        # the cells that could carry a blocker, counted in `drift_preserved`. Only the
        # missing-target errnos are absorbed; any other failure inside a drift callable
        # is a bug in the callable and propagates.
        try:
            drift(Path(project_root))
        except (FileNotFoundError, NotADirectoryError):
            counts["drifted"] = 0
        else:
            counts["drifted"] = 1
    before = world_tree(project_root, metadata_root)
    drift_footprint = tuple(
        rel for rel, value in before.items() if reconstructed.get(rel) != value
    )
    inaccessible = inaccessible_paths(before)

    failures: list[str] = []
    captured: list[tuple] = []
    refusal: str | None = None
    halted = False
    try:
        kind, _ = _enter_lease(
            project_root, metadata_root, storage, monkeypatch, captured, allowlist
        )
        halted = kind == "halted"
    except _Refused as refused:
        refusal = str(refused)
        counts["refused"] = 1
        if not inaccessible:
            failures.append(f"unexplained refusal with every mode accessible: {refusal}")
    counts["halted"] = int(halted)
    # Two different halts reach the same exception. A3's own `HaltPlan` arrives through
    # the classifier (captured, and compared below); an *assembly* halt
    # (`_halt_for_findings`, `coordinator/recover.py:499`) is raised from the topology
    # diff BEFORE any classification, so it carries no plan to compare -- what it owes
    # is that the halt was frozen durably before it was raised.
    counts["plan_halted"] = int(halted and bool(captured))
    counts["assembly_halted"] = int(halted and not captured)
    counts["classified"] = len(captured)

    world = world_tree(project_root, metadata_root)
    digest = world_digest(project_root, metadata_root)

    if refusal is not None:
        second = None
        try:
            _enter_lease(
                project_root, metadata_root, storage, monkeypatch, [], allowlist
            )
        except _Refused as again:
            second = str(again)
        violation = (
            None
            if second == refusal and world_digest(project_root, metadata_root) == digest
            else f"first pass refused {refusal!r}; second pass gave {second!r}"
        )
        if not retain:
            _discard_roots(project_root, metadata_root)
        return CellResult(
            agrees=True,
            halted=False,
            projection=None,
            model_projection=None,
            world=world,
            counts=counts,
            second_pass_violation=violation,
            side_assertion_failures=tuple(failures),
        )

    # `recover.resolve` classifies exactly once per resolution (`recover.py:712`), so a
    # second capture would mean the lease resolved twice inside one entry -- the spy is
    # the only place that could ever notice.
    assert len(captured) <= 1, f"classify_recovery ran {len(captured)} times in one entry"

    facts = _inspect(project_root, metadata_root, storage, allowlist)
    projection = facts["projection"]
    model_projection = _model_projection(*captured[0]) if captured else None
    disagreement: str | None = None

    if halted and not captured:
        record = facts["record"]
        if record is None or (
            record.assembly_halt is None and record.state is not TransactionState.HALTED
        ):
            failures.append("a halt was raised without a durable halt record")

    if projection is not None and not captured and not halted:
        # The fifth outcome: a durable record `resolve` had nothing to classify, because
        # it was already settled and detached before the cut. Without its own bucket
        # these cells hide inside "cells - everything else", and a regression that
        # silently stopped classifying would look like a sweep full of settled cells.
        counts["settled"] = 1
        record = facts["record"]
        assert record is not None
        if facts["active"] or record.state not in {
            TransactionState.COMMITTED,
            TransactionState.ROLLED_BACK,
        }:
            failures.append(
                f"a durable record was neither classified nor settled: state="
                f"{record.state.name} active={facts['active']}"
            )

    if projection is None:
        counts["no_record"] = 1
        if captured:
            failures.append("recovery classified a world holding no durable record")
        differences = _external_differences(before, world)
        if differences:
            failures.append(f"external state not preserved: {differences}")
    elif captured:
        if projection != model_projection:
            disagreement = (
                f"durable {projection!r} disagrees with A3's fixed point "
                f"{model_projection!r}"
            )
        plan = captured[0][1]
        if type(plan) is HaltPlan:
            record = facts["record"]
            assert record is not None
            if record.halt_diagnostic != plan.diagnostic:
                disagreement = (
                    "the persisted halt diagnostic is not the plan's: "
                    f"{record.halt_diagnostic!r} != {plan.diagnostic!r}"
                )

    if captured and drift_footprint:
        # Counted only where recovery actually classified the world: a refused or
        # record-free cell preserves the blocker by never looking at it, which is not
        # the property design §6's drift family is about. ALL of the footprint must
        # survive -- a drift that plants three paths and keeps one is not "the external
        # blocker was preserved".
        planted = _normalized_tree(before)
        settled_world = _normalized_tree(world)
        counts["drift_preserved"] = int(
            all(settled_world.get(rel) == planted.get(rel) for rel in drift_footprint)
        )

    # --- side assertions A3 does not model (design §5) ------------------------------
    chain = facts["chain"]
    failures.extend(chain["failures"])
    record = facts["record"]
    if chain["present"] and record is not None:
        if record.registration_digest is not None and (
            record.registration_digest not in chain["registrations"]
        ):
            failures.append(
                f"record registration digest {record.registration_digest} is not a "
                "durable chain entry"
            )
        if record.settlement_digest is not None and (
            record.settlement_digest not in chain["settlements"]
        ):
            failures.append(
                f"record settlement digest {record.settlement_digest} is not a "
                "durable chain entry"
            )
    terminal = not facts["active"] and not halted
    if terminal:
        survivors = _scratch_survivors(world)
        if survivors:
            failures.append(f"scratch survived a terminal state: {survivors}")
        occupied = _occupied_slots(world)
        if occupied:
            failures.append(f"workspace slots are not empty: {occupied}")
        if chain["present"] and chain["survivors"]:
            failures.append("a chain staging survivor outlived a terminal state")
    if facts["unindexed_blobs"]:
        failures.append(f"unindexed blobs survived reclamation: {facts['unindexed_blobs']}")

    # --- the mandatory second pass (design §5) --------------------------------------
    violation: str | None = None
    try:
        _enter_lease(project_root, metadata_root, storage, monkeypatch, [], allowlist)
    except _Refused as refused:
        violation = f"the second pass refused where the first resolved: {refused}"
    if violation is None:
        again = _inspect(project_root, metadata_root, storage, allowlist)
        if again["projection"] != projection:
            violation = (
                f"the second pass moved the projection: {projection!r} -> "
                f"{again['projection']!r}"
            )
        elif world_digest(project_root, metadata_root) != digest:
            violation = "the second pass moved the world"

    if not retain:
        _discard_roots(project_root, metadata_root)
    return CellResult(
        agrees=disagreement is None,
        halted=halted,
        projection=projection,
        model_projection=model_projection,
        world=world,
        counts=counts,
        disagreement=disagreement,
        second_pass_violation=violation,
        side_assertion_failures=tuple(failures),
    )


def run_cell_in_a_fresh_process(
    cell: Cell,
    ext4_volume: Path,
    *,
    slot: str,
    drift=None,
) -> dict:
    """Reconstruct one cell and run its **first** recovery in a fresh process.

    Steps 1 and 2 of `run_cell` are performed identically -- reconstruct, then
    `realign_durable_identities` before anything recovers, then the family's drift -- and
    the lease entry that follows is `tests/coordinator_child.py`, the same child the kill
    matrix's `_recover` spawns, rather than an in-process `root._recovery_lease`. The
    child builds its own allowlist from its own roots, so nothing about the parent's
    patched module state can leak into the placement being measured; that is the point of
    the arm.

    Returns the child's parsed JSON (`halted`, `projection`, `lease`, `durable`). The
    caller compares the `projection` against the in-process cell's, serialized by the
    same `serialize_projection`. The reconstructed roots are always discarded: the child
    already read everything the comparison needs.

    The two child config keys are this arm's alone: `projection` (the document to
    compare) and `torn_blobs` (a reconstructed cut can carry a durable `blob` row whose
    bytes were still pending, which is a defect anywhere else and a fact here).
    """
    from tests.test_coordinator_kill_matrix import _recover

    project_root, metadata_root = cell_roots(ext4_volume, slot)
    project_root.mkdir()
    metadata_root.mkdir()
    reconstruct(cell.state, project_root, metadata_root)
    realign_durable_identities(project_root, metadata_root)
    if drift is not None:
        # Same carve-out as `run_cell`: a cut predating the drift target's own directory
        # simply runs undrifted.
        with contextlib.suppress(FileNotFoundError, NotADirectoryError):
            drift(Path(project_root))
    try:
        return _recover(
            project_root, metadata_root, {"projection": True, "torn_blobs": True}
        )
    finally:
        _discard_roots(project_root, metadata_root)


def _placement_complaints(result: CellResult, child: dict) -> tuple[str, ...]:
    """Every way the fresh-process placement of one cell disagrees with the in-process
    one (design §8).

    Three comparisons, in widening order of what they would catch:

    - the **halt** verdict: both placements must either raise `TransactionHalted` at
      lease entry or neither must;
    - the **persisted halt diagnostic**, byte-exactly. `encode_diagnostic` is
      token-free, so there is nothing here to alpha-rename and no reason to compare up
      to anything -- a single differing byte is a real divergence;
    - the whole canonical **projection**, up to `serialize_projection` -- state, commit
      decision, rollback result, journals, and whether the record is still active.

    The diagnostic is compared on its own as well as inside the projection so a failure
    names the halt rather than dumping the whole document twice.
    """
    from tests.exerciser import serialize_projection

    expected = serialize_projection(result.projection)
    complaints: list[str] = []
    if bool(child["halted"]) != result.halted:
        complaints.append(
            f"the child {'halted' if child['halted'] else 'resolved'} where the "
            f"in-process placement {'halted' if result.halted else 'resolved'}"
        )
    left = None if expected is None else expected["halt_diagnostic"]
    right = None if child["projection"] is None else child["projection"]["halt_diagnostic"]
    if left != right:
        complaints.append(
            f"the persisted halt diagnostic differs across placements: {left!r} != {right!r}"
        )
    if child["projection"] != expected:
        complaints.append(
            f"the child's projection {child['projection']!r} disagrees with the "
            f"in-process {expected!r}"
        )
    return tuple(complaints)


@dataclass(frozen=True)
class SweepReport:
    """One scenario's sweep. `cells`/`deduped`/`skips` come from `enumerate_cells`'
    accounting; the three failure tuples are empty on a healthy sweep and name the cell
    and the fault when they are not. `subprocess_cells`/`subprocess_disagreements`
    (Task 7's placement axis) and `designated_failures` (Task 9's sabotage arms) are
    created here and filled there."""

    cells: int
    deduped: int
    skips: dict[str, int]  # a copy: a frozen report must not alias the accounting's dict
    disagreements: tuple[str, ...]
    second_pass_violations: tuple[str, ...]
    side_assertion_failures: tuple[str, ...]
    named_tuple_cells_ran: int
    preserved_drift_cells: int
    subprocess_cells: int
    subprocess_disagreements: tuple[str, ...]
    designated_failures: tuple[str, ...]
    # How many of `subprocess_cells` halted. The placement arm's sharpest assertion is
    # that the persisted halt diagnostic compares byte-exactly across processes, and a
    # scenario whose cells never halt asserts that vacuously -- so the count is reported
    # and a directed test requires it to be nonzero where halts are expected.
    subprocess_halt_cells: int = 0
    classified_cells: int = 0
    halted_cells: int = 0
    plan_halted_cells: int = 0
    settled_cells: int = 0
    refused_cells: int = 0
    no_record_cells: int = 0
    seconds: float = 0.0


@dataclass(frozen=True)
class NamedCell:
    """One of design §9.4's named tuples, already run. Construction asserts the cell was
    clean, so a directed test reads `result` without re-checking the sweep's invariants."""

    scenario: str
    tuple_name: str
    cell: Cell
    result: CellResult

    def __post_init__(self) -> None:
        assert self.result.disagreement is None, self.result.disagreement
        assert self.result.second_pass_violation is None, self.result.second_pass_violation
        assert not self.result.side_assertion_failures, self.result.side_assertion_failures


class Sweeper:
    """The `cut_matrix` fixture's object: sweep a scenario, or run one named tuple cell.

    Each recording gets its own **fresh project root**, unlike `coordinator_on`'s single
    shared one: a scenario's `seed_world` builds the same paths every time, so two
    recordings in one test (Task 7's four compound scenarios, Task 8's two directions)
    would collide in the shared root. The test allowlist is built once per fixture, off
    a throwaway root on the same volume -- it names the resolved mount tuple and the
    storage profile, never a path, so one is valid for every cell.
    """

    def __init__(self, ext4_volume: Path, storage, monkeypatch) -> None:
        self._volume = Path(ext4_volume)
        self._storage = storage
        self._monkeypatch = monkeypatch
        self._slots = itertools.count()
        self._allowlist = None

    def allowlist(self):
        if self._allowlist is None:
            from atoms.fs.linux import LinuxBackend
            from atoms.fs.lock import acquire_project_lock
            from tests.fs_support import build_test_allowlist

            root = self._volume / "allowlist-project"
            root.mkdir()
            with acquire_project_lock(
                LinuxBackend(), str(self._volume / "allowlist-metadata")
            ) as lock:
                self._allowlist = build_test_allowlist(lock, str(root), self._storage)
        return self._allowlist

    def record(self, name: str, *, caught: bool = False) -> Stream:
        """Record one scenario, then **undo the recording's patches**.

        `record_scenario` installs two of them: `_enable_commands`' allowlist, and
        `attach_store_sequencer`'s wrapper on `Store.transaction`. Left standing, that
        wrapper appends a `Commit` event -- and takes a full `_backup_db` of the
        *recording's* database -- for every store transaction any later recovery in the
        same test performs, and a second recording in one test (Task 7 sweeps four
        scenarios) would nest a second wrapper inside the first. A private
        `MonkeyPatch` undone here scopes both to the recording that needs them; the
        `Stream` it produced is plain data and outlives them.
        """
        import pytest

        from atoms.fs.linux import LinuxBackend
        from tests.exerciser import scenario

        index = next(self._slots)
        project_root = self._volume / f"record-{index}-project"
        project_root.mkdir()
        ingredients = (
            LinuxBackend(),
            str(project_root),
            str(self._volume / f"record-{index}-metadata"),
            self._storage,
        )
        recording = pytest.MonkeyPatch()
        try:
            return record_scenario(
                scenario(name), ingredients, recording, caught=caught
            )
        finally:
            recording.undo()

    def _run(self, name: str, cell: Cell, stream: Stream, drift) -> CellResult:
        return run_cell(
            cell,
            stream,
            self._volume,
            self._storage,
            self._monkeypatch,
            slot=f"{name}-{next(self._slots)}",
            allowlist=self.allowlist(),
            drift=drift,
        )

    def __call__(
        self,
        scenario_name: str,
        *,
        caught: bool = False,
        drift: bool = False,
        subprocess_subset: bool = False,
        sabotage: str | None = None,
    ) -> SweepReport:
        """Sweep one scenario: every cell of `enumerate_cells`, then §9.4's named tuples.

        `subprocess_subset=True` adds design §8's **placement axis** on top: a declared
        subset of the swept cells is reconstructed a second time and has its *first*
        recovery run in a fresh process (`run_cell_in_a_fresh_process`), and the two
        placements' canonical projections must agree.

        THE SUBSET RULE (declared, never sampled -- design §8). A cell joins the
        subprocess arm iff it is:

        1. one of design §9.4's **named-tuple cells** -- the dual-name/anchor-only worlds,
           where one inode carries two live names and a fresh process must resolve the
           same one;
        2. an **A3-halt cell** -- `classify_recovery` returned a `HaltPlan`, so the run
           persists a halt diagnostic, and the arm's sharpest assertion (the persisted
           token-free diagnostic compares byte-exactly across the placement boundary) has
           something to compare;
        3. the **first cell at or after each store-commit label** -- for every `Commit`
           event in the recorded stream, the first enumerated cell whose cut folds that
           commit into its durable base (`cut >= index + 1`). Store commits are where the
           durable record moves, so this walks the record through every state it reaches
           while spending one child per store transition rather than one per cell.
           Concretely this is almost always the **empty-survivor** cell at that cut:
           `enumerate_cells` walks cuts in order and emits each cut's combos in
           increasing size, so the first cell at or after a commit is the bare durable
           base with no pending unit surviving -- the clean "the store committed and
           nothing else landed" world, which is the right one to place a child on.

        Cells whose reconstructed world refuses the engine outright (`refused`) are
        excluded: their obligation is a deterministic *refusal*, which is asserted
        in-process, and the child would exit nonzero rather than serialize a projection.

        Everything identity-bearing stays in-process, where it is alpha-renamed:
        `realign_durable_identities` rewrites the durable approval evidence onto the
        reconstructed inodes and `_normalized_tree` rewrites hard-link-group keys. What
        crosses the process boundary is `serialize_projection`'s token-free document
        only.
        """
        from tests.exerciser import scenario

        if sabotage is not None:
            raise NotImplementedError("the sabotage arms land in Task 9")

        entry = scenario(scenario_name)
        planted = None
        if drift:
            if entry.drift is None:
                raise KeyError(f"scenario {scenario_name!r} declares no drift")
            planted = entry.drift

        started = time.monotonic()
        stream = self.record(scenario_name, caught=caught)
        cells, accounting = enumerate_cells(stream)
        named = named_tuples(stream)

        disagreements: list[str] = []
        second_pass: list[str] = []
        side: list[str] = []
        totals = {
            "classified": 0,
            "halted": 0,
            "plan_halted": 0,
            "assembly_halted": 0,
            "refused": 0,
            "no_record": 0,
            "settled": 0,
            "drift": 0,
        }

        def absorb(label: str, cell: Cell, result: CellResult) -> None:
            where = f"{scenario_name} {label} cut={cell.cut}"
            if result.disagreement is not None:
                disagreements.append(f"{where}: {result.disagreement}")
            if result.second_pass_violation is not None:
                second_pass.append(f"{where}: {result.second_pass_violation}")
            side.extend(f"{where}: {item}" for item in result.side_assertion_failures)
            totals["classified"] += min(result.counts["classified"], 1)
            totals["halted"] += result.counts["halted"]
            totals["plan_halted"] += result.counts["plan_halted"]
            totals["assembly_halted"] += result.counts["assembly_halted"]
            totals["refused"] += result.counts["refused"]
            totals["no_record"] += result.counts["no_record"]
            totals["settled"] += result.counts["settled"]
            totals["drift"] += result.counts["drift_preserved"]

        # Retained only for the placement arm, which needs each cell's verdict *after*
        # the whole sweep to select the A3-halt ones. A `CellResult` carries the cell's
        # entire post-recovery world tree, and the compound sweeps run ~300 of them, so
        # an unconditional list would hold every tree of every cell alive to the end of
        # the sweep for nothing.
        ran: list[tuple[str, Cell, CellResult]] = []
        for index, cell in enumerate(cells):
            result = self._run(scenario_name, cell, stream, planted)
            absorb(f"cell {index}", cell, result)
            if subprocess_subset:
                ran.append((f"cell {index}", cell, result))
        named_labels = set()
        for tuple_name, named_cell in sorted(named.items()):
            cell = complete_named_cell(named_cell, stream)
            result = self._run(scenario_name, cell, stream, planted)
            absorb(tuple_name, cell, result)
            named_labels.add(tuple_name)
            if subprocess_subset:
                ran.append((tuple_name, cell, result))

        subprocess_disagreements: list[str] = []
        subprocess_cells = 0
        subprocess_halts = 0
        if subprocess_subset:
            for label, cell, result in self._placement_subset(stream, ran, named_labels):
                subprocess_cells += 1
                subprocess_halts += int(result.halted)
                child = run_cell_in_a_fresh_process(
                    cell,
                    self._volume,
                    slot=f"{scenario_name}-child-{next(self._slots)}",
                    drift=planted,
                )
                subprocess_disagreements.extend(
                    f"{scenario_name} {label} cut={cell.cut}: {complaint}"
                    for complaint in _placement_complaints(result, child)
                )
            # Structural, not test-side: the subset rule is *declared*, so a rule that
            # selected nothing -- a stream with no store commit, a scenario whose cells
            # were all refused -- is a broken arm, and it must fail here rather than
            # leave every caller to remember an emptiness check.
            assert subprocess_cells > 0, (
                f"{scenario_name}: the declared placement subset selected no cell "
                f"(commits={len(stream.commits())} cells={len(cells)} "
                f"named={len(named)})"
            )

        # Every cell lands in exactly one outcome bucket, and the buckets must add back
        # up to the cells run. This is the assertion that keeps the sweep honest about
        # what it actually exercised: a regression that stopped classifying (as the
        # pre-realignment assembly halt did, silently, for *every* record-bearing cell)
        # moves cells between buckets rather than failing anything, so only a partition
        # check notices. `classified` counts cells that reached `classify_recovery`,
        # A3-halts included; `assembly_halted` are halts raised before classification.
        partition = (
            totals["classified"]
            + totals["assembly_halted"]
            + totals["refused"]
            + totals["no_record"]
            + totals["settled"]
        )
        run = len(cells) + len(named)
        if partition != run:
            side.append(
                f"{scenario_name}: outcome buckets do not partition the sweep -- "
                f"classified={totals['classified']} "
                f"assembly_halted={totals['assembly_halted']} "
                f"refused={totals['refused']} no_record={totals['no_record']} "
                f"settled={totals['settled']} sum={partition} cells={run}"
            )

        report = SweepReport(
            cells=accounting.cells + len(named),
            deduped=accounting.deduped,
            skips=dict(accounting.skips),
            disagreements=tuple(disagreements),
            second_pass_violations=tuple(second_pass),
            side_assertion_failures=tuple(side),
            named_tuple_cells_ran=len(named),
            preserved_drift_cells=totals["drift"],
            subprocess_cells=subprocess_cells,
            subprocess_disagreements=tuple(subprocess_disagreements),
            subprocess_halt_cells=subprocess_halts,
            designated_failures=(),
            classified_cells=totals["classified"],
            halted_cells=totals["halted"],
            plan_halted_cells=totals["plan_halted"],
            settled_cells=totals["settled"],
            refused_cells=totals["refused"],
            no_record_cells=totals["no_record"],
            seconds=time.monotonic() - started,
        )
        print(
            f"\n[cut-matrix] {scenario_name}: cells={report.cells} "
            f"(classified={report.classified_cells} halted={report.halted_cells} "
            f"a3-halted={report.plan_halted_cells} "
            f"refused={report.refused_cells} no-record={report.no_record_cells} "
            f"settled={report.settled_cells} "
            f"named={report.named_tuple_cells_ran} drift-preserved="
            f"{report.preserved_drift_cells}) deduped={report.deduped} "
            f"subprocess={report.subprocess_cells} "
            f"(halted={report.subprocess_halt_cells}) "
            f"skips={report.skips} in {report.seconds:.1f}s"
        )
        return report

    def _placement_subset(
        self,
        stream: Stream,
        ran: list[tuple[str, Cell, CellResult]],
        named_labels: set[str],
    ) -> list[tuple[str, Cell, CellResult]]:
        """The declared subprocess subset -- `__call__`'s docstring states the rule.

        Insertion-ordered and deduplicated by label: several store commits routinely
        select the same first-cell-after, and a named-tuple cell can also be an A3-halt
        cell, but a cell is worth exactly one child.
        """
        selected: dict[str, tuple[str, Cell, CellResult]] = {}
        enumerated = [item for item in ran if item[0] not in named_labels]
        for index, event in enumerate(stream.events):
            if type(event) is not Commit:
                continue
            for item in enumerated:
                if item[1].cut >= index + 1 and not item[2].counts["refused"]:
                    selected.setdefault(item[0], item)
                    break
        for item in ran:
            label, _cell, result = item
            if result.counts["refused"]:
                continue
            if label in named_labels or result.counts["plan_halted"]:
                selected.setdefault(label, item)
        return list(selected.values())

    def named_cell(self, scenario_name: str, tuple_name: str, *, caught: bool = False):
        stream = self.record(scenario_name, caught=caught)
        cell = complete_named_cell(named_tuples(stream)[tuple_name], stream)
        return NamedCell(
            scenario=scenario_name,
            tuple_name=tuple_name,
            cell=cell,
            result=self._run(scenario_name, cell, stream, None),
        )
