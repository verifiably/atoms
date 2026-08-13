import dataclasses
import io

import pytest

import atoms.fs.volume as volume_module
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


def _configuration() -> VolumeConfiguration:
    return VolumeConfiguration(
        backend_id="linux",
        backend_revision="linux-1",
        kernel_identifier="7.1.5-arch1-1",
        filesystem_type="ext4",
        barrier_options=("async", "barrier=1", "commit=5", "data=ordered"),
        durability_features=(),
    )


def _value_family(
    storage: StorageProfile | None = None,
) -> tuple[
    VolumeConfiguration,
    StorageProfile,
    AllowlistEntry,
    DurabilityAllowlist,
]:
    configuration = _configuration()
    declared_storage = storage or StorageProfile(profile_id="atoms-test-profile")
    entry = AllowlistEntry(
        configuration=configuration,
        storage=declared_storage,
        certification_ref="crash-2026-07-29-a",
    )
    allowlist = DurabilityAllowlist(entries=frozenset({entry}))
    return configuration, declared_storage, entry, allowlist


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


def test_parse_mountinfo_decodes_only_the_four_kernel_escapes_once():
    text = (
        r"41 25 259:2 / /mnt/space\040tab\011line\012slash\134040 "
        "rw,noatime - ext4 /dev/nvme0n1p2 rw"
    )

    entry = parse_mountinfo(text)[0]

    assert entry.mount_point == "/mnt/space tab\tline\nslash\\040"


def test_parse_mountinfo_refuses_an_impossible_kernel_escape():
    text = (
        r"41 25 259:2 / /mnt/impossible\777escape "
        "rw,noatime - ext4 /dev/nvme0n1p2 rw"
    )

    with pytest.raises(CapabilityUnavailable, match="mountinfo line 1"):
        parse_mountinfo(text)


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(
            "41 25 259:2 / /data rw,noatime",
            id="missing-separator",
        ),
        pytest.param(
            "41 25 259:2 / /data rw,noatime - ext4 /dev/x rw - trailing",
            id="duplicate-separator",
        ),
        pytest.param(
            "41 25 259:2 / /data rw,noatime - ext4 /dev/x",
            id="truncated-post-separator",
        ),
        pytest.param(
            "41 25 259:2 / /data rw,noatime - ext4 /dev/x rw trailing",
            id="trailing-token",
        ),
        pytest.param(
            "+41 25 259:2 / /data rw,noatime - ext4 /dev/x rw",
            id="signed-mount-id",
        ),
        pytest.param(
            "41 parent 259:2 / /data rw,noatime - ext4 /dev/x rw",
            id="nondecimal-parent-id",
        ),
        pytest.param(
            "41 25 259:minor / /data rw,noatime - ext4 /dev/x rw",
            id="nonnumeric-device",
        ),
    ],
)
def test_parse_mountinfo_refuses_malformed_records(text):
    with pytest.raises(CapabilityUnavailable, match="mountinfo line 1"):
        parse_mountinfo(text)


def test_parse_mountinfo_refuses_duplicate_mount_ids():
    text = (
        "41 25 259:2 / /data rw - ext4 /dev/x rw\n"
        "41 25 259:3 / /other rw - ext4 /dev/y rw\n"
    )

    with pytest.raises(CapabilityUnavailable, match="duplicate mount id 41"):
        parse_mountinfo(text)


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


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("mnt_id:\n", id="truncated"),
        pytest.param("mnt_id:\t41 trailing\n", id="trailing-token"),
        pytest.param("mnt_id:\t+41\n", id="signed"),
        pytest.param("mnt_id:\tnot-decimal\n", id="nonnumeric"),
        pytest.param("mnt_id:\t41\nmnt_id:\t42\n", id="duplicate"),
    ],
)
def test_parse_mount_id_requires_exactly_one_decimal_token(text):
    with pytest.raises(CapabilityUnavailable, match="mnt_id"):
        parse_mount_id(text)


def test_read_mount_id_adds_the_descriptor_path_to_malformed_fdinfo(
    monkeypatch
):
    monkeypatch.setattr(
        volume_module,
        "open",
        lambda *args, **kwargs: io.StringIO("mnt_id:\tbad\n"),
        raising=False,
    )

    with pytest.raises(CapabilityUnavailable, match="/proc/self/fdinfo/7") as caught:
        volume_module.read_mount_id(7)

    assert isinstance(caught.value.__cause__, CapabilityUnavailable)


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


def test_resolve_mount_entry_contextualizes_malformed_mountinfo(monkeypatch):
    monkeypatch.setattr("atoms.fs.volume.read_mount_id", lambda fd: 41)

    with pytest.raises(CapabilityUnavailable, match="mountinfo.*descriptor 7") as caught:
        resolve_mount_entry(7, "41 25 truncated")

    assert isinstance(caught.value.__cause__, CapabilityUnavailable)


def test_read_mountinfo_preserves_unrelated_non_utf8_mountpoints_for_matching(
    monkeypatch
):
    payload = (
        b"41 25 259:2 / /unrelated-\xff rw - ext4 /dev/x rw\n"
        b"42 25 259:3 / /data rw - ext4 /dev/y rw\n"
    )

    def open_mountinfo(path, *, encoding, errors):
        assert path == "/proc/self/mountinfo"
        assert encoding == "utf-8"
        assert errors == "surrogateescape"
        return io.TextIOWrapper(
            io.BytesIO(payload),
            encoding=encoding,
            errors=errors,
        )

    monkeypatch.setattr(volume_module, "open", open_mountinfo, raising=False)
    monkeypatch.setattr(volume_module, "read_mount_id", lambda fd: 42)

    text = volume_module.read_mountinfo()
    entry = resolve_mount_entry(7, text)

    assert "\udcff" in parse_mountinfo(text)[0].mount_point
    assert entry.mount_id == 42
    assert entry.mount_point == "/data"


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


