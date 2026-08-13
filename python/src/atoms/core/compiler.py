"""A2's filesystem-independent compilation proof (design §5.4).

``compile_spec`` explicitly validates A2's lexical/model rules and returns a frozen
``CompiledSpec``. A3 may consume that proof; A4 consumes it and produces the distinct
``ProjectApprovedSpec`` required by A5-A8. Filesystem-dependent checks — root and
metadata-root identity, ancestor traversal, lookup policy, capability availability, and
live preconditions — remain A4's.

Invalid specifications are refused with ``SpecValidationError``. Unexpected internal
exceptions propagate unchanged. Only the four precedence edges named by the A2 design are
stable; the numbering of independent validation phases is not a general error-order contract.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Any

from atoms.core.effects import (
    CreateDirectory,
    CreateFileNoClobber,
    DeletePath,
    MoveNoClobber,
    ReplaceFile,
    occurrences,
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
from atoms.core.paths import portability_equivalence_key, require_rel_path
from atoms.core.spec import (
    SCHEMA_VERSION,
    Dependency,
    SurfaceEntry,
    TransactionSpec,
    validate_v2_members,
)
from atoms.core.timeline import PathTimeline, build_timelines

SHA256_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")

# SHA-256 of the empty byte string. A zero-length file must carry exactly this hash.
EMPTY_CONTENT_HASH = "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

MAX_MODE = 0o7777
MAX_SQLITE_INTEGER = 2**63 - 1

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

# Per variant: which state classes each state-valued field may legally hold (phase 5).
_ALLOWED_STATES: dict[type, dict[str, tuple[type, ...]]] = {
    ReplaceFile: {"pre": (FileState,), "post": (FileState,)},
    CreateFileNoClobber: {"post": (FileState,)},
    DeletePath: {"pre": (FileState, SymlinkState)},
    MoveNoClobber: {"source_pre": (FileState,)},
    CreateDirectory: {"post": (DirectoryState,)},
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


_COMPILED_SPEC_CONSTRUCTION_TOKEN = object()


@dataclass(frozen=True, slots=True, init=False)
class CompiledSpec:
    """A2's factory-issued proof of filesystem-independent specification rules."""

    spec: TransactionSpec
    timelines: tuple[PathTimeline, ...]

    def __init__(
        self,
        *,
        spec: TransactionSpec,
        timelines: tuple[PathTimeline, ...],
        _construction_token: object | None = None,
    ) -> None:
        if _construction_token is not _COMPILED_SPEC_CONSTRUCTION_TOKEN:
            raise TypeError("CompiledSpec values are created only by compile_spec")
        object.__setattr__(self, "spec", spec)
        object.__setattr__(self, "timelines", timelines)


def _new_compiled_spec(
    *,
    spec: TransactionSpec,
    timelines: tuple[PathTimeline, ...],
) -> CompiledSpec:
    return CompiledSpec(
        spec=spec,
        timelines=timelines,
        _construction_token=_COMPILED_SPEC_CONSTRUCTION_TOKEN,
    )


def _require(condition: object, message: str) -> None:
    if not condition:
        raise SpecValidationError(message)


_MISSING = object()


def _type_name(value: object) -> str:
    value_type = type(value)
    try:
        return value_type.__name__
    except Exception:  # noqa: BLE001 - contain hostile metaclass behavior at name lookup
        return "<type name unavailable>"


def _required_field(obj: object, field_name: str, what: str) -> Any:
    value = getattr(obj, field_name, _MISSING)
    _require(value is not _MISSING, f"{what}.{field_name} is missing")
    return value


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
    _require(
        type(state) in _STATE_STR_FIELDS,
        f"{what} must be one of the four path states, got {_type_name(state)}",
    )
    for field_name in _STATE_STR_FIELDS[type(state)]:
        value = _required_field(state, field_name, what)
        _require_str(value, f"{what}.{field_name}")
    for field_name in _STATE_INT_FIELDS[type(state)]:
        value = _required_field(state, field_name, what)
        _require_int(value, f"{what}.{field_name}")


