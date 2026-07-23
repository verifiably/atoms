"""Reserved scratch grammar (design §5.1).

The discriminating sigil is the exact leaf prefix ``.#~`` — three ASCII punctuation
bytes, none of which has a case or NFC/NFD variant. A leaf is a scratch name iff it
begins with the sigil; only the sigil participates in classification.
"""

from __future__ import annotations

import unicodedata

from atoms.core.identifiers import require_valid_identifier

SCRATCH_SIGIL = ".#~"


def leaf_of(rel_path: str) -> str:
    """Return the last ``/``-separated component of a project-relative path."""
    return rel_path.rsplit("/", 1)[-1]


def is_scratch_leaf(leaf: str) -> bool:
    """True iff ``leaf`` begins with the reserved sigil (plain prefix test)."""
    return leaf.startswith(SCRATCH_SIGIL)


def aliases_scratch_sigil(leaf: str) -> bool:
    """True iff any case- or NFC/NFD-normalized form of ``leaf`` begins with the sigil.

    Because the sigil is letter-free, this must agree with :func:`is_scratch_leaf` on
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
    require_valid_identifier("role", role)
    return f"{SCRATCH_SIGIL}{txid}.{effect_id}.{role}"
