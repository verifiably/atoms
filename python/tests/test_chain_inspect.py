"""The typed chain-inspection core and its two public commands (design §4, §6-§8).

Every directory-constructible defect is asserted **twice**: once as `MalformedChain`
through `inspect_chain_detached`, and once as `ChainStateInvalid` through
`validate_chain`, with the same message. That pair is what proves the taxonomy has
not forked into a returning validator and a raising one.
"""

from __future__ import annotations

import ast
import shutil
import sqlite3
from collections.abc import Iterator
from dataclasses import FrozenInstanceError, dataclass
from pathlib import Path

import pytest

from atoms.chain.errors import ChainStateInvalid
from atoms.chain.inspect import (
    AbsentChain,
    ChainDefect,
    ChainInspection,
    ChainScan,
    DefectKind,
    MalformedChain,
    WellFormedChain,
    inspect_scan,
    validate_scan,
)
from atoms.chain.model import (
    ChainOutcome,
    Entry,
    GenesisEntry,
    IntentEntry,
    RegisteredEntry,
    SettledEntry,
    encode_entry,
    entry_digest,
)
from atoms.chain.read import STAGING_LEAF, validate_chain
from atoms.coordinator.commands import inspect_chain, inspect_chain_detached, read_chain
from atoms.core.errors import ProtocolError
from atoms.core.scratch import CHAIN_LEAF
from atoms.fs import audit
from atoms.fs.audit import AuditedBackend
from atoms.fs.linux import LinuxBackend
from tests.test_coordinator_commands import (
    _durable_entries,
    _enable_commands,
    _register,
)

CONTENT = "sha256:" + "1" * 64
MISSING = "b" * 64


@dataclass(frozen=True)
class Chain:
    """A fabricated chain directory, readable through both surfaces."""

    backend: AuditedBackend
    project: Path
    directory: Path
    chain_fd: int

    def put(self, previous: str | None, entry: Entry) -> str:
        envelope = encode_entry(previous, entry)
        digest = entry_digest(envelope)
        (self.directory / digest).write_bytes(envelope)
        return digest

    def stage(self, previous: str | None, entry: Entry) -> str:
        envelope = encode_entry(previous, entry)
        (self.directory / STAGING_LEAF).write_bytes(envelope)
        return entry_digest(envelope)

    def inspect(self) -> ChainInspection:
        return inspect_chain_detached(LinuxBackend(), str(self.project))


@pytest.fixture
def chain(tmp_path: Path) -> Iterator[Chain]:
    project = tmp_path / "project"
    metadata = tmp_path / "metadata"
    directory = project / CHAIN_LEAF
    directory.mkdir(parents=True)
    metadata.mkdir()
    backend = AuditedBackend(
        LinuxBackend(), project_root=str(project), metadata_root=str(metadata)
    )
    project_fd = backend.open_root(str(project))
    chain_fd = backend.open_child_directory(project_fd, CHAIN_LEAF)
    try:
        yield Chain(backend, project, directory, chain_fd)
    finally:
        backend.close_fd(chain_fd)
        backend.close_fd(project_fd)


def _registered(
    txid: str, *, fulfills: str | None = None, tag: str = "consumer"
) -> RegisteredEntry:
    return RegisteredEntry(
        txid=txid,
        intent_digest=CONTENT,
        consumer_tag=tag,
        initial=(),
        final=(),
        fulfills=fulfills,
    )


def _settled(
    txid: str, registration: str, outcome: ChainOutcome = ChainOutcome.COMMITTED
) -> SettledEntry:
    return SettledEntry(txid=txid, registration=registration, outcome=outcome)


def _both_surfaces(chain: Chain, kind: DefectKind, subject: str | None) -> ChainDefect:
    """Assert the same first defect from the returning and the raising surface."""
    reported = chain.inspect()
    assert type(reported) is MalformedChain
    defect = reported.defect
    assert (defect.kind, defect.subject) == (kind, subject)
    with pytest.raises(ChainStateInvalid) as caught:
        validate_chain(chain.backend, chain.chain_fd)
    assert str(caught.value) == defect.detail
    return defect