def _phase1_structure(spec: TransactionSpec) -> None:
    """Exhaustively type and initialize-check the closed A1 model."""
    _require(
        type(spec) is TransactionSpec,
        f"spec must be a TransactionSpec, got {_type_name(spec)}",
    )

    schema_version = _require_int(
        _required_field(spec, "schema_version", "spec"),
        "schema_version",
    )
    _require(
        schema_version == SCHEMA_VERSION,
        f"unsupported schema_version; expected {SCHEMA_VERSION}",
    )
    consumer_tag = _require_str(
        _required_field(spec, "consumer_tag", "spec"),
        "consumer_tag",
    )
    require_valid_identifier("consumer_tag", consumer_tag)
    intent_digest = _require_str(
        _required_field(spec, "intent_digest", "spec"),
        "intent_digest",
    )
    _require(
        SHA256_DIGEST.fullmatch(intent_digest) is not None,
        f"intent_digest {intent_digest!r} must match sha256:<64 lowercase hex>",
    )

    for label in ("initial_surface", "final_surface"):
        entries = _require_tuple(_required_field(spec, label, "spec"), label)
        for index, entry in enumerate(entries):
            what = f"{label}[{index}]"
            _require(
                type(entry) is SurfaceEntry,
                f"{what} must be a SurfaceEntry, got {_type_name(entry)}",
            )
            _require_str(
                _required_field(entry, "path", what),
                f"{what}.path",
            )
            _require_state_structure(
                _required_field(entry, "state", what),
                f"{what}.state",
            )

    effects = _require_tuple(_required_field(spec, "effects", "spec"), "effects")
    _require(effects, "spec must declare at least one effect")
    for index, effect in enumerate(effects):
        what = f"effects[{index}]"
        _require(
            type(effect) in _EFFECT_FIELDS,
            f"{what} must be one of the five effect variants, got {_type_name(effect)}",
        )
        _require_str(
            _required_field(effect, "effect_id", what),
            f"{what}.effect_id",
        )
        path_fields, state_fields = _EFFECT_FIELDS[type(effect)]
        for field_name in path_fields:
            _require_str(
                _required_field(effect, field_name, what),
                f"{what}.{field_name}",
            )
        for field_name in state_fields:
            _require_state_structure(
                _required_field(effect, field_name, what),
                f"{what}.{field_name}",
            )

    dependencies = _require_tuple(
        _required_field(spec, "dependencies", "spec"),
        "dependencies",
    )
    for index, dependency in enumerate(dependencies):
        what = f"dependencies[{index}]"
        _require(
            type(dependency) is Dependency,
            f"{what} must be a Dependency, got {_type_name(dependency)}",
        )
        _require_str(
            _required_field(dependency, "before", what),
            f"{what}.before",
        )
        _require_str(
            _required_field(dependency, "after", what),
            f"{what}.after",
        )


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
            _require(
                0 <= state.byte_len <= MAX_SQLITE_INTEGER,
                f"{what}.byte_len must be in 0..2**63 - 1",
            )
            is_empty_hash = state.content_hash == EMPTY_CONTENT_HASH
            _require(
                is_empty_hash == (state.byte_len == 0),
                f"{what} is inconsistent: byte_len 0 requires the empty-content hash and vice versa",
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
                f"{what}.mode must be permission bits in 0..0o7777",
            )


def _declared_paths(spec: TransactionSpec) -> list[tuple[str, str]]:
    """Every declared path with a context label, in a deterministic order."""
    found: list[tuple[str, str]] = []
    for label in ("initial_surface", "final_surface"):
        for index, entry in enumerate(getattr(spec, label)):
            found.append((entry.path, f"{label}[{index}].path"))
    for index, effect in enumerate(spec.effects):
        path_fields, _ = _EFFECT_FIELDS[type(effect)]
        for field in path_fields:
            found.append((getattr(effect, field), f"effects[{index}].{field}"))
    return found


def _phase3_path_grammar(spec: TransactionSpec) -> None:
    for path, what in _declared_paths(spec):
        require_rel_path(what, path)


