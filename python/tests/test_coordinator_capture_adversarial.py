"""A6 tier 5 -- adversarial, scoped to A6 (design §11.6)."""

from __future__ import annotations

import os

import pytest

from atoms.core.errors import PreconditionRefused
from tests.capture_support import (
    AFTER,
    DictPayloads,
    approved_replace,
    digest_of,
)
from tests.coordinator_support import project_state


def payloads() -> DictPayloads:
    return DictPayloads({digest_of(AFTER): AFTER})


def test_no_project_path_is_mutated_by_capture(leased, ext4_project_root):
    """Authority §13.5. A6 writes only into staging/<txid>/.

    `project_state` records the root itself, every descendant, `lstat` rather than
    `stat`, and st_dev/st_ino -- so a path replaced by an inode of identical kind, mode,
    and content is still visible, and a chmod on the root is not invisible.
    """
    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        approved = approved_replace(lease)
        before = project_state(str(ext4_project_root))
        with (
            open_workspace(lease, approved) as workspace,
            capture_initial_surface(lease, approved, workspace, payloads()),
        ):
            pass
        assert project_state(str(ext4_project_root)) == before


def test_no_project_path_is_mutated_by_a_refused_capture(leased, ext4_project_root):
    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        approved = approved_replace(lease)
        before = project_state(str(ext4_project_root))
        bad = DictPayloads({digest_of(AFTER): b"wrong"})
        with (
            open_workspace(lease, approved) as workspace,
            pytest.raises(PreconditionRefused),
            capture_initial_surface(lease, approved, workspace, bad),
        ):
            pass
        assert project_state(str(ext4_project_root)) == before


def test_a_leaf_swapped_between_the_walk_and_the_observation_refuses(leased, monkeypatch):
    """Between two A6 steps, not before capture begins.

    The table opens and validates `d`; the observation then looks `f.txt` up beneath the
    held descriptor. Swapping the leaf in the window between them is the race A6 itself
    can close -- a swap after observation is A7's destructive-transfer validation, and
    asserting it here would prove nothing about the code that will own it.
    """
    from atoms.coordinator import capture as capture_module
    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        approved = approved_replace(lease)
        root_fd = lease._binding.project_root_fd
        real_verify = capture_module._verify_stops

        def swap_then_verify(table, spec):
            os.unlink("d/f.txt", dir_fd=root_fd)
            os.symlink("/etc/passwd", "d/f.txt", dir_fd=root_fd)
            return real_verify(table, spec)

        monkeypatch.setattr(capture_module, "_verify_stops", swap_then_verify)
        with (
            open_workspace(lease, approved) as workspace,
            pytest.raises(PreconditionRefused),
            capture_initial_surface(lease, approved, workspace, payloads()),
        ):
            pass


def test_a_mount_crossing_mid_walk_refuses(leased, monkeypatch):
    """DirectoryConstraints carries lookup_proof and name_max only.

    A constraints comparison alone would pass a directory replaced by a bind mount, so
    mount membership is read separately and compared with the bound volume's.
    """
    from atoms.coordinator import descriptors
    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        approved = approved_replace(lease)
        real_read = descriptors.read_mount_id
        root_fd = lease._binding.project_root_fd

        def foreign_mount(fd):
            # The project root itself keeps its real mount; the child `d` reports a
            # different one, which is the shape a bind mount over `d` would produce.
            return real_read(fd) if fd == root_fd else real_read(fd) + 1_000_000

        monkeypatch.setattr(descriptors, "read_mount_id", foreign_mount)
        with (
            open_workspace(lease, approved) as workspace,
            pytest.raises(PreconditionRefused, match="mount"),
            capture_initial_surface(lease, approved, workspace, payloads()),
        ):
            pass


def test_project_root_constraint_drift_after_approval_refuses(leased, monkeypatch):
    """§5.3: retention is not discharge."""
    from atoms.coordinator import descriptors
    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace
    from atoms.fs.lookup import DirectoryConstraints, LookupProof

    with leased() as lease:
        approved = approved_replace(lease)
        drifted = DirectoryConstraints(lookup_proof=LookupProof.EXACT_BYTES, name_max=64)
        monkeypatch.setattr(
            descriptors, "read_lookup_constraints", lambda fd, kind: drifted
        )
        with (
            open_workspace(lease, approved) as workspace,
            pytest.raises(PreconditionRefused, match="constraints"),
            capture_initial_surface(lease, approved, workspace, payloads()),
        ):
            pass
