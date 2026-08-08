"""Builders shared by the A6 capture tiers.

Plain functions, not fixtures: the fixture-registry guard requires every fixture to live
in `tests/conftest.py`, and these are values a test constructs rather than resources a
test needs torn down. Same rule `tests/coordinator_support.py` follows.
"""

from __future__ import annotations

import hashlib
import io
import os

from atoms.coordinator.lease import Lease
from atoms.core.compiler import CompiledSpec, compile_spec
from atoms.core.effects import (
    CreateDirectory,
    CreateFileNoClobber,
    DeletePath,
    ReplaceFile,
)
from atoms.core.fingerprint import ABSENT, DirectoryState, FileState, SymlinkState
from atoms.core.spec import TransactionSpec, build_spec

DIRECTORY_POST = DirectoryState(mode=0o755)
BEFORE = b"before"
AFTER = b"after"


def digest_of(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def state_of(payload: bytes, mode: int = 0o644) -> FileState:
    return FileState(content_hash=digest_of(payload), mode=mode, byte_len=len(payload))


def write_project_file(
    lease: Lease, path: str, payload: bytes, mode: int = 0o644
) -> None:
    """Create a real file in project space, making its parents as needed."""
    root_fd = lease._binding.project_root_fd
    parts = path.split("/")
    for index in range(1, len(parts)):
        try:
            os.mkdir("/".join(parts[:index]), dir_fd=root_fd)
        except FileExistsError:
            pass
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode, dir_fd=root_fd)
    try:
        os.write(fd, payload)
    finally:
        os.close(fd)
    os.chmod(path, mode, dir_fd=root_fd)


def replace_spec() -> TransactionSpec:
    """One `ReplaceFile` under an existing directory `d`.

    The preimage is real and must be captured; the postimage must be supplied.
    """
    pre, post = state_of(BEFORE), state_of(AFTER)
    return build_spec(
        consumer_tag="test",
        intent_digest="sha256:" + "a" * 64,
        initial_surface={"d/f.txt": pre},
        final_surface={"d/f.txt": post},
        effects=[ReplaceFile(effect_id="e1", path="d/f.txt", pre=pre, post=post)],
    )


def compiled_replace(lease: Lease) -> CompiledSpec:
    write_project_file(lease, "d/f.txt", BEFORE)
    return compile_spec(replace_spec())


def approved_replace(lease: Lease):
    from atoms.coordinator.admission import admit

    return admit(lease, compiled_replace(lease))


def deep_replace_spec() -> TransactionSpec:
    """One `ReplaceFile` three directories deep, under `a/b/c`.

    None of `a`, `a/b`, or `a/b/c` is itself declared -- only `a/b/c/f.txt` is -- so
    each is an undeclared `TopologyDirectory` rather than a `PersistentNode`.
    """
    pre, post = state_of(BEFORE), state_of(AFTER)
    return build_spec(
        consumer_tag="test",
        intent_digest="sha256:" + "e" * 64,
        initial_surface={"a/b/c/f.txt": pre},
        final_surface={"a/b/c/f.txt": post},
        effects=[ReplaceFile(effect_id="e1", path="a/b/c/f.txt", pre=pre, post=post)],
    )


def compiled_deep_replace(lease: Lease) -> CompiledSpec:
    write_project_file(lease, "a/b/c/f.txt", BEFORE)
    return compile_spec(deep_replace_spec())


def approved_deep_replace(lease: Lease):
    from atoms.coordinator.admission import admit

    return admit(lease, compiled_deep_replace(lease))


