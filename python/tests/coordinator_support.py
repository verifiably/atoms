"""Builders shared by every coordinator tier.

Plain functions, not fixtures: the fixture-registry guard requires every fixture to live
in `tests/conftest.py`, and these are values a test constructs rather than resources a
test needs torn down.
"""

from __future__ import annotations

import hashlib
import os

from atoms.coordinator.lease import Lease
from atoms.core.canonical import canonical_json
from atoms.core.compiler import CompiledSpec, compile_spec
from atoms.core.effects import CreateDirectory, CreateFileNoClobber
from atoms.core.fingerprint import ABSENT, DirectoryState
from atoms.core.spec import TransactionSpec, build_spec
from tests.store_support import file_state

AFTER = b"after"
POST = file_state(AFTER)
DIRECTORY_POST = DirectoryState(mode=0o755)


def make_child_directory(lease: Lease, name: str = "d") -> None:
    """Create one directory in project space, tolerating an existing one.

    `compiled_for` declares `d/f.txt`, whose parent must already exist for A4b to
    approve it as an `ApprovedExistingDirectory` -- the branch of §6.4 that has an
    identity to compare against.
    """
    try:
        os.mkdir(name, dir_fd=lease._binding.project_root_fd)
    except FileExistsError:
        pass


def file_spec() -> TransactionSpec:
    """One `CreateFileNoClobber` under an existing directory.

    Measured shape: parent node `TopologyDirectory(node_id=0)`, scratch leaf
    `.#~<txid>.e1.staging`, `work_base` None.
    """
    return build_spec(
        consumer_tag="test",
        intent_digest="sha256:" + "1" * 64,
        initial_surface={"d/f.txt": ABSENT},
        final_surface={"d/f.txt": POST},
        effects=[CreateFileNoClobber(effect_id="e1", path="d/f.txt", post=POST)],
    )


def directory_spec() -> TransactionSpec:
    """A created directory with a child, so all three §6.4 branches appear at once.

    Measured shape: `ApprovedExistingDirectory(ProjectRoot())`,
    `ApprovedPlannedDirectory(PersistentNode('d'))`,
    `ApprovedPlannedDirectory(WorkRoot())`, and a populated `work_base`.
    """
    return build_spec(
        consumer_tag="test",
        intent_digest="sha256:" + "2" * 64,
        initial_surface={"d": ABSENT, "d/f.txt": ABSENT},
        final_surface={"d": DIRECTORY_POST, "d/f.txt": POST},
        effects=[
            CreateDirectory(effect_id="e1", path="d", post=DIRECTORY_POST),
            CreateFileNoClobber(effect_id="e2", path="d/f.txt", post=POST),
        ],
    )


def spec_digest(spec: TransactionSpec) -> str:
    """A short stable identity for a spec, for comparison across a process boundary.

    `canonical_json` is the same encoding A5a stores and re-verifies, so two specs share
    a digest exactly when the store would treat them as one. Hashed rather than sent
    whole so the child's JSON stays small and an assertion failure stays readable.
    """
    return hashlib.sha256(canonical_json(spec).encode("utf-8")).hexdigest()


def compiled_for(lease: Lease) -> CompiledSpec:
    make_child_directory(lease)
    return compile_spec(file_spec())


def compiled_creating_a_directory(lease: Lease) -> CompiledSpec:
    """`d` must NOT exist: A4b approves it as planned only while it is absent."""
    _ = lease
    return compile_spec(directory_spec())
