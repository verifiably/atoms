"""Explicit recovery-model fixture registry."""

import pytest

from tests.recovery_support import (
    make_create_file_case,
    make_halted_authority_snapshot,
    make_noop_replace_case,
    make_pending_clean_case,
    make_pending_drift_case,
    make_pending_scratch_case,
    make_reducer_step_cases,
    make_replace_case,
    make_replace_started_case,
    make_replace_transform_case,
    make_terminal_snapshot,
    make_three_effect_snapshot,
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