# --------------------------------------------------------------------------- #
# The directory-constructible fabrications (design §14.1).
# --------------------------------------------------------------------------- #


def test_a_non_digest_leaf_name_is_a_foreign_leaf(chain: Chain) -> None:
    chain.put(None, GenesisEntry(b"root", ()))
    (chain.directory / "foreign").write_bytes(b"not an entry")

    _both_surfaces(chain, DefectKind.FOREIGN_LEAF, "foreign")


def test_a_digest_named_symlink_is_a_foreign_leaf(chain: Chain) -> None:
    chain.put(None, GenesisEntry(b"root", ()))
    (chain.directory / ("a" * 64)).symlink_to("elsewhere")

    _both_surfaces(chain, DefectKind.FOREIGN_LEAF, "a" * 64)


def test_a_digest_named_directory_is_a_foreign_leaf(chain: Chain) -> None:
    chain.put(None, GenesisEntry(b"root", ()))
    (chain.directory / ("a" * 64)).mkdir()

    _both_surfaces(chain, DefectKind.FOREIGN_LEAF, "a" * 64)


@pytest.mark.parametrize("spelling", ["directory", "symlink", "unreadable"])
def test_a_staging_leaf_that_is_not_a_readable_regular_file_is_foreign(
    chain: Chain, spelling: str
) -> None:
    """Design §8's ratified carve-out scope: the exemption covers nothing else."""
    chain.put(None, GenesisEntry(b"root", ()))
    occupant = chain.directory / STAGING_LEAF
    if spelling == "directory":
        occupant.mkdir()
    elif spelling == "symlink":
        occupant.symlink_to("elsewhere")
    else:
        occupant.write_bytes(b"{}")
        occupant.chmod(0o000)

    _both_surfaces(chain, DefectKind.FOREIGN_LEAF, STAGING_LEAF)


def test_a_leaf_whose_bytes_do_not_hash_to_its_name_is_a_mismatch(chain: Chain) -> None:
    envelope = encode_entry(None, GenesisEntry(b"root", ()))
    (chain.directory / ("c" * 64)).write_bytes(envelope)

    _both_surfaces(chain, DefectKind.NAME_BYTES_MISMATCH, "c" * 64)


def test_a_correctly_named_non_canonical_leaf_is_undecodable(chain: Chain) -> None:
    payload = b'{"class":"genesis"}'
    (chain.directory / entry_digest(payload)).write_bytes(payload)

    defect = _both_surfaces(chain, DefectKind.UNDECODABLE_ENTRY, entry_digest(payload))
    assert "missing or unexpected fields" in defect.detail


def test_a_chain_with_no_genesis_reports_genesis_count(chain: Chain) -> None:
    chain.put(MISSING, IntentEntry(b"orphaned intent"))

    _both_surfaces(chain, DefectKind.GENESIS_COUNT, None)


def test_a_chain_with_two_genesis_entries_reports_genesis_count(chain: Chain) -> None:
    chain.put(None, GenesisEntry(b"root", ()))
    chain.put(None, GenesisEntry(b"other root", ()))

    _both_surfaces(chain, DefectKind.GENESIS_COUNT, None)


def test_an_entry_naming_an_absent_predecessor_is_missing_predecessor(
    chain: Chain,
) -> None:
    chain.put(None, GenesisEntry(b"root", ()))
    stranded = chain.put(MISSING, IntentEntry(b"stranded"))

    _both_surfaces(chain, DefectKind.MISSING_PREDECESSOR, stranded)


def test_two_successors_of_one_entry_are_a_sibling_branch(chain: Chain) -> None:
    genesis = chain.put(None, GenesisEntry(b"root", ()))
    chain.put(genesis, IntentEntry(b"left"))
    chain.put(genesis, IntentEntry(b"right"))

    _both_surfaces(chain, DefectKind.SIBLING_BRANCH, genesis)


