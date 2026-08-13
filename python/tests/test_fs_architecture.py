import ast
import ctypes
import importlib
import inspect
import sys
import typing
from importlib.util import resolve_name
from pathlib import Path

import pytest

import atoms.fs
from atoms.core.errors import CapabilityUnavailable
from atoms.fs import platform as fs_platform
from atoms.fs.volume import CERTIFIED_ALLOWLIST, DurabilityAllowlist
from tests.architecture_support import (
    fixture_names,
    oserror_handler_discriminates,
    unregistered_test_arguments,
)

SOURCE_ROOT = Path(__file__).parents[1] / "src" / "atoms"

_MUTATING_OS = {
    "rename",
    "replace",
    "unlink",
    "mkdir",
    "rmdir",
    "symlink",
    "link",
    "chmod",
    "fchmod",
    "lchmod",
    "setxattr",
    "fsetxattr",
    "write",
    "close",
    "open",
}
_FACADE_EXEMPT = ("fs/audit.py", "fs/linux.py", "fs/syscalls/")
_OS_OPEN_ALLOWED = ("fs/resolve.py",)


def _os_attribute_references(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "os"
        ):
            yield node


def test_no_direct_os_mutation_or_close_outside_the_facade():
    """Design §5.2: the facade is the only mutation and close surface; SQLite's VFS never
    spells os.* in our source, so it needs no carve-out here."""
    offenders = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        rel = str(path.relative_to(SOURCE_ROOT))
        if rel.startswith(_FACADE_EXEMPT):
            continue
        for node in _os_attribute_references(path):
            if node.attr == "open" and rel in _OS_OPEN_ALLOWED:
                continue
            if node.attr in _MUTATING_OS:
                offenders.append(f"{rel}:{node.lineno} os.{node.attr}")
    assert offenders == []


def test_the_raw_backend_is_constructed_only_by_the_platform_factory():
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        rel = str(path.relative_to(SOURCE_ROOT))
        if rel in {"fs/platform.py", "fs/linux.py"}:
            continue
        assert "LinuxBackend(" not in path.read_text(encoding="utf-8"), rel


def test_the_facade_is_constructed_only_at_the_composition_root():
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        rel = str(path.relative_to(SOURCE_ROOT))
        if rel in {"coordinator/root.py", "fs/audit.py"}:
            continue
        assert "AuditedBackend(" not in path.read_text(encoding="utf-8"), rel

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


def _imports_filesystem_layer(tree: ast.Module, *, package: str) -> bool:
    return any(
        target == "atoms.fs" or target.startswith("atoms.fs.")
        for target in _resolved_imports(tree, package=package)
    )


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


def test_chain_never_imports_the_coordinator_or_store() -> None:
    chain_root = SOURCE_ROOT / "chain"
    modules = sorted(chain_root.rglob("*.py"))
    assert modules, "expected to find modules under atoms/chain"
    for source_path in modules:
        imports = _resolved_imports(
            ast.parse(source_path.read_text(encoding="utf-8")),
            package=_core_package(source_path),
        )
        forbidden = {
            name
            for name in imports
            if name in {"atoms.coordinator", "atoms.store"}
            or name.startswith(("atoms.coordinator.", "atoms.store."))
        }
        assert not forbidden, f"{source_path} imports {sorted(forbidden)}"


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

    # Importing the temporary module below replaces this package attribute, while
    # restoring only sys.modules would leave later tests holding the temporary one.
    monkeypatch.setattr(atoms.fs, "platform", fs_platform)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(ctypes, "CDLL", lambda *args, **kwargs: LibcWithoutSyscall())
    monkeypatch.delitem(sys.modules, "atoms.fs.platform")
    monkeypatch.delitem(sys.modules, "atoms.fs.syscalls.linux")
    monkeypatch.delattr(sys.modules["atoms.fs.syscalls"], "linux", raising=False)

    reloaded_platform = importlib.import_module("atoms.fs.platform")

    with pytest.raises(CapabilityUnavailable, match="platform"):
        reloaded_platform.select_backend()


def test_non_linux_reload_restores_the_package_platform_attribute():
    assert vars(atoms.fs)["platform"] is fs_platform


def test_select_backend_is_typed_to_the_backend_protocol():
    assert typing.get_type_hints(fs_platform.select_backend)["return"] is atoms.fs.Backend


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


