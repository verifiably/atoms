import ast
import inspect
import subprocess
import sys
from importlib.util import resolve_name
from pathlib import Path
from typing import get_type_hints

import pytest

from atoms.core import recovery
from atoms.core.compiler import CompiledSpec
from atoms.core.spec import TransactionSpec

_CLASSIFIER_MODULE = "atoms.core.recovery.classifier"
_PYTEST_BUILTINS = {
    "cache",
    "capfd",
    "capfdbinary",
    "caplog",
    "capsys",
    "capsysbinary",
    "capteesys",
    "doctest_namespace",
    "monkeypatch",
    "pytestconfig",
    "record_property",
    "record_testsuite_property",
    "record_xml_attribute",
    "recwarn",
    "request",
    "tmp_path",
    "tmp_path_factory",
    "tmpdir",
    "tmpdir_factory",
}


def _python_imports(source_path: Path) -> set[str]:
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(
                alias.name.split(".", 1)[0] for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.add(node.module.split(".", 1)[0])
    return imports


def _resolved_import_targets(
    source: str,
    *,
    package: str,
) -> set[str]:
    tree = ast.parse(source)
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


def _imports_classifier(source: str, *, package: str) -> bool:
    return any(
        target == _CLASSIFIER_MODULE
        or target.startswith(f"{_CLASSIFIER_MODULE}.")
        for target in _resolved_import_targets(source, package=package)
    )


def _decorator_name(decorator: ast.expr) -> str | None:
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return None


def _fixture_names(conftest_path: Path) -> set[str]:
    tree = ast.parse(conftest_path.read_text(encoding="utf-8"))
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(
            _decorator_name(decorator) == "fixture"
            for decorator in node.decorator_list
        )
    }


def _parametrize_names(
    test: ast.FunctionDef | ast.AsyncFunctionDef,
) -> set[str]:
    names: set[str] = set()
    for decorator in test.decorator_list:
        if (
            not isinstance(decorator, ast.Call)
            or _decorator_name(decorator) != "parametrize"
            or not decorator.args
        ):
            continue
        raw_names = decorator.args[0]
        if isinstance(raw_names, ast.Constant) and isinstance(
            raw_names.value,
            str,
        ):
            names.update(
                name.strip()
                for name in raw_names.value.split(",")
                if name.strip()
            )
            continue
        if isinstance(raw_names, (ast.Tuple, ast.List)):
            literal_names: list[str] = []
            for item in raw_names.elts:
                if not (
                    isinstance(item, ast.Constant)
                    and isinstance(item.value, str)
                ):
                    break
                literal_names.append(item.value)
            else:
                names.update(literal_names)
                continue
        if isinstance(raw_names, (ast.Tuple, ast.List)):
            raise TypeError(
                f"{test.name} has a non-literal parametrize signature"
            )
        raise TypeError(
            f"{test.name} has a non-literal parametrize signature"
        )
    return names


def _collected_test_functions(
    tree: ast.Module,
) -> list[
    tuple[ast.FunctionDef | ast.AsyncFunctionDef, set[str]]
]:
    collected: list[
        tuple[ast.FunctionDef | ast.AsyncFunctionDef, set[str]]
    ] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test_"):
                collected.append((node, set()))
            continue
        if not isinstance(node, ast.ClassDef) or not node.name.startswith(
            "Test"
        ):
            continue
        for method in node.body:
            if not isinstance(
                method,
                (ast.FunctionDef, ast.AsyncFunctionDef),
            ) or not method.name.startswith("test_"):
                continue
            positional = (*method.args.posonlyargs, *method.args.args)
            receivers = (
                {positional[0].arg}
                if positional and positional[0].arg in {"self", "cls"}
                else set()
            )
            collected.append((method, receivers))
    return collected


def _unregistered_test_arguments(
    tests_root: Path,
    registered: set[str],
) -> set[str]:
    missing: set[str] = set()
    for test_path in tests_root.glob("test_recovery_*.py"):
        tree = ast.parse(test_path.read_text(encoding="utf-8"))
        for node, receivers in _collected_test_functions(tree):
            arguments = {
                argument.arg
                for argument in (
                    *node.args.posonlyargs,
                    *node.args.args,
                    *node.args.kwonlyargs,
                )
            }
            missing.update(
                arguments
                - _parametrize_names(node)
                - registered
                - _PYTEST_BUILTINS
                - receivers
            )
    return missing


