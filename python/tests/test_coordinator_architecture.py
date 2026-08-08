"""A5b tier 6 -- import direction, package surface, and the fixture registry."""

from __future__ import annotations

import ast
import re
from importlib.util import resolve_name
from pathlib import Path

from tests.architecture_support import (
    decorator_name,
    fixture_names,
    unregistered_test_arguments,
)

SOURCE_ROOT = Path(__file__).parents[1] / "src" / "atoms"
TESTS = Path(__file__).parent
_STORE_IMPORT_EXEMPT = {"coordinator", "store"}


def _resolved_imports(tree: ast.Module, *, package: str) -> set[str]:
    targets: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets.update(alias.name for alias in node.names)
            continue
        if not isinstance(node, ast.ImportFrom):
            continue
        module = node.module or ""
        imported_from = (
            resolve_name(f"{'.' * node.level}{module}", package)
            if node.level
            else module
        )
        targets.add(imported_from)
        targets.update(
            f"{imported_from}.{alias.name}"
            for alias in node.names
            if alias.name != "*"
        )
    return targets


def _store_importers(source_root: Path) -> list[str]:
    offenders = []
    for path in sorted(source_root.rglob("*.py")):
        parts = path.relative_to(source_root).parts
        if parts[0] in _STORE_IMPORT_EXEMPT:
            continue
        package = ".".join(("atoms", *parts[:-1]))
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(
            name == "atoms.store" or name.startswith("atoms.store.")
            for name in _resolved_imports(tree, package=package)
        ):
            offenders.append(str(path.relative_to(source_root)))
    return offenders


def test_only_the_coordinator_and_the_store_itself_import_the_store():
    assert _store_importers(SOURCE_ROOT) == []


def test_the_store_import_scanner_finds_a_planted_offender(tmp_path):
    root = tmp_path / "atoms"
    for relative, source in (
        ("fs/leak.py", "from atoms.store import Store\n"),
        ("core/leak.py", "import atoms.store.connection\n"),
        ("store/records.py", "from atoms.store.schema import variant_of\n"),
        ("coordinator/root.py", "from atoms.store.connection import open_store\n"),
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")

    assert _store_importers(root) == ["core/leak.py", "fs/leak.py"]


def test_the_coordinator_exports_nothing():
    import atoms.coordinator as package

    assert package.__all__ == ()
    assert not hasattr(package, "Lease")
    assert not hasattr(package, "_recovery_lease")


_PRIVATE_COORDINATOR_MODULES = ("atoms.coordinator.lease", "atoms.coordinator.root")


def _private_coordinator_importers(source_root: Path) -> list[str]:
    offenders = []
    for path in sorted(source_root.rglob("*.py")):
        parts = path.relative_to(source_root).parts
        if parts[0] == "coordinator":
            continue
        package = ".".join(("atoms", *parts[:-1]))
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(
            name == private or name.startswith(f"{private}.")
            for name in _resolved_imports(tree, package=package)
            for private in _PRIVATE_COORDINATOR_MODULES
        ):
            offenders.append(str(path.relative_to(source_root)))
    return offenders


def test_no_module_outside_the_coordinator_imports_the_lease_or_the_root():
    assert _private_coordinator_importers(SOURCE_ROOT) == []


def test_the_private_coordinator_scanner_finds_a_planted_offender(tmp_path):
    root = tmp_path / "atoms"
    for relative, source in (
        ("fs/leak.py", "from atoms.coordinator import lease\n"),
        ("core/leak.py", "from atoms.coordinator.root import _recovery_lease\n"),
        ("store/leak.py", "import atoms.coordinator.lease\n"),
        ("coordinator/root.py", "from atoms.coordinator.lease import Lease\n"),
        ("capture/reader.py", "from atoms.coordinator.prepare import open_workspace\n"),
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")

    assert _private_coordinator_importers(root) == [
        "core/leak.py",
        "fs/leak.py",
        "store/leak.py",
    ]


def test_the_coordinator_fixture_registry_covers_every_test_argument():
    registered = fixture_names(TESTS / "conftest.py")
    assert (
        unregistered_test_arguments(TESTS, registered, "test_coordinator_*.py") == set()
    )
    misplaced = sorted(
        f"{path.name}::{node.name}"
        for path in TESTS.glob("test_coordinator_*.py")
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and any(
            decorator_name(decorator) == "fixture" for decorator in node.decorator_list
        )
    )
    assert misplaced == [], f"fixtures must be declared in conftest.py: {misplaced}"


def _flat(lines: list[str]) -> str:
    return " ".join(" ".join(lines).split())


def _status_section(text: str) -> str:
    """The `## Status…` section: its heading through the next `## ` heading.

    Whitespace is flattened because both AGENTS.md and README.md wrap their status
    sentences across lines, and a reflow must not silently disable a guard.
    """
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("## Status"))
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")),
        len(lines),
    )
    return _flat(lines[start:end])


def _status_field(text: str) -> str:
    """A design's `**Status:**` header field, bounded by a blank line or the next field.

    Scoped to the header on purpose. A design's status is a claim about the present; its
    body may legitimately QUOTE a status string while recording an amendment, and A6's
    §3.3 does exactly that -- it prints the authority's old header verbatim so the
    before/after is auditable. A whole-file scan conflates the claim with the record of
    the claim changing, and would force the record to be edited to keep the guard green:
    the precise inversion this guard exists to prevent.
    """
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("**Status:**"))
    field = [lines[start]]
    for line in lines[start + 1 :]:
        if not line.strip() or re.match(r"\*\*[A-Za-z][^*]*:\*\*", line):
            break
        field.append(line)
    return _flat(field)


def test_a6_status_is_synchronized_across_authority_documents():
    root = Path(__file__).parents[2]

    def read(name: str) -> str:
        return (root / name).read_text(encoding="utf-8")

    agents = _status_section(read("AGENTS.md"))
    readme = _status_section(read("README.md"))
    a6 = _status_field(read("docs/plans/2026-08-07-a6-coherent-capture-design.md"))
    a5b = _status_field(read("docs/plans/2026-08-02-a5b-recovery-lease-design.md"))
    authority = _status_field(
        read("docs/plans/2026-07-23-recoverable-fs-effect-engine-design.md")
    )

    assert "A5 and A6 are implemented; A7–A8 remain unimplemented" in agents
    assert "A6 — coherent capture" in agents
    assert "**Status:** Implemented on 2026-08-07." in a6
    # No current status claim may still say A6 is unimplemented. A5b's design carries the
    # same sentence and is the one easiest to leave behind.
    for region in (agents, readme, a6, a5b, authority):
        assert "A6–A8 remain unimplemented" not in region
    assert "A7–A8 remain unimplemented" in a5b

    # The authority header spells its remainder differently -- "A6–A8 (coherent capture,
    # ...) remain" -- so the shared forbidden string cannot police it. Assert the header
    # positively, and forbid the span it replaces.
    assert "A1–A6 are implemented" in authority
    assert "A7–A8 (effect/recovery execution, synthetic exerciser) remain." in authority
    assert "A1–A5b are implemented" not in authority
    assert "A6–A8 (coherent capture" not in authority

    # The README's roadmap is the reader's map of what exists; it was three sub-plans
    # stale before A6 and must not be left that way.
    assert "A4–A8 remain unimplemented" not in readme
    assert "no filesystem mutation code has landed" not in readme
    for heading in (
        "**A4 — capability backend, volume binding, and project approval (implemented):**",
        "**A5 — durable metadata store and recovery lease (implemented):**",
        "**A6 — coherent capture and the observation mechanism (implemented):**",
    ):
        assert heading in readme
