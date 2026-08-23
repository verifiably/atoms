"""Reserved scratch grammar (design §5.1).

The discriminating sigil is the exact leaf prefix ``.#~`` — three ASCII punctuation
bytes, none of which has a case or NFC/NFD variant. Engine-reserved leaves begin with
the sigil; scratch leaves additionally follow the closed generated-name grammar.
"""

from __future__ import annotations

import unicodedata

from atoms.core.errors import ProtocolError
from atoms.core.identifiers import is_valid_identifier, require_valid_identifier

SCRATCH_SIGIL = ".#~"
CHAIN_LEAF = ".#~chain"
ROOT_CLAIM_LEAF = ".#~root-claim"
"""The fixed root-creation claim (lifecycle design §5.1): temporary engine
bookkeeping at the root, published no-clobber, removed before completion."""
SCRATCH_ROLES: frozenset[str] = frozenset({"staging", "tombstone", "anchor", "work"})


def leaf_of(rel_path: str) -> str:
    """Return the last ``/``-separated component of a project-relative path."""
    return rel_path.rsplit("/", 1)[-1]


def is_scratch_leaf(leaf: str) -> bool:
    """True iff ``leaf`` is a valid engine-generated scratch leaf."""
    if not leaf.startswith(SCRATCH_SIGIL):
        return False
    parts = leaf[len(SCRATCH_SIGIL) :].split(".")
    if len(parts) != 3:
        return False
    txid, effect_id, role = parts
    return is_valid_identifier(txid) and is_valid_identifier(effect_id) and role in SCRATCH_ROLES


def is_engine_reserved_leaf(leaf: str) -> bool:
    """True iff ``leaf`` begins with the engine-reserved sigil."""
    return leaf.startswith(SCRATCH_SIGIL)


def aliases_scratch_sigil(leaf: str) -> bool:
    """True iff any case- or NFC/NFD-normalized form of ``leaf`` begins with the sigil.

    Because the sigil is letter-free, this must agree with :func:`is_engine_reserved_leaf` on
    every input; the agreement is the property that proves the letter-free choice sound
    (design §13.3).
    """
    forms = {
        leaf,
        unicodedata.normalize("NFC", leaf),
        unicodedata.normalize("NFD", leaf),
        leaf.casefold(),
    }
    return any(form.startswith(SCRATCH_SIGIL) for form in forms)


def scratch_leaf(txid: str, effect_id: str, role: str) -> str:
    """Build a scratch leaf name ``.#~<txid>.<effect-id>.<role>``.

    Every interpolated part is validated against the safe-identifier grammar first, so a
    malformed (multi-component, over-long, or NUL-bearing) part raises ``SpecValidationError``
    here rather than producing a leaf that fails a later ``*at`` syscall.
    """
    require_valid_identifier("txid", txid)
    require_valid_identifier("effect_id", effect_id)
    if role not in SCRATCH_ROLES:
        raise ProtocolError(f"unknown scratch role: {role!r}")
    return f"{SCRATCH_SIGIL}{txid}.{effect_id}.{role}"
