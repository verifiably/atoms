import dataclasses

import pytest

from atoms.core.errors import CapabilityUnavailable
from atoms.fs.volume import (
    _BARRIER_OPTIONS,
    CERTIFIED_ALLOWLIST,
    AllowlistEntry,
    DurabilityAllowlist,
    StorageProfile,
    VolumeConfiguration,
    build_configuration,
    parse_mount_id,
    parse_mountinfo,
    resolve_mount_entry,
)


def test_parse_mountinfo_separates_per_mount_and_super_options(mountinfo_text):
    entries = parse_mountinfo(mountinfo_text("ext4_writeback"))
    entry = next(item for item in entries if item.mount_point == "/data")
    assert entry.mount_options == ("rw", "noatime")
    assert entry.filesystem_type == "ext4"
    assert "data=writeback" in entry.super_options
    assert "data=writeback" not in entry.mount_options


def test_parse_mountinfo_unescapes_octal_mount_points(mountinfo_text):
    entries = parse_mountinfo(mountinfo_text("escaped_space"))
    assert any(entry.mount_point == "/mnt/my volume" for entry in entries)


def test_parse_mountinfo_handles_variable_optional_fields(mountinfo_text):
    entries = parse_mountinfo(mountinfo_text("optional_fields"))
    shared = next(item for item in entries if item.mount_point == "/shared")
    assert shared.filesystem_type == "ext4"
    assert shared.mount_id == 41


def test_parse_mountinfo_distinguishes_bind_mounts_sharing_a_device(mountinfo_text):
    entries = parse_mountinfo(mountinfo_text("bind_same_device"))
    binds = [item for item in entries if item.device == "259:2"]
    assert len(binds) == 2
    assert binds[0].mount_id != binds[1].mount_id


def test_parse_mount_id_reads_the_fdinfo_field(fdinfo_text):
    assert parse_mount_id(fdinfo_text("plain")) == 41


def test_resolve_mount_entry_matches_on_mount_id_not_device(monkeypatch, mountinfo_text):
    # st_dev alone is ambiguous: bind mounts share a device but differ in mount ID.
    monkeypatch.setattr("atoms.fs.volume.read_mount_id", lambda fd: 43)
    entry = resolve_mount_entry(7, mountinfo_text("bind_same_device"))
    assert entry.mount_id == 43
    assert entry.mount_point == "/data/bind"


def test_resolve_mount_entry_refuses_an_unresolvable_mount(monkeypatch, mountinfo_text):
    monkeypatch.setattr("atoms.fs.volume.read_mount_id", lambda fd: 9999)
    with pytest.raises(CapabilityUnavailable, match="mount"):
        resolve_mount_entry(7, mountinfo_text("bind_same_device"))


def test_build_configuration_normalizes_absent_ext4_options(mountinfo_text):
    entries = parse_mountinfo(mountinfo_text("ext4_defaults"))
    entry = next(item for item in entries if item.mount_point == "/data")
    configuration = build_configuration(entry, "7.1.5-arch1-1")
    assert configuration.barrier_options == (
        "async",
        "barrier=1",
        "commit=5",
        "data=ordered",
    )


def test_build_configuration_reads_super_only_values(mountinfo_text):
    # A field-6-only parser finds no data= at all and would normalize this
    # explicit data=writeback to the safe data=ordered default.
    entries = parse_mountinfo(mountinfo_text("ext4_writeback"))
    entry = next(item for item in entries if item.mount_point == "/data")
    configuration = build_configuration(entry, "7.1.5-arch1-1")
    assert "data=writeback" in configuration.barrier_options
    assert "data=ordered" not in configuration.barrier_options


def test_build_configuration_reads_per_mount_only_values(mountinfo_text):
    entries = parse_mountinfo(mountinfo_text("ext4_sync"))
    entry = next(item for item in entries if item.mount_point == "/data")
    configuration = build_configuration(entry, "7.1.5-arch1-1")
    assert "sync" in configuration.barrier_options
    assert "dirsync" in configuration.barrier_options


def test_build_configuration_normalizes_absent_xfs_options(mountinfo_text):
    # Exact tuple, not membership: shipping a barrier table for a filesystem means
    # shipping a durability claim about it, and a silently added or dropped option
    # changes which allowlist entry a real volume matches.
    entries = parse_mountinfo(mountinfo_text("xfs_defaults"))
    entry = next(item for item in entries if item.mount_point == "/data")
    configuration = build_configuration(entry, "7.1.5-arch1-1")
    assert configuration.barrier_options == ("async", "barrier=1")


def test_build_configuration_reads_xfs_super_only_values(mountinfo_text):
    # wsync appears only in field 11, so a field-6-only parser reports the default.
    entries = parse_mountinfo(mountinfo_text("xfs_wsync"))
    entry = next(item for item in entries if item.mount_point == "/data")
    configuration = build_configuration(entry, "7.1.5-arch1-1")
    assert configuration.barrier_options == ("async", "barrier=1", "wsync")


