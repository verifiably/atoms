"""Unit decomposition, coverage, replacement, and coalescing (design §4.1-§4.2)."""

import pytest

from tests.exerciser import SCENARIOS as _SCENARIOS


def test_recording_is_success_only_and_decomposes_renames(persistence_recording):
    stream = persistence_recording("minimal-move")
    renames = [
        event for event in stream.mutations()
        if {unit.change for unit in event.units} == {"remove", "insert"}
    ]
    assert renames, "the move's transfer_noclobber must decompose into remove+insert"
    remove, insert = sorted(renames[-1].units, key=lambda unit: unit.change != "remove")
    assert remove.key[0] == "entry" and insert.key[0] == "entry"
    assert remove.object_token == insert.object_token


def test_writes_coalesce_to_one_pending_image_per_inode(persistence_recording):
    stream = persistence_recording("minimal-create")
    data_keys = [
        unit.key for event in stream.mutations() for unit in event.units
        if unit.key[0] == "data"
    ]
    # However many write() calls streamed the payload, pending state holds one image.
    pending = stream.pending_before(stream.commit_index("e1-done"))
    images = [key for key in pending if key[0] == "data"]
    assert len(images) == len(set(images))
    assert data_keys


def test_flush_directory_covers_entries_and_directory_metadata(persistence_recording):
    stream = persistence_recording("minimal-mkdir")
    covered = set().union(*(e.covered for e in stream.barriers()))
    assert any(key[0] == "entry" for key in covered)
    assert any(key[0] == "meta" for key in covered)


def test_database_family_is_model_owned(persistence_recording):
    stream = persistence_recording("minimal-create")
    names = {
        unit.key[2] if len(unit.key) > 2 else unit.key[1]
        for event in stream.mutations() for unit in event.units
        if unit.key[0] == "entry"
    }
    assert not any(str(name).startswith("atoms.db") for name in names)


def test_each_store_commit_carries_a_backup(persistence_recording):
    stream = persistence_recording("minimal-create")
    commits = stream.commits()
    assert [c.backup_id for c in commits] == sorted({c.backup_id for c in commits})
    assert all(c.backup_id in stream.backups for c in commits)


@pytest.mark.parametrize("name", ["minimal-create", "minimal-move", "minimal-mkdir"])
def test_full_durable_reconstruction_equals_the_live_final_world(
    name, persistence_recording, ext4_volume
):
    """Design §9's fidelity self-check, run per scenario.

    Parametrized rather than looped over one `persistence_recording` fixture instance:
    `ext4_project_root` (`coordinator_on`'s project root) is shared across every call
    within one test, so a second scenario's `seed_world` collides with the first's
    leftover tree -- each scenario needs its own fresh fixtures, which parametrize
    gives for free.

    "Every pending unit surviving" means the *maximal* survivable subset
    (`_maximal_survivors`), not the raw `pending_keys_at` set verbatim: capability
    probing performs un-flushed create+remove churn inside the recorded transaction
    itself, leaving `data`/`meta` keys (and the occasional `remove`) permanently
    orphaned once keyed replacement forgets their paired entry -- inert leftovers with
    zero observable effect either way, never a discarded real change (design §9's
    `apply_survivors` text: "an unreachable inode carries no observable state"). The
    dropped set is asserted, not merely trusted: every dropped key must be a `data`/
    `meta` unit (nameless-token churn) or an entry `remove` (a no-op with no durable
    target) -- an entry `replace` is the one shape whose drop *would* be observable
    (silently un-recording a real `exchange`), so dropping one is a hard failure here,
    not a quiet coverage gap.
    """
    from tests.persistence_model import (
        Skip,
        _maximal_survivors,
        apply_survivors,
        durable_state,
        pending_keys_at,
        reconstruct,
        world_digest,
    )

    stream = persistence_recording(name)
    end = len(stream.events)
    survivors = _maximal_survivors(stream, end)
    pending = stream.pending_before(end)
    dropped = pending_keys_at(stream, end) - survivors
    for key in dropped:
        if key[0] == "entry":
            assert pending[key].change == "remove", (
                f"dropped entry key must be a remove, never insert/replace: "
                f"{key!r} change={pending[key].change!r}"
            )
        else:
            assert key[0] in ("data", "meta"), f"unexpected dropped key kind: {key!r}"
    state = apply_survivors(durable_state(stream, end), stream, end, survivors)
    assert not isinstance(state, Skip), state
    project = ext4_volume / f"recon-{name}-p"
    metadata = ext4_volume / f"recon-{name}-m"
    project.mkdir()
    metadata.mkdir()
    reconstruct(state, project, metadata)
    assert world_digest(project, metadata) == stream.final_world_digest


