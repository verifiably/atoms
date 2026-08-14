"""ReplaceFile forward execution."""

from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator
from typing import cast

import pytest

from atoms.coordinator.capture import capture_initial_surface
from atoms.coordinator.effects.common import EffectMismatch
from atoms.coordinator.effects.replace_file import apply
from atoms.coordinator.effects.sites import ReplaceSite, _site_for
from atoms.coordinator.prepare import open_workspace, prepare_transaction
from atoms.core.effects import ReplaceFile
from tests.capture_support import AFTER, BEFORE, DictPayloads, approved_replace, digest_of


@contextlib.contextmanager
def replace_execution(lease) -> Iterator[tuple[ReplaceSite, ReplaceFile]]:
    approved = approved_replace(lease)
    payloads = DictPayloads({digest_of(AFTER): AFTER})
    with open_workspace(lease, approved) as workspace, capture_initial_surface(
        lease, approved, workspace, payloads
    ) as captured:
        prepare_transaction(lease, approved, workspace, captured.manifest)
        backend = lease._binding.backend
        backend.set_declared_paths(frozenset(entry.path for entry in approved.paths))
        try:
            effect = cast(ReplaceFile, approved.compiled.spec.effects[0])
            yield cast(ReplaceSite, _site_for(approved, captured.descriptors, effect)), effect
        finally:
            backend.clear_declared_paths()


def read_at(parent_fd: int, leaf: str) -> bytes:
    fd = os.open(leaf, os.O_RDONLY, dir_fd=parent_fd)
    try:
        return os.read(fd, 1024)
    finally:
        os.close(fd)


def overwrite_at(parent_fd: int, leaf: str, content: bytes) -> None:
    fd = os.open(leaf, os.O_WRONLY | os.O_TRUNC, dir_fd=parent_fd)
    try:
        os.write(fd, content)
    finally:
        os.close(fd)


def test_replace_publishes_postimage_and_displaces_preimage(leased) -> None:
    with leased() as lease, replace_execution(lease) as (site, effect):
        apply(lease._binding.backend, lease._store, site, effect)

        assert read_at(site.parent_fd, site.live_leaf) == AFTER
        assert read_at(site.parent_fd, site.staging_leaf) == BEFORE


def test_replace_verification_failure_exchanges_back(leased, monkeypatch) -> None:
    with leased() as lease, replace_execution(lease) as (site, effect):
        inner = lease._binding.backend._inner
        real_set_mode = inner.set_mode
        monkeypatch.setattr(inner, "set_mode", lambda fd, mode: real_set_mode(fd, 0o600))

        with pytest.raises(EffectMismatch, match="verify_mode"):
            apply(lease._binding.backend, lease._store, site, effect)

        assert read_at(site.parent_fd, site.live_leaf) == BEFORE
        assert read_at(site.parent_fd, site.staging_leaf) == AFTER


def test_replace_both_changed_performs_no_compensation(leased, monkeypatch) -> None:
    with leased() as lease, replace_execution(lease) as (site, effect):
        inner = lease._binding.backend._inner
        real_open = inner.open_regular_nofollow

        def drift(parent_fd: int, leaf: str) -> int:
            if leaf == site.staging_leaf:
                overwrite_at(parent_fd, site.staging_leaf, b"altered staging")
                os.unlink(site.live_leaf, dir_fd=parent_fd)
                live = os.open(
                    site.live_leaf,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o644,
                    dir_fd=parent_fd,
                )
                os.write(live, b"foreign live")
                os.close(live)
            return real_open(parent_fd, leaf)

        monkeypatch.setattr(inner, "open_regular_nofollow", drift)

        with pytest.raises(EffectMismatch):
            apply(lease._binding.backend, lease._store, site, effect)

        assert read_at(site.parent_fd, site.live_leaf) == b"foreign live"
        assert read_at(site.parent_fd, site.staging_leaf) == b"altered staging"
