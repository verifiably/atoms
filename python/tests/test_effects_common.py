"""Shared executor effect mechanics."""

from __future__ import annotations

import errno

import pytest

from atoms.coordinator.effects.common import (
    _DETERMINATE,
    EffectMismatch,
    run_determinate,
)
from atoms.core.errors import ProtocolError

EXPECTED_DETERMINATE = {
    "unlink_child": frozenset(
        {errno.ENOENT, errno.EISDIR, errno.EBUSY, errno.EACCES, errno.EPERM}
    ),
    "rmdir_child": frozenset(
        {
            errno.ENOENT,
            errno.ENOTDIR,
            errno.EBUSY,
            errno.EACCES,
            errno.EPERM,
            errno.ENOTEMPTY,
            errno.EEXIST,
        }
    ),
    "transfer_noclobber": frozenset(
        {
            errno.ENOENT,
            errno.EEXIST,
            errno.ENOTDIR,
            errno.EXDEV,
            errno.EBUSY,
            errno.EACCES,
            errno.EPERM,
        }
    ),
    "exchange": frozenset(
        {
            errno.ENOENT,
            errno.ENOTDIR,
            errno.EXDEV,
            errno.EBUSY,
            errno.EACCES,
            errno.EPERM,
        }
    ),
    "link_anchor": frozenset(
        {errno.ENOENT, errno.EEXIST, errno.ENOTDIR, errno.EXDEV, errno.EACCES, errno.EPERM}
    ),
    "create_exclusive": frozenset(
        {errno.ENOENT, errno.EEXIST, errno.ENOTDIR, errno.EACCES}
    ),
    "mkdir_child": frozenset(
        {errno.ENOENT, errno.EEXIST, errno.ENOTDIR, errno.EACCES}
    ),
    "repair_entry_mode": frozenset(
        {errno.ENOENT, errno.ENOTDIR, errno.EACCES, errno.EPERM}
    ),
    "lstat": frozenset({errno.ENOENT, errno.ENOTDIR, errno.EACCES}),
    "open_regular_nofollow": frozenset(
        {errno.ENOENT, errno.ENOTDIR, errno.ELOOP, errno.EISDIR, errno.EACCES}
    ),
    "symlink_fingerprint": frozenset(
        {errno.ENOENT, errno.ENOTDIR, errno.EINVAL, errno.EACCES}
    ),
    "open_child_directory": frozenset(
        {errno.ENOENT, errno.ENOTDIR, errno.ELOOP, errno.EXDEV, errno.EACCES}
    ),
}


def test_determinate_errno_table_is_complete() -> None:
    assert _DETERMINATE == EXPECTED_DETERMINATE


@pytest.mark.parametrize(
    ("operation", "error"),
    [
        (operation, error)
        for operation, errors in EXPECTED_DETERMINATE.items()
        for error in errors
    ],
)
def test_determinate_errno_becomes_an_effect_mismatch(
    operation: str, error: int
) -> None:
    def fail() -> None:
        raise OSError(error, "injected")

    with pytest.raises(EffectMismatch) as caught:
        run_determinate(operation, "slot", fail)

    assert (caught.value.operation, caught.value.slot, caught.value.errno) == (
        operation,
        "slot",
        error,
    )


@pytest.mark.parametrize("operation", EXPECTED_DETERMINATE)
def test_indeterminate_errno_propagates(operation: str) -> None:
    failure = OSError(errno.EIO, "injected")

    with pytest.raises(OSError) as caught:
        run_determinate(operation, "slot", lambda: (_ for _ in ()).throw(failure))

    assert caught.value is failure


def test_passthrough_errno_propagates() -> None:
    failure = OSError(errno.EEXIST, "injected")

    with pytest.raises(OSError) as caught:
        run_determinate(
            "create_exclusive",
            "slot",
            lambda: (_ for _ in ()).throw(failure),
            passthrough=(errno.EEXIST,),
        )

    assert caught.value is failure


def test_unknown_operation_refuses_before_invoking_the_callable() -> None:
    invoked = False

    def call() -> None:
        nonlocal invoked
        invoked = True

    with pytest.raises(ProtocolError, match="unknown determinate operation"):
        run_determinate("invented", "slot", call)

    assert not invoked