def test_a_settlement_without_an_ancestor_registration_is_reported(chain: Chain) -> None:
    genesis = chain.put(None, GenesisEntry(b"root", ()))
    settlement = chain.put(genesis, _settled("tx_1", MISSING))

    _both_surfaces(chain, DefectKind.SETTLEMENT_WITHOUT_REGISTRATION, settlement)


def test_a_settlement_naming_another_transactions_registration_is_a_mismatch(
    chain: Chain,
) -> None:
    genesis = chain.put(None, GenesisEntry(b"root", ()))
    registration = chain.put(genesis, _registered("tx_1"))
    settlement = chain.put(registration, _settled("tx_2", registration))

    _both_surfaces(chain, DefectKind.SETTLEMENT_TXID_MISMATCH, settlement)


def test_a_second_settlement_of_one_transaction_is_a_duplicate(chain: Chain) -> None:
    genesis = chain.put(None, GenesisEntry(b"root", ()))
    registration = chain.put(genesis, _registered("tx_1"))
    first = chain.put(registration, _settled("tx_1", registration))
    second = chain.put(first, _settled("tx_1", registration, ChainOutcome.ROLLED_BACK))

    _both_surfaces(chain, DefectKind.DUPLICATE_SETTLEMENT, second)


def test_a_second_registration_of_one_transaction_is_a_duplicate(chain: Chain) -> None:
    genesis = chain.put(None, GenesisEntry(b"root", ()))
    first = chain.put(genesis, _registered("tx_1"))
    second = chain.put(first, _registered("tx_1", tag="other"))

    _both_surfaces(chain, DefectKind.DUPLICATE_REGISTRATION, second)


def test_a_fulfills_naming_no_entry_is_unresolved(chain: Chain) -> None:
    genesis = chain.put(None, GenesisEntry(b"root", ()))
    registration = chain.put(genesis, _registered("tx_1", fulfills=MISSING))

    defect = _both_surfaces(chain, DefectKind.FULFILLS_UNRESOLVED, registration)
    assert defect.detail.startswith("missing ")


def test_a_fulfills_naming_a_non_intent_entry_is_unresolved(chain: Chain) -> None:
    genesis = chain.put(None, GenesisEntry(b"root", ()))
    first = chain.put(genesis, _registered("tx_1"))
    second = chain.put(first, _registered("tx_2", fulfills=first))

    defect = _both_surfaces(chain, DefectKind.FULFILLS_UNRESOLVED, second)
    assert defect.detail.startswith("non-intent ")


def test_a_second_committed_fulfillment_names_the_second_settlement(chain: Chain) -> None:
    """Design §4.4: the subject is the settlement, never either registration.

    The two registrations are deliberately non-adjacent, so an implementation that
    named a registration would report a different string.
    """
    genesis = chain.put(None, GenesisEntry(b"root", ()))
    intent = chain.put(genesis, IntentEntry(b"one intent"))
    first_registration = chain.put(intent, _registered("tx_1", fulfills=intent))
    first_settlement = chain.put(first_registration, _settled("tx_1", first_registration))
    second_registration = chain.put(first_settlement, _registered("tx_2", fulfills=intent))
    second_settlement = chain.put(second_registration, _settled("tx_2", second_registration))

    defect = _both_surfaces(chain, DefectKind.DUPLICATE_FULFILLMENT, second_settlement)
    assert defect.subject not in {first_registration, second_registration}


def test_only_genesis_count_carries_no_subject(chain: Chain) -> None:
    """Design acceptance §17.3: `subject is None` for exactly one kind."""
    chain.put(None, GenesisEntry(b"root", ()))
    chain.put(None, GenesisEntry(b"other root", ()))

    reported = chain.inspect()

    assert type(reported) is MalformedChain
    assert reported.defect.kind is DefectKind.GENESIS_COUNT
    assert reported.defect.subject is None


