"""Read-only validation of project-local chain evidence."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import cast as _cast

from atoms.chain.errors import ChainStateInvalid
from atoms.chain.inspect import (
    STAGING_LEAF,
    ChainDefect,
    ChainScan,
    WellFormedChain,
    foreign_leaf,
    read_leaf,
    scan_chain_directory,
    validate_scan,
)
from atoms.chain.model import Entry, decode_entry, entry_digest
from atoms.core.errors import ProtocolError
from atoms.fs.audit import AuditedBackend


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


def _read_regular(backend: AuditedBackend, chain_fd: int, name: str) -> bytes:
    """`read_leaf`'s raising disposition -- one predicate, one FOREIGN_LEAF wording."""

    data = read_leaf(backend, chain_fd, name)
    if data is None:
        raise ChainStateInvalid(foreign_leaf(name).detail)
    return data


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


def validate_chain(
    backend: AuditedBackend,
    chain_fd: int,
    planned: tuple[bytes, ...] = (),
) -> ValidatedChain:
    """Validate every durable entry and classify the fixed staging survivor.

    The raising disposition of `atoms.chain.inspect`'s core (design §4.6): this
    computes no defect of its own, so the taxonomy the inspecting commands report and
    the taxonomy the mutating paths refuse cannot fork. `_planned` runs first,
    preserving the current ordering of `ProtocolError` against `ChainStateInvalid`.
    """

    planned_entries = _planned(planned)
    scanned = scan_chain_directory(backend, chain_fd)
    if type(scanned) is ChainDefect:
        raise ChainStateInvalid(scanned.detail)
    scan = _cast(ChainScan, scanned)
    result = validate_scan(scan)
    # The one place a returned arm is flattened, and it is exact: an absent chain is
    # `ValidatedChain`'s existing empty shape, which every call site already handles.
    entries: tuple[tuple[str, Entry], ...] = ()
    tip: str | None = None
    if type(result) is WellFormedChain:
        entries, tip = result.entries, result.tip
    durable_envelopes = {digest: envelope for digest, _, _, envelope in scan.found}
    staged = scan.staged
    for envelope, previous in planned_entries.items():
        durable = durable_envelopes.get(entry_digest(envelope))
        if durable != envelope and previous != tip:
            raise ProtocolError(
                "a planned envelope must already be durable or derive from the validated tip"
            )
    survivors: tuple[SurvivorAction, ...] = ()
    if staged is not None:
        already_durable = durable_envelopes.get(entry_digest(staged)) == staged
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