def test_the_only_production_bind_caller_is_the_coordinator_root():
    source_root = Path(__file__).parents[1] / "src"
    assert _production_bind_callers(source_root) == {
        source_root / "atoms" / "coordinator" / "root.py"
    }


def test_the_production_bind_call_passes_the_certified_allowlist():
    """Ledger #18: the sole production call site names the shipping constant."""
    path = Path(__file__).parents[1] / "src" / "atoms" / "coordinator" / "root.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _called_name(node) == "bind_project_volume"
    ]
    assert len(calls) == 1
    keywords = {keyword.arg: keyword.value for keyword in calls[0].keywords}
    allowlist = keywords["allowlist"]
    assert isinstance(allowlist, ast.Name)
    assert allowlist.id == "CERTIFIED_ALLOWLIST"
    assert "atoms.fs.volume.CERTIFIED_ALLOWLIST" in _resolved_imports(
        tree, package="atoms.coordinator"
    )
    assert CERTIFIED_ALLOWLIST == DurabilityAllowlist(entries=frozenset())


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
    for name in atoms.fs.__all__:
        assert hasattr(atoms.fs, name), name


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


FORBIDDEN_FOR_RESOLUTION = (
    "atoms.core.compiler",
    "atoms.core.spec",
    "atoms.core.recovery",
)


def _imports_forbidden_resolution(tree: ast.Module, *, package: str) -> bool:
    return any(
        name.startswith(forbidden)
        for name in _resolved_imports(tree, package=package)
        for forbidden in FORBIDDEN_FOR_RESOLUTION
    )


@pytest.mark.parametrize(
    "source",
    [
        "from atoms.core import compiler",
        "from ..core import recovery",
    ],
)
def test_resolution_import_scanner_detects_imported_aliases_and_relatives(source):
    assert _imports_forbidden_resolution(ast.parse(source), package="atoms.fs")


@pytest.mark.parametrize("module_name", ["resolve", "lookup"])
def test_resolution_modules_judge_no_specification(module_name):
    """A dependency on any of these would mean the mechanism had begun judging."""
    source = (SOURCE_ROOT / "fs" / f"{module_name}.py").read_text()
    assert not _imports_forbidden_resolution(ast.parse(source), package="atoms.fs")


@pytest.mark.parametrize("name", ["PathResolver", "read_lookup_constraints"])
def test_resolution_internals_are_not_exported(name):
    import atoms.fs as package

    assert name not in package.__all__
    assert not hasattr(package, name)


def test_observe_imports_only_the_recovery_model():
    """Ledger #13's "may not pre-classify" as a mechanical property.

    A blacklist on `snapshot` would be insufficient: `atoms/core/recovery/__init__.py`
    re-exports `classify_recovery` and `authorize_recovery_step`, so a classifier is
    reachable through the package facade.

    The scan reuses `_resolved_imports`, which already resolves plain `import`, aliased
    `from atoms.core import recovery`, and relative forms through `resolve_name`. A
    hand-rolled scanner over `node.module` alone would miss all three: `from atoms.core
    import recovery` names the parent package, and a relative import names nothing that
    starts with `atoms`.
    """
    source = SOURCE_ROOT / "fs" / "observe.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    permitted = "atoms.core.recovery.model"
    offenders = sorted(
        name
        for name in _resolved_imports(tree, package="atoms.fs")
        if name.startswith("atoms.core.recovery")
        and name != permitted
        and not name.startswith(f"{permitted}.")
    )
    assert offenders == []


@pytest.mark.parametrize("module_name", ["resolve", "lookup"])
def test_no_blanket_oserror_handler(module_name):
    """Every OSError handler either discriminates or delegates to the discriminator."""
    source = (SOURCE_ROOT / "fs" / f"{module_name}.py").read_text()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.ExceptHandler) or node.type is None:
            continue
        names = (
            [node.type] if not isinstance(node.type, ast.Tuple) else list(node.type.elts)
        )
        for entry in names:
            if isinstance(entry, ast.Name) and entry.id == "OSError":
                assert oserror_handler_discriminates(node), (
                    f"{module_name}.py catches OSError without discriminating on errno "
                    "or passing the caught object to _frontier_from"
                )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            (
                "try:\n"
                "    pass\n"
                "except OSError as caught:\n"
                "    if caught.errno == 2:\n"
                "        raise\n"
            ),
            True,
        ),
        (
            (
                "try:\n"
                "    pass\n"
                "except OSError as caught:\n"
                "    return self._frontier_from(caught)\n"
            ),
            True,
        ),
        (
            (
                "try:\n"
                "    pass\n"
                "except OSError as caught:\n"
                "    caught.errno\n"
                "    raise\n"
            ),
            False,
        ),
        (
            (
                "try:\n"
                "    pass\n"
                "except OSError as caught:\n"
                "    raise RuntimeError('errno')\n"
            ),
            False,
        ),
    ],
)
def test_oserror_guard_requires_control_flow_or_delegation(source, expected):
    handler = next(
        node for node in ast.walk(ast.parse(source)) if isinstance(node, ast.ExceptHandler)
    )
    assert oserror_handler_discriminates(handler) is expected