def test_reconstruction_preserves_hard_link_relations(persistence_recording, ext4_volume):
    """The move's anchor and destination must share one inode after reconstruction."""
    import os

    from tests.persistence_model import (
        Skip,
        _maximal_survivors,
        _resolve,
        apply_survivors,
        durable_state,
        reconstruct,
    )

    stream = persistence_recording("minimal-move")
    cut = stream.index_after(change="insert")
    state = apply_survivors(
        durable_state(stream, cut), stream, cut, _maximal_survivors(stream, cut)
    )
    assert not isinstance(state, Skip), state

    linked = [
        paths
        for paths in _group_by_link(state.tree)
        if len(paths) > 1
    ]
    assert linked, "the cut after the anchor's insert must show a durable link group"
    anchor, dest = sorted(linked[0])

    project = ext4_volume / "recon-move-link-p"
    metadata = ext4_volume / "recon-move-link-m"
    project.mkdir()
    metadata.mkdir()
    reconstruct(state, project, metadata)

    assert os.stat(_resolve(dest, project, metadata)).st_ino == os.stat(
        _resolve(anchor, project, metadata)
    ).st_ino


def _group_by_link(tree):
    groups: dict[tuple, list[str]] = {}
    for path, value in tree.items():
        if value[0] == "file" and len(value) == 4:
            groups.setdefault(value[3], []).append(path)
    return groups.values()


# --- Task 5: cut/survivor enumeration, skip accounting, named move tuples ----------


def test_move_generates_all_named_forward_tuples(persistence_recording):
    from tests.persistence_model import named_tuples

    stream = persistence_recording("minimal-move")
    cells = named_tuples(stream)
    assert {"dual-name-forward", "anchor-only-forward"} <= cells.keys()


def test_reverse_move_tuples_come_from_the_caught_stream(persistence_recording):
    from tests.persistence_model import named_tuples

    stream = persistence_recording("caught-rollback-move", caught=True)
    cells = named_tuples(stream)
    assert {"dual-name-reverse", "anchor-only-reverse"} <= cells.keys()
    assert {"dual-name-forward", "anchor-only-forward"} <= cells.keys()


def test_named_tuples_is_empty_on_a_non_move_scenario(persistence_recording):
    """Erratum 3: named-tuple assertions apply only to move-bearing scenarios."""
    from tests.persistence_model import named_tuples

    stream = persistence_recording("minimal-create")
    assert named_tuples(stream) == {}


def test_enumeration_counts_and_reports_skips_by_reason(persistence_recording):
    from tests.persistence_model import enumerate_cells

    stream = persistence_recording("minimal-create")
    cells, accounting = enumerate_cells(stream)
    assert accounting.cells > 0
    assert accounting.cells == len(cells)
    assert accounting.probe_folded > 0
    assert set(accounting.skips) <= {
        "remove-without-target", "replace-without-target", "orphan-object-state",
    }


def test_the_pending_cap_fails_loud(persistence_recording):
    """A realistic boundary, not a degenerate zero: `corpus-write`'s transactional
    pending-set size genuinely reaches 4 at some cut, so `pending_cap=3` breaches on the
    scenario's own complexity, not on the mere presence of any pending key at all.

    The peak was 6 before design §4.1's creation-mode ruling (2026-08-15) folded every
    creation's mode into its entry insert; re-measured at 4 afterwards, and the cap here
    re-tuned with it -- a cap test pinned to a stale peak stops testing the boundary and
    starts testing nothing.
    """
    import pytest

    from tests.persistence_model import PendingCapExceeded, enumerate_cells

    stream = persistence_recording("corpus-write")
    with pytest.raises(PendingCapExceeded):
        enumerate_cells(stream, pending_cap=3)


_RECORDABLE_SCENARIOS = [
    (entry.name, entry.inject_failure is not None)
    for entry in _SCENARIOS
    if entry.family != "refusal"
]


@pytest.mark.parametrize("name,caught", _RECORDABLE_SCENARIOS)
def test_the_default_cap_holds_across_every_recordable_scenario(
    name, caught, persistence_recording
):
    """The transactional dimension `pending_cap` bounds stays small on every scenario
    `SCENARIOS` carries (probe-noise is unbounded and handled separately, `_fold`) --
    `enumerate_cells`'s default `pending_cap=12` must never raise on a real recorded
    stream. `"refusal-capability"` is excluded: design's own §6 text rules capability
    refusals outside the persistence-cut product (they raise before any durable
    transaction exists, so `persistence_recording` cannot even produce a `Stream` for
    one). Parametrized (not one shared-fixture loop) for the same reason
    `test_full_durable_reconstruction_equals_the_live_final_world` above is: each
    scenario's `seed_world` needs its own fresh project root, not a second scenario's
    leftover tree from `coordinator_on`.
    """
    from tests.persistence_model import enumerate_cells

    stream = persistence_recording(name, caught=caught)
    _, accounting = enumerate_cells(stream)
    assert accounting.cells > 0