def test_build_configuration_normalizes_absent_btrfs_options(mountinfo_text):
    entries = parse_mountinfo(mountinfo_text("btrfs_defaults"))
    entry = next(item for item in entries if item.mount_point == "/data")
    configuration = build_configuration(entry, "7.1.5-arch1-1")
    assert configuration.barrier_options == ("barrier=1", "commit=30", "noflushoncommit")


def test_build_configuration_reads_btrfs_super_only_values(mountinfo_text):
    # An explicit flushoncommit and a non-default commit interval must both survive;
    # normalizing either to its default would silently widen the certified claim.
    entries = parse_mountinfo(mountinfo_text("btrfs_flushoncommit"))
    entry = next(item for item in entries if item.mount_point == "/data")
    configuration = build_configuration(entry, "7.1.5-arch1-1")
    assert configuration.barrier_options == ("barrier=1", "commit=15", "flushoncommit")


def test_every_supported_filesystem_has_normalization_coverage(mountinfo_text):
    # The production table is the source of truth. A hard-coded loop over the three
    # current filesystems would keep passing when a fourth table entry was added.
    super_only_cases = {
        "ext4": "ext4_writeback",
        "xfs": "xfs_wsync",
        "btrfs": "btrfs_flushoncommit",
    }
    assert set(_BARRIER_OPTIONS) == set(super_only_cases)
    for filesystem in _BARRIER_OPTIONS:
        defaults = parse_mountinfo(mountinfo_text(f"{filesystem}_defaults"))
        default_entry = next(item for item in defaults if item.mount_point == "/data")
        default_configuration = build_configuration(default_entry, "7.1.5-arch1-1")
        assert default_configuration.filesystem_type == filesystem

        super_only = parse_mountinfo(mountinfo_text(super_only_cases[filesystem]))
        super_entry = next(item for item in super_only if item.mount_point == "/data")
        super_configuration = build_configuration(super_entry, "7.1.5-arch1-1")
        assert super_configuration.filesystem_type == filesystem
        # The coverage fixture must actually isolate a non-default value in field 11.
        # Mapping a filesystem to its defaults fixture would otherwise satisfy the
        # key-set check without exercising super-options parsing.
        assert super_entry.mount_options == default_entry.mount_options
        assert super_entry.super_options != default_entry.super_options
        assert super_configuration.barrier_options != default_configuration.barrier_options


def test_build_configuration_carries_the_exact_kernel_and_backend_revision(mountinfo_text):
    entries = parse_mountinfo(mountinfo_text("ext4_defaults"))
    entry = next(item for item in entries if item.mount_point == "/data")
    configuration = build_configuration(entry, "7.1.5-arch1-1")
    # Not a major.minor truncation: one crash test must not certify a whole kernel line.
    assert configuration.kernel_identifier == "7.1.5-arch1-1"
    assert configuration.backend_revision == "linux-1"


def test_build_configuration_refuses_an_unlisted_filesystem(mountinfo_text):
    entries = parse_mountinfo(mountinfo_text("tmpfs"))
    entry = next(item for item in entries if item.mount_point == "/tmp")
    with pytest.raises(CapabilityUnavailable, match="tmpfs"):
        build_configuration(entry, "7.1.5-arch1-1")


def test_durability_features_are_empty_until_a_resolver_exists(mountinfo_text):
    entries = parse_mountinfo(mountinfo_text("ext4_defaults"))
    entry = next(item for item in entries if item.mount_point == "/data")
    assert build_configuration(entry, "7.1.5-arch1-1").durability_features == ()


def test_certified_allowlist_ships_empty():
    # Fail closed: production binding refuses every volume until A8 certifies one.
    assert CERTIFIED_ALLOWLIST.entries == frozenset()


def test_allowlist_matches_only_on_exact_configuration_and_profile(test_storage_profile):
    configuration = VolumeConfiguration(
        backend_id="linux",
        backend_revision="linux-1",
        kernel_identifier="7.1.5-arch1-1",
        filesystem_type="ext4",
        barrier_options=("async", "barrier=1", "commit=5", "data=ordered"),
        durability_features=(),
    )
    entry = AllowlistEntry(
        configuration=configuration,
        storage=test_storage_profile,
        certification_ref="crash-2026-07-29-a",
    )
    allowlist = DurabilityAllowlist(entries=frozenset({entry}))
    assert allowlist.match(configuration, test_storage_profile) is entry
    assert allowlist.match(configuration, StorageProfile(profile_id="other")) is None
    widened = dataclasses.replace(configuration, kernel_identifier="7.1.6-arch1-1")
    assert allowlist.match(widened, test_storage_profile) is None


def test_value_types_are_frozen(test_storage_profile):
    with pytest.raises(dataclasses.FrozenInstanceError):
        test_storage_profile.profile_id = "mutated"
