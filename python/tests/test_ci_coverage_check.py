"""The hosted coverage checker (.github/scripts/check_ci_coverage.py).

A hosted run that drops a module, or stops partway, must fail its job rather than pass
with fewer tests than were collected. The checker compares pytest's own collection
with the JUnit report the run wrote, module by module, and lists each skip by node id.
"""

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / ".github" / "scripts" / "check_ci_coverage.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_ci_coverage", _SCRIPT)
    assert spec is not None and spec.loader is not None, _SCRIPT
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


checker = _load()


def _junit(*cases: str) -> str:
    return '<?xml version="1.0"?><testsuites><testsuite>' + "".join(cases) + "</testsuite></testsuites>"


def _case(classname: str, name: str, body: str = "") -> str:
    return f'<testcase classname="{classname}" name="{name}">{body}</testcase>'


def _write(tmp_path: Path, collected: str, junit: str) -> list[str]:
    collected_path = tmp_path / "collected.txt"
    junit_path = tmp_path / "pytest.xml"
    collected_path.write_text(collected)
    junit_path.write_text(junit)
    return [str(collected_path), str(junit_path)]


def test_equal_counts_pass(tmp_path):
    collected_text = "tests/test_a.py: 2\ntests/test_b.py: 1\n"
    junit_text = _junit(
        _case("tests.test_a", "test_x"),
        _case("tests.test_a", "test_y", '<skipped message="no volume"/>'),
        _case("tests.test_b", "test_z"),
    )
    collected = checker.parse_collected(collected_text)
    assert checker.problems(collected, checker.junit_counts(junit_text, collected)) == []
    assert checker.main(_write(tmp_path, collected_text, junit_text)) == 0


def test_missing_module_fails(tmp_path):
    collected_text = "tests/test_a.py: 1\ntests/test_b.py: 1\n"
    junit_text = _junit(_case("tests.test_a", "test_x"))
    collected = checker.parse_collected(collected_text)
    found = checker.problems(collected, checker.junit_counts(junit_text, collected))
    assert len(found) == 1
    assert "tests/test_b.py" in found[0]
    assert checker.main(_write(tmp_path, collected_text, junit_text)) == 1


def test_partial_run_fails():
    collected = checker.parse_collected("tests/test_a.py: 3\n")
    junit_text = _junit(_case("tests.test_a", "test_x"), _case("tests.test_a", "test_y"))
    found = checker.problems(collected, checker.junit_counts(junit_text, collected))
    assert len(found) == 1
    assert "3" in found[0] and "2" in found[0]


def test_class_and_param_names_map_to_module():
    collected = checker.parse_collected("tests/test_a.py: 3\ntests/test_ab.py: 1\n")
    junit_text = _junit(
        _case("tests.test_a.TestThing", "test_method"),
        _case("tests.test_a", "test_param[1-x]"),
        _case("tests.test_a", "test_param[2-y]"),
        _case("tests.test_ab", "test_other"),
    )
    assert checker.junit_counts(junit_text, collected) == {"tests/test_a.py": 3, "tests/test_ab.py": 1}


def test_unknown_classname_is_error(tmp_path):
    collected_text = "tests/test_a.py: 1\n"
    junit_text = _junit(_case("other.mod", "test_x"))
    assert checker.main(_write(tmp_path, collected_text, junit_text)) == 2


def test_malformed_collected_line_is_error():
    with pytest.raises(ValueError):
        checker.parse_collected("tests/test_a.py: 1\ngarbage\n")


def test_skips_listed_individually():
    junit_text = _junit(
        _case("tests.test_a", "test_x[1]", '<skipped message="no user xattrs"/>'),
        _case("tests.test_a", "test_x[2]", '<skipped message="no user xattrs"/>'),
        _case("tests.test_a", "test_passes"),
    )
    assert checker.skipped_tests(junit_text) == [
        ("tests.test_a::test_x[1]", "no user xattrs"),
        ("tests.test_a::test_x[2]", "no user xattrs"),
    ]
