"""Explicit recovery-model fixture registry."""

import contextlib
import itertools
import os
import tempfile
from pathlib import Path

import pytest

from atoms.fs.linux import LinuxBackend
from atoms.fs.lock import acquire_project_lock
from atoms.fs.volume import StorageProfile
from tests.fs_support import (
    build_test_allowlist,
    casefold_volume_or_reason,
    ext4_volume_or_skip_reason,
    find_distinct_mount,
    make_bound_volume,
    make_fake_backend,
    make_fdinfo_text,
    make_metadata_root,
    make_mountinfo_text,
    make_project_root,
    make_test_allowlist,
    test_volume_or_skip_reason,
)
from tests.recovery_support import (
    make_classifier_plan,
    make_committed_halt_source,
    make_committed_repeated_replace_snapshot,
    make_committed_snapshot,
    make_committed_superseded_cleanup_case,
    make_create_file_case,
    make_delete_case,
    make_directory_case,
    make_generated_snapshots,
    make_halt_restart_case,
    make_halted_authority_snapshot,
    make_halted_snapshot,
    make_identity_case,
    make_move_case,
    make_noop_replace_case,
    make_pending_clean_case,
    make_pending_drift_case,
    make_pending_scratch_case,
    make_prepared_drift_snapshot,
    make_recovery_case,
    make_reducer_step_cases,
    make_repeated_path_mid_plan_halt,
    make_replace_case,
    make_replace_started_case,
    make_replace_transform_case,
    make_snapshot_pair_differing_only_dependencies,
    make_terminal_snapshot,
    make_three_effect_snapshot,
    make_two_effect_snapshot,
    make_undone_drift_case,
)


@pytest.fixture
def terminal_snapshot():
    return make_terminal_snapshot()


@pytest.fixture
def reducer_step_cases():
    return make_reducer_step_cases


@pytest.fixture
def replace_started_case():
    return make_replace_started_case


@pytest.fixture
def replace_transform_case():
    return make_replace_transform_case


@pytest.fixture
def three_effect_snapshot():
    return make_three_effect_snapshot


@pytest.fixture
def halted_authority_snapshot():
    return make_halted_authority_snapshot


@pytest.fixture
def replace_case():
    return make_replace_case


@pytest.fixture
def noop_replace_case():
    return make_noop_replace_case


@pytest.fixture
def create_file_case():
    return make_create_file_case


@pytest.fixture
def delete_case():
    return make_delete_case


@pytest.fixture
def move_case():
    return make_move_case


@pytest.fixture
def directory_case():
    return make_directory_case


@pytest.fixture
def pending_drift_case():
    return make_pending_drift_case


@pytest.fixture
def pending_clean_case():
    return make_pending_clean_case


@pytest.fixture
def pending_scratch_case():
    return make_pending_scratch_case


@pytest.fixture
def undone_drift_case():
    return make_undone_drift_case


@pytest.fixture
def two_effect_snapshot():
    return make_two_effect_snapshot


@pytest.fixture
def committed_snapshot():
    return make_committed_snapshot()


@pytest.fixture
def committed_repeated_replace_snapshot():
    return make_committed_repeated_replace_snapshot()


@pytest.fixture
def committed_superseded_cleanup_case():
    return make_committed_superseded_cleanup_case


@pytest.fixture
def prepared_drift_snapshot():
    return make_prepared_drift_snapshot()


@pytest.fixture
def halted_snapshot():
    return make_halted_snapshot()


@pytest.fixture
def recovery_case():
    return make_recovery_case


@pytest.fixture
def committed_halt_source():
    return make_committed_halt_source()


@pytest.fixture
def repeated_path_mid_plan_halt():
    return make_repeated_path_mid_plan_halt()


@pytest.fixture
def snapshot_pair_differing_only_dependencies():
    return make_snapshot_pair_differing_only_dependencies()


@pytest.fixture
def classifier_plan():
    return make_classifier_plan()


@pytest.fixture
def generated_snapshots():
    return make_generated_snapshots()


@pytest.fixture
def identity_case():
    return make_identity_case()


@pytest.fixture
def halt_restart_case():
    return make_halt_restart_case()


@pytest.fixture
def test_volume(tmp_path_factory):
    base, reason = test_volume_or_skip_reason()
    if base is None:
        pytest.skip(reason)
    base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=base) as directory:
        yield Path(directory)