def test_public_surface_has_exactly_five_operations():
    operations = {
        name
        for name in recovery.__all__
        if inspect.isfunction(getattr(recovery, name))
    }
    assert operations == {
        "build_recovery_snapshot",
        "classify_recovery",
        "authorize_recovery_step",
        "reduce_recovery_plan_prefix",
        "apply_recovery_plan",
    }


def test_recovery_package_has_no_io_or_sqlite_imports():
    root = (
        Path(__file__).parents[1]
        / "src"
        / "atoms"
        / "core"
        / "recovery"
    )
    forbidden = {
        "ctypes",
        "datetime",
        "os",
        "pathlib",
        "random",
        "secrets",
        "sqlite3",
        "subprocess",
        "time",
    }
    for source_path in root.glob("*.py"):
        assert not _python_imports(source_path) & forbidden, source_path


def test_a3_does_not_accept_raw_transaction_spec():
    annotations = get_type_hints(recovery.build_recovery_snapshot)
    assert annotations["compiled"] is CompiledSpec
    assert TransactionSpec not in annotations.values()


def test_classifier_journal_and_variants_ignore_dependencies():
    root = (
        Path(__file__).parents[1]
        / "src"
        / "atoms"
        / "core"
        / "recovery"
    )
    for name in ("classifier.py", "journal.py", "variants.py"):
        source_path = root / name
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        accesses = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and node.attr == "dependencies"
        ]
        assert accesses == [], source_path


def test_authorization_does_not_import_the_classifier():
    source_path = (
        Path(__file__).parents[1]
        / "src"
        / "atoms"
        / "core"
        / "recovery"
        / "authorization.py"
    )
    assert not _imports_classifier(
        source_path.read_text(encoding="utf-8"),
        package="atoms.core.recovery",
    )


@pytest.mark.parametrize(
    "source",
    [
        "from .classifier import classify_recovery as classify",
        "from . import classifier as recovery_classifier",
        "from atoms.core.recovery import classifier as recovery_classifier",
        "import atoms.core.recovery.classifier",
        "from atoms.core.recovery.classifier import classify_recovery",
    ],
    ids=[
        "relative-symbol",
        "relative-module",
        "absolute-package-member",
        "absolute-module",
        "absolute-symbol",
    ],
)
def test_classifier_import_scanner_rejects_equivalent_static_imports(
    source: str,
):
    assert _imports_classifier(source, package="atoms.core.recovery")


@pytest.mark.parametrize(
    "source",
    [
        "from . import classifier_helpers",
        "from atoms.core.recovery import classifier_helpers",
    ],
    ids=[
        "relative-sibling",
        "absolute-package-sibling",
    ],
)
def test_classifier_import_scanner_allows_sibling_modules(source: str):
    assert not _imports_classifier(
        source,
        package="atoms.core.recovery",
    )


def test_recovery_import_does_not_require_a_filesystem_backend():
    code = """
import builtins

blocked = {
    "atoms.backend",
    "atoms.backends",
    "atoms.core.backend",
    "atoms.core.filesystem",
    "atoms.filesystem",
}
original_import = builtins.__import__

def guarded_import(name, *args, **kwargs):
    if any(name == item or name.startswith(item + ".") for item in blocked):
        raise ImportError(f"blocked architecture dependency: {name}")
    return original_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
import atoms.core.recovery
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_recovery_fixture_registry_covers_every_test_argument():
    tests_root = Path(__file__).parent
    registered = _fixture_names(tests_root / "conftest.py")
    assert _unregistered_test_arguments(tests_root, registered) == set()


def test_recovery_fixture_registry_scans_test_class_methods(tmp_path):
    (tmp_path / "test_recovery_nested.py").write_text(
        "class TestNested:\n"
        "    def test_uses_fixture(self, missing_fixture):\n"
        "        pass\n",
        encoding="utf-8",
    )

    assert _unregistered_test_arguments(tmp_path, set()) == {
        "missing_fixture"
    }
