import pytest

from atoms.core.fingerprint import (
    ABSENT,
    AbsentState,
    DirectoryState,
    FileState,
    PathKind,
    SymlinkState,
    kind_of,
)


def test_states_are_frozen_and_hashable():
    f = FileState(content_hash="sha256:" + "0" * 64, mode=0o644, byte_len=10)
    assert hash(f) == hash(FileState(content_hash="sha256:" + "0" * 64, mode=0o644, byte_len=10))
    with pytest.raises(Exception):  # noqa: B017
        f.mode = 0o600  # type: ignore[misc]


def test_absent_is_a_singleton_value():
    assert ABSENT is ABSENT  # noqa: PLR0124
    assert AbsentState() == ABSENT


def test_kind_of_maps_every_variant():
    assert kind_of(ABSENT) is PathKind.ABSENT
    assert kind_of(FileState(content_hash="sha256:" + "a" * 64, mode=0o644, byte_len=1)) is PathKind.FILE
    assert kind_of(DirectoryState(mode=0o755)) is PathKind.DIRECTORY
    assert kind_of(SymlinkState(target="../x", mode=0o777)) is PathKind.SYMLINK
