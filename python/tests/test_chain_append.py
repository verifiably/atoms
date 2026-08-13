from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any, ClassVar, NamedTuple, cast

import pytest

from atoms.chain.errors import ChainStateInvalid
from atoms.chain.model import (
    ChainOutcome,
    Entry,
    GenesisEntry,
    IntentEntry,
    RegisteredEntry,
    SettledEntry,
    decode_entry,
    encode_entry,
    entry_digest,
)
from atoms.chain.read import (
    SurvivorAction,
    SurvivorDisposition,
    ValidatedChain,
    validate_chain,
)
from atoms.core.errors import ProtocolError
from atoms.core.scratch import CHAIN_LEAF
from atoms.fs.audit import AuditedBackend
from atoms.fs.linux import LinuxBackend

HEX_A = "a" * 64
CONTENT_A = "sha256:" + "1" * 64
STAGING_LEAF = ".#~stage"


class ChainDirectory(NamedTuple):
    backend: AuditedBackend
    project_fd: int
    chain_fd: int
    path: Path


@pytest.fixture
def chain_directory(tmp_path: Path) -> Iterator[ChainDirectory]:
    project = tmp_path / "project"
    metadata = tmp_path / "metadata"
    chain = project / CHAIN_LEAF
    chain.mkdir(parents=True)
    metadata.mkdir()
    backend = AuditedBackend(
        LinuxBackend(), project_root=str(project), metadata_root=str(metadata)
    )
    project_fd = backend.open_root(str(project))
    chain_fd = backend.open_child_directory(project_fd, CHAIN_LEAF)
    try:
        yield ChainDirectory(backend, project_fd, chain_fd, chain)
    finally:
        backend.close_fd(chain_fd)
        backend.close_fd(project_fd)


def _put(chain: Path, previous: str | None, entry: Entry) -> tuple[str, bytes]:
    envelope = encode_entry(previous, entry)
    digest = entry_digest(envelope)
    (chain / digest).write_bytes(envelope)
    return digest, envelope


def _three_entries(chain: Path) -> tuple[tuple[str, Entry], ...]:
    genesis = GenesisEntry(b"root", ())
    genesis_digest, _ = _put(chain, None, genesis)
    registered = RegisteredEntry(
        txid="tx_1",
        intent_digest=CONTENT_A,
        consumer_tag="consumer",
        initial=(),
        final=(),
        fulfills=None,
    )
    registered_digest, _ = _put(chain, genesis_digest, registered)
    settled = SettledEntry(
        txid="tx_1",
        registration=registered_digest,
        outcome=ChainOutcome.COMMITTED,
    )
    settled_digest, _ = _put(chain, registered_digest, settled)
    return (
        (genesis_digest, genesis),
        (registered_digest, registered),
        (settled_digest, settled),
    )


def test_validate_chain_returns_a_genesis_first_linear_history_and_tip(
    chain_directory: ChainDirectory,
) -> None:
    expected = _three_entries(chain_directory.path)

    validated = validate_chain(chain_directory.backend, chain_directory.chain_fd)

    assert validated == ValidatedChain(
        entries=expected,
        tip=expected[-1][0],
        survivors=(),
    )


def test_validate_chain_accepts_the_legitimate_pre_genesis_directory(
    chain_directory: ChainDirectory,
) -> None:
    assert validate_chain(
        chain_directory.backend, chain_directory.chain_fd
    ) == ValidatedChain((), None, ())


@pytest.mark.parametrize("entry_point", ["validate", "apply", "append"])
def test_chain_entry_points_refuse_a_non_chain_directory_before_mutation(
    chain_directory: ChainDirectory,
    entry_point: str,
) -> None:
    from atoms.chain.append import append_entry, apply_survivors

    ordinary = chain_directory.path.parent / "ordinary"
    ordinary.mkdir()
    ordinary_fd = chain_directory.backend.open_child_directory(
        chain_directory.project_fd, "ordinary"
    )
    try:
        with pytest.raises(ProtocolError, match="reserved chain directory"):
            if entry_point == "validate":
                validate_chain(chain_directory.backend, ordinary_fd)
            elif entry_point == "apply":
                apply_survivors(
                    chain_directory.backend,
                    ordinary_fd,
                    ValidatedChain((), None, ()),
                )
            else:
                append_entry(
                    chain_directory.backend,
                    ordinary_fd,
                    ValidatedChain((), None, ()),
                    GenesisEntry(b"root", ()),
                )
        assert list(ordinary.iterdir()) == []
    finally:
        chain_directory.backend.close_fd(ordinary_fd)


