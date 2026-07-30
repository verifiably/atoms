import ast
import ctypes
import importlib
import inspect
import sys
from importlib.util import resolve_name
from pathlib import Path

import pytest

import atoms.fs
from atoms.core.errors import CapabilityUnavailable
from atoms.fs import platform as fs_platform
from atoms.fs.volume import CERTIFIED_ALLOWLIST, DurabilityAllowlist
from tests.architecture_support import fixture_names, unregistered_test_arguments

_CORE_IMPORT_ALLOWLIST = {
    "__future__",
    "atoms",
    "collections",
    "dataclasses",
    "enum",
    "functools",
    "itertools",
    "json",
    "re",
    "typing",
    "unicodedata",
}


def _top_level_imports(source_path: Path) -> set[str]:
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names.add(node.module.split(".", 1)[0])
    return names


def _core_modules() -> list[Path]:
    root = Path(__file__).parents[1] / "src" / "atoms" / "core"
    return sorted(root.rglob("*.py"))


def _core_package(source_path: Path) -> str:
    source_root = Path(__file__).parents[1] / "src"
    return ".".join(source_path.relative_to(source_root).parent.parts)


def _imports_filesystem_layer(tree: ast.Module, *, package: str) -> bool:
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
    return any(target == "atoms.fs" or target.startswith("atoms.fs.") for target in targets)


@pytest.mark.parametrize(
    ("source", "package"),
    [
        ("from atoms import fs", "atoms.core"),
        ("from .. import fs", "atoms.core"),
    ],
)
def test_filesystem_import_scanner_detects_alias_and_relative_imports(
    source: str,
    package: str,
):
    assert _imports_filesystem_layer(ast.parse(source), package=package)


def test_core_imports_only_the_allowlisted_modules():
    # An allowlist, not a denylist: a denylist only ever forbids what someone
    # thought to enumerate, and fcntl/platform/shutil/tempfile were all omitted.
    assert _core_modules(), "expected to find modules under atoms/core"
    for source_path in _core_modules():
        disallowed = _top_level_imports(source_path) - _CORE_IMPORT_ALLOWLIST
        assert not disallowed, f"{source_path} imports {sorted(disallowed)}"


def test_core_never_imports_the_filesystem_layer():
    for source_path in _core_modules():
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        assert not _imports_filesystem_layer(tree, package=_core_package(source_path)), source_path


def test_select_backend_refuses_a_non_linux_platform(monkeypatch):
    monkeypatch.setattr(fs_platform.sys, "platform", "darwin")
    with pytest.raises(CapabilityUnavailable, match="platform"):
        fs_platform.select_backend()


def test_select_backend_refuses_an_unlisted_architecture(monkeypatch):
    monkeypatch.setattr(fs_platform.sys, "platform", "linux")
    monkeypatch.setattr(fs_platform.platform, "machine", lambda: "riscv64")
    with pytest.raises(CapabilityUnavailable, match="architecture"):
        fs_platform.select_backend()


