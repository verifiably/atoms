"""Canonical chain entries and path-state evidence."""

from __future__ import annotations

import base64
import binascii
import enum
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, NoReturn, cast

from atoms.chain.errors import ChainStateInvalid
from atoms.core.errors import AtomsError, ProtocolError, SpecValidationError
from atoms.core.fingerprint import (
    ABSENT,
    AbsentState,
    DirectoryState,
    FileState,
    PathState,
    SymlinkState,
)
from atoms.core.identifiers import is_valid_identifier
from atoms.core.paths import require_rel_path

PathStateJSON = tuple[tuple[str, str], ...]


class ChainOutcome(enum.Enum):
    COMMITTED = "committed"
    ROLLED_BACK = "rolled-back"


@dataclass(frozen=True, slots=True)
class GenesisEntry:
    payload: bytes
    baseline: tuple[tuple[str, PathStateJSON], ...]


@dataclass(frozen=True, slots=True)
class RegisteredEntry:
    txid: str
    intent_digest: str
    consumer_tag: str
    initial: tuple[tuple[str, PathStateJSON], ...]
    final: tuple[tuple[str, PathStateJSON], ...]
    fulfills: str | None


@dataclass(frozen=True, slots=True)
class SettledEntry:
    txid: str
    registration: str
    outcome: ChainOutcome


@dataclass(frozen=True, slots=True)
class IntentEntry:
    payload: bytes


Entry = GenesisEntry | RegisteredEntry | SettledEntry | IntentEntry

_HEX = re.compile(r"^[0-9a-f]{64}$")
_CONTENT_HASH = re.compile(r"^sha256:([0-9a-f]{64})$")
_EMPTY_HASH = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
_MAX_INTEGER = 2**63 - 1
_MAX_MODE = 0o7777


def _fail(error: type[AtomsError], message: str) -> NoReturn:
    raise error(message)


def _require(condition: object, error: type[AtomsError], message: str) -> None:
    if not condition:
        _fail(error, message)


def _string(value: object, error: type[AtomsError], label: str) -> str:
    _require(type(value) is str, error, f"{label} must be an exact str")
    return cast(str, value)


def _digest(value: object, error: type[AtomsError], label: str) -> str:
    value = _string(value, error, label)
    _require(
        _HEX.fullmatch(value) is not None,
        error,
        f"{label} must be 64 lowercase hexadecimal characters",
    )
    return value


def _identifier(value: object, error: type[AtomsError], label: str) -> str:
    value = _string(value, error, label)
    _require(
        is_valid_identifier(value),
        error,
        f"{label} must be 1–64 characters of [A-Za-z0-9_-]",
    )
    return value


def _path(value: object, error: type[AtomsError], label: str) -> str:
    value = _string(value, error, label)
    try:
        return require_rel_path(label, value)
    except SpecValidationError as caught:
        raise error(str(caught)) from caught


def _mode(value: object, error: type[AtomsError], label: str) -> int:
    value = _string(value, error, label)
    try:
        parsed = int(value, 8)
    except ValueError as caught:
        raise error(f"{label} must be a canonical octal mode") from caught
    _require(
        0 <= parsed <= _MAX_MODE and oct(parsed) == value,
        error,
        f"{label} must be a canonical octal mode in 0o0..0o7777",
    )
    return parsed


def _decimal(value: object, error: type[AtomsError], label: str) -> int:
    value = _string(value, error, label)
    canonical = value == "0" or (
        value[:1] in "123456789" and value.isascii() and value.isdecimal()
    )
    _require(canonical, error, f"{label} must be a canonical decimal integer")
    parsed = int(value)
    _require(parsed <= _MAX_INTEGER, error, f"{label} exceeds 2**63 - 1")
    return parsed


def _target(value: object, error: type[AtomsError], label: str) -> str:
    value = _string(value, error, label)
    _require(bool(value), error, f"{label} may not be empty")
    _require("\x00" not in value, error, f"{label} contains a NUL byte")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as caught:
        raise error(f"{label} is not encodable as UTF-8") from caught
    return value