def test_the_backend_protocol_and_revision_are_exact():
    from atoms.fs.backend import Backend
    from atoms.fs.platform import BACKEND_REVISION

    assert BACKEND_REVISION == "linux-3"
    assert {
        name
        for name, member in inspect.getmembers(Backend, inspect.isfunction)
        if not name.startswith("__")
    } == {
        "close_fd",
        "create_exclusive",
        "create_or_open",
        "detach_fd",
        "exchange",
        "flush_directory",
        "flush_file",
        "link_anchor",
        "lock_exclusive",
        "mkdir_child",
        "open_child_directory",
        "open_existing",
        "open_regular_nofollow",
        "open_root",
        "repair_entry_mode",
        "rmdir_child",
        "set_marker_xattr",
        "set_mode",
        "symlink_child",
        "symlink_fingerprint",
        "transfer_noclobber",
        "try_lock_exclusive",
        "unlink_child",
        "write",
    }


_CONDITIONAL_EXECUTION = (
    ast.If,
    ast.IfExp,
    ast.BoolOp,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
    ast.comprehension,
    ast.Match,
    ast.Lambda,
)


def _conditional_ancestors(root: ast.AST, target: ast.AST) -> list[ast.AST]:
    parents = {
        child: parent
        for parent in ast.walk(root)
        for child in ast.iter_child_nodes(parent)
    }
    guarded: list[ast.AST] = []
    node = target
    while (parent := parents.get(node)) is not None:
        if isinstance(parent, _CONDITIONAL_EXECUTION):
            guarded.append(parent)
        node = parent
    return guarded


@pytest.mark.parametrize(
    "source",
    [
        (
            "def _facts_for():\n"
            "    return ready and read_lookup_constraints(fd, filesystem)\n"
        ),
        (
            "def _facts_for():\n"
            "    for unused in ():\n"
            "        read_lookup_constraints(fd, filesystem)\n"
        ),
        (
            "def _facts_for():\n"
            "    return [item for item in () if "
            "read_lookup_constraints(fd, filesystem)]\n"
        ),
    ],
)
def test_constraint_read_guard_catches_missed_conditional_mutations(source):
    tree = ast.parse(source)
    facts_for = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_facts_for"
    )
    read = next(
        node
        for node in ast.walk(facts_for)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "read_lookup_constraints"
    )
    assert _conditional_ancestors(facts_for, read)


def test_the_memo_never_shortcuts_the_constraints_read():
    """A structural guard on §6.5's rule.

    The behavioural tests in the walk suite can only fail once someone reintroduces
    the shortcut; this one names the shape, so the reason survives a refactor. The
    memo lookup must not gate the read_lookup_constraints call.
    """
    source = (SOURCE_ROOT / "fs" / "resolve.py").read_text()
    tree = ast.parse(source)
    facts_for = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_facts_for"
    )
    reads = [
        node
        for node in ast.walk(facts_for)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "read_lookup_constraints"
    ]
    assert len(reads) == 1
    guarded = _conditional_ancestors(facts_for, reads[0])
    assert guarded == [], "read_lookup_constraints must not sit behind a memo branch"