def _phase4_alias_distinctness(spec: TransactionSpec) -> None:
    first_seen: dict[str, str] = {}
    for path in sorted({path for path, _ in _declared_paths(spec)}):
        key = portability_equivalence_key(path)
        previous = first_seen.setdefault(key, path)
        _require(
            previous == path,
            f"declared paths {previous!r} and {path!r} alias one another under Unicode "
            f"caseless matching; they may name a single entry on a case- or "
            f"normalization-insensitive volume",
        )


def _phase5_variant_shapes(spec: TransactionSpec) -> None:
    for index, effect in enumerate(spec.effects):
        what = f"effects[{index}]"
        require_valid_identifier(f"{what}.effect_id", effect.effect_id)
        for field, allowed in _ALLOWED_STATES[type(effect)].items():
            state = getattr(effect, field)
            _require(
                isinstance(state, allowed),
                f"{what} is a {_type_name(effect)}, whose {field!r} may not hold a "
                f"{_type_name(state)}",
            )
        if isinstance(effect, MoveNoClobber):
            _require(
                effect.source != effect.destination,
                f"{what} moves {effect.source!r} onto itself; source and destination must differ",
            )


def _phase6_unique_effect_ids(spec: TransactionSpec) -> None:
    exact_seen: set[str] = set()
    first_by_portability_key: dict[str, str] = {}
    for effect in spec.effects:
        _require(
            effect.effect_id not in exact_seen,
            f"duplicate effect_id: {effect.effect_id!r}",
        )
        exact_seen.add(effect.effect_id)

        key = portability_equivalence_key(effect.effect_id)
        previous = first_by_portability_key.setdefault(key, effect.effect_id)
        _require(
            previous == effect.effect_id,
            f"portability-equivalent effect_id values {previous!r} and "
            f"{effect.effect_id!r} would generate aliasing scratch leaves",
        )


def _phase7_dependencies(spec: TransactionSpec) -> None:
    order = {effect.effect_id: index for index, effect in enumerate(spec.effects)}
    seen: set[tuple[str, str]] = set()
    for index, dependency in enumerate(spec.dependencies):
        what = f"dependencies[{index}]"
        for endpoint in (dependency.before, dependency.after):
            _require(endpoint in order, f"{what} names unknown effect {endpoint!r}")
        _require(
            dependency.before != dependency.after,
            f"{what} makes effect {dependency.before!r} depend on itself",
        )
        edge = (dependency.before, dependency.after)
        _require(edge not in seen, f"duplicate dependency: {dependency.before!r} -> {dependency.after!r}")
        seen.add(edge)
        _require(
            order[dependency.before] < order[dependency.after],
            f"{what} requires {dependency.before!r} before {dependency.after!r}, but the "
            f"authoritative effect order places it after",
        )


def _phase8_surface_shape(spec: TransactionSpec) -> None:
    for label in ("initial_surface", "final_surface"):
        seen: set[str] = set()
        for entry in getattr(spec, label):
            _require(entry.path not in seen, f"{label} declares a duplicate path: {entry.path!r}")
            seen.add(entry.path)


def _phase9_registration(spec: TransactionSpec) -> None:
    validate_v2_members(spec)


def _surface_map(spec: TransactionSpec, label: str) -> dict[str, PathState]:
    return {entry.path: entry.state for entry in getattr(spec, label)}


def _phase10_coverage(
    timelines: tuple[PathTimeline, ...],
    initial: dict[str, PathState],
    final: dict[str, PathState],
) -> None:
    effect_paths = {timeline.path for timeline in timelines}
    for label, declared in (("initial_surface", initial), ("final_surface", final)):
        undeclared = sorted(effect_paths - set(declared))
        _require(
            not undeclared,
            f"{label} omits {len(undeclared)} path(s) that an effect mutates: {undeclared}",
        )
        untouched = sorted(set(declared) - effect_paths)
        _require(
            not untouched,
            f"{label} declares {len(untouched)} path(s) that no effect mutates: {untouched}",
        )


