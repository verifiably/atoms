"""A5b tier 6 -- import direction, package surface, and the fixture registry."""

from __future__ import annotations

import ast
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