# --------------------------------------------------------------------------- #
# The three classes no directory can spell (design §14.2), through the pure core.
# --------------------------------------------------------------------------- #


def _row(
    digest: str, previous: str | None, entry: Entry
) -> tuple[str, str | None, Entry, bytes]:
    """One scan row whose digest is deliberately not its envelope's hash."""
    return (digest, previous, entry, b"")


def _injected(*rows: tuple[str, str | None, Entry, bytes]) -> ChainScan:
    return ChainScan(found=tuple(rows), staged=None)


def _core_only(scan: ChainScan, kind: DefectKind, subject: str | None) -> ChainDefect:
    reported = inspect_scan(scan)
    assert type(reported) is MalformedChain
    defect = reported.defect
    assert (defect.kind, defect.subject) == (kind, subject)
    with pytest.raises(ChainStateInvalid) as caught:
        validate_scan(scan)
    assert str(caught.value) == defect.detail
    return defect


def test_a_cycle_is_reported_at_the_revisited_digest() -> None:
    """A hash fixed point, so it is unbuildable on disk and injected instead."""
    first, second = "a" * 64, "b" * 64
    scan = _injected(
        _row(first, None, GenesisEntry(b"root", ())),
        _row(first, second, IntentEntry(b"revisit")),
        _row(second, first, IntentEntry(b"forward")),
    )

    _core_only(scan, DefectKind.CYCLE, first)


def test_an_orphan_history_names_the_lowest_unvisited_digest() -> None:
    """Design §4.4 step 10, ruled 2026-08-22: the lowest, not an arbitrary member."""
    genesis = "0" * 64
    low, middle, high = "5" * 64, "7" * 64, "9" * 64
    scan = _injected(
        _row(genesis, None, GenesisEntry(b"root", ())),
        _row(low, high, IntentEntry(b"low")),
        _row(middle, low, IntentEntry(b"middle")),
        _row(high, middle, IntentEntry(b"high")),
    )

    defect = _core_only(scan, DefectKind.ORPHAN_HISTORY, low)
    assert low in defect.detail


def test_a_fulfills_naming_a_later_entry_is_a_non_ancestor() -> None:
    genesis, registration, intent = "0" * 64, "1" * 64, "2" * 64
    scan = _injected(
        _row(genesis, None, GenesisEntry(b"root", ())),
        _row(registration, genesis, _registered("tx_1", fulfills=intent)),
        _row(intent, registration, IntentEntry(b"later intent")),
    )

    defect = _core_only(scan, DefectKind.FULFILLS_UNRESOLVED, registration)
    assert defect.detail.startswith("non-ancestor ")


def test_inspect_scan_needs_no_backend_and_no_descriptor() -> None:
    """Design §17.1: the second pass is pure, which is what makes injection honest."""
    genesis = "0" * 64

    result = inspect_scan(_injected(_row(genesis, None, GenesisEntry(b"root", ()))))

    assert type(result) is WellFormedChain
    assert result.tip == genesis


# --------------------------------------------------------------------------- #
# Determinism (design §17.4).
# --------------------------------------------------------------------------- #


def test_the_first_defect_in_traversal_order_wins_over_a_later_one(chain: Chain) -> None:
    genesis = chain.put(None, GenesisEntry(b"root", ()))
    first = chain.put(genesis, _registered("tx_1"))
    second = chain.put(first, _registered("tx_1", tag="other"))
    (chain.directory / "foreign").write_bytes(b"not an entry")

    _both_surfaces(chain, DefectKind.FOREIGN_LEAF, "foreign")

    (chain.directory / "foreign").unlink()

    _both_surfaces(chain, DefectKind.DUPLICATE_REGISTRATION, second)


# --------------------------------------------------------------------------- #
# Well-formed, absent, pending, and staging cases -- detached mode.
# --------------------------------------------------------------------------- #


