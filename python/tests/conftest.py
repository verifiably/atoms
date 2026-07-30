"""Explicit recovery-model fixture registry."""

import tempfile
from pathlib import Path

import pytest

from atoms.fs.linux import LinuxBackend
from atoms.fs.lock import acquire_project_lock
from atoms.fs.volume import StorageProfile
from tests.fs_support import (
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
    return Path(tempfile.mkdtemp(dir=base))


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
    return Path(tempfile.mkdtemp(dir=found))
