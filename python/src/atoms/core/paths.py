"""Project-relative path grammar and Unicode name-equivalence (design §5.4, A2 phases 3-4).

Purely lexical: nothing here consults a filesystem. Resolution, containment, and
volume-specific name folding are A4's (design §6).
"""

from __future__ import annotations

import unicodedata

from atoms.core.errors import SpecValidationError
from atoms.core.scratch import aliases_scratch_sigil


def require_rel_path(kind: str, value: str) -> str:
    """Return ``value`` if it is a well-formed project-relative path, else raise.

    The caller guarantees ``value`` is a ``str``; compiler phase 1 is the type gate, so
    this function performs no defensive type check (design §6).
    """
    if not value:
        raise SpecValidationError(f"{kind} may not be empty")
    if "\x00" in value:
        raise SpecValidationError(f"{kind} {value!r} contains a NUL byte")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        # A lone surrogate passes every other rule here but makes canonical_bytes
        # raise, so a spec would compile and then be unserializable.
        raise SpecValidationError(f"{kind} {value!r} is not encodable as UTF-8: {exc}") from exc
    if value.startswith("/"):
        raise SpecValidationError(f"{kind} {value!r} must be project-relative, not absolute")
    if value.endswith("/"):
        raise SpecValidationError(f"{kind} {value!r} may not end with '/'")
    for component in value.split("/"):
        if not component:
            raise SpecValidationError(f"{kind} {value!r} contains an empty component")
        if component in (".", ".."):
            raise SpecValidationError(f"{kind} {value!r} contains a {component!r} component")
        if aliases_scratch_sigil(component):
            raise SpecValidationError(
                f"{kind} {value!r} contains component {component!r}, which aliases the reserved scratch sigil"
            )
    return value


def portability_equivalence_key(value: str) -> str:
    """Return A2's fixed Unicode portability key for a path or effect ID."""
    return unicodedata.normalize("NFC", unicodedata.normalize("NFC", value).casefold())