def test_a_detached_well_formed_chain_reports_its_linearization(chain: Chain) -> None:
    genesis_entry = GenesisEntry(b"root", ())
    genesis = chain.put(None, genesis_entry)
    registration_entry = _registered("tx_1")
    registration = chain.put(genesis, registration_entry)
    settlement_entry = _settled("tx_1", registration)
    settlement = chain.put(registration, settlement_entry)

    result = chain.inspect()

    assert result == WellFormedChain(
        genesis_digest=genesis,
        entries=(
            (genesis, genesis_entry),
            (registration, registration_entry),
            (settlement, settlement_entry),
        ),
        tip=settlement,
        pending=(),
    )


def test_a_rolled_back_settlement_settles(chain: Chain) -> None:
    genesis = chain.put(None, GenesisEntry(b"root", ()))
    registration = chain.put(genesis, _registered("tx_1"))
    chain.put(registration, _settled("tx_1", registration, ChainOutcome.ROLLED_BACK))

    result = chain.inspect()

    assert type(result) is WellFormedChain
    assert result.pending == ()


def test_an_unsettled_registration_is_pending(chain: Chain) -> None:
    genesis = chain.put(None, GenesisEntry(b"root", ()))
    registration = chain.put(genesis, _registered("tx_1"))

    result = chain.inspect()

    assert type(result) is WellFormedChain
    assert result.pending == (("tx_1", registration),)
    assert result.tip == registration


def test_a_detached_root_without_a_chain_directory_is_absent(tmp_path: Path) -> None:
    project = tmp_path / "arrived"
    project.mkdir()

    assert inspect_chain_detached(LinuxBackend(), str(project)) == AbsentChain()


def test_a_detached_empty_chain_directory_is_absent(chain: Chain) -> None:
    assert chain.inspect() == AbsentChain()


def test_a_detached_chain_leaf_that_is_a_regular_file_is_a_foreign_leaf(
    tmp_path: Path,
) -> None:
    project = tmp_path / "arrived"
    project.mkdir()
    (project / CHAIN_LEAF).write_bytes(b"not a directory")

    result = inspect_chain_detached(LinuxBackend(), str(project))

    assert type(result) is MalformedChain
    assert result.defect.kind is DefectKind.FOREIGN_LEAF
    assert result.defect.subject == CHAIN_LEAF


def test_a_readable_regular_staging_leaf_is_never_a_defect(chain: Chain) -> None:
    genesis_entry = GenesisEntry(b"root", ())
    genesis = chain.put(None, genesis_entry)
    chain.stage(genesis, IntentEntry(b"a survivor"))

    result = chain.inspect()

    assert type(result) is WellFormedChain
    assert result.entries == ((genesis, genesis_entry),)
    assert result.pending == ()


def test_a_staged_registration_joins_detached_pending_without_joining_entries(
    chain: Chain,
) -> None:
    """Design §8, ratified 2026-08-22: the weaker detached invariant, pinned.

    A later implementation that quietly restored "every pending digest is an entry's"
    would turn this red.
    """
    genesis = chain.put(None, GenesisEntry(b"root", ()))
    durable = chain.put(genesis, _registered("tx_1"))
    staged = chain.stage(durable, _registered("tx_2"))

    result = chain.inspect()

    assert type(result) is WellFormedChain
    assert result.pending == (("tx_1", durable), ("tx_2", staged))
    durable_digests = {digest for digest, _ in result.entries}
    assert durable in durable_digests
    assert staged not in durable_digests


@pytest.mark.parametrize("survivor", ["undecodable", "stale", "other-class"])
def test_an_inert_staging_survivor_is_neither_a_defect_nor_pending(
    chain: Chain, survivor: str
) -> None:
    genesis = chain.put(None, GenesisEntry(b"root", ()))
    durable = chain.put(genesis, _registered("tx_1"))
    if survivor == "undecodable":
        (chain.directory / STAGING_LEAF).write_bytes(b"not canonical")
    elif survivor == "stale":
        chain.stage(genesis, _registered("tx_1"))
    else:
        chain.stage(durable, IntentEntry(b"not a registration"))

    result = chain.inspect()

    assert type(result) is WellFormedChain
    assert result.pending == (("tx_1", durable),)


