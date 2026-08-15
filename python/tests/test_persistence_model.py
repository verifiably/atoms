"""Unit decomposition, coverage, replacement, and coalescing (design §4.1-§4.2)."""

import pytest


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
