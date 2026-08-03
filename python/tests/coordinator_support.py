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
from atoms.core.recovery import ScratchRole
from atoms.core.spec import TransactionSpec, build_spec
from atoms.fs.approval import ProjectApprovedSpec
from atoms.store.blobs import StagedBlob
from atoms.store.workspace import Workspace
from tests.store_support import digest_of, file_state, stage

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


def admission_for(lease: Lease) -> ProjectApprovedSpec:
    from atoms.coordinator.admission import admit

    return admit(lease, compiled_for(lease))


def stage_manifest(
    workspace: Workspace, contents: tuple[bytes, ...] = (AFTER,)
) -> tuple[StagedBlob, ...]:
    """Stage each content through the workspace and describe it for promotion.

    A5a's coherence barrier requires a `blob` row for every digest the record
    references, so a spec whose final surface names a file cannot be published without
    this.
    """
    manifest = []
    for index, content in enumerate(contents):
        name = f"blob-{index}"
        stage(workspace, name, content)
        manifest.append(
            StagedBlob(name=name, digest=digest_of(content), byte_len=len(content))
        )
    return tuple(manifest)


def prepared(lease: Lease) -> ProjectApprovedSpec:
    """An admitted, prepared, published transaction with its workspace released."""
    from atoms.coordinator.prepare import open_workspace, prepare_transaction

    approved = admission_for(lease)
    workspace = open_workspace(lease, approved)
    try:
        prepare_transaction(lease, approved, workspace, stage_manifest(workspace))
    finally:
        workspace.close()
    return approved


def create_the_planned_directory(lease: Lease, approved: ProjectApprovedSpec) -> None:
    """Make an ApprovedPlannedDirectory exist after its proof was issued."""
    _ = approved
    os.mkdir("d", dir_fd=lease._binding.project_root_fd)


def replace_the_parent_directory(lease: Lease, approved: ProjectApprovedSpec) -> None:
    """Give the approved scratch parent a new inode at the same path.

    Measured on the ext4 test volume: rmdir followed by mkdir returns the *same*
    `st_ino` every time, because the inode is freed and immediately reallocated. So the
    replacement is built beside `d` while `d` still holds its inode -- which forces a
    distinct one -- and then renamed over the emptied name. The spelling is identical
    either way; only the identity moves, which is precisely the drift ledger #19 names.
    """
    _ = approved
    root_fd = lease._binding.project_root_fd
    before = os.stat("d", dir_fd=root_fd).st_ino
    os.mkdir("d.replacement", dir_fd=root_fd)
    os.rmdir("d", dir_fd=root_fd)
    os.rename("d.replacement", "d", src_dir_fd=root_fd, dst_dir_fd=root_fd)
    assert os.stat("d", dir_fd=root_fd).st_ino != before, (
        "the replacement reused the original inode, so the test would pass vacuously"
    )


def occupy_the_scratch_leaf(lease: Lease, approved: ProjectApprovedSpec) -> str:
    """Create the file the proof's staging leaf names, and return its relative path."""
    scratch = next(
        entry for entry in approved.scratch if entry.role is ScratchRole.STAGING
    )
    relative = f"d/{scratch.leaf}"
    os.close(
        os.open(
            relative,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
            dir_fd=lease._binding.project_root_fd,
        )
    )
    return relative
