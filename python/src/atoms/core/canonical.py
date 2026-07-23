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
from atoms.core.fingerprint import (
    AbsentState,
    DirectoryState,
    FileState,
    PathState,
    SymlinkState,
)
from atoms.core.spec import Dependency, SurfaceEntry, TransactionSpec


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
