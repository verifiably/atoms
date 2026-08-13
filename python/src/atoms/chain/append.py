"""Crash-safe publication into the project-local chain."""

from __future__ import annotations

import errno

from atoms.chain.errors import ChainStateInvalid
from atoms.chain.model import Entry, decode_entry, encode_entry, entry_digest
from atoms.chain.read import (
    STAGING_LEAF,
    SurvivorAction,
    SurvivorDisposition,
    ValidatedChain,
    _read_regular,
    validate_chain,
)
from atoms.core.errors import ProtocolError
from atoms.core.scratch import CHAIN_LEAF
from atoms.fs.audit import AuditedBackend, Provenance, RootKind


def _validated(value: object) -> ValidatedChain:
    if type(value) is not ValidatedChain:
        raise ProtocolError("validated chain must be an exact ValidatedChain")
    if type(value.entries) is not tuple or type(value.survivors) is not tuple:
        raise ProtocolError("validated chain collections must be exact tuples")
    previous: str | None = None
    for index, item in enumerate(value.entries):
        if type(item) is not tuple or len(item) != 2:
            raise ProtocolError(
                f"validated chain entry {index} must be an exact digest/entry pair"
            )
        digest, entry = item
        if type(digest) is not str:
            raise ProtocolError(
                f"validated chain entry {index} digest must be an exact str"
            )
        if entry_digest(encode_entry(previous, entry)) != digest:
            raise ProtocolError(
                f"validated chain entry {index} digest does not name its envelope"
            )
        previous = digest
    if value.tip != previous:
        raise ProtocolError("validated chain tip does not match its history")
    if len(value.survivors) > 1:
        raise ProtocolError("the fixed staging leaf permits at most one survivor")
    for survivor in value.survivors:
        _action(survivor, value.tip)
    return value


def _action(value: object, tip: str | None) -> SurvivorAction:
    if type(value) is not SurvivorAction:
        raise ProtocolError("survivor action must be an exact SurvivorAction")
    if value.name != STAGING_LEAF:
        raise ProtocolError("a survivor action may name only the fixed staging leaf")
    if type(value.disposition) is not SurvivorDisposition:
        raise ProtocolError("survivor disposition has the wrong exact type")
    if value.disposition is SurvivorDisposition.REMOVE:
        if value.envelope is not None:
            raise ProtocolError("a REMOVE survivor must not carry an envelope")
        return value
    if type(value.envelope) is not bytes:
        raise ProtocolError("a FINISH survivor must carry exact envelope bytes")
    try:
        previous, _ = decode_entry(value.envelope)
    except ChainStateInvalid as caught:
        raise ProtocolError("a FINISH survivor must carry a canonical envelope") from caught
    if previous != tip:
        raise ProtocolError("a FINISH survivor must link from the validated tip")
    return value


def _same_history(left: ValidatedChain, right: ValidatedChain) -> bool:
    return left.entries == right.entries and left.tip == right.tip


def _unlink_stage(backend: AuditedBackend, chain_fd: int) -> None:
    try:
        backend.unlink_child(chain_fd, STAGING_LEAF)
    except OSError as caught:
        if caught.errno != errno.ENOENT:
            raise
    backend.flush_directory(chain_fd)


def _finish(
    backend: AuditedBackend,
    chain_fd: int,
    envelope: bytes,
    digest: str,
) -> None:
    if _read_regular(backend, chain_fd, STAGING_LEAF) != envelope:
        raise ChainStateInvalid("the staging survivor changed after validation")
    try:
        backend.transfer_noclobber(chain_fd, STAGING_LEAF, chain_fd, digest)
    except OSError as caught:
        if caught.errno != errno.EEXIST:
            raise
        if _read_regular(backend, chain_fd, digest) != envelope:
            raise ChainStateInvalid(
                "the existing destination is not the planned chain entry"
            ) from caught
        if _read_regular(backend, chain_fd, STAGING_LEAF) != envelope:
            raise ChainStateInvalid(
                "the staging survivor is not the planned chain entry"
            ) from caught
        _unlink_stage(backend, chain_fd)
        return
    backend.flush_directory(chain_fd)