#: Every mutating method, with arguments the guard must refuse before they are used.
#: `repair_entry_mode` is called separately: its last parameter is keyword-only.
_MUTATING_CALLS = {
    "create_exclusive": (0, "name", 0o600),
    "create_or_open": (0, "name", 0o600),
    "write": (0, b""),
    "set_mode": (0, 0o600),
    "mkdir_child": (0, "name", 0o700),
    "unlink_child": (0, "name"),
    "rmdir_child": (0, "name"),
    "symlink_child": (0, "name", "target"),
    "set_marker_xattr": (0, "user.x", b""),
    "exchange": (0, "left", "right"),
    "transfer_noclobber": (0, "src", 0, "dst"),
    "link_anchor": (0, "src", 0, "dst"),
}


def _authority_deriving_methods() -> dict[str, ast.FunctionDef]:
    """Exactly the methods that derive mutation authority from a target's provenance."""
    tree = ast.parse(Path(audit.__file__).read_text(encoding="utf-8"))
    facade = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "AuditedBackend"
    )
    return {
        node.name: node
        for node in facade.body
        if isinstance(node, ast.FunctionDef)
        and any(
            isinstance(inner, ast.Attribute)
            and inner.attr in {"_classify_child", "_classify_provenance"}
            for inner in ast.walk(node)
        )
    }


def test_the_detached_facade_refuses_every_mutation() -> None:
    """Design §7.1: read-only is a property of the facade, not of caller care.

    The method list is derived from the source rather than transcribed, so a mutating
    method added later without the guard turns this red instead of slipping through.
    """
    methods = _authority_deriving_methods()
    assert set(methods) == set(_MUTATING_CALLS) | {"repair_entry_mode"}
    for name, node in methods.items():
        first = node.body[0]
        assert (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Call)
            and isinstance(first.value.func, ast.Attribute)
            and first.value.func.attr == "_require_attached"
        ), f"{name} does not refuse detached mutation as its first statement"

    facade = AuditedBackend.detached(LinuxBackend(), project_root="/")
    for name, arguments in _MUTATING_CALLS.items():
        with pytest.raises(ProtocolError, match="detached backend performs no mutation"):
            getattr(facade, name)(*arguments)
    with pytest.raises(ProtocolError, match="detached backend performs no mutation"):
        facade.repair_entry_mode(0, "name", 0o600, before_change=lambda: None)


