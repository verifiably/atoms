"""One typed validation core for project-local chain evidence (design §4).

`validate_chain` is not a second validator: it is the *raising disposition* of this
core. Two passes over a chain directory and a third over the linearized result produce
at most one `ChainDefect`, and it is the first in §4.4's pinned traversal order. The
inspecting commands return it; the raising paths raise `ChainStateInvalid` carrying its
`detail`. There is exactly one place a defect is recognized and exactly one place its
wording is minted -- which is what keeps a chain from being simultaneously appendable
and reported malformed.
"""

from __future__ import annotations

import enum
import os
import re
import stat
from dataclasses import dataclass, replace
from typing import cast

from atoms.chain.errors import ChainStateInvalid
from atoms.chain.model import (
    ChainOutcome,
    Entry,
    IntentEntry,
    RegisteredEntry,
    SettledEntry,
    decode_entry,
    entry_digest,
)
from atoms.core.errors import ProtocolError
from atoms.core.scratch import CHAIN_LEAF
from atoms.fs.audit import AuditedBackend, Provenance, RootKind

STAGING_LEAF = ".#~stage"
_DIGEST_NAME = re.compile(r"^[0-9a-f]{64}$")
_READ_SIZE = 1024 * 1024


class DefectKind(enum.Enum):
    FOREIGN_LEAF = "foreign-leaf"
    NAME_BYTES_MISMATCH = "name-bytes-mismatch"
    UNDECODABLE_ENTRY = "undecodable-entry"
    GENESIS_COUNT = "genesis-count"
    MISSING_PREDECESSOR = "missing-predecessor"
    SIBLING_BRANCH = "sibling-branch"
    CYCLE = "cycle"
    ORPHAN_HISTORY = "orphan-history"
    SETTLEMENT_WITHOUT_REGISTRATION = "settlement-without-registration"
    SETTLEMENT_TXID_MISMATCH = "settlement-txid-mismatch"
    DUPLICATE_SETTLEMENT = "duplicate-settlement"
    DUPLICATE_REGISTRATION = "duplicate-registration"
    FULFILLS_UNRESOLVED = "fulfills-unresolved"
    DUPLICATE_FULFILLMENT = "duplicate-fulfillment"


@dataclass(frozen=True, slots=True)
class ChainDefect:
    """One defect, its offending subject, and the single place its wording is minted.

    `subject` is `None` for exactly one kind, `GENESIS_COUNT`: a chain with zero or
    several genesis entries has no single offending entry, and naming an arbitrary one
    of several would invent a fact.
    """

    kind: DefectKind
    subject: str | None
    detail: str


@dataclass(frozen=True)
class WellFormedChain:
    """The chain's linearization, plus the registrations nothing has settled.

    `pending`'s invariant differs by mode. In **registered** mode every digest in
    `pending` is the digest of an entry of `entries`. In **detached** mode a staged
    registration may contribute one pair whose digest names non-durable bytes, so a
    detached `pending` digest need not occur in `entries` (design §4.5, §8).
    """

    genesis_digest: str
    entries: tuple[tuple[str, Entry], ...]
    tip: str
    pending: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class MalformedChain:
    defect: ChainDefect


@dataclass(frozen=True)
class AbsentChain:
    """No durable chain claim: no chain directory, or an empty one."""


ChainInspection = WellFormedChain | MalformedChain | AbsentChain


@dataclass(frozen=True, slots=True)
class ChainScan:
    """Pass 1's result. `found` is in `sorted(os.listdir(...))` order, by contract."""

    found: tuple[tuple[str, str | None, Entry, bytes], ...]
    staged: bytes | None


def foreign_leaf(name: str) -> ChainDefect:
    """The one wording for a foreign object at a chain-directory position."""

    return ChainDefect(
        DefectKind.FOREIGN_LEAF, name, f"foreign chain-directory leaf {name!r}"
    )


