"""The opt-in that lets CI report a missing capability instead of failing.

The conversion is exercised by every capability-dependent test on a runner; what is
easy to get wrong, and cheap to pin here, is the gate in front of it. Anything but an
explicit "1" must leave the suite closed, because a half-set variable disarming the
gate is the failure nobody would notice.
"""

from tests.uncertified_host import UNCERTIFIED_HOST_VAR, uncertified_host


def test_only_an_explicit_one_declares_an_uncertified_host():
    assert uncertified_host({UNCERTIFIED_HOST_VAR: "1"})


def test_absent_empty_or_stray_values_keep_the_suite_closed():
    for value in ("", "0", "yes", "true", "TRUE", " 1", "1 "):
        assert not uncertified_host({UNCERTIFIED_HOST_VAR: value}), value
    assert not uncertified_host({})