def apply_survivors(
    backend: AuditedBackend,
    chain_fd: int,
    validated: ValidatedChain,
) -> ValidatedChain:
    """Apply one validated staging action and return a fresh immutable proof."""

    validated = _validated(validated)
    if not validated.survivors:
        fresh = validate_chain(backend, chain_fd)
        if fresh != validated:
            raise ChainStateInvalid("the chain changed after validation")
        return fresh

    action = _action(validated.survivors[0], validated.tip)
    if action.disposition is SurvivorDisposition.REMOVE:
        current = validate_chain(backend, chain_fd)
        if not _same_history(current, validated):
            raise ChainStateInvalid("the durable chain changed before survivor removal")
        if not current.survivors:
            return current
        _unlink_stage(backend, chain_fd)
        return validate_chain(backend, chain_fd)

    envelope = action.envelope
    if type(envelope) is not bytes:
        raise ProtocolError("a FINISH survivor must carry exact envelope bytes")
    previous, entry = decode_entry(envelope)
    if previous != validated.tip:
        raise ProtocolError("a FINISH survivor must link from the validated tip")
    digest = entry_digest(envelope)
    expected_entries = validated.entries + ((digest, entry),)
    current = validate_chain(backend, chain_fd, (envelope,))
    if _same_history(current, validated) and current.survivors == (action,):
        _finish(backend, chain_fd, envelope, digest)
    elif current.entries == expected_entries and current.tip == digest:
        if current.survivors:
            if _read_regular(backend, chain_fd, STAGING_LEAF) != envelope:
                raise ChainStateInvalid(
                    "the duplicate staging survivor is not the planned entry"
                )
            _unlink_stage(backend, chain_fd)
    else:
        raise ChainStateInvalid("the chain changed before survivor completion")
    fresh = validate_chain(backend, chain_fd)
    if fresh.entries != expected_entries or fresh.tip != digest or fresh.survivors:
        raise ChainStateInvalid("survivor completion did not produce the planned chain")
    return fresh


def _write_all(backend: AuditedBackend, fd: int, data: bytes) -> None:
    offset = 0
    while offset < len(data):
        written = backend.write(fd, data[offset:])
        if type(written) is not int or not 0 < written <= len(data) - offset:
            raise ProtocolError("backend.write did not make a valid forward step")
        offset += written


def _prove_eexist(
    backend: AuditedBackend,
    chain_fd: int,
    envelope: bytes,
    digest: str,
    expected: tuple[str | None, Entry],
) -> None:
    destination = _read_regular(backend, chain_fd, digest)
    if destination != envelope or decode_entry(destination) != expected:
        raise ChainStateInvalid(
            "the existing destination is not the entry being appended"
        )
    if _read_regular(backend, chain_fd, STAGING_LEAF) != envelope:
        raise ChainStateInvalid("the staging survivor changed before EEXIST proof")
    _unlink_stage(backend, chain_fd)


def append_entry(
    backend: AuditedBackend,
    chain_fd: int,
    validated: ValidatedChain,
    entry: Entry,
) -> str:
    """Append one entry after re-proving the complete validated directory."""

    validated = _validated(validated)
    if validated.survivors:
        raise ProtocolError("survivors must be applied before appending")
    envelope = encode_entry(validated.tip, entry)
    digest = entry_digest(envelope)
    if validate_chain(backend, chain_fd) != validated:
        raise ChainStateInvalid("the chain changed after validation")

    try:
        staging_fd = backend.create_exclusive(chain_fd, STAGING_LEAF, 0o600)
    except OSError as caught:
        if caught.errno == errno.EEXIST:
            raise ChainStateInvalid("the staging leaf appeared after validation") from caught
        raise
    try:
        _write_all(backend, staging_fd, envelope)
        backend.flush_file(staging_fd)
    finally:
        backend.close_fd(staging_fd)

    try:
        backend.transfer_noclobber(chain_fd, STAGING_LEAF, chain_fd, digest)
    except OSError as caught:
        if caught.errno != errno.EEXIST:
            raise
        _prove_eexist(
            backend,
            chain_fd,
            envelope,
            digest,
            (validated.tip, entry),
        )
        return digest
    backend.flush_directory(chain_fd)
    return digest


def bootstrap_chain(
    backend: AuditedBackend, project_root_fd: int
) -> int:
    """Create or reopen the reserved chain directory and durably publish it."""

    if backend.provenance_of(project_root_fd) != Provenance(RootKind.PROJECT, ""):
        raise ProtocolError("project_root_fd must name the project root")
    try:
        backend.mkdir_child(project_root_fd, CHAIN_LEAF, 0o700)
    except OSError as caught:
        if caught.errno != errno.EEXIST:
            raise
    chain_fd = backend.open_child_directory(project_root_fd, CHAIN_LEAF)
    try:
        backend.flush_directory(project_root_fd)
    except BaseException:
        backend.close_fd(chain_fd)
        raise
    return chain_fd
