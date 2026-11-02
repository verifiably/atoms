#!/usr/bin/env python3
"""Fail a hosted run whose JUnit report does not cover pytest's collection.

    check_ci_coverage.py <collected.txt> <pytest.xml>
    check_ci_coverage.py --skips <pytest.xml>

collected.txt is `pytest --collect-only -qq` output, one `tests/test_x.py: N` line per
module. Every collected module must appear in the JUnit report with exactly N
testcases: a dropped module or a run that stopped partway is a failure, never a pass
with fewer tests. Prints the problems and exits 1, or prints `coverage ok: <modules>
modules, <tests> tests` and exits 0. Malformed input exits 2.

--skips prints one `node_id<TAB>reason` line per skipped testcase, in report order.
pytest's -rs summary folds skips sharing a location and reason, so the JUnit report is
the only place each skip keeps its own identity.

Standard library only: it runs on a bare runner Python.
"""

import re
import sys
import xml.etree.ElementTree as ET
from collections.abc import Iterable

_COLLECTED_LINE = re.compile(r"(\S+\.py): (\d+)")


def parse_collected(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        match = _COLLECTED_LINE.fullmatch(line.strip())
        if match is None:
            raise ValueError(f"not a collection line: {line!r}")
        counts[match.group(1)] = int(match.group(2))
    return counts


def _dotted(module: str) -> str:
    return module.removesuffix(".py").replace("/", ".")


def junit_counts(xml_text: str, modules: Iterable[str]) -> dict[str, int]:
    """Testcases per module, matching each classname to the module whose dotted name is
    its longest prefix on whole components (tests.test_a never claims tests.test_ab)."""
    by_dotted = {_dotted(module): module for module in modules}
    counts: dict[str, int] = {}
    for case in ET.fromstring(xml_text).iter("testcase"):
        classname = case.get("classname", "")
        owner = None
        for dotted in by_dotted:
            if (classname == dotted or classname.startswith(dotted + ".")) and (
                owner is None or len(dotted) > len(owner)
            ):
                owner = dotted
        if owner is None:
            raise ValueError(f"testcase {classname}::{case.get('name', '')} matches no collected module")
        module = by_dotted[owner]
        counts[module] = counts.get(module, 0) + 1
    return counts


def problems(collected: dict[str, int], junit: dict[str, int]) -> list[str]:
    found = []
    for module in sorted(set(collected) | set(junit)):
        want, got = collected.get(module), junit.get(module)
        if want == got:
            continue
        if got is None:
            found.append(f"{module}: collected {want}, missing from JUnit (0)")
        elif want is None:
            found.append(f"{module}: not collected (0), JUnit {got}")
        else:
            found.append(f"{module}: collected {want}, JUnit {got}")
    return found


def skipped_tests(xml_text: str) -> list[tuple[str, str]]:
    skips = []
    for case in ET.fromstring(xml_text).iter("testcase"):
        skipped = case.find("skipped")
        if skipped is not None:
            node_id = f"{case.get('classname', '')}::{case.get('name', '')}"
            skips.append((node_id, skipped.get("message", "")))
    return skips


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def main(argv: list[str]) -> int:
    try:
        if len(argv) == 2 and argv[0] == "--skips":
            for node_id, reason in skipped_tests(_read(argv[1])):
                print(f"{node_id}\t{reason}")
            return 0
        if len(argv) != 2:
            print("usage: check_ci_coverage.py <collected.txt> <pytest.xml> | --skips <pytest.xml>", file=sys.stderr)
            return 2
        collected = parse_collected(_read(argv[0]))
        found = problems(collected, junit_counts(_read(argv[1]), collected))
    except (ValueError, OSError, ET.ParseError) as error:
        print(f"check_ci_coverage: {error}", file=sys.stderr)
        return 2
    if found:
        for line in found:
            print(line)
        return 1
    print(f"coverage ok: {len(collected)} modules, {sum(collected.values())} tests")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