APPROVAL_MODULES = ("approval", "judgment", "topology")
PURE_MODULES = ("judgment", "topology")
PURE_IMPORT_PREFIXES = ("__future__", "atoms.core", "collections", "dataclasses")
PURE_FS_IMPORTS = {
    "judgment": frozenset(
        {
            "atoms.fs.lookup",
            "atoms.fs.lookup.DirectoryConstraints",
            "atoms.fs.lookup.lookup_equivalence_key",
            "atoms.fs.resolve",
            "atoms.fs.resolve.EntryKind",
            "atoms.fs.resolve.PresentFrontier",
            "atoms.fs.resolve.ResolvedPrefix",
            "atoms.fs.topology",
            "atoms.fs.topology.ApprovedScratch",
            "atoms.fs.topology.ResolvedTopology",
        }
    ),
    "topology": frozenset(
        {
            "atoms.fs.lookup",
            "atoms.fs.lookup.DirectoryConstraints",
            "atoms.fs.lookup.inherited_constraints",
            "atoms.fs.lookup.lookup_equivalence_key",
            "atoms.fs.resolve",
            "atoms.fs.resolve.DirectoryFacts",
            "atoms.fs.resolve.EntryKind",
            "atoms.fs.resolve.FilesystemIdentity",
            "atoms.fs.resolve.Frontier",
            "atoms.fs.resolve.PresentFrontier",
            "atoms.fs.resolve.ResolvedPrefix",
        }
    ),
}


def test_the_resolution_guard_still_names_exactly_the_two_mechanism_modules():
    """The seam is the guard's module list. If A4b-2's modules were added to it they
    could not import a CompiledSpec; if resolve.py were removed from it, the mechanism
    could start judging."""
    source = (Path(__file__)).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name != "test_resolution_modules_judge_no_specification":
            continue
        marks = [
            decorator
            for decorator in node.decorator_list
            if isinstance(decorator, ast.Call)
        ]
        names = ast.literal_eval(marks[0].args[1])
        assert names == ["resolve", "lookup"]
        return
    raise AssertionError("the resolution guard is missing")


@pytest.mark.parametrize("module_name", APPROVAL_MODULES)
def test_approval_modules_may_judge_a_specification(module_name):
    """The complement of the resolution guard: these modules exist to see a spec."""
    source = (SOURCE_ROOT / "fs" / f"{module_name}.py").read_text(encoding="utf-8")
    assert ast.parse(source) is not None


def _forbidden_pure_imports(module_name, tree):
    imported = _resolved_imports(tree, package="atoms.fs")
    forbidden = {
        name
        for name in imported
        if not any(
            name == prefix or name.startswith(f"{prefix}.")
            for prefix in PURE_IMPORT_PREFIXES
        )
        and name not in PURE_FS_IMPORTS[module_name]
    }
    forbidden.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
        if alias.name == "atoms.fs" or alias.name.startswith("atoms.fs.")
    )
    if any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "open"
        for node in ast.walk(tree)
    ):
        forbidden.add("builtins.open")
    return forbidden


@pytest.mark.parametrize(
    "source",
    [
        "import os",
        "import atoms.fs.lookup as lookup",
        "import atoms.fs.resolve as resolve",
        "from atoms.fs.resolve import PathResolver",
        "from atoms.fs.syscalls import linux",
        "import atoms.corex",
        'open("path")',
    ],
)
def test_the_pure_import_guard_detects_direct_and_indirect_io(source):
    """The converse keeps the allowlist from becoming another vacuous denylist."""
    assert _forbidden_pure_imports("topology", ast.parse(source))


@pytest.mark.parametrize("module_name", PURE_MODULES)
def test_the_pure_modules_import_only_pure_dependencies(module_name):
    source = (SOURCE_ROOT / "fs" / f"{module_name}.py").read_text(encoding="utf-8")
    forbidden = _forbidden_pure_imports(module_name, ast.parse(source))
    assert not forbidden, f"{module_name}.py imports I/O authority: {sorted(forbidden)}"


def test_the_approved_proof_retains_only_the_authorized_schema():
    """Criterion 21 is a negative schema contract, not an identity assertion.

    Exact field sets make a copied frontier or mount identifier fail even if it is added
    with a default. The live binding deliberately retains its own VolumeEvidence; this
    guard covers the fields A4b-2 copies into its proof and approved fact values.
    """
    from atoms.fs.approval import ProjectApprovedSpec
    from atoms.fs.topology import (
        ApprovedExistingDirectory,
        ApprovedPath,
        ApprovedPlannedDirectory,
        ApprovedScratch,
        ApprovedWorkBase,
    )

    expected = {
        ProjectApprovedSpec: frozenset(
            {
                "compiled",
                "binding",
                "txid",
                "topology",
                "directories",
                "paths",
                "scratch",
                "work_base",
            }
        ),
        ApprovedExistingDirectory: frozenset({"node", "identity", "constraints"}),
        ApprovedPlannedDirectory: frozenset({"node", "constraints"}),
        ApprovedPath: frozenset({"path", "parent_node", "leaf"}),
        ApprovedScratch: frozenset({"effect_id", "role", "parent_node", "leaf"}),
        ApprovedWorkBase: frozenset({"identity", "constraints"}),
    }
    assert {
        value_type: frozenset(typing.get_type_hints(value_type))
        for value_type in expected
    } == expected