def test_validate_chain_refuses_a_second_genesis(
    chain_directory: ChainDirectory,
) -> None:
    _put(chain_directory.path, None, GenesisEntry(b"first", ()))
    _put(chain_directory.path, None, GenesisEntry(b"second", ()))

    with pytest.raises(ChainStateInvalid):
        validate_chain(chain_directory.backend, chain_directory.chain_fd)


@pytest.mark.parametrize(
    "damage",
    [
        "delete-middle",
        "sibling-successor",
        "orphan",
        "foreign-digest-name",
        "foreign-leaf",
    ],
)
def test_validate_chain_refuses_non_linear_or_foreign_durable_evidence(
    chain_directory: ChainDirectory,
    damage: str,
) -> None:
    entries = _three_entries(chain_directory.path)
    genesis_digest = entries[0][0]
    if damage == "delete-middle":
        (chain_directory.path / entries[1][0]).unlink()
    elif damage == "sibling-successor":
        _put(chain_directory.path, genesis_digest, IntentEntry(b"sibling"))
    elif damage == "orphan":
        _put(chain_directory.path, HEX_A, IntentEntry(b"orphan"))
    elif damage == "foreign-digest-name":
        (chain_directory.path / ("f" * 64)).write_bytes(b"foreign")
    else:
        (chain_directory.path / "foreign").write_bytes(b"foreign")

    with pytest.raises(ChainStateInvalid):
        validate_chain(chain_directory.backend, chain_directory.chain_fd)


@pytest.mark.parametrize(
    ("case", "planned", "expected_disposition", "expected_envelope"),
    [
        ("planned", True, SurvivorDisposition.FINISH, True),
        ("already-durable", True, SurvivorDisposition.REMOVE, False),
        ("partial", True, SurvivorDisposition.REMOVE, False),
        ("underived", False, SurvivorDisposition.REMOVE, False),
    ],
)
def test_validate_chain_classifies_the_fixed_staging_survivor_by_exact_bytes(
    chain_directory: ChainDirectory,
    case: str,
    planned: bool,
    expected_disposition: SurvivorDisposition,
    expected_envelope: bool,
) -> None:
    genesis_digest, _ = _put(
        chain_directory.path, None, GenesisEntry(b"root", ())
    )
    envelope = encode_entry(genesis_digest, IntentEntry(b"intent"))
    if case == "already-durable":
        (chain_directory.path / entry_digest(envelope)).write_bytes(envelope)
    staged = envelope[:5] if case == "partial" else envelope
    (chain_directory.path / STAGING_LEAF).write_bytes(staged)

    validated = validate_chain(
        chain_directory.backend,
        chain_directory.chain_fd,
        (envelope,) if planned else (),
    )

    assert validated.survivors == (
        SurvivorAction(
            STAGING_LEAF,
            expected_disposition,
            envelope if expected_envelope else None,
        ),
    )


def test_validate_chain_refuses_a_noncanonical_planned_envelope_as_a_protocol_bug(
    chain_directory: ChainDirectory,
) -> None:
    with pytest.raises(ProtocolError):
        validate_chain(
            chain_directory.backend,
            chain_directory.chain_fd,
            (b"not an envelope",),
        )


def test_validate_chain_refuses_an_underived_planned_envelope_as_a_protocol_bug(
    chain_directory: ChainDirectory,
) -> None:
    underived = encode_entry(HEX_A, IntentEntry(b"underived"))

    with pytest.raises(ProtocolError):
        validate_chain(
            chain_directory.backend,
            chain_directory.chain_fd,
            (underived,),
        )


class SimulatedCrash(RuntimeError):
    pass


class PublicationCutFacade:
    _PUBLICATION: ClassVar[frozenset[str]] = frozenset(
        {
            "create_exclusive",
            "write",
            "flush_file",
            "close_fd",
            "transfer_noclobber",
            "flush_directory",
        }
    )

    def __init__(self, inner: AuditedBackend, cut_after: int) -> None:
        self.inner = inner
        self.cut_after = cut_after
        self.calls: list[str] = []
        self.created_fd: int | None = None
        self._started = False
        self._cut = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    def _after(self, name: str, result: Any = None) -> Any:
        if self._started and name in self._PUBLICATION:
            self.calls.append(name)
            if len(self.calls) == self.cut_after and not self._cut:
                self._cut = True
                raise SimulatedCrash(f"cut after {name}")
        return result

    def create_exclusive(self, parent_fd: int, name: str, mode: int) -> int:
        self._started = True
        self.created_fd = self.inner.create_exclusive(parent_fd, name, mode)
        return cast(int, self._after("create_exclusive", self.created_fd))

    def write(self, fd: int, data: bytes) -> int:
        return cast(int, self._after("write", self.inner.write(fd, data)))

    def flush_file(self, fd: int) -> None:
        self.inner.flush_file(fd)
        self._after("flush_file")

    def close_fd(self, fd: int) -> None:
        self.inner.close_fd(fd)
        self._after("close_fd")

    def transfer_noclobber(
        self, src_fd: int, src: str, dst_fd: int, dst: str
    ) -> None:
        self.inner.transfer_noclobber(src_fd, src, dst_fd, dst)
        self._after("transfer_noclobber")

    def flush_directory(self, fd: int) -> None:
        self.inner.flush_directory(fd)
        self._after("flush_directory")