def _state_from_json(
    data: object, error: type[AtomsError], label: str
) -> PathState:
    _require(type(data) is tuple, error, f"{label} must be an exact tuple")
    pairs = cast(tuple[object, ...], data)
    for index, pair in enumerate(pairs):
        _require(
            type(pair) is tuple and len(pair) == 2,
            error,
            f"{label}[{index}] must be an exact two-item tuple",
        )
        pair_items = cast(tuple[object, object], pair)
        _require(
            type(pair_items[0]) is str and type(pair_items[1]) is str,
            error,
            f"{label}[{index}] must contain exact str values",
        )
    typed = cast(PathStateJSON, pairs)
    keys = tuple(key for key, _ in typed)
    values = dict(typed)
    _require(bool(typed) and keys[0] == "kind", error, f"{label} must start with kind")
    kind = values["kind"]
    if kind == "absent":
        _require(keys == ("kind",), error, f"{label} absent facts are not exact")
        return ABSENT
    if kind == "file":
        _require(
            keys == ("kind", "content_hash", "mode", "byte_len"),
            error,
            f"{label} file facts are not exact",
        )
        content_hash = _digest(values["content_hash"], error, f"{label}.content_hash")
        mode = _mode(values["mode"], error, f"{label}.mode")
        byte_len = _decimal(values["byte_len"], error, f"{label}.byte_len")
        _require(
            (content_hash == _EMPTY_HASH) == (byte_len == 0),
            error,
            f"{label} has inconsistent empty-file hash and length",
        )
        return FileState(f"sha256:{content_hash}", mode, byte_len)
    if kind == "symlink":
        _require(
            keys == ("kind", "target", "mode"),
            error,
            f"{label} symlink facts are not exact",
        )
        return SymlinkState(
            _target(values["target"], error, f"{label}.target"),
            _mode(values["mode"], error, f"{label}.mode"),
        )
    if kind == "directory":
        _require(
            keys == ("kind", "mode"),
            error,
            f"{label} directory facts are not exact",
        )
        return DirectoryState(_mode(values["mode"], error, f"{label}.mode"))
    _fail(error, f"{label}.kind is outside the closed path-state domain")


def state_from_json(data: PathStateJSON) -> PathState:
    """Decode exact path-state evidence from disk."""

    return _state_from_json(data, ChainStateInvalid, "path state")


def state_to_json(state: PathState) -> PathStateJSON:
    """Encode one engine path state to the chain's closed fact vocabulary."""

    if type(state) is AbsentState:
        return (("kind", "absent"),)
    if type(state) is FileState:
        match = _CONTENT_HASH.fullmatch(state.content_hash) if type(state.content_hash) is str else None
        _require(match is not None, ProtocolError, "file content_hash must match sha256:<64 lowercase hex>")
        _require(type(state.mode) is int, ProtocolError, "file mode must be an exact int")
        _require(type(state.byte_len) is int, ProtocolError, "file byte_len must be an exact int")
        result: PathStateJSON = (
            ("kind", "file"),
            ("content_hash", cast(re.Match[str], match).group(1)),
            ("mode", oct(state.mode)),
            ("byte_len", str(state.byte_len)),
        )
    elif type(state) is SymlinkState:
        _require(type(state.mode) is int, ProtocolError, "symlink mode must be an exact int")
        _target(state.target, ProtocolError, "symlink target")
        result = (
            ("kind", "symlink"),
            ("target", state.target),
            ("mode", oct(state.mode)),
        )
    elif type(state) is DirectoryState:
        _require(type(state.mode) is int, ProtocolError, "directory mode must be an exact int")
        result = (("kind", "directory"), ("mode", oct(state.mode)))
    else:
        _fail(ProtocolError, f"unknown path state {type(state).__name__}")
    _state_from_json(result, ProtocolError, "path state")
    return result


def _surface(
    value: object, error: type[AtomsError], label: str
) -> tuple[tuple[str, PathStateJSON], ...]:
    _require(type(value) is tuple, error, f"{label} must be an exact tuple")
    entries = cast(tuple[object, ...], value)
    result: list[tuple[str, PathStateJSON]] = []
    for index, item in enumerate(entries):
        _require(
            type(item) is tuple and len(item) == 2,
            error,
            f"{label}[{index}] must be an exact path/state pair",
        )
        item_pair = cast(tuple[object, object], item)
        path = _path(item_pair[0], error, f"{label}[{index}].path")
        state = cast(PathStateJSON, item_pair[1])
        _state_from_json(state, error, f"{label}[{index}].state")
        result.append((path, state))
    paths = tuple(path for path, _ in result)
    _require(paths == tuple(sorted(paths)), error, f"{label} must be sorted by path")
    _require(len(paths) == len(set(paths)), error, f"{label} must be duplicate-free")
    return tuple(result)


