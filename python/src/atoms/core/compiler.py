"""The compilation trust boundary (design §5.4).

``compile_spec`` proves every filesystem-independent invariant a ``TransactionSpec`` must
satisfy, in a fixed phase order, and returns a frozen ``CompiledSpec`` that A3-A8 may trust.
Filesystem-dependent checks — root and metadata-root identity, ancestor traversal, volume
name folding, capability availability, live preconditions — remain A4's.

Every refusal is ``SpecValidationError``; no other exception escapes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from atoms.core.effects import (
    CreateDirectory,
    CreateFileNoClobber,
    DeletePath,
    MoveNoClobber,
    ReplaceFile,
)
from atoms.core.errors import SpecValidationError
from atoms.core.fingerprint import (
    AbsentState,
    DirectoryState,
    FileState,
    PathState,
    SymlinkState,
)
from atoms.core.identifiers import require_valid_identifier
from atoms.core.spec import SCHEMA_VERSION, Dependency, SurfaceEntry, TransactionSpec
from atoms.core.timeline import PathTimeline, build_timelines

SHA256_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")

# SHA-256 of the empty byte string. A zero-length file must carry exactly this hash.
EMPTY_CONTENT_HASH = "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

MAX_MODE = 0o7777

# Per variant: the path-valued field names and the state-valued field names.
# Membership in this table is also phase 1's variant gate: the effect set is closed, so
# being a key here is exactly what it means to be an effect.
_EFFECT_FIELDS: dict[type, tuple[tuple[str, ...], tuple[str, ...]]] = {
    ReplaceFile: (("path",), ("pre", "post")),
    CreateFileNoClobber: (("path",), ("post",)),
    DeletePath: (("path",), ("pre",)),
    MoveNoClobber: (("source", "destination"), ("source_pre",)),
    CreateDirectory: (("path",), ("post",)),
}

# Per state class: the scalar field names, by kind.
_STATE_STR_FIELDS: dict[type, tuple[str, ...]] = {
    AbsentState: (),
    FileState: ("content_hash",),
    DirectoryState: (),
    SymlinkState: ("target",),
}
_STATE_INT_FIELDS: dict[type, tuple[str, ...]] = {
    AbsentState: (),
    FileState: ("mode", "byte_len"),
    DirectoryState: ("mode",),
    SymlinkState: ("mode",),
}


@dataclass(frozen=True, slots=True)
class CompiledSpec:
    """A ``TransactionSpec`` proven well-formed, with its repeated-path timelines."""

    spec: TransactionSpec
    timelines: tuple[PathTimeline, ...]


def _require(condition: object, message: str) -> None:
    if not condition:
        raise SpecValidationError(message)


# Every gate below tests the exact runtime type rather than `isinstance`. The model is
# closed: the four states, the five variants, and the scalars they hold are the whole
# vocabulary, and nothing downstream is written to survive a member it has never seen.
# A subclass would pass an `isinstance` gate and then break a later phase in a way the
# error contract forbids — by overriding a method a phase calls (`str.startswith`,
# `Dependency.__lt__`), by returning different members on each pass (a `tuple`
# subclass), or by missing from the field tables that phases 1, 3, and 5 index by
# `type(...)`. Requiring the exact type keeps those tables total by construction.


def _require_str(value: Any, what: str) -> str:
    _require(type(value) is str, f"{what} must be a string")
    return value


def _require_int(value: Any, what: str) -> int:
    # Exact type also refuses bool, which subclasses int and compares equal to 0 and 1.
    _require(type(value) is int, f"{what} must be an integer")
    return value


def _require_tuple(value: Any, what: str) -> tuple[Any, ...]:
    _require(type(value) is tuple, f"{what} must be a tuple")
    return value


def _require_state_structure(state: Any, what: str) -> None:
    # `type(...) in` is both the exact-type gate and the guard the two lookups need.
    _require(
        type(state) in _STATE_STR_FIELDS,
        f"{what} must be one of the four path states",
    )
    for field in _STATE_STR_FIELDS[type(state)]:
        _require_str(getattr(state, field), f"{what}.{field}")
    for field in _STATE_INT_FIELDS[type(state)]:
        _require_int(getattr(state, field), f"{what}.{field}")


def _phase1_structure(spec: TransactionSpec) -> None:
    """Exhaustive structural typing. Every later phase reads fields with no defensive checks."""
    _require(type(spec) is TransactionSpec, "spec must be a TransactionSpec")
    _require_int(spec.schema_version, "schema_version")
    _require(
        spec.schema_version == SCHEMA_VERSION,
        f"unsupported schema_version: {spec.schema_version!r} (expected {SCHEMA_VERSION})",
    )
    require_valid_identifier("consumer_tag", _require_str(spec.consumer_tag, "consumer_tag"))
    _require(
        SHA256_DIGEST.fullmatch(_require_str(spec.intent_digest, "intent_digest")) is not None,
        f"intent_digest {spec.intent_digest!r} must match sha256:<64 lowercase hex>",
    )

    for label in ("initial_surface", "final_surface"):
        for index, entry in enumerate(_require_tuple(getattr(spec, label), label)):
            what = f"{label}[{index}]"
            _require(
                type(entry) is SurfaceEntry,
                f"{what} must be a SurfaceEntry",
            )
            _require_str(entry.path, f"{what}.path")
            _require_state_structure(entry.state, f"{what}.state")

    effects = _require_tuple(spec.effects, "effects")
    _require(effects, "spec must declare at least one effect")
    for index, effect in enumerate(effects):
        what = f"effects[{index}]"
        _require(
            type(effect) in _EFFECT_FIELDS,
            f"{what} must be one of the five effect variants",
        )
        _require_str(effect.effect_id, f"{what}.effect_id")
        path_fields, state_fields = _EFFECT_FIELDS[type(effect)]
        for field in path_fields:
            _require_str(getattr(effect, field), f"{what}.{field}")
        for field in state_fields:
            _require_state_structure(getattr(effect, field), f"{what}.{field}")

    for index, dependency in enumerate(_require_tuple(spec.dependencies, "dependencies")):
        what = f"dependencies[{index}]"
        _require(
            type(dependency) is Dependency,
            f"{what} must be a Dependency",
        )
        _require_str(dependency.before, f"{what}.before")
        _require_str(dependency.after, f"{what}.after")


def _every_state(spec: TransactionSpec) -> list[tuple[PathState, str]]:
    """Every path state reachable from the spec, each with a context label."""
    found: list[tuple[PathState, str]] = []
    for label in ("initial_surface", "final_surface"):
        for index, entry in enumerate(getattr(spec, label)):
            found.append((entry.state, f"{label}[{index}].state"))
    for index, effect in enumerate(spec.effects):
        _, state_fields = _EFFECT_FIELDS[type(effect)]
        for field in state_fields:
            found.append((getattr(effect, field), f"effects[{index}].{field}"))
    return found


def _phase2_fingerprints(spec: TransactionSpec) -> None:
    for state, what in _every_state(spec):
        if isinstance(state, FileState):
            _require(
                SHA256_DIGEST.fullmatch(state.content_hash) is not None,
                f"{what}.content_hash {state.content_hash!r} must match sha256:<64 lowercase hex>",
            )
            _require(state.byte_len >= 0, f"{what}.byte_len must be non-negative, got {state.byte_len}")
            is_empty_hash = state.content_hash == EMPTY_CONTENT_HASH
            _require(
                is_empty_hash == (state.byte_len == 0),
                f"{what} is inconsistent: a byte_len of 0 requires the empty-content hash "
                f"and vice versa (byte_len={state.byte_len}, content_hash={state.content_hash!r})",
            )
        if isinstance(state, SymlinkState):
            _require(state.target, f"{what}.target may not be empty")
            try:
                state.target.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise SpecValidationError(
                    f"{what}.target {state.target!r} is not encodable as UTF-8: {exc}"
                ) from exc
            _require("\x00" not in state.target, f"{what}.target contains a NUL byte")
        if isinstance(state, (FileState, DirectoryState, SymlinkState)):
            _require(
                0 <= state.mode <= MAX_MODE,
                f"{what}.mode must be permission bits in 0..0o7777, got {state.mode:#o}",
            )


def _canonicalize(spec: TransactionSpec) -> TransactionSpec:
    """Sort the set-like fields. Effect order is authoritative and never changed."""
    return TransactionSpec(
        schema_version=spec.schema_version,
        consumer_tag=spec.consumer_tag,
        intent_digest=spec.intent_digest,
        initial_surface=tuple(sorted(spec.initial_surface, key=lambda entry: entry.path)),
        final_surface=tuple(sorted(spec.final_surface, key=lambda entry: entry.path)),
        effects=spec.effects,
        dependencies=tuple(sorted(spec.dependencies)),
    )


def compile_spec(spec: TransactionSpec) -> CompiledSpec:
    """Validate ``spec`` and return a frozen, trusted ``CompiledSpec``.

    The phase order is part of the contract: it decides which violation surfaces first
    when a spec breaks several rules at once.
    """
    try:
        _phase1_structure(spec)
        _phase2_fingerprints(spec)
        timelines = build_timelines(spec.effects)
        return CompiledSpec(spec=_canonicalize(spec), timelines=timelines)
    except SpecValidationError:
        raise
    except Exception as exc:
        raise SpecValidationError("spec is structurally invalid") from exc