def blocker_spec(pre) -> TransactionSpec:
    """`DeletePath("p")`, `CreateDirectory("p")`, `CreateFileNoClobber("p/q")`.

    `p`'s timeline is continuous (FILE -> ABSENT -> DIRECTORY) and `p/q` is absent
    precisely BECAUSE `p` is a file -- the shape design §8.2 exists for. `pre` is either a
    FileState or a SymlinkState, which selects the branch under test.
    """
    post = state_of(AFTER)
    return build_spec(
        consumer_tag="test",
        intent_digest="sha256:" + "b" * 64,
        initial_surface={"p": pre, "p/q": ABSENT},
        final_surface={"p": DIRECTORY_POST, "p/q": post},
        effects=[
            DeletePath(effect_id="e1", path="p", pre=pre),
            CreateDirectory(effect_id="e2", path="p", post=DIRECTORY_POST),
            CreateFileNoClobber(effect_id="e3", path="p/q", post=post),
        ],
    )


def approved_blocked(lease: Lease, pre):
    from atoms.coordinator.admission import admit

    return admit(lease, compile_spec(blocker_spec(pre)))


def planned_directory_spec() -> TransactionSpec:
    """`CreateDirectory("a")` + `CreateFileNoClobber("a/f")`, both declared ABSENT.

    The contrast case for `blocker_spec`: nothing in this timeline accounts for an
    entry at `a`, so one being there is drift and admission refuses it.
    """
    return build_spec(
        consumer_tag="test",
        intent_digest="sha256:" + "c" * 64,
        initial_surface={"a": ABSENT, "a/f": ABSENT},
        final_surface={"a": DIRECTORY_POST, "a/f": state_of(AFTER)},
        effects=[
            CreateDirectory(effect_id="mk", path="a", post=DIRECTORY_POST),
            CreateFileNoClobber(effect_id="e1", path="a/f", post=state_of(AFTER)),
        ],
    )


def delete_symlink_spec(target: str = "elsewhere") -> TransactionSpec:
    pre = SymlinkState(target=target, mode=0o777)
    return build_spec(
        consumer_tag="test",
        intent_digest="sha256:" + "c" * 64,
        initial_surface={"link": pre},
        final_surface={"link": ABSENT},
        effects=[DeletePath(effect_id="e1", path="link", pre=pre)],
    )


def approved_delete_symlink(lease: Lease, target: str = "elsewhere"):
    from atoms.coordinator.admission import admit

    root_fd = lease._binding.project_root_fd
    try:
        os.unlink("link", dir_fd=root_fd)
    except FileNotFoundError:
        pass
    os.symlink(target, "link", dir_fd=root_fd)
    return admit(lease, compile_spec(delete_symlink_spec(target)))


def superseded_spec() -> TransactionSpec:
    """`DeletePath("a.txt")` then `CreateFileNoClobber("a.txt")`.

    Committed, this is the committed-cleanup shape: e1's tombstone is retained scratch
    awaiting removal while the live path already holds the final surface. It mirrors
    `recovery_support.make_committed_superseded_cleanup_case("delete_then_create")`,
    but over real approved scratch names and real files.
    """
    pre, post = state_of(BEFORE), state_of(AFTER)
    return build_spec(
        consumer_tag="test",
        intent_digest="sha256:" + "d" * 64,
        initial_surface={"a.txt": pre},
        final_surface={"a.txt": post},
        effects=[
            DeletePath(effect_id="e1", path="a.txt", pre=pre),
            CreateFileNoClobber(effect_id="e2", path="a.txt", post=post),
        ],
    )


def approved_superseded(lease: Lease):
    """Approved with the FINAL surface already live, as after a committed run."""
    from atoms.coordinator.admission import admit

    write_project_file(lease, "a.txt", BEFORE)
    approved = admit(lease, compile_spec(superseded_spec()))
    write_project_file(lease, "a.txt", AFTER)
    return approved


class DictPayloads:
    """A PayloadSource over an in-memory map.

    `open` returns a FRESH stream each call, which capture owns and closes. A source that
    handed back a shared or already-read stream would make a second staging attempt
    silently produce a short blob. An unknown digest raises `KeyError` -- the one signal
    capture translates into `ProtocolError`.
    """

    def __init__(self, contents: dict[str, bytes]) -> None:
        self._contents = contents
        self.requested: list[str] = []

    def open(self, digest: str) -> io.BytesIO:
        self.requested.append(digest)
        return io.BytesIO(self._contents[digest])