def _surface_obj(
    value: object, error: type[AtomsError], label: str
) -> list[list[object]]:
    return [
        [path, [list(pair) for pair in state]]
        for path, state in _surface(value, error, label)
    ]


def _surface_from_obj(
    value: object, error: type[AtomsError], label: str
) -> tuple[tuple[str, PathStateJSON], ...]:
    _require(type(value) is list, error, f"{label} must be an array")
    result: list[tuple[str, PathStateJSON]] = []
    for index, item in enumerate(cast(list[object], value)):
        _require(
            type(item) is list and len(item) == 2,
            error,
            f"{label}[{index}] must be a path/state pair",
        )
        item_pair = cast(list[object], item)
        raw_state = item_pair[1]
        _require(type(raw_state) is list, error, f"{label}[{index}].state must be an array")
        pairs: list[tuple[str, str]] = []
        for pair_index, pair in enumerate(cast(list[object], raw_state)):
            _require(
                type(pair) is list and len(pair) == 2,
                error,
                f"{label}[{index}].state[{pair_index}] must be a pair",
            )
            pair_items = cast(list[object], pair)
            pairs.append(
                (
                    _string(pair_items[0], error, f"{label}[{index}] fact key"),
                    _string(pair_items[1], error, f"{label}[{index}] fact value"),
                )
            )
        result.append(
            (
                _string(item_pair[0], error, f"{label}[{index}].path"),
                tuple(pairs),
            )
        )
    return _surface(tuple(result), error, label)


def _payload(value: object, error: type[AtomsError], label: str) -> str:
    _require(type(value) is bytes, error, f"{label} must be exact bytes")
    return base64.b64encode(cast(bytes, value)).decode("ascii")


def _payload_from_obj(value: object, error: type[AtomsError], label: str) -> bytes:
    value = _string(value, error, label)
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as caught:
        raise error(f"{label} is not valid base64") from caught
    _require(
        base64.b64encode(decoded).decode("ascii") == value,
        error,
        f"{label} is not canonical base64",
    )
    return decoded


def _entry_obj(
    previous: object, entry: object, error: type[AtomsError]
) -> dict[str, object]:
    if type(entry) is GenesisEntry:
        _require(previous is None, error, "genesis must not have a previous digest")
        return {
            "class": "genesis",
            "previous": None,
            "payload": _payload(entry.payload, error, "genesis payload"),
            "baseline": _surface_obj(entry.baseline, error, "genesis baseline"),
        }
    previous = _digest(previous, error, "previous")
    if type(entry) is RegisteredEntry:
        intent_digest = _string(entry.intent_digest, error, "registered intent_digest")
        _require(
            _CONTENT_HASH.fullmatch(intent_digest) is not None,
            error,
            "registered intent_digest must match sha256:<64 lowercase hex>",
        )
        fulfills = entry.fulfills
        if fulfills is not None:
            fulfills = _digest(fulfills, error, "registered fulfills")
        return {
            "class": "registered",
            "previous": previous,
            "txid": _identifier(entry.txid, error, "registered txid"),
            "intent_digest": intent_digest,
            "consumer_tag": _identifier(
                entry.consumer_tag, error, "registered consumer_tag"
            ),
            "initial": _surface_obj(entry.initial, error, "registered initial"),
            "final": _surface_obj(entry.final, error, "registered final"),
            "fulfills": fulfills,
        }
    if type(entry) is SettledEntry:
        _require(
            type(entry.outcome) is ChainOutcome,
            error,
            "settled outcome must be an exact ChainOutcome",
        )
        return {
            "class": "settled",
            "previous": previous,
            "txid": _identifier(entry.txid, error, "settled txid"),
            "registration": _digest(
                entry.registration, error, "settled registration"
            ),
            "outcome": entry.outcome.value,
        }
    if type(entry) is IntentEntry:
        return {
            "class": "intent",
            "previous": previous,
            "payload": _payload(entry.payload, error, "intent payload"),
        }
    _fail(error, f"unknown chain entry {type(entry).__name__}")


