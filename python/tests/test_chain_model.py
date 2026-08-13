from __future__ import annotations

import json
from typing import Any, cast

import pytest

from atoms.chain.errors import ChainStateInvalid
from atoms.chain.model import (
    ChainOutcome,
    GenesisEntry,
    IntentEntry,
    PathStateJSON,
    RegisteredEntry,
    SettledEntry,
    decode_entry,
    encode_entry,
    entry_digest,
    state_from_json,
    state_to_json,
)
from atoms.core.errors import ProtocolError
from atoms.core.fingerprint import (
    ABSENT,
    DirectoryState,
    FileState,
    PathState,
    SymlinkState,
)

HEX_A = "a" * 64
HEX_B = "b" * 64
CONTENT_A = "sha256:" + "1" * 64
ABSENT_JSON: PathStateJSON = (("kind", "absent"),)
FILE_JSON: PathStateJSON = (
    ("kind", "file"),
    ("content_hash", "1" * 64),
    ("mode", "0o640"),
    ("byte_len", "7"),
)


@pytest.mark.parametrize(
    ("previous", "entry", "expected"),
    [
        (
            None,
            GenesisEntry(payload=b"\x00\xff", baseline=(("a", ABSENT_JSON),)),
            (
                '{"baseline":[["a",[["kind","absent"]]]],'
                '"class":"genesis","payload":"AP8=","previous":null}'
            ),
        ),
        (
            HEX_A,
            RegisteredEntry(
                txid="tx_1",
                intent_digest=CONTENT_A,
                consumer_tag="consumer_1",
                initial=(("a", ABSENT_JSON),),
                final=(("b", FILE_JSON),),
                fulfills=None,
            ),
            '{"class":"registered","consumer_tag":"consumer_1",'
            '"final":[["b",[["kind","file"],["content_hash","'
            + "1" * 64
            + '"],["mode","0o640"],["byte_len","7"]]]],'
            '"fulfills":null,"initial":[["a",[["kind","absent"]]]],'
            '"intent_digest":"sha256:'
            + "1" * 64
            + '","previous":"'
            + HEX_A
            + '","txid":"tx_1"}',
        ),
        (
            HEX_A,
            SettledEntry(
                txid="tx_1",
                registration=HEX_B,
                outcome=ChainOutcome.COMMITTED,
            ),
            '{"class":"settled","outcome":"committed","previous":"'
            + HEX_A
            + '","registration":"'
            + HEX_B
            + '","txid":"tx_1"}',
        ),
        (
            HEX_A,
            SettledEntry(
                txid="tx_1",
                registration=HEX_B,
                outcome=ChainOutcome.ROLLED_BACK,
            ),
            '{"class":"settled","outcome":"rolled-back","previous":"'
            + HEX_A
            + '","registration":"'
            + HEX_B
            + '","txid":"tx_1"}',
        ),
        (
            HEX_A,
            IntentEntry(payload=b"\xfb\xff"),
            '{"class":"intent","payload":"+/8=","previous":"' + HEX_A + '"}',
        ),
    ],
    ids=("genesis", "registered", "committed", "rolled-back", "intent"),
)
def test_entries_round_trip_in_the_exact_canonical_wire_shape(
    previous: str | None,
    entry: GenesisEntry | RegisteredEntry | SettledEntry | IntentEntry,
    expected: str,
) -> None:
    encoded = encode_entry(previous, entry)

    assert encoded == expected.encode()
    assert decode_entry(encoded) == (previous, entry)


def test_entry_digest_is_the_raw_lowercase_sha256() -> None:
    assert entry_digest(b"abc") == (
        "ba7816bf8f01cfea414140de5dae2223"
        "b00361a396177a9cb410ff61f20015ad"
    )


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (ABSENT, ABSENT_JSON),
        (
            FileState(content_hash=CONTENT_A, mode=0o640, byte_len=7),
            FILE_JSON,
        ),
        (
            SymlinkState(target="../target", mode=0o777),
            (("kind", "symlink"), ("target", "../target"), ("mode", "0o777")),
        ),
        (
            DirectoryState(mode=0o750),
            (("kind", "directory"), ("mode", "0o750")),
        ),
    ],
    ids=("absent", "file", "symlink", "directory"),
)
def test_path_states_use_the_closed_string_pair_shape(
    state: PathState, expected: PathStateJSON
) -> None:
    encoded = state_to_json(state)

    assert encoded == expected
    assert state_from_json(encoded) == state


def _json_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


@pytest.mark.parametrize(
    "malformed",
    [
        encode_entry(None, GenesisEntry(b"payload", ()))[:-1],
        b'{"class":"unknown","previous":null}',
        _json_bytes(
            {
                "class": "genesis",
                "previous": None,
                "payload": "",
                "baseline": [["b", [["kind", "absent"]]], ["a", [["kind", "absent"]]]],
            }
        ),
        _json_bytes(
            {
                "class": "genesis",
                "previous": None,
                "payload": "***",
                "baseline": [],
            }
        ),
        _json_bytes(
            {
                "class": "settled",
                "previous": HEX_A,
                "txid": "tx_1",
                "registration": HEX_B,
                "outcome": "unknown",
            }
        ),
        b'{"baseline":[],"class":"genesis","payload":"","previous":null,"extra":"x"}',
        b'{"baseline":[],"class":"genesis","previous":null}',
        b'{"baseline":[],"class":"genesis","payload":"","payload":"","previous":null}',
        b'{ "baseline":[],"class":"genesis","payload":"","previous":null}',
    ],
    ids=(
        "truncated",
        "unknown-class",
        "unsorted-baseline",
        "invalid-base64",
        "unknown-outcome",
        "extra-field",
        "missing-field",
        "duplicate-field",
        "noncanonical-bytes",
    ),
)
def test_decode_refuses_every_malformed_external_envelope(malformed: bytes) -> None:
    with pytest.raises(ChainStateInvalid):
        decode_entry(malformed)