def test_a_mode_change_tears_and_the_zero_mode_path_still_reconstructs(
    persistence_recording, ext4_volume
):
    """CONTROLLER RULING (design §4.1 as amended 2026-08-15): a *creation's* mode is
    atomic with inode creation and cannot tear, but a later mode **change** is an
    independent durability unit that can -- leaving the object at the mode it was
    created with.

    This replaces the earlier creation-tear directed test, which pinned a reconstructed
    mode-`0` directory built by excluding a creation's paired `meta mode` unit -- a unit
    the ruling deleted, and a world no crash can produce. The shape pinned now is the one
    the ruling preserved, and `minimal-mkdir` produces it for real: `CreateDirectory`
    mkdirs its work name at `0o700`, then `set_mode`s the declared `0o755` through the
    retained descriptor (`src/atoms/coordinator/effects/create_directory.py`:
    `mkdir_child` -> `repair_entry_mode` -> `open_child_directory` -> `set_mode` ->
    `flush_file`), so the survivor product genuinely holds that directory at both modes.

    The second half keeps design §4.4's mkdir-`0o700`/populate/reverse-depth-chmod
    ordering covered. No recorded stream reaches a mode-`0` object any more -- that was
    the ruling's whole point -- so the state is *derived* from a real cell with
    `dataclasses.replace` on its `tree` alone: `reconstruct` reads only `tree` and
    `backup_bytes`, the other four fields stay exactly as `enumerate_cells` built them,
    and `_materialize_tree`'s mode-`0` branch stays exercised rather than becoming
    untested code.
    """
    import dataclasses
    import os
    import stat

    from tests.persistence_model import (
        Mutation,
        _resolve,
        enumerate_cells,
        reconstruct,
    )

    stream = persistence_recording("minimal-mkdir")
    changes = {
        unit.key: unit.payload
        for event in stream.events
        if type(event) is Mutation
        for unit in event.units
        if unit.key[0] == "meta" and unit.key[2] == "mode"
    }
    assert changes, "minimal-mkdir must record at least one mode CHANGE unit"

    cells, _ = enumerate_cells(stream)
    by_path: dict[str, dict[int, int]] = {}
    for index, cell in enumerate(cells):
        for path, value in cell.state.tree.items():
            if value[0] == "dir":
                by_path.setdefault(path, {}).setdefault(value[1], index)

    torn = [(path, modes) for path, modes in by_path.items() if len(modes) > 1]
    assert torn, "no directory appears at two modes: the mode-change axis is gone"
    path, modes = torn[0]
    changed_mode, creation_mode = max(modes), min(modes)
    token = next(key[1] for key, payload in changes.items() if payload == changed_mode)
    assert stream.creation_modes[token] == creation_mode, (
        "the mode a torn-away change leaves behind must be the object's creation mode"
    )

    for label, mode in (("torn", creation_mode), ("landed", changed_mode)):
        state = cells[modes[mode]].state
        project = ext4_volume / f"mode-{label}-p"
        metadata = ext4_volume / f"mode-{label}-m"
        project.mkdir()
        metadata.mkdir()
        reconstruct(state, project, metadata)
        assert stat.S_IMODE(os.stat(_resolve(path, project, metadata)).st_mode) == mode

    # --- the mode-0 reconstruction path (design §4.4's ordering) --------------------
    source = cells[modes[creation_mode]].state
    populated = next(
        (
            directory
            for directory, value in source.tree.items()
            if value[0] == "dir"
            and any(other.startswith(f"{directory}/") for other in source.tree)
        ),
        None,
    )
    assert populated is not None, "no populated directory to lock down to mode 0"
    zero_tree = dict(source.tree)
    zero_tree[populated] = ("dir", 0)
    state = dataclasses.replace(source, tree=zero_tree)

    project = ext4_volume / "recon-zero-mode-p"
    metadata = ext4_volume / "recon-zero-mode-m"
    project.mkdir()
    metadata.mkdir()
    reconstruct(state, project, metadata)

    on_disk_dir = _resolve(populated, project, metadata)
    assert stat.S_IMODE(os.stat(on_disk_dir).st_mode) == 0

    # Mode 0 genuinely blocks even the owner from resolving a name underneath it (POSIX
    # requires search ("x") on the containing directory to look a child up by name) --
    # asserted above, before anything is touched. To verify the child `reconstruct`
    # populated *before* locking the directory down (rather than merely trusting the
    # in-memory tree), temporarily restore search access, purely for this assertion,
    # then put it back.
    child_path = next(other for other in state.tree if other.startswith(f"{populated}/"))
    on_disk_child = _resolve(child_path, project, metadata)
    child_value = state.tree[child_path]
    os.chmod(on_disk_dir, 0o700)
    try:
        child_info = os.lstat(on_disk_child)
        if child_value[0] == "file":
            assert stat.S_IMODE(child_info.st_mode) == child_value[2]
            assert on_disk_child.read_bytes() == child_value[1]
        else:
            assert stat.S_ISDIR(child_info.st_mode) or stat.S_ISLNK(child_info.st_mode)
    finally:
        os.chmod(on_disk_dir, 0)