def _canonical_bytes(obj: dict[str, object]) -> bytes:
    return json.dumps(
        obj,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def encode_entry(previous: str | None, entry: Entry) -> bytes:
    """Validate and canonically encode an engine-created chain entry."""

    return _canonical_bytes(_entry_obj(previous, entry, ProtocolError))


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ChainStateInvalid(f"duplicate chain-envelope JSON key {key!r}")
        result[key] = value
    return result


def _only(obj: dict[str, Any], fields: tuple[str, ...], label: str) -> None:
    _require(
        set(obj) == set(fields),
        ChainStateInvalid,
        f"{label} has missing or unexpected fields",
    )


def _previous(value: object) -> str | None:
    return None if value is None else _digest(value, ChainStateInvalid, "previous")


def _decode_obj(obj: dict[str, Any]) -> tuple[str | None, Entry]:
    entry_class = _string(obj.get("class"), ChainStateInvalid, "entry class")
    if entry_class == "genesis":
        _only(obj, ("class", "previous", "payload", "baseline"), "genesis")
        previous = _previous(obj["previous"])
        entry: Entry = GenesisEntry(
            _payload_from_obj(obj["payload"], ChainStateInvalid, "genesis payload"),
            _surface_from_obj(obj["baseline"], ChainStateInvalid, "genesis baseline"),
        )
    elif entry_class == "registered":
        _only(
            obj,
            (
                "class",
                "previous",
                "txid",
                "intent_digest",
                "consumer_tag",
                "initial",
                "final",
                "fulfills",
            ),
            "registered entry",
        )
        previous = _previous(obj["previous"])
        raw_fulfills = obj["fulfills"]
        fulfills = (
            None
            if raw_fulfills is None
            else _digest(raw_fulfills, ChainStateInvalid, "registered fulfills")
        )
        entry = RegisteredEntry(
            _string(obj["txid"], ChainStateInvalid, "registered txid"),
            _string(
                obj["intent_digest"],
                ChainStateInvalid,
                "registered intent_digest",
            ),
            _string(
                obj["consumer_tag"], ChainStateInvalid, "registered consumer_tag"
            ),
            _surface_from_obj(
                obj["initial"], ChainStateInvalid, "registered initial"
            ),
            _surface_from_obj(obj["final"], ChainStateInvalid, "registered final"),
            fulfills,
        )
    elif entry_class == "settled":
        _only(
            obj,
            ("class", "previous", "txid", "registration", "outcome"),
            "settled entry",
        )
        previous = _previous(obj["previous"])
        try:
            outcome = ChainOutcome(
                _string(obj["outcome"], ChainStateInvalid, "settled outcome")
            )
        except ValueError as caught:
            raise ChainStateInvalid(
                "settled outcome is outside the closed domain"
            ) from caught
        entry = SettledEntry(
            _string(obj["txid"], ChainStateInvalid, "settled txid"),
            _string(
                obj["registration"], ChainStateInvalid, "settled registration"
            ),
            outcome,
        )
    elif entry_class == "intent":
        _only(obj, ("class", "previous", "payload"), "intent entry")
        previous = _previous(obj["previous"])
        entry = IntentEntry(
            _payload_from_obj(obj["payload"], ChainStateInvalid, "intent payload")
        )
    else:
        raise ChainStateInvalid(f"unknown chain entry class {entry_class!r}")
    _entry_obj(previous, entry, ChainStateInvalid)
    return previous, entry


def decode_entry(data: bytes) -> tuple[str | None, Entry]:
    """Decode untrusted canonical chain bytes, rejecting every other representation."""

    _require(type(data) is bytes, ChainStateInvalid, "chain envelope must be exact bytes")
    try:
        text = data.decode("utf-8")
        raw = json.loads(text, object_pairs_hook=_no_duplicate_keys)
    except ChainStateInvalid:
        raise
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
        TypeError,
        RecursionError,
    ) as caught:
        raise ChainStateInvalid("chain envelope is not valid UTF-8 JSON") from caught
    _require(type(raw) is dict, ChainStateInvalid, "chain envelope must be an object")
    previous, entry = _decode_obj(cast(dict[str, Any], raw))
    try:
        canonical = _canonical_bytes(
            _entry_obj(previous, entry, ChainStateInvalid)
        )
    except UnicodeEncodeError as caught:
        raise ChainStateInvalid("chain envelope is not canonical UTF-8") from caught
    _require(canonical == data, ChainStateInvalid, "chain envelope bytes are not canonical")
    return previous, entry


def entry_digest(data: bytes) -> str:
    """Return the lowercase SHA-256 content name for an encoded envelope."""

    _require(type(data) is bytes, ProtocolError, "entry bytes must be exact bytes")
    return hashlib.sha256(data).hexdigest()
