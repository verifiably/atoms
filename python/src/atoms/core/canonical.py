"""Deterministic canonical serialization of a TransactionSpec (design §13.3, §7.2)."""

from __future__ import annotations

import json
from functools import singledispatch
from typing import Any

from atoms.core.effects import (
    CreateDirectory,
    CreateFileNoClobber,
    DeletePath,
    Effect,
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
from atoms.core.spec import (
    SCHEMA_VERSION,
    Dependency,
    SurfaceEntry,
    TransactionSpec,
    validate_v2_members,
)


@singledispatch
def _state_obj(state: PathState) -> dict[str, Any]:
    raise TypeError(f"unknown path state: {type(state).__name__}")


@_state_obj.register
def _(state: AbsentState) -> dict[str, Any]:
    return {"type": "absent"}


@_state_obj.register
def _(state: FileState) -> dict[str, Any]:
    return {
        "type": "file",
        "content_hash": state.content_hash,
        "mode": state.mode,
        "byte_len": state.byte_len,
    }


@_state_obj.register
def _(state: DirectoryState) -> dict[str, Any]:
    return {"type": "directory", "mode": state.mode}


@_state_obj.register
def _(state: SymlinkState) -> dict[str, Any]:
    return {"type": "symlink", "target": state.target, "mode": state.mode}


@singledispatch
def _effect_obj(effect: Effect) -> dict[str, Any]:
    raise TypeError(f"unknown effect variant: {type(effect).__name__}")


@_effect_obj.register
def _(effect: ReplaceFile) -> dict[str, Any]:
    return {
        "type": "ReplaceFile",
        "effect_id": effect.effect_id,
        "path": effect.path,
        "pre": _state_obj(effect.pre),
        "post": _state_obj(effect.post),
    }


@_effect_obj.register
def _(effect: CreateFileNoClobber) -> dict[str, Any]:
    return {
        "type": "CreateFileNoClobber",
        "effect_id": effect.effect_id,
        "path": effect.path,
        "post": _state_obj(effect.post),
    }


@_effect_obj.register
def _(effect: DeletePath) -> dict[str, Any]:
    return {
        "type": "DeletePath",
        "effect_id": effect.effect_id,
        "path": effect.path,
        "pre": _state_obj(effect.pre),
    }


@_effect_obj.register
def _(effect: MoveNoClobber) -> dict[str, Any]:
    return {
        "type": "MoveNoClobber",
        "effect_id": effect.effect_id,
        "source": effect.source,
        "destination": effect.destination,
        "source_pre": _state_obj(effect.source_pre),
    }


@_effect_obj.register
def _(effect: CreateDirectory) -> dict[str, Any]:
    return {
        "type": "CreateDirectory",
        "effect_id": effect.effect_id,
        "path": effect.path,
        "post": _state_obj(effect.post),
    }


def _surface_obj(entry: SurfaceEntry) -> dict[str, Any]:
    return {"path": entry.path, "state": _state_obj(entry.state)}


def _dependency_obj(dep: Dependency) -> dict[str, Any]:
    return {"before": dep.before, "after": dep.after}


def canonical_obj(spec: TransactionSpec) -> dict[str, Any]:
    # Set-like fields (surfaces keyed by path; dependencies) are sorted here so canonical
    # bytes are a pure function of content, independent of a spec's tuple order — even a
    # spec built directly, bypassing build_spec. `effects` is an ordered sequence (its
    # order is semantic, design §5.2) and is emitted as-is.
    initial = sorted(spec.initial_surface, key=lambda e: e.path)
    final = sorted(spec.final_surface, key=lambda e: e.path)
    deps = sorted(spec.dependencies, key=lambda d: (d.before, d.after))
    return {
        "schema_version": spec.schema_version,
        "consumer_tag": spec.consumer_tag,
        "intent_digest": spec.intent_digest,
        "initial_surface": [_surface_obj(e) for e in initial],
        "final_surface": [_surface_obj(e) for e in final],
        "effects": [_effect_obj(e) for e in spec.effects],
        "dependencies": [_dependency_obj(d) for d in deps],
        "fulfills": spec.fulfills,
        "registered_paths": list(spec.registered_paths),
    }


def canonical_json(spec: TransactionSpec) -> str:
    return json.dumps(
        canonical_obj(spec),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def canonical_bytes(spec: TransactionSpec) -> bytes:
    return canonical_json(spec).encode("utf-8")


# Scalar-type sentinels: distinct objects compared with `is`.
_STR = object()
_INT = object()

# Each state variant: (class, ((field, scalar-sentinel), ...)).
_STATE_SPEC: dict[str, tuple[Any, tuple[tuple[str, Any], ...]]] = {
    "absent": (AbsentState, ()),
    "file": (
        FileState,
        (("content_hash", _STR), ("mode", _INT), ("byte_len", _INT)),
    ),
    "directory": (DirectoryState, (("mode", _INT),)),
    "symlink": (SymlinkState, (("target", _STR), ("mode", _INT))),
}

# Each effect variant: (class, ((field, spec), ...)) where spec is _STR for a
# string field or a tuple of the state classes that field is permitted to hold.
_EFFECT_SPEC: dict[str, tuple[Any, tuple[tuple[str, Any], ...]]] = {
    "ReplaceFile": (
        ReplaceFile,
        (
            ("effect_id", _STR),
            ("path", _STR),
            ("pre", (FileState,)),
            ("post", (FileState,)),
        ),
    ),
    "CreateFileNoClobber": (
        CreateFileNoClobber,
        (("effect_id", _STR), ("path", _STR), ("post", (FileState,))),
    ),
    "DeletePath": (
        DeletePath,
        (
            ("effect_id", _STR),
            ("path", _STR),
            ("pre", (FileState, SymlinkState)),
        ),
    ),
    "MoveNoClobber": (
        MoveNoClobber,
        (
            ("effect_id", _STR),
            ("source", _STR),
            ("destination", _STR),
            ("source_pre", (FileState,)),
        ),
    ),
    "CreateDirectory": (
        CreateDirectory,
        (
            ("effect_id", _STR),
            ("path", _STR),
            ("post", (DirectoryState,)),
        ),
    ),
}


def _ensure(condition: object, message: str) -> None:
    if not condition:
        raise SpecValidationError(message)


def _as_dict(value: Any, ctx: str) -> dict[Any, Any]:
    _ensure(type(value) is dict, f"{ctx} must be an object")
    for field in value:
        _ensure(
            type(field) is str,
            (
                f"{ctx} has an unexpected object key; "
                "object keys must use the exact string type"
            ),
        )
    return value


def _as_list(value: Any, ctx: str) -> list[Any]:
    _ensure(type(value) is list, f"{ctx} must be an array")
    return value


def _as_str(value: Any, ctx: str) -> str:
    _ensure(type(value) is str, f"{ctx} must be a string")
    return value


def _as_int(value: Any, ctx: str) -> int:
    _ensure(type(value) is int, f"{ctx} must be an integer")
    return value


def _field_label(field: object) -> str:
    if type(field) is str:
        return repr(field)
    return f"<non-string {type(field).__name__}>"


def _require_only(
    what: str,
    obj: dict[Any, Any],
    allowed: tuple[str, ...],
) -> None:
    for field in allowed:
        _ensure(field in obj, f"{what} is missing field {field!r}")
    extra = [field for field in obj if field not in allowed]
    labels = sorted(_field_label(field) for field in extra)
    _ensure(not labels, f"{what} has unexpected field(s): {labels}")


def _decode_scalar(sentinel: Any, value: Any, ctx: str) -> Any:
    return _as_str(value, ctx) if sentinel is _STR else _as_int(value, ctx)


def _decode_discriminator(
    obj: dict[Any, Any],
    ctx: str,
    kind: str,
    variants: dict[str, Any],
) -> str:
    _ensure("type" in obj, f"{ctx} is missing field 'type'")
    tag = _as_str(obj["type"], f"{ctx} {kind} discriminator")
    _ensure(
        tag in variants,
        f"{ctx}: unknown {kind} discriminator: {tag!r}",
    )
    return tag


def _decode_state(value: Any, ctx: str) -> PathState:
    obj = _as_dict(value, ctx)
    tag = _decode_discriminator(obj, ctx, "path-state", _STATE_SPEC)
    cls, fields = _STATE_SPEC[tag]
    _require_only(
        f"{ctx} state {tag}",
        obj,
        ("type", *(name for name, _ in fields)),
    )
    kwargs = {
        name: _decode_scalar(sentinel, obj[name], f"{ctx}.{name}")
        for name, sentinel in fields
    }
    return cls(**kwargs)


def _decode_effect(value: Any, ctx: str) -> Effect:
    obj = _as_dict(value, ctx)
    tag = _decode_discriminator(obj, ctx, "effect", _EFFECT_SPEC)
    cls, fields = _EFFECT_SPEC[tag]
    _require_only(
        f"{ctx} effect {tag}",
        obj,
        ("type", *(name for name, _ in fields)),
    )
    kwargs: dict[str, Any] = {}
    for name, field_spec in fields:
        if field_spec is _STR:
            kwargs[name] = _as_str(obj[name], f"{ctx}.{name}")
        else:
            state = _decode_state(obj[name], f"{ctx}.{name}")
            _ensure(
                isinstance(state, field_spec),
                (
                    f"{ctx} effect {tag} field {name!r} may not hold "
                    f"a {type(state).__name__}"
                ),
            )
            kwargs[name] = state
    return cls(**kwargs)


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    seen: dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            raise SpecValidationError(f"duplicate JSON key: {key!r}")
        seen[key] = value
    return seen


def _decode_canonical_obj(obj: object) -> TransactionSpec:
    spec = _as_dict(obj, "spec")
    _require_only(
        "spec",
        spec,
        (
            "schema_version",
            "consumer_tag",
            "intent_digest",
            "initial_surface",
            "final_surface",
            "effects",
            "dependencies",
            "fulfills",
            "registered_paths",
        ),
    )
    version = _as_int(spec["schema_version"], "schema_version")
    _ensure(
        version == SCHEMA_VERSION,
        f"unsupported schema_version: {version!r}",
    )

    surfaces: dict[str, tuple[SurfaceEntry, ...]] = {}
    for key in ("initial_surface", "final_surface"):
        entries = []
        for index, raw in enumerate(_as_list(spec[key], key)):
            item = _as_dict(raw, f"{key}[{index}]")
            _require_only(f"{key}[{index}]", item, ("path", "state"))
            entries.append(
                SurfaceEntry(
                    path=_as_str(item["path"], f"{key}[{index}].path"),
                    state=_decode_state(
                        item["state"],
                        f"{key}[{index}].state",
                    ),
                )
            )
        surfaces[key] = tuple(entries)

    dependencies = []
    for index, raw in enumerate(
        _as_list(spec["dependencies"], "dependencies")
    ):
        item = _as_dict(raw, f"dependencies[{index}]")
        _require_only(f"dependencies[{index}]", item, ("before", "after"))
        dependencies.append(
            Dependency(
                before=_as_str(
                    item["before"],
                    f"dependencies[{index}].before",
                ),
                after=_as_str(
                    item["after"],
                    f"dependencies[{index}].after",
                ),
            )
        )

    effects = tuple(
        _decode_effect(raw, f"effects[{index}]")
        for index, raw in enumerate(_as_list(spec["effects"], "effects"))
    )
    decoded = TransactionSpec(
        schema_version=version,
        consumer_tag=_as_str(spec["consumer_tag"], "consumer_tag"),
        intent_digest=_as_str(spec["intent_digest"], "intent_digest"),
        initial_surface=surfaces["initial_surface"],
        final_surface=surfaces["final_surface"],
        effects=effects,
        dependencies=tuple(dependencies),
        fulfills=(
            None
            if spec["fulfills"] is None
            else _as_str(spec["fulfills"], "fulfills")
        ),
        registered_paths=tuple(
            _as_str(path, f"registered_paths[{index}]")
            for index, path in enumerate(_as_list(spec["registered_paths"], "registered_paths"))
        ),
    )
    validate_v2_members(decoded)
    return decoded


def from_canonical_obj(obj: object) -> TransactionSpec:
    try:
        return _decode_canonical_obj(obj)
    except SpecValidationError:
        raise
    except Exception as exc:
        raise SpecValidationError("malformed canonical object") from exc


def from_canonical_json(text: str) -> TransactionSpec:
    _as_str(text, "canonical JSON")
    try:
        parsed = json.loads(text, object_pairs_hook=_no_duplicate_keys)
    except SpecValidationError:
        raise
    except Exception as exc:
        raise SpecValidationError(f"malformed canonical JSON: {exc}") from exc
    return from_canonical_obj(parsed)


def from_canonical_bytes(data: bytes) -> TransactionSpec:
    _ensure(type(data) is bytes, "canonical bytes must be bytes")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SpecValidationError(
            f"canonical bytes are not valid UTF-8: {exc}"
        ) from exc
    return from_canonical_json(text)