RAW_COMPONENT_ATTRS = frozenset({"leaf", "declared_component"})
LAUNDERING_CALLS = frozenset({"lookup_equivalence_key", "len"})


def _called_name(call):
    func = call.func
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")


def _mentions_raw(node):
    """True if this expression still carries a raw path component.

    A subtree rooted at a laundering call does not: lookup_equivalence_key is the whole
    point of the rule, and len() yields a byte width, which no equality decision about a
    name can be made from.
    """
    if isinstance(node, ast.Call) and _called_name(node) in LAUNDERING_CALLS:
        return False
    if isinstance(node, ast.Attribute) and node.attr in RAW_COMPONENT_ATTRS:
        return True
    return any(_mentions_raw(child) for child in ast.iter_child_nodes(node))


def _aliases_of_raw(tree):
    """Locals bound to a raw component, so `leaf = entry.leaf` does not launder it.

    Only plain-name targets are tainted. `seen[key] = entry.leaf` stores a raw component
    in a container, which is not aliasing — tainting `seen` there would reject the
    pipeline's own idiom.
    """
    tainted = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        if node.value is None or not _mentions_raw(node.value):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if isinstance(target, ast.Name):
                tainted.add(target.id)
            elif isinstance(target, ast.Tuple):
                tainted.update(
                    element.id
                    for element in target.elts
                    if isinstance(element, ast.Name)
                )
    return tainted


@pytest.mark.parametrize("module_name", PURE_MODULES)
def test_the_pure_modules_never_decide_equality_on_a_raw_component(module_name):
    """What the injected_equivalence double cannot catch: under an identity key a direct
    comparison behaves exactly like the function it bypasses, so every EXACT_BYTES case
    still passes and the double reports nothing.

    Guarded shapes are comparisons — which covers `==`, `!=`, `in`, and `not in` — and
    mapping keys, over both a raw attribute and any local aliased from one. A value
    produced BY lookup_equivalence_key launders the taint, which is what makes the
    pipeline's `key = (parent, lookup_equivalence_key(...))` idiom legal.

    **This is a lint over the shapes it names, not a soundness proof.** Alias discovery is
    one hop, so `a = entry.leaf; b = a; b == x` slips through, as do a component recovered
    via `split`, a key built from a length, and a dict-literal key. Making it sound needs
    real dataflow analysis, which is not worth building here. The positive check is what
    carries the weight: injected_equivalence records the names it is asked about, so a
    call site that bypasses the helper contributes nothing to that list however it is
    written. This guard catches the direct shape cheaply; the recording catches omission.
    """
    tree = ast.parse(
        (SOURCE_ROOT / "fs" / f"{module_name}.py").read_text(encoding="utf-8")
    )
    tainted = _aliases_of_raw(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            checked = [node.left, *node.comparators]
        elif isinstance(node, ast.Subscript):
            checked = [node.slice]
        else:
            continue
        for expression in checked:
            for inner in ast.walk(expression):
                if isinstance(inner, ast.Attribute):
                    assert inner.attr not in RAW_COMPONENT_ATTRS, (
                        f"{module_name}.py decides {inner.attr} equality directly; "
                        "route it through lookup_equivalence_key"
                    )
                if isinstance(inner, ast.Name):
                    assert inner.id not in tainted, (
                        f"{module_name}.py decides equality on {inner.id}, aliased from "
                        "a raw path component; route it through lookup_equivalence_key"
                    )


def test_approval_catches_nothing_a_resolver_raises():
    """Ledger #20 is categorical. The correct number of handlers enclosing a resolver
    call is zero — not "no blanket handler", which is the weaker rule resolve.py has.
    `contextlib.suppress` is checked too: it swallows exactly as a handler does, and a
    guard that missed it would be satisfied by the one bypass someone would reach for."""
    tree = ast.parse((SOURCE_ROOT / "fs" / "approval.py").read_text(encoding="utf-8"))
    calls = {"resolve", "work_base_facts", "PathResolver"}
    enclosing = [node for node in ast.walk(tree) if isinstance(node, ast.Try)]
    enclosing += [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.With)
        and any(
            isinstance(item.context_expr, ast.Call)
            and _called_name(item.context_expr) == "suppress"
            for item in node.items
        )
    ]
    for node in enclosing:
        for inner in ast.walk(node):
            if isinstance(inner, ast.Call):
                assert _called_name(inner) not in calls, (
                    f"a handler in approval.py encloses {_called_name(inner)}(); "
                    "ledger #20 requires every A4b-1 refusal to reach the caller "
                    "unhandled"
                )