@pytest.mark.parametrize("cut_after", range(1, 7))
def test_validate_apply_append_converges_after_every_publication_cut(
    chain_directory: ChainDirectory,
    cut_after: int,
) -> None:
    from atoms.chain.append import append_entry, apply_survivors

    entry = GenesisEntry(b"root", ())
    envelope = encode_entry(None, entry)
    digest = entry_digest(envelope)
    cut = PublicationCutFacade(chain_directory.backend, cut_after)

    with pytest.raises(SimulatedCrash):
        append_entry(
            cast(AuditedBackend, cut),
            chain_directory.chain_fd,
            validate_chain(chain_directory.backend, chain_directory.chain_fd),
            entry,
        )

    if cut.created_fd is not None:
        try:
            chain_directory.backend.provenance_of(cut.created_fd)
        except ProtocolError:
            pass
        else:
            chain_directory.backend.close_fd(cut.created_fd)
    validated = validate_chain(
        chain_directory.backend,
        chain_directory.chain_fd,
        (envelope,),
    )
    validated = apply_survivors(
        chain_directory.backend, chain_directory.chain_fd, validated
    )
    if validated.tip is None:
        assert append_entry(
            chain_directory.backend,
            chain_directory.chain_fd,
            validated,
            entry,
        ) == digest

    final = validate_chain(chain_directory.backend, chain_directory.chain_fd)
    assert final.entries == ((digest, entry),)
    assert final.tip == digest
    assert final.survivors == ()
    assert sorted(path.name for path in chain_directory.path.iterdir()) == [digest]
    assert cut.calls[:cut_after] == [
        "create_exclusive",
        "write",
        "flush_file",
        "close_fd",
        "transfer_noclobber",
        "flush_directory",
    ][:cut_after]


def test_apply_returns_fresh_validation_and_append_refuses_the_stale_value(
    chain_directory: ChainDirectory,
) -> None:
    from atoms.chain.append import append_entry, apply_survivors

    genesis_digest, _ = _put(
        chain_directory.path, None, GenesisEntry(b"root", ())
    )
    entry = IntentEntry(b"planned")
    envelope = encode_entry(genesis_digest, entry)
    (chain_directory.path / STAGING_LEAF).write_bytes(envelope)
    stale = validate_chain(
        chain_directory.backend, chain_directory.chain_fd, (envelope,)
    )

    with pytest.raises(ProtocolError):
        append_entry(
            chain_directory.backend,
            chain_directory.chain_fd,
            stale,
            IntentEntry(b"next"),
        )

    fresh = apply_survivors(
        chain_directory.backend, chain_directory.chain_fd, stale
    )
    repeated = apply_survivors(
        chain_directory.backend, chain_directory.chain_fd, stale
    )
    assert fresh.survivors == ()
    assert repeated == fresh


def test_apply_refuses_a_new_survivor_after_a_survivor_free_validation(
    chain_directory: ChainDirectory,
) -> None:
    from atoms.chain.append import apply_survivors

    stale = validate_chain(chain_directory.backend, chain_directory.chain_fd)
    (chain_directory.path / STAGING_LEAF).write_bytes(b"appeared")

    with pytest.raises(ChainStateInvalid):
        apply_survivors(chain_directory.backend, chain_directory.chain_fd, stale)


def test_remove_survivor_application_is_idempotent(
    chain_directory: ChainDirectory,
) -> None:
    from atoms.chain.append import apply_survivors

    (chain_directory.path / STAGING_LEAF).write_bytes(b"partial")
    stale = validate_chain(chain_directory.backend, chain_directory.chain_fd)

    fresh = apply_survivors(
        chain_directory.backend, chain_directory.chain_fd, stale
    )
    repeated = apply_survivors(
        chain_directory.backend, chain_directory.chain_fd, stale
    )

    assert fresh == ValidatedChain((), None, ())
    assert repeated == fresh