def test_decode_translates_excessive_json_nesting_to_chain_state_invalid() -> None:
    with pytest.raises(ChainStateInvalid):
        decode_entry(b"[" * 100_000 + b"]" * 100_000)


@pytest.mark.parametrize(
    "malformed",
    [
        (("kind", "file"), ("mode", "0o644"), ("content_hash", "1" * 64), ("byte_len", "1")),
        (("kind", "file"), ("content_hash", "A" * 64), ("mode", "0o644"), ("byte_len", "1")),
        (("kind", "directory"), ("mode", "0o0750")),
        (("kind", "file"), ("content_hash", "1" * 64), ("mode", "0o644"), ("byte_len", "01")),
        (("kind", "absent"), ("extra", "value")),
        (("kind", "unknown"),),
    ],
    ids=("wrong-order", "uppercase-hash", "noncanonical-mode", "noncanonical-length", "extra", "unknown-kind"),
)
def test_state_decoder_refuses_noncanonical_or_unknown_external_facts(malformed: object) -> None:
    with pytest.raises(ChainStateInvalid):
        state_from_json(cast(PathStateJSON, malformed))


@pytest.mark.parametrize(
    "malformed",
    [
        (
            ("kind", "file"),
            ("content_hash", "1" * 64),
            ("mode", "0o644"),
            ("byte_len", "9" * 5_000),
        ),
        (("kind", "directory"), ("mode", "0o" + "7" * 5_000)),
    ],
    ids=("huge-byte-len", "huge-mode"),
)
def test_state_decoder_refuses_huge_numeric_evidence_without_leaking_conversion_errors(
    malformed: PathStateJSON,
) -> None:
    with pytest.raises(ChainStateInvalid):
        state_from_json(malformed)


@pytest.mark.parametrize(
    "defect",
    ["file-mode", "file-byte-len", "symlink-mode", "directory-mode"],
)
def test_state_encoder_refuses_unbounded_engine_integers_as_protocol_errors(
    defect: str,
) -> None:
    huge = 1 << 20_000
    if defect == "file-mode":
        state: PathState = FileState(CONTENT_A, huge, 7)
    elif defect == "file-byte-len":
        state = FileState(CONTENT_A, 0o644, huge)
    elif defect == "symlink-mode":
        state = SymlinkState("target", huge)
    else:
        state = DirectoryState(huge)

    with pytest.raises(ProtocolError):
        state_to_json(state)


@pytest.mark.parametrize(
    ("previous", "entry"),
    [
        (HEX_A, GenesisEntry(b"payload", ())),
        (None, IntentEntry(b"payload")),
        (None, SettledEntry("tx", HEX_B, ChainOutcome.COMMITTED)),
        (
            HEX_A,
            RegisteredEntry(
                txid="bad/tx",
                intent_digest=CONTENT_A,
                consumer_tag="consumer",
                initial=(),
                final=(),
                fulfills=None,
            ),
        ),
        (
            "A" * 64,
            IntentEntry(b"payload"),
        ),
        (
            HEX_A,
            SettledEntry("tx", "A" * 64, ChainOutcome.COMMITTED),
        ),
        (
            HEX_A,
            RegisteredEntry(
                txid="tx",
                intent_digest="sha256:" + "A" * 64,
                consumer_tag="consumer",
                initial=(),
                final=(),
                fulfills=None,
            ),
        ),
        (
            HEX_A,
            RegisteredEntry(
                txid="tx",
                intent_digest=CONTENT_A,
                consumer_tag="consumer",
                initial=(),
                final=(),
                fulfills="A" * 64,
            ),
        ),
        (
            HEX_A,
            RegisteredEntry(
                txid="tx",
                intent_digest=CONTENT_A,
                consumer_tag="consumer",
                initial=(("b", ABSENT_JSON), ("a", ABSENT_JSON)),
                final=(),
                fulfills=None,
            ),
        ),
    ],
    ids=(
        "linked-genesis",
        "unlinked-intent",
        "unlinked-settlement",
        "bad-txid",
        "unsorted-surface",
        "bad-previous",
        "bad-registration",
        "bad-intent-digest",
        "bad-fulfills",
    ),
)
def test_encode_refuses_engine_created_protocol_defects(
    previous: str | None,
    entry: GenesisEntry | RegisteredEntry | SettledEntry | IntentEntry,
) -> None:
    with pytest.raises(ProtocolError):
        encode_entry(previous, entry)


def test_encode_translates_a_missing_entry_slot_to_protocol_error() -> None:
    forged = object.__new__(GenesisEntry)

    with pytest.raises(ProtocolError):
        encode_entry(None, forged)


@pytest.mark.parametrize(
    "state_type",
    [FileState, SymlinkState, DirectoryState],
    ids=("file", "symlink", "directory"),
)
def test_state_encoder_translates_missing_engine_slots_to_protocol_error(
    state_type: type[FileState | SymlinkState | DirectoryState],
) -> None:
    forged = object.__new__(state_type)

    with pytest.raises(ProtocolError):
        state_to_json(cast(PathState, forged))