def test_the_approved_spec_is_not_exported():
    import atoms.fs as package

    assert "ProjectApprovedSpec" not in package.__all__
    assert not hasattr(package, "ProjectApprovedSpec")


_TRANSACTION_STAGE_ENTRY_POINTS = {
    "atoms/coordinator/capture.py": ("capture_initial_surface",),
    "atoms/coordinator/prepare.py": ("open_workspace", "prepare_transaction"),
    "atoms/coordinator/transitions.py": ("persist_plan_prefix",),
}


def _first_statement(function: ast.FunctionDef) -> ast.stmt:
    body = function.body
    if (
        isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        return body[1]
    return body[0]


_DEFINING_MODULE = SOURCE_ROOT / "fs" / "approval.py"


def test_only_the_coordinator_consumes_the_approved_spec():
    consumers = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        if path == _DEFINING_MODULE:
            continue
        if path.relative_to(SOURCE_ROOT).parts[0] == "coordinator":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        _, package = _source_module(SOURCE_ROOT.parent, path)
        if "atoms.fs.approval.ProjectApprovedSpec" in _resolved_imports(
            tree, package=package
        ) or any(
            (isinstance(node, ast.Name) and node.id == "ProjectApprovedSpec")
            or (isinstance(node, ast.Attribute) and node.attr == "ProjectApprovedSpec")
            for node in ast.walk(tree)
        ):
            consumers.append(str(path.relative_to(SOURCE_ROOT)))
    assert consumers == []


def test_every_transaction_stage_entry_point_opens_with_the_proof_gate():
    for relative, names in _TRANSACTION_STAGE_ENTRY_POINTS.items():
        tree = ast.parse(
            (SOURCE_ROOT.parent / relative).read_text(encoding="utf-8")
        )
        functions = {
            node.name: node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
        }
        assert set(names) <= set(functions), relative
        for name in names:
            first = _first_statement(functions[name])
            assert isinstance(first, ast.Expr), f"{relative}::{name}"
            assert isinstance(first.value, ast.Call), f"{relative}::{name}"
            assert _called_name(first.value) == "_require_admitted", (
                f"{relative}::{name}"
            )


def test_no_unregistered_public_function_accepts_the_proof():
    registered = {
        f"{relative}::{name}"
        for relative, names in _TRANSACTION_STAGE_ENTRY_POINTS.items()
        for name in names
    }
    found = set()
    for path in sorted((SOURCE_ROOT / "coordinator").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        relative = str(path.relative_to(SOURCE_ROOT.parent))
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef) or node.name.startswith("_"):
                continue
            if any(
                isinstance(argument.annotation, ast.Name)
                and argument.annotation.id == "ProjectApprovedSpec"
                for argument in node.args.args
            ):
                found.add(f"{relative}::{node.name}")
    assert found == registered


def test_ledger_entry_nine_stays_open_against_the_stages_that_owe_it():
    """#9's enforcement is A5-A8's, and AGENTS.md must keep naming the two halves.

    Status wording lives in `test_docs_status.py`; what this asserts is the ledger's own
    shape -- entry #9 open, owned by A5, scoped to A5-A8 -- which no status guard covers.
    """
    root = Path(__file__).parents[2]
    agents = (root / "AGENTS.md").read_text(encoding="utf-8")
    assert "It admits #21, the txid binding, owned by A5." in agents
    assert "factory half of #9" in agents

    ledger = (root / "docs" / "deferred-obligation-ledger.md").read_text(
        encoding="utf-8"
    )
    open_obligations, discharged = ledger.split("## Discharged obligations", 1)
    row = next(
        line for line in open_obligations.splitlines() if line.startswith("| 9 |")
    )
    assert "| A5 |" in row
    assert "A5–A8" in row
    assert not any(line.startswith("| 9 |") for line in discharged.splitlines())