#: The reserved chain name occupied by something that is not a directory (design §6.2).
OCCUPIED_CHAIN_LEAF = ChainDefect(
    DefectKind.FOREIGN_LEAF,
    CHAIN_LEAF,
    "the reserved chain leaf is not a stable directory",
)


def read_leaf(backend: AuditedBackend, chain_fd: int, name: str) -> bytes | None:
    """The bytes of a readable no-follow regular leaf, or `None` for anything else.

    The three `None` conditions are the whole of what "a readable entry leaf" means to
    the engine, so the staging exemption's boundary (§8) is this predicate rather than
    a second notion of readability.
    """

    try:
        fd = backend.open_regular_nofollow(chain_fd, name)
    except OSError:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            return None
        chunks: list[bytes] = []
        while chunk := os.read(fd, _READ_SIZE):
            chunks.append(chunk)
        return b"".join(chunks)
    except OSError:
        return None
    finally:
        backend.close_fd(fd)


def scan_chain_directory(
    backend: AuditedBackend, chain_fd: int
) -> ChainScan | ChainDefect:
    """Pass 1: every leaf, in sorted-name order, one leaf fully before the next."""

    if backend.provenance_of(chain_fd) != Provenance(RootKind.PROJECT, CHAIN_LEAF):
        raise ProtocolError("chain_fd must name the reserved chain directory")
    try:
        names = sorted(os.listdir(chain_fd))
    except OSError as caught:
        # A directory that cannot be read yields no evidence at all, and therefore no
        # verdict either.
        raise ChainStateInvalid("the chain directory cannot be listed") from caught

    found: list[tuple[str, str | None, Entry, bytes]] = []
    staged: bytes | None = None
    for name in names:
        if name == STAGING_LEAF:
            # Bookkeeping only when it IS a staged envelope: a directory or a symlink
            # squatting on the reserved name is a foreign object (design §4.4 step 1).
            staged = read_leaf(backend, chain_fd, name)
            if staged is None:
                return foreign_leaf(name)
            continue
        if _DIGEST_NAME.fullmatch(name) is None:
            return foreign_leaf(name)
        envelope = read_leaf(backend, chain_fd, name)
        if envelope is None:
            return foreign_leaf(name)
        if entry_digest(envelope) != name:
            return ChainDefect(
                DefectKind.NAME_BYTES_MISMATCH,
                name,
                f"chain entry name {name!r} does not match its bytes",
            )
        try:
            previous, entry = decode_entry(envelope)
        except ChainStateInvalid as caught:
            return ChainDefect(
                DefectKind.UNDECODABLE_ENTRY,
                name,
                f"chain entry {name!r} is not canonical: {caught}",
            )
        found.append((name, previous, entry, envelope))
    return ChainScan(tuple(found), staged)


def inspect_scan(scan: ChainScan) -> ChainInspection:
    """Passes 2 and 3. Pure: no backend, no descriptor, no filesystem."""

    if not scan.found:
        return AbsentChain()
    linearized = _linearize(scan.found)
    if type(linearized) is ChainDefect:
        return MalformedChain(linearized)
    entries, tip = cast(tuple[tuple[tuple[str, Entry], ...], str], linearized)
    defect = _inspect_entry_classes(entries)
    if defect is not None:
        return MalformedChain(defect)
    return WellFormedChain(
        genesis_digest=entries[0][0],
        entries=entries,
        tip=tip,
        pending=_pending_of(entries),
    )


def validate_scan(scan: ChainScan) -> WellFormedChain | AbsentChain:
    """The raising disposition of `inspect_scan`, which `validate_chain` calls."""

    result = inspect_scan(scan)
    if type(result) is MalformedChain:
        raise ChainStateInvalid(result.defect.detail)
    return cast(WellFormedChain | AbsentChain, result)