def _phase11_endpoints(
    timelines: tuple[PathTimeline, ...],
    initial: dict[str, PathState],
    final: dict[str, PathState],
) -> None:
    for timeline in timelines:
        first = timeline.occurrences[0]
        last = timeline.occurrences[-1]
        _require(
            first.pre == initial[timeline.path],
            f"path {timeline.path!r} declares an initial state of {initial[timeline.path]!r} "
            f"but effect {first.effect_id!r} expects {first.pre!r}",
        )
        _require(
            last.post == final[timeline.path],
            f"path {timeline.path!r} declares a final state of {final[timeline.path]!r} "
            f"but effect {last.effect_id!r} leaves it {last.post!r}",
        )


@dataclass(slots=True)
class _PathTrieNode:
    children: dict[str, _PathTrieNode] = dataclass_field(default_factory=dict)
    surface: tuple[str, PathState] | None = None
    creator: tuple[str, int] | None = None


def _insert_path(root: _PathTrieNode, path: str) -> _PathTrieNode:
    node = root
    for component in path.split("/"):
        child = node.children.get(component)
        if child is None:
            child = _PathTrieNode()
            node.children[component] = child
        node = child
    return node


def _phase12_surface_tree(surface: dict[str, PathState], label: str) -> None:
    root = _PathTrieNode()
    for path, state in surface.items():
        _insert_path(root, path).surface = (path, state)

    constraint: tuple[str, PathState] | None
    stack: list[tuple[_PathTrieNode, tuple[str, PathState] | None]] = [(root, None)]
    while stack:
        node, constraint = stack.pop()
        next_constraint = constraint
        if node.surface is not None:
            path, state = node.surface
            if constraint is not None:
                ancestor_path, ancestor_state = constraint
                _require(
                    isinstance(state, AbsentState),
                    f"{label} declares {path!r} beneath {ancestor_path!r}, which is "
                    f"{_type_name(ancestor_state)} and so cannot contain entries; "
                    f"{path!r} must be declared absent",
                )
            next_constraint = None if isinstance(state, DirectoryState) else (path, state)
        for child in node.children.values():
            stack.append((child, next_constraint))


def _phase13_ancestor_ordering(spec: TransactionSpec) -> None:
    root = _PathTrieNode()
    has_creator = False
    for index, effect in enumerate(spec.effects):
        if isinstance(effect, CreateDirectory):
            _insert_path(root, effect.path).creator = (effect.path, index)
            has_creator = True
    if not has_creator:
        return

    for index, effect in enumerate(spec.effects):
        for occurrence in occurrences(effect):
            node = root
            for component in occurrence.path.split("/")[:-1]:
                child = node.children.get(component)
                if child is None:
                    break
                node = child
                if node.creator is None:
                    continue
                creator_path, creator_index = node.creator
                _require(
                    creator_index < index,
                    f"effect {effect.effect_id!r} touches {occurrence.path!r} beneath "
                    f"{creator_path!r}, which this transaction creates later; outer "
                    f"directory creation must come before every affected descendant",
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
        fulfills=spec.fulfills,
        registered_paths=spec.registered_paths,
    )


def compile_spec(spec: TransactionSpec) -> CompiledSpec:
    """Validate ``spec`` and return A2's frozen filesystem-independent proof."""
    _phase1_structure(spec)
    _phase2_fingerprints(spec)
    _phase3_path_grammar(spec)
    _phase4_alias_distinctness(spec)
    _phase5_variant_shapes(spec)
    _phase6_unique_effect_ids(spec)
    _phase7_dependencies(spec)
    _phase8_surface_shape(spec)
    _phase9_registration(spec)

    timelines = build_timelines(spec.effects)
    initial = _surface_map(spec, "initial_surface")
    final = _surface_map(spec, "final_surface")

    _phase10_coverage(timelines, initial, final)
    _phase11_endpoints(timelines, initial, final)
    _phase12_surface_tree(initial, "initial_surface")
    _phase12_surface_tree(final, "final_surface")
    _phase13_ancestor_ordering(spec)

    return _new_compiled_spec(spec=_canonicalize(spec), timelines=timelines)
