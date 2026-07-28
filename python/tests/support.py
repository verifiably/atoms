"""Shared fixtures for the A2 compiler tests."""

from __future__ import annotations

from atoms.core.effects import CreateFileNoClobber
from atoms.core.fingerprint import ABSENT, DirectoryState, FileState, SymlinkState
from atoms.core.spec import TransactionSpec, build_spec

F = FileState(content_hash="sha256:" + "1" * 64, mode=0o644, byte_len=3)
G = FileState(content_hash="sha256:" + "2" * 64, mode=0o644, byte_len=5)
D = DirectoryState(mode=0o755)
L = SymlinkState(target="../target", mode=0o777)
EMPTY = FileState(
    content_hash="sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    mode=0o644,
    byte_len=0,
)
DIGEST = "sha256:" + "0" * 64


def valid_spec(**overrides) -> TransactionSpec:
    """A minimal spec that passes every phase, with fields replaceable by keyword.

    Overrides are applied to the built spec by direct construction, so a test may
    install a deliberately malformed value that ``build_spec`` would not produce.
    """
    spec = build_spec(
        consumer_tag="cnsmr",
        intent_digest=DIGEST,
        initial_surface={"a.txt": ABSENT},
        final_surface={"a.txt": F},
        effects=(CreateFileNoClobber(effect_id="e1", path="a.txt", post=F),),
    )
    if not overrides:
        return spec
    fields = {
        "schema_version": spec.schema_version,
        "consumer_tag": spec.consumer_tag,
        "intent_digest": spec.intent_digest,
        "initial_surface": spec.initial_surface,
        "final_surface": spec.final_surface,
        "effects": spec.effects,
        "dependencies": spec.dependencies,
    }
    fields.update(overrides)
    return TransactionSpec(**fields)