@pytest.fixture
def linux_backend():
    return LinuxBackend()


@pytest.fixture
def metadata_root(test_volume):
    return make_metadata_root(test_volume)


@pytest.fixture
def project_root(test_volume):
    return make_project_root(test_volume)


@pytest.fixture
def project(tmp_path):
    """A plain directory descriptor. Tier 1 needs no lease, store, or approval."""
    fd = os.open(str(tmp_path), os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        yield tmp_path, fd
    finally:
        os.close(fd)


@pytest.fixture
def held_lock(linux_backend):
    def acquire(metadata_root):
        return acquire_project_lock(linux_backend, str(metadata_root))

    return acquire


@pytest.fixture
def fake_backend():
    return make_fake_backend()


@pytest.fixture
def mountinfo_text():
    return make_mountinfo_text()


@pytest.fixture
def fdinfo_text():
    return make_fdinfo_text()


@pytest.fixture
def test_storage_profile():
    return StorageProfile(profile_id="atoms-test-profile")


@pytest.fixture
def test_allowlist():
    return make_test_allowlist()


@pytest.fixture
def bound_volume(project_root, metadata_root, test_storage_profile):
    return make_bound_volume(
        make_fake_backend(), project_root, metadata_root, test_storage_profile
    )


@pytest.fixture
def distinct_volume(test_volume):
    found = find_distinct_mount(test_volume)
    if found is None:
        pytest.skip("no writable mount with a distinct mount id is available")
    with tempfile.TemporaryDirectory(dir=found) as directory:
        yield Path(directory)


@pytest.fixture
def directory_fd(tmp_path):
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    yield fd
    os.close(fd)


@pytest.fixture
def ext4_volume():
    base, reason = ext4_volume_or_skip_reason()
    if base is None:
        pytest.skip(reason)
    base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=base) as directory:
        yield Path(directory)


