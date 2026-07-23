"""Bounded safe-identifier grammar for names interpolated into path components.

A stable effect ID (consumer-supplied) and the engine's txid/role are woven into a
single scratch-leaf component (design §5.1). Restricting them to a bounded ASCII
grammar makes that leaf a well-formed single component and turns a malformed ID into a
compile-time refusal (design §5.4) rather than a `*at` syscall failure at mutation time.
"""

from __future__ import annotations

import re

from atoms.core.errors import SpecValidationError

SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def is_valid_identifier(value: str) -> bool:
    return SAFE_IDENTIFIER.fullmatch(value) is not None


def require_valid_identifier(kind: str, value: str) -> str:
    if not is_valid_identifier(value):
        raise SpecValidationError(
            f"{kind} {value!r} is not a valid identifier: expected 1–64 chars of [A-Za-z0-9_-]"
        )
    return value
