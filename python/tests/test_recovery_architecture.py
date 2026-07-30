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
from tests.architecture_support import fixture_names, unregistered_test_arguments

_CLASSIFIER_MODULE = "atoms.core.recovery.classifier"


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
    registered = fixture_names(tests_root / "conftest.py")
    assert unregistered_test_arguments(
        tests_root, registered, "test_recovery_*.py"
    ) == set()


def test_recovery_fixture_registry_scans_test_class_methods(tmp_path):
    (tmp_path / "test_recovery_nested.py").write_text(
        "class TestNested:\n"
        "    def test_uses_fixture(self, missing_fixture):\n"
        "        pass\n",
        encoding="utf-8",
    )

    assert unregistered_test_arguments(
        tmp_path, set(), "test_recovery_*.py"
    ) == {
        "missing_fixture"
    }