def test_append_derives_linkage_and_refuses_non_genesis_on_an_empty_chain_before_writing(
    chain_directory: ChainDirectory,
) -> None:
    from atoms.chain.append import append_entry

    empty = validate_chain(chain_directory.backend, chain_directory.chain_fd)
    records_before = chain_directory.backend.records
    with pytest.raises(ProtocolError):
        append_entry(
            chain_directory.backend,
            chain_directory.chain_fd,
            empty,
            IntentEntry(b"too early"),
        )
    assert chain_directory.backend.records == records_before
    assert list(chain_directory.path.iterdir()) == []

    genesis_digest, _ = _put(
        chain_directory.path, None, GenesisEntry(b"root", ())
    )
    validated = validate_chain(chain_directory.backend, chain_directory.chain_fd)
    intent_digest = append_entry(
        chain_directory.backend,
        chain_directory.chain_fd,
        validated,
        IntentEntry(b"linked"),
    )
    previous, decoded = decode_entry(
        (chain_directory.path / intent_digest).read_bytes()
    )
    assert previous == genesis_digest
    assert decoded == IntentEntry(b"linked")


@pytest.mark.parametrize(
    "invalid",
    [
        ValidatedChain((), HEX_A, ()),
        ValidatedChain(((HEX_A, GenesisEntry(b"root", ())),), HEX_A, ()),
    ],
    ids=("tip-without-history", "digest-does-not-name-entry"),
)
def test_append_refuses_an_invalid_engine_created_validation_before_writing(
    chain_directory: ChainDirectory,
    invalid: ValidatedChain,
) -> None:
    from atoms.chain.append import append_entry

    records_before = chain_directory.backend.records
    with pytest.raises(ProtocolError):
        append_entry(
            chain_directory.backend,
            chain_directory.chain_fd,
            invalid,
            IntentEntry(b"invalid proof"),
        )

    assert chain_directory.backend.records == records_before
    assert list(chain_directory.path.iterdir()) == []


@pytest.mark.parametrize("change", ["added-file", "rewritten-tip"])
def test_append_reproves_the_complete_directory_before_any_write(
    chain_directory: ChainDirectory,
    change: str,
) -> None:
    from atoms.chain.append import append_entry

    if change == "rewritten-tip":
        tip, _ = _put(chain_directory.path, None, GenesisEntry(b"root", ()))
        validated = validate_chain(chain_directory.backend, chain_directory.chain_fd)
        (chain_directory.path / tip).write_bytes(b"tampered")
        entry = IntentEntry(b"next")
    else:
        validated = validate_chain(chain_directory.backend, chain_directory.chain_fd)
        (chain_directory.path / "foreign").write_bytes(b"foreign")
        entry = GenesisEntry(b"root", ())
    records_before = chain_directory.backend.records

    with pytest.raises(ChainStateInvalid):
        append_entry(
            chain_directory.backend,
            chain_directory.chain_fd,
            validated,
            entry,
        )

    assert chain_directory.backend.records == records_before
    assert not (chain_directory.path / STAGING_LEAF).exists()


@pytest.mark.parametrize("destination", ["ours", "foreign"])
def test_append_proves_an_eexist_destination_before_removing_staging(
    chain_directory: ChainDirectory,
    destination: str,
) -> None:
    from atoms.chain.append import append_entry

    genesis_digest, _ = _put(
        chain_directory.path, None, GenesisEntry(b"root", ())
    )
    validated = validate_chain(chain_directory.backend, chain_directory.chain_fd)
    entry = IntentEntry(b"intent")
    envelope = encode_entry(genesis_digest, entry)
    digest = entry_digest(envelope)
    real_transfer = chain_directory.backend.transfer_noclobber

    def collide(src_fd: int, src: str, dst_fd: int, dst: str) -> None:
        assert dst == digest
        (chain_directory.path / dst).write_bytes(
            envelope if destination == "ours" else b"foreign"
        )
        real_transfer(src_fd, src, dst_fd, dst)

    chain_directory.backend.transfer_noclobber = collide  # type: ignore[method-assign]
    try:
        if destination == "foreign":
            with pytest.raises(ChainStateInvalid):
                append_entry(
                    chain_directory.backend,
                    chain_directory.chain_fd,
                    validated,
                    entry,
                )
            assert (chain_directory.path / STAGING_LEAF).read_bytes() == envelope
        else:
            assert append_entry(
                chain_directory.backend,
                chain_directory.chain_fd,
                validated,
                entry,
            ) == digest
            assert not (chain_directory.path / STAGING_LEAF).exists()
    finally:
        chain_directory.backend.transfer_noclobber = real_transfer  # type: ignore[method-assign]