@pytest.fixture
def ext4_probe_fd(ext4_volume):
    fd = os.open(ext4_volume, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    yield fd
    os.close(fd)


@pytest.fixture
def ext4_project_root(ext4_volume):
    return make_project_root(ext4_volume)


@pytest.fixture
def ext4_metadata_root(ext4_volume):
    return make_metadata_root(ext4_volume)


@pytest.fixture
def ext4_bound_volume(ext4_project_root, ext4_metadata_root, test_storage_profile):
    return make_bound_volume(
        make_fake_backend(), ext4_project_root, ext4_metadata_root, test_storage_profile
    )


@pytest.fixture
def ext4_nested_bound_volume(ext4_project_root, test_storage_profile):
    """A binding whose metadata root sits INSIDE its project root.

    The sibling layout of `project_root`/`metadata_root` makes every declared path's
    relative spelling start with '..', which require_rel_path rejects, so a
    metadata-root containment test written against it can only skip itself. This is
    also the layout a real project uses.
    """
    return make_bound_volume(
        make_fake_backend(),
        ext4_project_root,
        ext4_project_root / "metadata",
        test_storage_profile,
    )


@pytest.fixture
def resolver_on(ext4_bound_volume):
    """A resolver and its live binding, on ext4."""
    from atoms.fs.resolve import PathResolver

    @contextlib.contextmanager
    def build():
        with ext4_bound_volume() as binding:
            yield PathResolver(binding), binding

    return build


@pytest.fixture
def injected_lookup(monkeypatch):
    """Make resolver contract tests independent of the volume's lookup mechanism."""
    from atoms.fs.lookup import DirectoryConstraints, LookupProof

    constraints = DirectoryConstraints(
        lookup_proof=LookupProof.EXACT_BYTES, name_max=255
    )
    monkeypatch.setattr(
        "atoms.fs.resolve.read_lookup_constraints",
        lambda fd, filesystem_type: constraints,
    )
    return constraints


@pytest.fixture
def injected_equivalence(monkeypatch):
    """Install a chosen name-equivalence double on the modules that consume it.

    Patched by consuming-module path, like injected_lookup patches
    atoms.fs.resolve.read_lookup_constraints, because both modules bind the name at
    import time.

    A factory rather than a fixed double, because the two behaviours under test need
    different relations. Case folding merges directories (`A` and `a`), which A2 admits
    because its phase 4 key is the whole path and `A` differs from `a/x`. Case folding
    can NOT exercise endpoint distinctness: two leaves fold in one parent only when their
    whole paths fold too, and A2 phase 4 already refuses that pair before approval sees
    it. Endpoint tests therefore use a truncating relation — a real filesystem
    equivalence class that A2's key does not subsume.

    `install` returns the list of names the double was asked about, in call order. That
    is the positive half of the bypass check: a call site that decides a name without the
    helper contributes nothing to the list, whatever shape the bypass takes. Under an
    identity key the *behaviour* is unchanged, so only the recording distinguishes the
    two. The AST guard in test_fs_architecture.py is the negative half and catches the
    specific shapes it names; neither alone is a proof, and the pair is what the design
    asks for.

    This is a double either way. It proves the call sites route through the function and
    merge whatever it merges; it proves nothing about any real relation, all of which
    stay unreproducible and refused.
    """

    def install(key):
        calls: list[str] = []

        def recording(constraints, name):
            calls.append(name)
            return key(name)

        for module in _EQUIVALENCE_CONSUMERS:
            monkeypatch.setattr(f"{module}.lookup_equivalence_key", recording)
        return calls

    return install


_EQUIVALENCE_CONSUMERS = ("atoms.fs.topology", "atoms.fs.judgment")


def truncate_to_eight(name: str) -> str:
    """A truncating name equivalence, in conftest so the registry guard sees it."""
    return name[:8]


@pytest.fixture
def injected_resolver_on(bound_volume, injected_lookup):
    """A resolver whose lookup proof is injected; valid on every A4a test volume."""
    from atoms.fs.resolve import PathResolver

    @contextlib.contextmanager
    def build():
        with bound_volume() as binding:
            yield PathResolver(binding), binding

    return build


@pytest.fixture
def injected_resolver_after_lock_release(
    injected_lookup, linux_backend, project_root, metadata_root, test_storage_profile
):
    """A resolver whose binding is still active but whose lock has been released.

    ProjectBinding._require_active checks its own flag AND lock.held, so this is a
    distinct liveness failure from a closed binding — and it needs a binding that
    outlives its lock, which no context-managed fixture produces.
    """
    from atoms.fs.binding import bind_project_volume
    from atoms.fs.resolve import PathResolver

    with acquire_project_lock(linux_backend, str(metadata_root)) as lock:
        allowlist = build_test_allowlist(lock, project_root, test_storage_profile)
        binding = bind_project_volume(
            str(project_root),
            lock,
            allowlist=allowlist,
            storage=test_storage_profile,
        )
        resolver = PathResolver(binding)
    with binding:
        assert binding.active and not lock.held
        yield resolver


@pytest.fixture
def casefold_volume():
    base, reason, is_error = casefold_volume_or_reason()
    if base is None:
        if is_error:
            pytest.fail(reason)
        pytest.skip(reason)
    with tempfile.TemporaryDirectory(dir=base) as directory:
        yield Path(directory)


@pytest.fixture
def casefold_project_root(casefold_volume):
    return make_project_root(casefold_volume)


@pytest.fixture
def casefold_bound_volume(casefold_project_root, casefold_volume, test_storage_profile):
    return make_bound_volume(
        make_fake_backend(),
        casefold_project_root,
        make_metadata_root(casefold_volume),
        test_storage_profile,
    )


@pytest.fixture
def mixed_policy(casefold_project_root):
    """A folded directory and a plain sibling inside one project root.

    Both live under the project root rather than beside it, so a PathResolver bound to
    that root can be asked about each — which is the assertion the tier exists for.
    """
    import subprocess

    plain = casefold_project_root / "plain"
    folded = casefold_project_root / "folded"
    plain.mkdir()
    folded.mkdir()
    completed = subprocess.run(
        ["chattr", "+F", str(folded)], capture_output=True, text=True, check=False
    )
    if completed.returncode != 0:
        pytest.fail(
            f"chattr +F failed on {folded}: {completed.stderr.strip()}. "
            "The volume is probably not formatted with -O casefold."
        )
    return plain, folded


@pytest.fixture
def approval_context(ext4_bound_volume):
    """A ProjectContext over a real ext4 binding, with a fixed txid."""
    import contextlib

    from atoms.fs.approval import ProjectContext

    @contextlib.contextmanager
    def build(txid: str = "tx01", withhold=frozenset()):
        with ext4_bound_volume(withhold=withhold) as binding:
            yield ProjectContext(binding=binding, txid=txid), binding

    return build


@pytest.fixture
def store_on(ext4_volume, ext4_project_root, test_storage_profile):
    """A bound ext4 volume with an empty metadata root, ready for store creation.

    Each call binds a *fresh* metadata root under the same ext4 volume, rather than
    the one `ext4_metadata_root` fixture instance every other consumer shares. A test
    that cuts a creation sequence and then opens a second, uninterrupted store for
    comparison needs the second store_on() to see an empty metadata root, not the
    first call's surviving `atoms.db`. Repeated calls therefore cannot model a restart
    of one metadata root; that requires retaining its project/metadata paths and rebinding
    them explicitly, as the fresh-process tests do.
    """
    counter = itertools.count()

    def bind(withhold=frozenset()):
        metadata_root = ext4_volume / f"metadata-{next(counter)}"
        return make_bound_volume(
            make_fake_backend(), ext4_project_root, metadata_root, test_storage_profile
        )(withhold=withhold)

    return bind


@pytest.fixture
def coordinator_on(ext4_volume, test_storage_profile):
    """The raw ingredients `_recovery_lease` builds its own stack from.

    Unlike `store_on`, this fixture binds nothing: the lease owns lock acquisition,
    probe reclamation, binding, and store opening, and a fixture that pre-bound them
    would leave four of the six entry-order steps unexercised. Each call names a fresh
    project root AND a fresh metadata root under the same ext4 volume, so two calls
    really are two projects: under the lifecycle rules a genesis without its own
    carrier is never re-registrable, so a shared project root would make the second
    scenario a bare-genesis refusal rather than a fresh project. Restart modeling
    retains one call's paths and rebinds them explicitly, as the fresh-process tests
    do.
    """
    counter = itertools.count()

    def ingredients():
        from atoms.fs.linux import LinuxBackend

        index = next(counter)
        project_root = ext4_volume / f"coordinator-project-{index}"
        metadata_root = ext4_volume / f"coordinator-metadata-{index}"
        project_root.mkdir()
        return (
            LinuxBackend(),
            str(project_root),
            str(metadata_root),
            test_storage_profile,
        )

    return ingredients


@pytest.fixture
def leased(coordinator_on, monkeypatch):
    """An entered production lease, with `CERTIFIED_ALLOWLIST` patched for the volume.

    The constant ships empty and production binding fails closed, so every test drives
    the real composition root with the module attribute replaced. Patching the constant
    rather than threading a parameter keeps `root.py` the single bind call site that
    ledger #18 asserts over; a test-only allowlist parameter would create a second one.
    """
    import contextlib

    from atoms.coordinator import root
    from atoms.fs.lock import acquire_project_lock
    from tests.fs_support import build_test_allowlist

    @contextlib.contextmanager
    def enter(ingredients=None):
        backend, project_root, metadata_root, storage = ingredients or coordinator_on()
        with acquire_project_lock(backend, metadata_root) as probe:
            allowlist = build_test_allowlist(probe, project_root, storage)
        monkeypatch.setattr(root, "CERTIFIED_ALLOWLIST", allowlist)
        with root._recovery_lease(
            backend, project_root, metadata_root, storage
        ) as lease:
            yield lease

    return enter


@pytest.fixture
def exerciser_run(coordinator_on, monkeypatch):
    """Run a named exerciser scenario clean; returns (ingredients, outcome)."""

    def run(name: str):
        from tests.exerciser import run_clean, scenario

        ingredients = coordinator_on()
        outcome = run_clean(scenario(name), ingredients, monkeypatch)
        return ingredients, outcome

    return run


@pytest.fixture
def persistence_recording(coordinator_on, monkeypatch):
    """Record a named exerciser scenario through the persistence-cut model's Stream."""

    def record(name: str, *, caught: bool = False):
        from tests.exerciser import scenario
        from tests.persistence_model import record_scenario

        return record_scenario(scenario(name), coordinator_on(), monkeypatch, caught=caught)

    return record


@pytest.fixture
def cut_matrix(ext4_volume, test_storage_profile, monkeypatch):
    """The persistence-cut sweep (design §5): `Sweeper(...)` sweeps a whole scenario,
    `Sweeper.named_cell(...)` runs one of §9.4's named tuples.

    Deliberately NOT built on `persistence_recording`: that fixture records through
    `coordinator_on`, whose project root is one shared directory, and every scenario's
    `seed_world` builds the same paths -- so a test sweeping two scenarios (Task 7's
    four compound ones, Task 8's two directions) would fail seeding the second. The
    `Sweeper` mints a fresh project root per recording on the same ext4 volume instead.
    """
    from tests.persistence_model import Sweeper

    return Sweeper(ext4_volume, test_storage_profile, monkeypatch)


@pytest.fixture
def exerciser_child(ext4_volume, test_storage_profile, monkeypatch):
    """Run a whole exerciser scenario -- setup, transaction, projection -- in a child.

    Design §6's whole-cell placement: `tests/execute_child.py` seeds the world, registers
    the root through `exerciser.setup_clean`, runs the transaction (catching exactly the
    injected failure for a `inject_failure` scenario, per erratum 1), and prints the
    canonical durable projection. Nothing about the transaction happens in this process.

    The mandatory second pass happens here, in-process, because design §5 asks only that
    a *fresh lease* re-enter: a subsequent entry must take no recovery action at all --
    `classify_recovery` is never reached over a settled, detached record -- and must
    leave the projection exactly where the child left it. Returns the child's projection.
    """
    counter = itertools.count()

    def run(name: str) -> dict:
        from atoms.fs.linux import LinuxBackend
        from tests.persistence_model import _enter_lease, durable_projection
        from tests.test_coordinator_kill_matrix import _child

        index = next(counter)
        project = ext4_volume / f"whole-cell-{index}-project"
        metadata = ext4_volume / f"whole-cell-{index}-metadata"
        project.mkdir()
        projection = _child(
            project,
            metadata,
            {"scenario": name, "setup": True, "projection": True},
        )["projection"]
        assert projection is not None, f"{name}: the child recorded no durable record"

        with acquire_project_lock(LinuxBackend(), str(metadata)) as lock:
            allowlist = build_test_allowlist(lock, str(project), test_storage_profile)
        captured: list[tuple] = []
        kind, _ = _enter_lease(
            project, metadata, test_storage_profile, monkeypatch, captured, allowlist
        )
        assert kind == "resolved", f"{name}: the second pass halted"
        assert captured == [], (
            f"{name}: the second pass took a recovery action over a settled record"
        )
        assert (
            durable_projection(project, metadata, test_storage_profile, allowlist)
            == projection
        ), f"{name}: the second pass moved the projection"
        return projection

    return run


@pytest.fixture
def exerciser_kill_matrix(ext4_volume, test_storage_profile, monkeypatch):
    """Drive the kill matrix's own SIGKILL contract over an exerciser scenario.

    The rehearsal idiom, unchanged from `tests/test_coordinator_kill_matrix.py`: run the
    scenario once under the recording backend with the store sequencer counting commits,
    then, for each recorded durability event, kill a fresh child right after it and
    require the surviving world to converge.

    Three things are derived rather than hardcoded, because a multi-effect scenario has
    no fixed barrier schedule the way the single-effect kill-matrix variants do:

    - the **cut sites** are every recorded `flush_file`/`flush_directory`/`exchange`/
      `transfer_noclobber` in the whole stream -- the calls that publish something
      durably -- and **no site is excluded**. Each is cut on the **before** side (a
      positive countdown), and additionally on the **after** side from the first store
      commit onward. The asymmetry is forced, not chosen: an after-side kill fires only
      once the delegated call *returns*, and capability probing deliberately calls
      `transfer_noclobber`/`exchange` on operands it expects to *fail* (that is how it
      learns the syscall's semantics), so an after-side kill there never fires at all --
      the child survives and the cut asserts nothing. The before side fires regardless,
      which makes it the one placement that is total over the stream; the after side is
      added exactly where every call provably returns. Everything between the end of
      probing and the first commit -- workspace creation under `staging/` and `work/`,
      blob payload flushes, promotion into `blobs/sha256`, staging reclamation -- is
      swept by both;
    - **whether a cut commits** is read off the interleaved commit count. The store's
      barrier schedule for an E-effect transaction is `prepared`,
      `registration-binding`, `applying`, then `started`/`done` per effect, then
      `applied`, `committed`, `settlement-binding`, `detach` -- 2E+7 commits, of which
      the commit decision is the (2E+5)th, i.e. the third from last. The shape is
      asserted against the recorded stream rather than trusted;
    - the **terminal worlds** are observed, not spelled: the seeded world (before the
      rehearsal transacts) is what an uncommitted cut must converge back to, and the
      rehearsal's own finished world is what a committed cut must converge to.

    `_assert_terminal` then applies the existing contract per cut -- recover twice,
    require the two observations and the world to be identical, require no active
    transaction, and require the expected world. Returns the number of cuts driven.
    """

    def drive(name: str) -> int:
        from atoms.fs.linux import LinuxBackend
        from tests.exerciser import scenario, setup_clean
        from tests.test_coordinator_kill_matrix import _assert_terminal, _child, _world

        entry = scenario(name)
        effects = len(entry.build_spec().effects)

        def prepared(slot: str) -> tuple[Path, Path]:
            project = ext4_volume / f"kill-{name}-{slot}-project"
            metadata = ext4_volume / f"kill-{name}-{slot}-metadata"
            project.mkdir()
            setup_clean(
                entry,
                (LinuxBackend(), str(project), str(metadata), test_storage_profile),
                monkeypatch,
            )
            return project, metadata

        rehearsal_project, rehearsal_metadata = prepared("rehearsal")
        seeded = _world(rehearsal_project)
        events = _child(
            rehearsal_project,
            rehearsal_metadata,
            {
                "scenario": name,
                "record": True,
                "store_cut": "record",
                "countdown": 10_000,
            },
        )["events"]
        finished = _world(rehearsal_project)

        commits = 2 * effects + 7
        assert events.count("commit") == commits, (
            f"{name}: {events.count('commit')} store commits for {effects} effects, "
            f"expected {commits} -- the barrier schedule changed"
        )
        methods = ("flush_file", "flush_directory", "exchange", "transfer_noclobber")
        sites = [
            index for index, event in enumerate(events) if event.startswith(methods)
        ]
        first_commit = events.index("commit")
        cuts = [(site, "before") for site in sites]
        cuts += [(site, "after") for site in sites if site >= first_commit]
        for ordinal, (target, side) in enumerate(cuts):
            method = events[target].partition(":")[0]
            countdown = sum(
                event.startswith(method) for event in events[: target + 1]
            )
            committed = events[:target].count("commit") >= commits - 2
            project, metadata = prepared(f"{ordinal}-{side}")
            _child(
                project,
                metadata,
                {
                    "scenario": name,
                    "method": method,
                    "countdown": countdown if side == "before" else -countdown,
                },
                killed=True,
            )
            _assert_terminal(
                project,
                metadata,
                name,
                committed=committed,
                expected=finished if committed else seeded,
            )
        return len(cuts)

    return drive


@pytest.fixture
def opened_store(store_on):
    """A live Store over a fresh ext4 project, closed on exit."""
    from atoms.store.connection import open_store

    with store_on() as binding:
        store = open_store(binding)
        try:
            yield store
        finally:
            store.close()


@pytest.fixture
def store_binding(request):
    """The ProjectBinding behind `opened_store`, for asserting on durable rows directly.

    Depends on the same fixture instance rather than building a second volume, so the two
    always name one store.
    """
    return request.getfixturevalue("opened_store")._binding


@pytest.fixture
def promoted_blob(opened_store):
    """A store holding one promoted, indexed blob, with its digest and bytes."""
    from atoms.store.blobs import StagedBlob
    from tests.store_support import APPROVAL_EVIDENCE, digest_of, spec_referencing, stage

    content = b"the blob's bytes"
    digest = digest_of(content)
    with opened_store.create_workspace("fixture") as workspace:
        stage(workspace, "capture", content)
        with opened_store.transaction() as txn:
            txn.promote_staging(
                workspace,
                (StagedBlob(name="capture", digest=digest, byte_len=len(content)),),
            )
            txn.insert_record(
                "fixture",
                spec_referencing(content),
                approval_evidence=APPROVAL_EVIDENCE,
            )
    return opened_store, digest, content