def _linearize(
    found: tuple[tuple[str, str | None, Entry, bytes], ...],
) -> tuple[tuple[tuple[str, Entry], ...], str] | ChainDefect:
    """Pass 2 -- linkage, in §4.4 steps 6 to 10's order.

    Every step reads the scan's ROWS, not a digest-keyed map: a scanned directory has
    one row per name, but an injected scan need not, and deriving genesis membership
    and the successor relation from rows is what keeps the cycle check reachable rather
    than vacuous. Ascending digest order is pinned at steps 7 and 8 so determinism does
    not rest on dict insertion order.
    """

    by_digest = {digest: entry for digest, _previous, entry, _envelope in found}
    genesis = [digest for digest, previous, _entry, _envelope in found if previous is None]
    if len(genesis) != 1:
        # An entry with `previous is None` is a genesis and conversely, proved at
        # decode -- so a class/linkage disagreement is already UNDECODABLE_ENTRY.
        return ChainDefect(
            DefectKind.GENESIS_COUNT,
            None,
            "a non-empty chain must contain exactly one genesis",
        )

    ascending = sorted(found, key=lambda row: row[0])
    for digest, previous, _entry, _envelope in ascending:
        if previous is not None and previous not in by_digest:
            return ChainDefect(
                DefectKind.MISSING_PREDECESSOR,
                digest,
                f"chain entry {digest} names missing predecessor {previous}",
            )

    successors: dict[str, list[str]] = {}
    for digest, previous, _entry, _envelope in ascending:
        if previous is not None:
            successors.setdefault(previous, []).append(digest)
    for previous in sorted(successors):
        if len(successors[previous]) > 1:
            return ChainDefect(
                DefectKind.SIBLING_BRANCH,
                previous,
                f"chain entry {previous} has more than one successor",
            )

    ordered: list[tuple[str, Entry]] = []
    visited: set[str] = set()
    current = genesis[0]
    while True:
        if current in visited:
            return ChainDefect(
                DefectKind.CYCLE,
                current,
                f"the chain revisits entry {current}, so it contains a cycle",
            )
        visited.add(current)
        ordered.append((current, by_digest[current]))
        following = successors.get(current)
        if following is None:
            break
        current = following[0]
    if visited != set(by_digest):
        # Naming a member of the disconnected component is the operator's entry point
        # into the orphan; the lowest keeps the choice deterministic (design §4.4).
        lowest = min(set(by_digest) - visited)
        return ChainDefect(
            DefectKind.ORPHAN_HISTORY,
            lowest,
            f"the chain contains an orphan history reaching entry {lowest}",
        )
    return tuple(ordered), current


def fulfills_fault(
    entries: tuple[tuple[str, Entry], ...], index: int, fulfills: str
) -> str | None:
    """`missing`, `non-ancestor`, `non-intent`, or `None` for an admissible referent.

    One predicate at two indices (design §11.2): pass 3 calls it with the referencing
    registration's own index, and `run_transaction`'s referent gate calls it with
    `len(entries)` -- the index the registration it is about to append will occupy, so
    "index below the referencing entry's" *is* membership there.
    """

    for position, (digest, entry) in enumerate(entries):
        if digest != fulfills:
            continue
        if position >= index:
            return "non-ancestor"
        if type(entry) is not IntentEntry:
            return "non-intent"
        return None
    return "missing"


def committed_fulfillments(entries: tuple[tuple[str, Entry], ...]) -> frozenset[str]:
    """The intents a committed settlement has already fulfilled (design §11.2).

    The submission gate's duplicate check runs against this set, at a strictly earlier
    state than pass 3's settlement-time check -- two states, two checks, no shared
    predicate.
    """

    by_digest = dict(entries)
    fulfilled: set[str] = set()
    for _digest, entry in entries:
        if type(entry) is not SettledEntry or entry.outcome is not ChainOutcome.COMMITTED:
            continue
        registration = by_digest.get(entry.registration)
        if type(registration) is RegisteredEntry and registration.fulfills is not None:
            fulfilled.add(registration.fulfills)
    return frozenset(fulfilled)


