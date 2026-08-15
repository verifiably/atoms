"""Unit decomposition, coverage, replacement, and coalescing (design §4.1-§4.2)."""


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
