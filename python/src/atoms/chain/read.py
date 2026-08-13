"""Read-only validation of project-local chain evidence."""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from enum import Enum

from atoms.chain.errors import ChainStateInvalid
from atoms.chain.model import Entry, GenesisEntry, decode_entry, entry_digest
from atoms.core.errors import ProtocolError
from atoms.core.scratch import CHAIN_LEAF
from atoms.fs.audit import AuditedBackend, Provenance, RootKind

STAGING_LEAF = ".#~stage"
_DIGEST_NAME = re.compile(r"^[0-9a-f]{64}$")
_READ_SIZE = 1024 * 1024


class SurvivorDisposition(Enum):
    FINISH = "finish"
    REMOVE = "remove"


@dataclass(frozen=True, slots=True)
class SurvivorAction:
    name: str
    disposition: SurvivorDisposition
    envelope: bytes | None


@dataclass(frozen=True, slots=True)
class ValidatedChain:
    entries: tuple[tuple[str, Entry], ...]
    tip: str | None
    survivors: tuple[SurvivorAction, ...]


def _read_regular(
    backend: AuditedBackend, chain_fd: int, name: str
) -> bytes:
    try:
        fd = backend.open_regular_nofollow(chain_fd, name)
    except OSError as caught:
        raise ChainStateInvalid(
            f"chain entry {name!r} is not a readable no-follow regular file"
        ) from caught
    try:
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise ChainStateInvalid(
                    f"chain entry {name!r} is not a regular file"
                )
            chunks: list[bytes] = []
            while chunk := os.read(fd, _READ_SIZE):
                chunks.append(chunk)
            return b"".join(chunks)
        except OSError as caught:
            raise ChainStateInvalid(
                f"chain entry {name!r} could not be read coherently"
            ) from caught
    finally:
        backend.close_fd(fd)


def _planned(planned: tuple[bytes, ...]) -> dict[bytes, str | None]:
    if type(planned) is not tuple:
        raise ProtocolError("planned envelopes must be an exact tuple")
    checked: dict[bytes, str | None] = {}
    for index, envelope in enumerate(planned):
        if type(envelope) is not bytes:
            raise ProtocolError(f"planned envelope {index} must be exact bytes")
        try:
            previous, _ = decode_entry(envelope)
        except ChainStateInvalid as caught:
            raise ProtocolError(
                f"planned envelope {index} is not a canonical chain entry"
            ) from caught
        if envelope in checked:
            raise ProtocolError("planned envelopes must be duplicate-free")
        checked[envelope] = previous
    return checked


def _linearize(
    found: dict[str, tuple[str | None, Entry, bytes]],
) -> tuple[tuple[tuple[str, Entry], ...], str | None]:
    if not found:
        return (), None
    genesis = [
        digest
        for digest, (previous, entry, _) in found.items()
        if previous is None and type(entry) is GenesisEntry
    ]
    if len(genesis) != 1:
        raise ChainStateInvalid("a non-empty chain must contain exactly one genesis")
    successors: dict[str, str] = {}
    for digest, (previous, _, _) in found.items():
        if previous is None:
            continue
        if previous not in found:
            raise ChainStateInvalid(
                f"chain entry {digest} names missing predecessor {previous}"
            )
        if previous in successors:
            raise ChainStateInvalid(
                f"chain entry {previous} has more than one successor"
            )
        successors[previous] = digest

    ordered: list[tuple[str, Entry]] = []
    current = genesis[0]
    while True:
        if any(digest == current for digest, _ in ordered):
            raise ChainStateInvalid("the chain contains a cycle")
        ordered.append((current, found[current][1]))
        successor = successors.get(current)
        if successor is None:
            break
        current = successor
    if len(ordered) != len(found):
        raise ChainStateInvalid("the chain contains an orphan history")
    return tuple(ordered), current


def validate_chain(
    backend: AuditedBackend,
    chain_fd: int,
    planned: tuple[bytes, ...] = (),
) -> ValidatedChain:
    """Validate every durable entry and classify the fixed staging survivor."""

    planned_entries = _planned(planned)
    if backend.provenance_of(chain_fd) != Provenance(RootKind.PROJECT, CHAIN_LEAF):
        raise ProtocolError("chain_fd must name the reserved chain directory")
    try:
        names = sorted(os.listdir(chain_fd))
    except OSError as caught:
        raise ChainStateInvalid("the chain directory cannot be listed") from caught

    found: dict[str, tuple[str | None, Entry, bytes]] = {}
    staged: bytes | None = None
    for name in names:
        if name == STAGING_LEAF:
            staged = _read_regular(backend, chain_fd, name)
            continue
        if _DIGEST_NAME.fullmatch(name) is None:
            raise ChainStateInvalid(f"foreign chain-directory leaf {name!r}")
        envelope = _read_regular(backend, chain_fd, name)
        if entry_digest(envelope) != name:
            raise ChainStateInvalid(
                f"chain entry name {name!r} does not match its bytes"
            )
        previous, entry = decode_entry(envelope)
        found[name] = (previous, entry, envelope)

    entries, tip = _linearize(found)
    for envelope, previous in planned_entries.items():
        durable = found.get(entry_digest(envelope))
        if (durable is None or durable[2] != envelope) and previous != tip:
            raise ProtocolError(
                "a planned envelope must already be durable or derive from the validated tip"
            )
    survivors: tuple[SurvivorAction, ...] = ()
    if staged is not None:
        digest = entry_digest(staged)
        already_durable = digest in found and found[digest][2] == staged
        if staged in planned_entries and not already_durable:
            action = SurvivorAction(
                STAGING_LEAF, SurvivorDisposition.FINISH, staged
            )
        else:
            action = SurvivorAction(
                STAGING_LEAF, SurvivorDisposition.REMOVE, None
            )
        survivors = (action,)
    return ValidatedChain(entries, tip, survivors)