def test_a_detached_root_that_cannot_be_opened_raises(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        inspect_chain_detached(LinuxBackend(), str(tmp_path / "absent"))


def test_the_inspection_arms_are_frozen_and_inert(chain: Chain) -> None:
    genesis = chain.put(None, GenesisEntry(b"root", ()))

    result = chain.inspect()

    assert type(result) is WellFormedChain
    for field, replacement in (
        ("genesis_digest", "0" * 64),
        ("entries", ()),
        ("tip", genesis),
        ("pending", ()),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(result, field, replacement)


# --------------------------------------------------------------------------- #
# Registered mode (design §6, §14.3).
# --------------------------------------------------------------------------- #


def _inspect(ingredients) -> ChainInspection:
    backend, project_root, metadata_root, storage = ingredients
    return inspect_chain(backend, project_root, metadata_root, storage)


def test_inspect_chain_agrees_with_read_chain_on_a_healthy_root(
    coordinator_on, monkeypatch
) -> None:
    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    backend, project_root, metadata_root, storage = ingredients
    _register(ingredients, b"root", ())

    result = _inspect(ingredients)
    view = read_chain(backend, project_root, metadata_root, storage)

    assert type(result) is WellFormedChain
    assert result.genesis_digest == view.genesis_digest
    assert result.entries == view.entries
    assert result.tip == view.tip
    assert result.pending == ()


def test_inspect_chain_returns_malformed_without_running_recovery(
    coordinator_on, monkeypatch
) -> None:
    """Design §6.2: recovery never runs over damage, asserted rather than narrated."""
    from atoms.coordinator import commands

    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    _, project_root, _, _ = ingredients
    _register(ingredients, b"root", ())
    directory = Path(project_root) / CHAIN_LEAF
    (directory / STAGING_LEAF).write_bytes(b"a survivor")
    (directory / "foreign").write_bytes(b"not an entry")
    resolved: list[object] = []
    monkeypatch.setattr(
        commands, "resolve", lambda binding, store: resolved.append(binding)
    )

    result = _inspect(ingredients)

    assert type(result) is MalformedChain
    assert result.defect.kind is DefectKind.FOREIGN_LEAF
    assert resolved == []
    assert STAGING_LEAF in _durable_entries(project_root)


def test_inspect_chain_resolves_and_reports_what_resolution_left(
    coordinator_on, monkeypatch
) -> None:
    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    _, project_root, _, _ = ingredients
    genesis = _register(ingredients, b"root", ())
    (Path(project_root) / CHAIN_LEAF / STAGING_LEAF).write_bytes(b"a stale survivor")

    result = _inspect(ingredients)

    assert type(result) is WellFormedChain
    assert result.tip == genesis
    assert STAGING_LEAF not in _durable_entries(project_root)


def test_inspect_chain_reports_an_unregistered_root_as_absent(
    coordinator_on, monkeypatch
) -> None:
    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    _, project_root, metadata_root, _ = ingredients

    assert _inspect(ingredients) == AbsentChain()

    assert not (Path(project_root) / CHAIN_LEAF).exists()
    with sqlite3.connect(Path(metadata_root) / "atoms.db") as connection:
        counts = tuple(
            connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("transaction_record", "blob")
        )
    assert counts == (0, 0)


def test_inspect_chain_reports_an_occupied_chain_leaf_as_a_foreign_leaf(
    coordinator_on, monkeypatch
) -> None:
    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    _, project_root, _, _ = ingredients
    (Path(project_root) / CHAIN_LEAF).write_bytes(b"not a directory")

    result = _inspect(ingredients)

    assert type(result) is MalformedChain
    assert result.defect.kind is DefectKind.FOREIGN_LEAF
    assert result.defect.subject == CHAIN_LEAF


def test_an_empty_chain_directory_splits_on_the_live_record(
    coordinator_on, leased, monkeypatch
) -> None:
    """Design §6.2's last two rows, pinned as a pair rather than one of them alone."""
    from tests.store_support import APPROVAL_EVIDENCE, one_effect_spec

    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    _, project_root, _, _ = ingredients
    (Path(project_root) / CHAIN_LEAF).mkdir()

    assert _inspect(ingredients) == AbsentChain()
    assert inspect_chain_detached(LinuxBackend(), project_root) == AbsentChain()

    with leased(ingredients) as lease, lease._store.transaction() as transaction:
        transaction.insert_record(
            "tx1", one_effect_spec(), approval_evidence=APPROVAL_EVIDENCE
        )
        transaction.set_active("tx1")

    with pytest.raises(ChainStateInvalid):
        _inspect(ingredients)
    assert inspect_chain_detached(LinuxBackend(), project_root) == AbsentChain()


def test_a_copied_root_inspects_the_same_way_detached(
    coordinator_on, monkeypatch, tmp_path
) -> None:
    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    _, project_root, _, _ = ingredients
    _register(ingredients, b"root", ())
    registered = _inspect(ingredients)
    copy = tmp_path / "copy"
    (copy / CHAIN_LEAF).mkdir(parents=True)
    for name, payload in _durable_entries(project_root).items():
        (copy / CHAIN_LEAF / name).write_bytes(payload)

    assert inspect_chain_detached(LinuxBackend(), str(copy)) == registered

    shutil.rmtree(copy / CHAIN_LEAF)

    assert inspect_chain_detached(LinuxBackend(), str(copy)) == AbsentChain()