def test_every_barrier_option_ignores_a_wrong_field_decoy(mountinfo_text):
    wrong_field_cases = {
        "ext4": (
            "ext4_wrong_field_decoys",
            ("async", "barrier=1", "commit=5", "data=ordered"),
        ),
        "xfs": ("xfs_wrong_field_decoys", ("async", "barrier=1")),
        "btrfs": (
            "btrfs_wrong_field_decoys",
            ("barrier=1", "commit=30", "noflushoncommit"),
        ),
    }
    assert set(_BARRIER_OPTIONS) == set(wrong_field_cases)
    for filesystem, table in _BARRIER_OPTIONS.items():
        case, expected = wrong_field_cases[filesystem]
        entry = parse_mountinfo(mountinfo_text(case))[0]
        for name, source, _absent in table:
            wrong_options = (
                entry.mount_options if source == "super" else entry.super_options
            )
            assert any(
                option == name
                or option.startswith(f"{name}=")
                or option == f"no{name}"
                for option in wrong_options
            ), f"{filesystem} fixture lacks wrong-field decoy for {name}"
        assert (
            build_configuration(entry, "7.1.5-arch1-1").barrier_options == expected
        )


def test_build_configuration_carries_the_exact_kernel_and_backend_revision(mountinfo_text):
    entries = parse_mountinfo(mountinfo_text("ext4_defaults"))
    entry = next(item for item in entries if item.mount_point == "/data")
    configuration = build_configuration(entry, "7.1.5-arch1-1")
    # Not a major.minor truncation: one crash test must not certify a whole kernel line.
    assert configuration.kernel_identifier == "7.1.5-arch1-1"
    assert configuration.backend_revision == "linux-2"


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


def test_allowlist_matches_equal_but_distinct_configuration_and_profile(
    test_storage_profile,
):
    configuration, storage, entry, allowlist = _value_family(test_storage_profile)
    equal_configuration = dataclasses.replace(configuration)
    equal_storage = dataclasses.replace(storage)
    assert equal_configuration is not configuration
    assert equal_storage is not storage
    assert allowlist.match(equal_configuration, equal_storage) is entry


@pytest.mark.parametrize(
    "near_miss",
    [
        pytest.param(
            lambda configuration, storage: (
                dataclasses.replace(configuration, backend_id="other"),
                dataclasses.replace(storage),
            ),
            id="configuration.backend_id",
        ),
        pytest.param(
            lambda configuration, storage: (
                dataclasses.replace(configuration, backend_revision="linux-2"),
                dataclasses.replace(storage),
            ),
            id="configuration.backend_revision",
        ),
        pytest.param(
            lambda configuration, storage: (
                dataclasses.replace(
                    configuration, kernel_identifier="7.1.6-arch1-1"
                ),
                dataclasses.replace(storage),
            ),
            id="configuration.kernel_identifier",
        ),
        pytest.param(
            lambda configuration, storage: (
                dataclasses.replace(configuration, filesystem_type="xfs"),
                dataclasses.replace(storage),
            ),
            id="configuration.filesystem_type",
        ),
        pytest.param(
            lambda configuration, storage: (
                dataclasses.replace(
                    configuration,
                    barrier_options=("async", "barrier=0", "commit=5", "data=ordered"),
                ),
                dataclasses.replace(storage),
            ),
            id="configuration.barrier_options",
        ),
        pytest.param(
            lambda configuration, storage: (
                dataclasses.replace(configuration, durability_features=("metadata_csum",)),
                dataclasses.replace(storage),
            ),
            id="configuration.durability_features",
        ),
        pytest.param(
            lambda configuration, storage: (
                dataclasses.replace(configuration),
                dataclasses.replace(storage, profile_id="other"),
            ),
            id="storage.profile_id",
        ),
    ],
)
def test_allowlist_refuses_a_near_miss_in_every_matched_field(near_miss):
    configuration, storage, _entry, allowlist = _value_family()
    candidate_configuration, candidate_storage = near_miss(configuration, storage)
    assert allowlist.match(candidate_configuration, candidate_storage) is None


@pytest.mark.parametrize(
    "value_factory",
    [
        pytest.param(lambda: _value_family()[0], id="VolumeConfiguration"),
        pytest.param(lambda: _value_family()[1], id="StorageProfile"),
        pytest.param(lambda: _value_family()[2], id="AllowlistEntry"),
        pytest.param(lambda: _value_family()[3], id="DurabilityAllowlist"),
    ],
)
def test_equal_value_objects_have_equal_hashes(value_factory):
    value = value_factory()
    equal_value = dataclasses.replace(value)
    assert equal_value is not value
    assert equal_value == value
    assert hash(equal_value) == hash(value)


@pytest.mark.parametrize(
    ("value_factory", "field_name", "replacement"),
    [
        pytest.param(
            _configuration,
            "backend_id",
            "other",
            id="VolumeConfiguration",
        ),
        pytest.param(
            lambda: StorageProfile(profile_id="atoms-test-profile"),
            "profile_id",
            "other",
            id="StorageProfile",
        ),
        pytest.param(
            lambda: AllowlistEntry(
                configuration=_configuration(),
                storage=StorageProfile(profile_id="atoms-test-profile"),
                certification_ref="crash-2026-07-29-a",
            ),
            "certification_ref",
            "other",
            id="AllowlistEntry",
        ),
        pytest.param(
            DurabilityAllowlist,
            "entries",
            frozenset(),
            id="DurabilityAllowlist",
        ),
    ],
)
def test_value_types_are_frozen(value_factory, field_name, replacement):
    value = value_factory()
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(value, field_name, replacement)
