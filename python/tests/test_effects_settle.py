"""Authorized recovery filesystem mutations."""

from __future__ import annotations

import os
from typing import cast

import pytest

from atoms.coordinator.descriptors import _build_descriptor_table
from atoms.coordinator.effects.settle import (
    _observe_joint,
    apply_remove_scratch,
    apply_transform,
)
from atoms.core.errors import ProtocolError
from atoms.core.recovery import authorize_recovery_step
from atoms.core.recovery.plan import (
    AuthorizedStep,
    RemoveScratch,
    TransformEffectTuple,
)
from atoms.fs.observe import Observation
from tests.coordinator_support import (
    AFTER,
    prepared_with_remove_scratch,
    prepared_with_transform,
)


def write_at(parent_fd: int, leaf: str, content: bytes) -> None:
    fd = os.open(
        leaf,
        os.O_CREAT | os.O_EXCL | os.O_WRONLY,
        0o644,
        dir_fd=parent_fd,
    )
    try:
        os.write(fd, content)
    finally:
        os.close(fd)


def absent_at(parent_fd: int, leaf: str) -> bool:
    try:
        os.lstat(leaf, dir_fd=parent_fd)
    except FileNotFoundError:
        return True
    return False


def mutating_step(plan, kind):
    return next(
        (index, step)
        for index, step in enumerate(plan.steps)
        if type(step) is kind
    )


def test_create_file_recovery_moves_the_creation_back_to_staging(leased) -> None:
    with leased() as lease:
        approved, plan = prepared_with_transform(lease)
        path = approved.paths[0]
        scratch = approved.scratch[0]
        with lease._store.reopen_workspace(approved.txid) as workspace, Observation(
            lease._binding.backend
        ) as observation, _build_descriptor_table(
            lease, approved, workspace, observation
        ) as table:
            backend = lease._binding.backend
            backend.set_declared_paths(frozenset(row.path for row in approved.paths))
            write_at(table.fd_for(path.parent_node), path.leaf, AFTER)
            index, step = mutating_step(plan, TransformEffectTuple)
            observed = _observe_joint(lease, approved, table, step.expected_before)
            authorized = authorize_recovery_step(plan, index, observed)
            assert type(authorized) is AuthorizedStep

            apply_transform(lease, approved, table, authorized)

            parent_fd = table.fd_for(path.parent_node)
            assert absent_at(parent_fd, path.leaf)
            assert os.lstat(scratch.leaf, dir_fd=parent_fd).st_size == len(AFTER)


def test_remove_scratch_removes_only_the_authorized_slot(leased) -> None:
    with leased() as lease:
        approved, plan = prepared_with_remove_scratch(lease)
        scratch = approved.scratch[0]
        with lease._store.reopen_workspace(approved.txid) as workspace, Observation(
            lease._binding.backend
        ) as observation, _build_descriptor_table(
            lease, approved, workspace, observation
        ) as table:
            backend = lease._binding.backend
            backend.set_declared_paths(frozenset(row.path for row in approved.paths))
            parent_fd = table.fd_for(scratch.parent_node)
            write_at(parent_fd, scratch.leaf, AFTER)
            sibling = ".#~sibling.e1.staging"
            write_at(parent_fd, sibling, b"sibling")
            index, step = mutating_step(plan, RemoveScratch)
            observed = _observe_joint(lease, approved, table, step.expected_before)
            authorized = authorize_recovery_step(plan, index, observed)
            assert type(authorized) is AuthorizedStep

            apply_remove_scratch(lease, approved, table, authorized)

            assert absent_at(parent_fd, scratch.leaf)
            assert os.lstat(sibling, dir_fd=parent_fd).st_size == len(b"sibling")


def test_raw_transform_step_is_not_an_authorization_proof(leased) -> None:
    with leased() as lease:
        approved, plan = prepared_with_transform(lease)
        with lease._store.reopen_workspace(approved.txid) as workspace, Observation(
            lease._binding.backend
        ) as observation, _build_descriptor_table(
            lease, approved, workspace, observation
        ) as table:
            _, step = mutating_step(plan, TransformEffectTuple)
            with pytest.raises(ProtocolError, match="AuthorizedStep"):
                apply_transform(
                    lease,
                    approved,
                    table,
                    cast(AuthorizedStep, step),
                )