def test_select_backend_performs_no_io(monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("select_backend must not touch the filesystem")

    monkeypatch.setattr(fs_platform.os, "open", explode, raising=False)
    monkeypatch.setattr(fs_platform.sys, "platform", "linux")
    monkeypatch.setattr(fs_platform.platform, "machine", lambda: "riscv64")
    with pytest.raises(CapabilityUnavailable):
        fs_platform.select_backend()


def test_non_linux_platform_refusal_does_not_load_linux_syscalls(monkeypatch):
    class LibcWithoutSyscall:
        pass

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(ctypes, "CDLL", lambda *args, **kwargs: LibcWithoutSyscall())
    monkeypatch.delitem(sys.modules, "atoms.fs.platform")
    monkeypatch.delitem(sys.modules, "atoms.fs.syscalls.linux")
    monkeypatch.delattr(sys.modules["atoms.fs.syscalls"], "linux", raising=False)

    reloaded_platform = importlib.import_module("atoms.fs.platform")

    with pytest.raises(CapabilityUnavailable, match="platform"):
        reloaded_platform.select_backend()


def test_bind_requires_a_keyword_only_allowlist_with_no_default():
    signature = inspect.signature(atoms.fs.bind_project_volume)
    parameter = signature.parameters["allowlist"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is inspect.Parameter.empty
    storage = signature.parameters["storage"]
    assert storage.kind is inspect.Parameter.KEYWORD_ONLY
    assert storage.default is inspect.Parameter.empty


def test_certified_allowlist_is_empty_so_population_is_deliberate():
    assert CERTIFIED_ALLOWLIST == DurabilityAllowlist(entries=frozenset())


_BIND_PROJECT_VOLUME_TARGETS = {
    "atoms.fs.bind_project_volume",
    "atoms.fs.binding.bind_project_volume",
}


def _source_module(source_root: Path, source_path: Path) -> tuple[str, str]:
    parts = source_path.relative_to(source_root).with_suffix("").parts
    if parts[-1] == "__init__":
        module = ".".join(parts[:-1])
        return module, module
    module = ".".join(parts)
    return module, module.rpartition(".")[0]


def _dotted_name(expression: ast.expr) -> str | None:
    if isinstance(expression, ast.Name):
        return expression.id
    if isinstance(expression, ast.Attribute):
        parent = _dotted_name(expression.value)
        if parent is not None:
            return f"{parent}.{expression.attr}"
    return None


def _import_aliases(tree: ast.Module, *, module: str, package: str) -> dict[str, str]:
    aliases: dict[str, str] = {}
    if module == "atoms.fs.binding":
        aliases["bind_project_volume"] = "atoms.fs.binding.bind_project_volume"
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for imported in node.names:
                local = imported.asname or imported.name.split(".", 1)[0]
                aliases[local] = imported.name if imported.asname else local
            continue
        if not isinstance(node, ast.ImportFrom):
            continue
        imported_from = (
            resolve_name(
                f"{'.' * node.level}{node.module or ''}",
                package,
            )
            if node.level
            else node.module or ""
        )
        for imported in node.names:
            if imported.name == "*":
                if imported_from in {"atoms.fs", "atoms.fs.binding"}:
                    aliases["bind_project_volume"] = (
                        f"{imported_from}.bind_project_volume"
                    )
                continue
            aliases[imported.asname or imported.name] = (
                f"{imported_from}.{imported.name}"
            )
    return aliases


def _calls_bind_project_volume(
    tree: ast.Module,
    *,
    module: str,
    package: str,
) -> bool:
    aliases = _import_aliases(tree, module=module, package=package)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        called = _dotted_name(node.func)
        if called is None:
            continue
        head, separator, tail = called.partition(".")
        resolved_head = aliases.get(head, head)
        resolved = (
            f"{resolved_head}.{tail}"
            if separator
            else resolved_head
        )
        if resolved in _BIND_PROJECT_VOLUME_TARGETS:
            return True
    return False


def _production_bind_callers(source_root: Path) -> set[Path]:
    callers: set[Path] = set()
    for source_path in source_root.rglob("*.py"):
        module, package = _source_module(source_root, source_path)
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        if _calls_bind_project_volume(tree, module=module, package=package):
            callers.add(source_path)
    return callers


@pytest.mark.parametrize(
    ("relative_path", "source"),
    [
        pytest.param(
            "atoms/app/__init__.py",
            "from atoms.fs import bind_project_volume\n"
            "bind_project_volume()\n",
            id="caller-in-package-init",
        ),
        pytest.param(
            "atoms/other/binding.py",
            "from atoms.fs import bind_project_volume\n"
            "bind_project_volume()\n",
            id="caller-in-other-binding-module",
        ),
        pytest.param(
            "atoms/app/imported_alias.py",
            "from atoms.fs.binding import bind_project_volume as bind\n"
            "bind()\n",
            id="imported-alias",
        ),
        pytest.param(
            "atoms/app/module_alias.py",
            "import atoms.fs as fs\n"
            "fs.bind_project_volume()\n",
            id="module-alias",
        ),
    ],
)
def test_bind_caller_scanner_detects_paths_and_aliases(
    tmp_path: Path,
    relative_path: str,
    source: str,
):
    caller = tmp_path / relative_path
    caller.parent.mkdir(parents=True, exist_ok=True)
    caller.write_text(source, encoding="utf-8")

    assert _production_bind_callers(tmp_path) == {caller}


def test_bind_caller_scanner_ignores_only_the_definition_and_reexport(tmp_path: Path):
    package = tmp_path / "atoms" / "fs"
    package.mkdir(parents=True)
    (package / "binding.py").write_text(
        "def bind_project_volume():\n"
        "    pass\n",
        encoding="utf-8",
    )
    (package / "__init__.py").write_text(
        "from .binding import bind_project_volume\n",
        encoding="utf-8",
    )

    assert _production_bind_callers(tmp_path) == set()


@pytest.mark.parametrize(
    ("relative_path", "source"),
    [
        pytest.param(
            "atoms/fs/binding.py",
            "def bind_project_volume():\n"
            "    pass\n"
            "bind_project_volume()\n",
            id="call-in-defining-module",
        ),
        pytest.param(
            "atoms/fs/__init__.py",
            "from .binding import bind_project_volume\n"
            "bind_project_volume()\n",
            id="call-in-reexport-module",
        ),
    ],
)
def test_bind_caller_scanner_does_not_exempt_entire_boundary_modules(
    tmp_path: Path,
    relative_path: str,
    source: str,
):
    caller = tmp_path / relative_path
    caller.parent.mkdir(parents=True, exist_ok=True)
    caller.write_text(source, encoding="utf-8")

    assert _production_bind_callers(tmp_path) == {caller}


def test_no_production_caller_of_bind_exists_yet():
    # A4a has no production composition root, so it cannot assert which allowlist
    # is passed. That call-site assertion is ledger entry #18, owned by A5.
    source_root = Path(__file__).parents[1] / "src"
    assert _production_bind_callers(source_root) == set()


def _status_paragraph(path: Path) -> str:
    lines = path.read_text(encoding="utf-8").splitlines()
    start = next(
        index for index, line in enumerate(lines) if line.startswith("**Status:**")
    )
    paragraph: list[str] = []
    for line in lines[start:]:
        if not line:
            break
        paragraph.append(line)
    return " ".join(paragraph).removeprefix("**Status:** ")


def test_a4a_status_is_synchronized_across_authority_documents():
    root = Path(__file__).parents[2]
    expected = (
        "Implemented on 2026-07-30. A4b and A5–A8 remain unimplemented; "
        "A4a mutates only engine-owned `metadata_root`, never project paths."
    )
    assert _status_paragraph(
        root / "docs" / "plans" / "2026-07-29-a4a-capability-backend-design.md"
    ) == expected
    assert _status_paragraph(
        root / "docs" / "plans" / "2026-07-29-plan-a4a-capability-backend.md"
    ) == expected

    agents = (root / "AGENTS.md").read_text(encoding="utf-8")
    assert (
        "**A4a — capability backend and project volume binding: implemented on "
        "2026-07-30.**"
    ) in agents
    assert "A4b and A5–A8 remain unimplemented" in agents
    assert (
        "A4a mutates only engine-owned `metadata_root`, never project paths."
    ) in agents


def test_verified_child_path_is_not_exported():
    assert not hasattr(atoms.fs, "verified_child_path")


def test_public_surface_is_exactly_the_documented_names():
    assert sorted(atoms.fs.__all__) == [
        "AllowlistEntry",
        "Backend",
        "CERTIFIED_ALLOWLIST",
        "Capability",
        "DurabilityAllowlist",
        "HeldProjectLock",
        "ProjectBinding",
        "StorageProfile",
        "VolumeConfiguration",
        "VolumeEvidence",
        "acquire_project_lock",
        "bind_project_volume",
        "reclaim_probe_survivors",
        "select_backend",
    ]


def test_fs_fixture_registry_covers_every_test_argument():
    tests_root = Path(__file__).parent
    registered = fixture_names(tests_root / "conftest.py")
    assert unregistered_test_arguments(tests_root, registered, "test_fs_*.py") == set()


def test_fs_fixture_registry_subtracts_parametrized_arguments(tmp_path):
    # The A4a suite parametrizes on 'absent', 'operation', 'code', and 'expected'.
    # A scanner that treated every argument as a fixture would reject all four, so
    # this pins the behavior that makes reusing A3's scanner worth the extraction.
    (tmp_path / "test_fs_parametrized.py").write_text(
        "import pytest\n"
        "@pytest.mark.parametrize(('operation', 'code'), [('exchange', 22)])\n"
        "def test_uses_parametrized_values(operation, code):\n"
        "    pass\n"
        "@pytest.mark.parametrize('absent', [1])\n"
        "def test_uses_one_parametrized_value(absent):\n"
        "    pass\n",
        encoding="utf-8",
    )

    assert unregistered_test_arguments(tmp_path, set(), "test_fs_*.py") == set()


def test_fs_fixture_registry_still_catches_a_genuine_omission(tmp_path):
    # The converse, so the test above cannot pass by the scanner finding nothing.
    (tmp_path / "test_fs_missing.py").write_text(
        "def test_uses_unregistered(nonexistent_fixture):\n    pass\n",
        encoding="utf-8",
    )

    assert unregistered_test_arguments(tmp_path, set(), "test_fs_*.py") == {
        "nonexistent_fixture"
    }
