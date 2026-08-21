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


def _named_callers(name: str) -> set[str]:
    callers = set()
    for path in sorted((SOURCE_ROOT / "coordinator").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for function in (
            node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
        ):
            if any(
                isinstance(node, ast.Call)
                and (
                    isinstance(node.func, ast.Name)
                    and node.func.id == name
                    or isinstance(node.func, ast.Attribute)
                    and node.func.attr == name
                )
                for node in ast.walk(function)
            ):
                callers.add(f"{path.name}::{function.name}")
    return callers


def test_authority_minting_and_halt_persistence_have_one_caller_each():
    assert _named_callers("_approve_for_recovery") == {"recover.py::resolve"}
    assert _named_callers("_mutation_denied") == {"recover.py::run_plan"}
    assert _named_callers("set_assembly_halt") == {
        "recover.py::_persist_assembly_halt"
    }
    tree = ast.parse(
        (SOURCE_ROOT / "coordinator" / "recover.py").read_text(encoding="utf-8")
    )
    persist = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_persist_assembly_halt"
    )
    annotations = ast.unparse(persist.args)
    assert "ProjectApprovedSpec" not in annotations  # Design §9.3 exception.


def test_effect_modules_are_syscall_only_and_execution_ignores_dependencies():
    forbidden = {
        "atoms.chain",
        "atoms.coordinator.commands",
        "atoms.coordinator.commit",
        "atoms.coordinator.execute",
        "atoms.coordinator.recover",
    }
    for path in sorted((SOURCE_ROOT / "coordinator" / "effects").glob("*.py")):
        imports = _resolved_imports(
            ast.parse(path.read_text(encoding="utf-8")),
            package="atoms.coordinator.effects",
        )
        assert not any(
            item == blocked or item.startswith(f"{blocked}.")
            for item in imports
            for blocked in forbidden
        ), path.name
    for name in ("commit.py", "execute.py", "recover.py"):
        tree = ast.parse(
            (SOURCE_ROOT / "coordinator" / name).read_text(encoding="utf-8")
        )
        assert not any(
            isinstance(node, ast.Attribute) and node.attr == "dependencies"
            for node in ast.walk(tree)
        ), name


def test_only_the_recovery_loop_forwards_authorized_mutations_to_settle():
    assert _named_callers("apply_transform") == {"recover.py::_execute_mutating"}
    assert _named_callers("apply_remove_scratch") == {
        "recover.py::_execute_mutating"
    }


def test_read_chain_uses_the_lease_and_registered_root_seam_and_exposes_neither():
    """The read command is the mutators' seam projected, and nothing more.

    `test_fs_architecture.py` already pins that every public command enters
    `_recovery_lease`; what is specific to `read_chain` is that it enters
    `_registered_root` *inside* that lease -- the `append_intent`/`run_transaction`
    shape -- and that the value it hands back carries no descriptor, lease, store, or
    binding.
    """
    tree = ast.parse(
        (SOURCE_ROOT / "coordinator" / "commands.py").read_text(encoding="utf-8")
    )
    read_chain = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "read_chain"
    )
    lease = next(
        node
        for node in ast.walk(read_chain)
        if isinstance(node, ast.With)
        and ast.unparse(node.items[0].context_expr).startswith("_recovery_lease(")
    )
    # `_registered_root` is entered under the lease either as a later context of the
    # same `with` -- which is nesting -- or as a `with` in its body. Both spellings
    # produce this one ordered list, and a third context manager would break it.
    entered = [ast.unparse(item.context_expr) for item in lease.items]
    entered += [
        ast.unparse(item.context_expr)
        for node in lease.body
        if isinstance(node, ast.With)
        for item in node.items
    ]
    assert [name.partition("(")[0] for name in entered] == [
        "_recovery_lease",
        "_registered_root",
    ]
    assert read_chain.returns is not None
    assert ast.unparse(read_chain.returns) == "ChainView"

    view = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "ChainView"
    )
    assert [ast.unparse(decorator) for decorator in view.decorator_list] == [
        "dataclass(frozen=True)"
    ]
    assert [
        (ast.unparse(node.target), ast.unparse(node.annotation))
        for node in view.body
        if isinstance(node, ast.AnnAssign)
    ] == [
        ("genesis_digest", "str"),
        ("entries", "tuple[tuple[str, Entry], ...]"),
        ("tip", "str"),
    ]