class ShortWriteFacade:
    def __init__(self, inner: AuditedBackend) -> None:
        self.inner = inner

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    def write(self, fd: int, data: bytes) -> int:
        return self.inner.write(fd, data[: max(1, len(data) // 2)])


def test_append_completes_short_backend_writes(chain_directory: ChainDirectory) -> None:
    from atoms.chain.append import append_entry

    entry = GenesisEntry(b"a payload long enough for several writes", ())
    validated = validate_chain(chain_directory.backend, chain_directory.chain_fd)
    digest = append_entry(
        cast(AuditedBackend, ShortWriteFacade(chain_directory.backend)),
        chain_directory.chain_fd,
        validated,
        entry,
    )

    assert (chain_directory.path / digest).read_bytes() == encode_entry(None, entry)


class BootstrapFacade:
    def __init__(self, inner: AuditedBackend, *, cut_after_mkdir: bool = False) -> None:
        self.inner = inner
        self.cut_after_mkdir = cut_after_mkdir
        self.calls: list[tuple[str, int | str]] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    def mkdir_child(self, parent_fd: int, name: str, mode: int) -> None:
        self.calls.append(("mkdir_child", name))
        self.inner.mkdir_child(parent_fd, name, mode)
        if self.cut_after_mkdir:
            self.cut_after_mkdir = False
            raise SimulatedCrash("cut after mkdir")

    def open_child_directory(self, parent_fd: int, name: str) -> int:
        self.calls.append(("open_child_directory", name))
        return self.inner.open_child_directory(parent_fd, name)

    def flush_directory(self, fd: int) -> None:
        self.calls.append(("flush_directory", fd))
        self.inner.flush_directory(fd)


def _bootstrap_backend(tmp_path: Path) -> tuple[AuditedBackend, int, Path]:
    project = tmp_path / "bootstrap-project"
    metadata = tmp_path / "bootstrap-metadata"
    project.mkdir()
    metadata.mkdir()
    backend = AuditedBackend(
        LinuxBackend(), project_root=str(project), metadata_root=str(metadata)
    )
    return backend, backend.open_root(str(project)), project


def test_bootstrap_chain_is_idempotent_and_flushes_the_project_root(
    tmp_path: Path,
) -> None:
    from atoms.chain.append import bootstrap_chain

    backend, project_fd, project = _bootstrap_backend(tmp_path)
    recording = BootstrapFacade(backend)
    try:
        first = bootstrap_chain(cast(AuditedBackend, recording), project_fd)
        backend.close_fd(first)
        second = bootstrap_chain(cast(AuditedBackend, recording), project_fd)
        backend.close_fd(second)

        assert (project / CHAIN_LEAF).is_dir()
        assert recording.calls.count(("flush_directory", project_fd)) == 2
        assert recording.calls.count(("open_child_directory", CHAIN_LEAF)) == 2
    finally:
        backend.close_fd(project_fd)


def test_bootstrap_chain_refuses_a_non_project_root_before_mutation(
    chain_directory: ChainDirectory,
) -> None:
    from atoms.chain.append import bootstrap_chain

    ordinary = chain_directory.path.parent / "ordinary"
    ordinary.mkdir()
    ordinary_fd = chain_directory.backend.open_child_directory(
        chain_directory.project_fd, "ordinary"
    )
    try:
        with pytest.raises(ProtocolError, match="project root"):
            bootstrap_chain(chain_directory.backend, ordinary_fd)
        assert list(ordinary.iterdir()) == []
    finally:
        chain_directory.backend.close_fd(ordinary_fd)


def test_bootstrap_chain_converges_after_a_cut_between_mkdir_and_flush(
    tmp_path: Path,
) -> None:
    from atoms.chain.append import bootstrap_chain

    backend, project_fd, project = _bootstrap_backend(tmp_path)
    cut = BootstrapFacade(backend, cut_after_mkdir=True)
    try:
        with pytest.raises(SimulatedCrash):
            bootstrap_chain(cast(AuditedBackend, cut), project_fd)
        assert (project / CHAIN_LEAF).is_dir()

        chain_fd = bootstrap_chain(backend, project_fd)
        backend.close_fd(chain_fd)
        validation_fd = backend.open_child_directory(project_fd, CHAIN_LEAF)
        try:
            assert validate_chain(backend, validation_fd) == ValidatedChain(
                (), None, ()
            )
        finally:
            backend.close_fd(validation_fd)
    finally:
        backend.close_fd(project_fd)