def _inspect_entry_classes(
    entries: tuple[tuple[str, Entry], ...],
) -> ChainDefect | None:
    """Pass 3 -- entry class, index ascending, one entry fully before the next.

    Ancestry is position: pass 2 has proved the sequence is the single genesis-rooted
    chain containing every scanned entry, so the entry at index `j` is a strict
    ancestor of the entry at index `i` exactly when `j < i`.
    """

    index_of = {digest: index for index, (digest, _entry) in enumerate(entries)}
    registered: set[str] = set()
    settled: set[str] = set()
    fulfilled: set[str] = set()
    for index, (digest, entry) in enumerate(entries):
        if type(entry) is RegisteredEntry:
            if entry.txid in registered:
                return ChainDefect(
                    DefectKind.DUPLICATE_REGISTRATION,
                    digest,
                    f"registration {digest} repeats transaction {entry.txid}",
                )
            registered.add(entry.txid)
            if entry.fulfills is not None:
                fault = fulfills_fault(entries, index, entry.fulfills)
                if fault is not None:
                    return ChainDefect(
                        DefectKind.FULFILLS_UNRESOLVED,
                        digest,
                        f"{fault} fulfills referent {entry.fulfills} "
                        f"named by registration {digest}",
                    )
        elif type(entry) is SettledEntry:
            position = index_of.get(entry.registration)
            registration = None if position is None else entries[position][1]
            if position is None or position >= index or type(registration) is not RegisteredEntry:
                return ChainDefect(
                    DefectKind.SETTLEMENT_WITHOUT_REGISTRATION,
                    digest,
                    f"settlement {digest} names no ancestor registration",
                )
            if registration.txid != entry.txid:
                return ChainDefect(
                    DefectKind.SETTLEMENT_TXID_MISMATCH,
                    digest,
                    f"settlement {digest} names a registration of another transaction",
                )
            if entry.txid in settled:
                return ChainDefect(
                    DefectKind.DUPLICATE_SETTLEMENT,
                    digest,
                    f"settlement {digest} settles transaction {entry.txid} a second time",
                )
            settled.add(entry.txid)
            if entry.outcome is not ChainOutcome.COMMITTED or registration.fulfills is None:
                continue
            # Checked at the SECOND SETTLEMENT, not the second registration: at the
            # registration the outcome is not yet known, so reporting there would need
            # a forward reference (design §4.3).
            if registration.fulfills in fulfilled:
                return ChainDefect(
                    DefectKind.DUPLICATE_FULFILLMENT,
                    digest,
                    f"settlement {digest} commits a second fulfillment "
                    f"of intent {registration.fulfills}",
                )
            fulfilled.add(registration.fulfills)
    return None


def _pending_of(
    entries: tuple[tuple[str, Entry], ...],
) -> tuple[tuple[str, str], ...]:
    """Design §4.5: every registration no settlement anywhere in the chain settles.

    A ROLLED_BACK settlement settles -- a decided outcome is not evidence-starved. Only
    the *absence* of a settlement is.
    """

    settled = {entry.txid for _digest, entry in entries if type(entry) is SettledEntry}
    return tuple(
        (entry.txid, digest)
        for digest, entry in entries
        if type(entry) is RegisteredEntry and entry.txid not in settled
    )


def staged_pending(scan: ChainScan, chain: WellFormedChain) -> WellFormedChain:
    """Design §8's detached rule: staged registration evidence stays in `pending`.

    It errs toward reporting *more* pending, which is the safe direction: the
    consequence of a pending report is a refusal and the consequence of a missed one is
    an admission. Anything else staged is inert -- not a defect, not pending, not
    reported.
    """

    if scan.staged is None:
        return chain
    try:
        previous, entry = decode_entry(scan.staged)
    except ChainStateInvalid:
        return chain
    if previous != chain.tip or type(entry) is not RegisteredEntry:
        return chain
    digest = entry_digest(scan.staged)
    if any(digest == durable for durable, _entry in chain.entries):
        return chain
    return replace(chain, pending=chain.pending + ((entry.txid, digest),))
