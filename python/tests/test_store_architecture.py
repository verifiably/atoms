"""Tier 6 -- properties of the package as a whole (design §11.6)."""

from __future__ import annotations

import ast
import re
from importlib.util import resolve_name
from pathlib import Path

import pytest

import atoms.store
from tests.architecture_support import (
    decorator_name,
    fixture_names,
    handler_nodes,
    oserror_handler_discriminates,
    parametrize_names,
    unregistered_test_arguments,
)

PACKAGE = Path(atoms.store.__file__).parent
SOURCES = sorted(PACKAGE.glob("*.py"))
TESTS = Path(__file__).parent


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _forbidden_call_aliases(
    tree: ast.Module, *, module: str | None, attributes: set[str]
) -> tuple[set[str], set[str]]:
    modules: set[str] = set()
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and module is not None:
            modules.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name == module
            )
        elif isinstance(node, ast.ImportFrom) and node.module == module:
            aliases.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name in attributes
            )
    pending = _assigned_expressions(tree)
    while pending:
        resolved: set[str] = set()
        for name, value in pending:
            if isinstance(value, ast.Name) and value.id in modules:
                modules.add(name)
                resolved.add(name)
            elif (
                isinstance(value, ast.Name)
                and value.id in aliases
                or (
                    isinstance(value, ast.Attribute)
                    and value.attr in attributes
                    and (
                        module is None
                        or isinstance(value.value, ast.Name)
                        and value.value.id in modules
                    )
                )
            ):
                aliases.add(name)
                resolved.add(name)
        if not resolved:
            break
        pending = [(name, value) for name, value in pending if name not in resolved]
    return modules, aliases


def _calls_forbidden(
    tree: ast.Module, *, module: str | None, attributes: set[str]
) -> bool:
    modules, aliases = _forbidden_call_aliases(
        tree, module=module, attributes=attributes
    )
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id in aliases:
            return True
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr in attributes
            and (
                module is None
                or isinstance(node.func.value, ast.Name)
                and node.func.value.id in modules
            )
        ):
            return True
    return False


def _package_for(path: Path) -> str:
    parts = path.with_suffix("").parts
    atoms_index = len(parts) - 1 - parts[::-1].index("atoms")
    module = parts[atoms_index:]
    return ".".join(module[:-1])


def _imported_modules(tree: ast.Module, *, package: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imported_from = (
                resolve_name(f"{'.' * node.level}{module}", package)
                if node.level
                else module
            )
            names.add(imported_from)
            names.update(
                f"{imported_from}.{alias.name}"
                for alias in node.names
                if alias.name != "*"
            )
    return names


@pytest.mark.parametrize(
    "path",
    sorted((PACKAGE.parent / "fs").glob("*.py"))
    + sorted((PACKAGE.parent / "core").rglob("*.py")),
    ids=lambda path: path.name,
)
def test_neither_fs_nor_core_imports_the_store(path):
    assert not any(
        module.startswith("atoms.store")
        for module in _imported_modules(_tree(path), package=_package_for(path))
    ), f"{path.name} imports atoms.store"


def test_the_public_surface_is_exactly_the_documented_names():
    assert atoms.store.__all__ == (
        "StagedBlob",
        "Store",
        "StoredRecord",
        "Workspace",
        "open_store",
    )
    exported = {
        name
        for name in dir(atoms.store)
        if not name.startswith("_")
        and name
        not in {
            # Python 3.13 binds the compiler's __future__ feature object here; it is
            # interpreter-created, not an A5a export. __all__ remains authoritative.
            "annotations",
            "blobs",
            "connection",
            "errors",
            "records",
            "schema",
            "workspace",
        }
    }
    assert exported == set(atoms.store.__all__)


def test_the_transaction_type_is_not_exported():
    assert "StoreTransaction" not in dir(atoms.store)
    assert "_StoreTransaction" not in atoms.store.__all__


def test_the_store_exports_no_sqlite_connection():
    import sqlite3

    for name in atoms.store.__all__:
        assert getattr(atoms.store, name) is not sqlite3.Connection


def _function_definition(path: Path, name: str) -> ast.FunctionDef:
    return next(
        node
        for node in ast.walk(_tree(path))
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def _attribute_call_count(function: ast.FunctionDef, attribute: str) -> int:
    return sum(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == attribute
        for node in ast.walk(function)
    )


def test_fixed_engine_directory_walks_use_the_guarded_backend_helper():
    workspace_parent = _function_definition(PACKAGE / "workspace.py", "_parent_fd")
    blobs_parent = _function_definition(PACKAGE / "blobs.py", "_blobs_fd")
    promotion = _function_definition(PACKAGE / "blobs.py", "promote_staging")

    assert _attribute_call_count(workspace_parent, "open_child_directory") == 1
    assert _attribute_call_count(blobs_parent, "open_child_directory") == 2
    assert _attribute_call_count(promotion, "open_child_directory") == 1
    for function in (workspace_parent, blobs_parent, promotion):
        assert not any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "os"
            and node.func.attr == "open"
            for node in ast.walk(function)
        )


@pytest.mark.parametrize("path", SOURCES, ids=lambda path: path.name)
def test_no_raw_fsync_appears_in_the_package(path):
    assert not _calls_forbidden(
        _tree(path), module="os", attributes={"fsync", "fdatasync"}
    ), f"{path.name} calls os.fsync or os.fdatasync directly or through an alias"


@pytest.mark.parametrize("path", SOURCES, ids=lambda path: path.name)
def test_no_blanket_oserror_handler(path):
    for node in ast.walk(_tree(path)):
        if not isinstance(node, ast.ExceptHandler):
            continue
        caught = node.type
        if caught is None:
            pytest.fail(f"{path.name} has a bare except")
        names = [caught] if not isinstance(caught, ast.Tuple) else list(caught.elts)
        for name in names:
            label = name.id if isinstance(name, ast.Name) else getattr(name, "attr", "")
            if label == "OSError":
                assert oserror_handler_discriminates(node) and any(
                    isinstance(inner, ast.Raise) and inner.exc is None
                    for inner in handler_nodes(node)
                ), (
                    f"{path.name} catches OSError without discriminating on errno"
                )


SWALLOW_EXEMPTION = "connection._rollback_quietly"


def _assigned_expressions(tree: ast.Module) -> list[tuple[str, ast.expr]]:
    assigned: list[tuple[str, ast.expr]] = []

    def pair(target: ast.expr, value: ast.expr) -> None:
        if isinstance(target, ast.Name):
            assigned.append((target.id, value))
        elif (
            isinstance(target, ast.Tuple | ast.List)
            and isinstance(value, ast.Tuple | ast.List)
            and len(target.elts) == len(value.elts)
        ):
            for nested_target, nested_value in zip(target.elts, value.elts, strict=True):
                pair(nested_target, nested_value)

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                pair(target, node.value)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            pair(node.target, node.value)
    return assigned


def _sqlite_exception_aliases(
    tree: ast.Module,
) -> tuple[set[str], set[str], set[str]]:
    modules: set[str] = set()
    database_errors: set[str] = set()
    errors: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name == "sqlite3"
            )
        elif isinstance(node, ast.ImportFrom) and node.module == "sqlite3":
            database_errors.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name == "DatabaseError"
            )
            errors.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name == "Error"
            )
    pending = _assigned_expressions(tree)
    while pending:
        resolved: set[str] = set()
        for name, value in pending:
            if isinstance(value, ast.Name) and value.id in modules:
                modules.add(name)
                resolved.add(name)
            elif _catches_sqlite_exception(
                value, modules, database_errors, "DatabaseError"
            ):
                database_errors.add(name)
                resolved.add(name)
            elif _catches_sqlite_exception(value, modules, errors, "Error"):
                errors.add(name)
                resolved.add(name)
        if not resolved:
            break
        pending = [(name, value) for name, value in pending if name not in resolved]
    return modules, database_errors, errors


def _catches_sqlite_exception(
    caught: ast.expr | None,
    modules: set[str],
    names: set[str],
    attribute: str,
) -> bool:
    if isinstance(caught, ast.Tuple):
        return any(
            _catches_sqlite_exception(entry, modules, names, attribute)
            for entry in caught.elts
        )
    if isinstance(caught, ast.Name):
        return caught.id in names
    return (
        isinstance(caught, ast.Attribute)
        and caught.attr == attribute
        and isinstance(caught.value, ast.Name)
        and caught.value.id in modules
    )


def _handler_owners(tree: ast.Module) -> dict[ast.ExceptHandler, str | None]:
    owners: dict[ast.ExceptHandler, str | None] = {}

    def descend(node: ast.AST, scope: tuple[str, ...]) -> None:
        for child in ast.iter_child_nodes(node):
            child_scope = scope
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                child_scope = (*scope, child.name)
            if isinstance(child, ast.ExceptHandler):
                owners[child] = ".".join(scope) or None
            descend(child, child_scope)

    descend(tree, ())
    return owners


def _handler_always_reraises(handler: ast.ExceptHandler) -> bool:
    return (
        bool(handler.body)
        and isinstance(handler.body[-1], ast.Raise)
        and handler.body[-1].exc is None
        and not any(
            isinstance(node, ast.Return | ast.Break | ast.Continue)
            for node in handler_nodes(handler)
        )
    )


@pytest.mark.parametrize("path", SOURCES, ids=lambda path: path.name)
def test_every_database_error_handler_contains_a_bare_raise(path):
    tree = _tree(path)
    modules, database_errors, _errors = _sqlite_exception_aliases(tree)
    owners = _handler_owners(tree)
    for handler in ast.walk(tree):
        if not isinstance(handler, ast.ExceptHandler) or not _catches_sqlite_exception(
            handler.type, modules, database_errors, "DatabaseError"
        ):
            continue
        owner = owners[handler]
        qualified = f"{path.stem}.{owner or '<module>'}"
        if qualified == SWALLOW_EXEMPTION:
            continue
        assert _handler_always_reraises(handler), (
            f"{path.name}::{owner or '<module>'}'s DatabaseError handler can exit "
            "without a bare raise"
        )


@pytest.mark.parametrize("path", SOURCES, ids=lambda path: path.name)
def test_executescript_is_never_called(path):
    assert not _calls_forbidden(
        _tree(path), module=None, attributes={"executescript"}
    ), f"{path.name} calls executescript directly or through an alias"


def _module_bindings(tree: ast.Module) -> list[tuple[str, ast.expr]]:
    bindings: list[tuple[str, ast.expr]] = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            bindings.extend(
                (target.id, node.value)
                for target in node.targets
                if isinstance(target, ast.Name)
            )
        elif (
            isinstance(node, ast.AnnAssign)
            and node.value is not None
            and isinstance(node.target, ast.Name)
        ):
            bindings.append((node.target.id, node.value))
    return bindings


def _module_binding_counts(tree: ast.Module) -> dict[str, int]:
    counts: dict[str, int] = {}

    def bind(name: str) -> None:
        counts[name] = counts.get(name, 0) + 1

    def descend(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                bind(child.name)
                continue
            if isinstance(child, ast.Lambda):
                continue
            if isinstance(child, ast.alias):
                bind((child.asname or child.name).split(".")[0])
                continue
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
                bind(child.id)
            elif isinstance(child, ast.ExceptHandler) and child.name:
                bind(child.name)
            descend(child)

    descend(tree)
    return counts


def _literal_value(node: ast.expr) -> object:
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError, TypeError):
        pass
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "int"
        and node.func.attr == "from_bytes"
        and len(node.args) == 2
        and not node.keywords
    ):
        raw = ast.literal_eval(node.args[0])
        byteorder = ast.literal_eval(node.args[1])
        if isinstance(raw, bytes) and byteorder in ("big", "little"):
            return int.from_bytes(raw, byteorder)
    raise ValueError("not a permitted static value")


def _string_value(node: ast.expr, values: dict[str, object] | None = None) -> str | None:
    try:
        value = _literal_value(node)
    except (ValueError, SyntaxError, TypeError):
        value = None
    if isinstance(value, str):
        return value
    if not isinstance(node, ast.JoinedStr) or values is None:
        return None
    parts: list[str] = []
    for part in node.values:
        if isinstance(part, ast.Constant) and isinstance(part.value, str):
            parts.append(part.value)
        elif (
            isinstance(part, ast.FormattedValue)
            and isinstance(part.value, ast.Name)
            and part.value.id in values
            and part.conversion == -1
            and part.format_spec is None
        ):
            parts.append(str(values[part.value.id]))
        elif (
            isinstance(part, ast.FormattedValue)
            and isinstance(part.value, ast.Call)
            and isinstance(part.value.func, ast.Name)
            and part.value.func.id == "check_list"
            and len(part.value.args) == 1
            and isinstance(part.value.args[0], ast.Name)
            and part.value.args[0].id
            in {
                "CommitDecision",
                "EffectVariant",
                "JournalState",
                "RollbackResult",
                "TransactionState",
            }
            and not part.value.keywords
            and part.conversion == -1
            and part.format_spec is None
        ):
            parts.append("'<statically-enumerated>'")
        elif (
            isinstance(part, ast.FormattedValue)
            and isinstance(part.value, ast.Attribute)
            and part.value.attr == "value"
            and isinstance(part.value.value, ast.Attribute)
            and isinstance(part.value.value.value, ast.Name)
            and part.value.value.value.id in {"JournalState", "TransactionState"}
            and part.conversion == -1
            and part.format_spec is None
        ):
            parts.append("<enum-derived>")
        else:
            return None
    return "".join(parts)


def _static_values(path: Path, seen: frozenset[Path] = frozenset()) -> dict[str, object]:
    if path in seen:
        return {}
    tree = _tree(path)
    counts = _module_binding_counts(tree)
    values: dict[str, object] = {}
    for name, node in _module_bindings(tree):
        if counts[name] != 1:
            continue
        try:
            values[name] = _literal_value(node)
        except (ValueError, SyntaxError, TypeError):
            continue
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom):
            continue
        module = node.module
        if module is None or not module.startswith("atoms.store."):
            continue
        source = PACKAGE / f"{module.rsplit('.', 1)[-1]}.py"
        if not source.exists():
            continue
        imported = _static_values(source, seen | {path})
        values.update(
            {
                alias.asname or alias.name: imported[alias.name]
                for alias in node.names
                if alias.name in imported and counts[alias.asname or alias.name] == 1
            }
        )
    pending = {
        name: node
        for name, node in _module_bindings(tree)
        if counts[name] == 1
    }
    while pending:
        resolved = {
            name: text
            for name, node in pending.items()
            if (text := _string_value(node, values)) is not None
        }
        if not resolved:
            break
        values.update(resolved)
        pending = {name: node for name, node in pending.items() if name not in resolved}
    return values


def _nested_strings(
    node: ast.expr, label: str, values: dict[str, object]
) -> list[tuple[str, str]]:
    text = _string_value(node, values)
    if text is not None:
        return [(label, text)]
    if not isinstance(node, ast.Tuple | ast.List | ast.Set):
        return []
    found: list[tuple[str, str]] = []
    for index, element in enumerate(node.elts):
        found.extend(_nested_strings(element, f"{label}[{index}]", values))
    return found


def _sql_constants() -> dict[str, str]:
    constants: dict[str, str] = {}
    for path in SOURCES:
        values = _static_values(path)
        for name, value in _module_bindings(_tree(path)):
            constants.update(_nested_strings(value, f"{path.stem}.{name}", values))
    return constants


def _package_names(path: Path) -> set[str]:
    return {
        name for name, value in _static_values(path).items() if isinstance(value, str)
    }


def _literal_structure_names(
    path: Path, seen: frozenset[Path] = frozenset()
) -> set[str]:
    if path in seen:
        return set()
    tree = _tree(path)
    counts = _module_binding_counts(tree)
    values = _static_values(path)

    bindings = {
        name: value
        for name, value in _module_bindings(tree)
        if counts[name] == 1
    }

    def is_immutable_static(node: ast.expr) -> bool:
        if isinstance(node, ast.Tuple):
            return all(is_immutable_static(element) for element in node.elts)
        if isinstance(node, ast.Starred):
            # A concatenation of frozen literal tuples — schema v3 is exactly
            # the frozen v2 statements plus the lifecycle statements — is as
            # static as its parts, provided each part is itself a once-bound
            # module-level literal structure of this module.
            inner = node.value
            return (
                isinstance(inner, ast.Name)
                and isinstance(bindings.get(inner.id), ast.Tuple)
                and is_immutable_static(bindings[inner.id])
            )
        if _string_value(node, values) is not None:
            return True
        try:
            value = _literal_value(node)
        except (ValueError, SyntaxError, TypeError):
            return False
        return value is None or isinstance(value, str | bytes | int | float | complex)

    names = {
        name
        for name, value in _module_bindings(tree)
        if counts[name] == 1
        and isinstance(value, ast.Tuple)
        and is_immutable_static(value)
    }
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom):
            continue
        module = node.module
        if module is None or not module.startswith("atoms.store."):
            continue
        source = PACKAGE / f"{module.rsplit('.', 1)[-1]}.py"
        if not source.exists():
            continue
        imported = _literal_structure_names(source, seen | {path})
        names.update(
            alias.asname or alias.name
            for alias in node.names
            if alias.name in imported and counts[alias.asname or alias.name] == 1
        )
    return names


FunctionScope = ast.FunctionDef | ast.AsyncFunctionDef


def _scope_of(tree: ast.Module) -> dict[ast.AST, FunctionScope | None]:
    owner: dict[ast.AST, FunctionScope | None] = {}

    def descend(node: ast.AST, current: FunctionScope | None) -> None:
        for child in ast.iter_child_nodes(node):
            owner[child] = current
            descend(
                child,
                child
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef)
                else current,
            )

    descend(tree, None)
    return owner


PERMITTED_SQL_LOOPS: dict[str, tuple[int, ...] | None] = {
    "SCHEMA_STATEMENTS": None,
    "_CONNECTION_PRAGMAS": (1, 2),
}


def _module_level_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names.update(
                target.id for target in node.targets if isinstance(target, ast.Name)
            )
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.Import | ast.ImportFrom):
            names.update((alias.asname or alias.name).split(".")[0] for alias in node.names)
    return names


def _names_bound_in(scope: FunctionScope) -> set[str]:
    arguments = scope.args
    names = {
        argument.arg
        for group in (arguments.posonlyargs, arguments.args, arguments.kwonlyargs)
        for argument in group
    }
    names.update(
        argument.arg
        for argument in (arguments.vararg, arguments.kwarg)
        if argument is not None
    )
    for node in ast.walk(scope):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.alias):
            names.add((node.asname or node.name).split(".")[0])
    return names


def _literal_iterated_names(
    tree: ast.Module,
    owner: dict[ast.AST, FunctionScope | None],
    scope: FunctionScope,
    literal_structures: set[str],
) -> set[str]:
    shadowed = _names_bound_in(scope)
    bound: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.For) or not isinstance(node.iter, ast.Name):
            continue
        if owner.get(node) is not scope:
            continue
        if node.iter.id not in PERMITTED_SQL_LOOPS:
            continue
        if node.iter.id not in literal_structures or node.iter.id in shadowed:
            continue
        positions = PERMITTED_SQL_LOOPS[node.iter.id]
        if positions is None:
            if isinstance(node.target, ast.Name):
                bound.add(node.target.id)
            continue
        if not isinstance(node.target, ast.Tuple):
            continue
        for index in positions:
            element = node.target.elts[index]
            if isinstance(element, ast.Name):
                bound.add(element.id)
    return bound


def test_every_permitted_sql_loop_iterable_exists():
    declared = set(PERMITTED_SQL_LOOPS)
    bound = {
        name
        for path in SOURCES
        for name in _literal_structure_names(path)
    }
    assert declared <= bound, sorted(declared - bound)


SHADOWED_ITERABLE = """
def hostile(connection, SCHEMA_STATEMENTS):
    for statement in SCHEMA_STATEMENTS:
        connection.execute(statement)
"""


def test_a_shadowed_permitted_iterable_grants_nothing():
    tree = ast.parse(SHADOWED_ITERABLE)
    owner = _scope_of(tree)
    scope = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef))
    assert _literal_iterated_names(tree, owner, scope, _module_level_names(tree)) == set()
    assert _literal_iterated_names(tree, owner, scope, {"SCHEMA_STATEMENTS"}) == set()


def _parameters_fed_only_constants(
    tree: ast.Module, allowed: set[str]
) -> dict[str, set[str]]:
    by_name: dict[str, list[FunctionScope]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            by_name.setdefault(node.name, []).append(node)
    calls: dict[str, list[ast.Call]] = {}
    ambiguous: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = (
            node.func.id
            if isinstance(node.func, ast.Name)
            else node.func.attr
            if isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "self"
            else None
        )
        if name in by_name:
            calls.setdefault(name, []).append(node)
        elif isinstance(node.func, ast.Attribute) and node.func.attr in by_name:
            ambiguous.add(node.func.attr)
    resolved: dict[str, set[str]] = {}
    for name, definitions in by_name.items():
        if len(definitions) != 1 or name in ambiguous:
            resolved[name] = set()
            continue
        sites = calls.get(name, [])
        for function in definitions:
            positional = [argument.arg for argument in function.args.args]
            offset = 1 if positional and positional[0] == "self" else 0
            for index, parameter in enumerate(positional[offset:]):
                values: list[ast.expr] = []
                for call in sites:
                    if index < len(call.args):
                        values.append(call.args[index])
                    else:
                        supplied = [
                            keyword.value
                            for keyword in call.keywords
                            if keyword.arg == parameter
                        ]
                        if len(supplied) == 1:
                            values.extend(supplied)
                if len(values) == len(sites) and values and all(
                    isinstance(value, ast.Name) and value.id in allowed
                    for value in values
                ):
                    resolved.setdefault(name, set()).add(parameter)
                else:
                    resolved.setdefault(name, set()).discard(parameter)
    return resolved


@pytest.mark.parametrize("path", SOURCES, ids=lambda path: path.name)
def test_every_execute_argument_resolves_to_a_module_level_constant(path):
    tree = _tree(path)
    owner = _scope_of(tree)
    literal_structures = _literal_structure_names(path)
    module_constants = _package_names(path)
    parameters = _parameters_fed_only_constants(tree, module_constants)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr not in (
            "execute",
            "executemany",
        ):
            continue
        if not node.args:
            pytest.fail(f"{path.name}: execute() with no statement")
        first = node.args[0]
        if not isinstance(first, ast.Name):
            pytest.fail(
                f"{path.name}: execute({ast.unparse(first)}) is not a bare name; only a "
                "name resolving to a module-level SQL constant is permitted"
            )
        scope = owner.get(node)
        allowed = set(module_constants)
        if scope is not None:
            allowed -= _names_bound_in(scope)
            allowed |= _literal_iterated_names(
                tree, owner, scope, literal_structures
            )
            allowed |= parameters.get(scope.name, set())
        where = "module level" if scope is None else scope.name
        assert first.id in allowed, (
            f"{path.name}: execute({first.id}) in {where} does not resolve to a "
            "module-level SQL constant of the package"
        )


def _untranslated_execute_sites(path: Path) -> list[int]:
    sites: list[int] = []

    def descend(node: ast.AST, owner: str | None, translated_depth: int) -> None:
        child_owner = owner
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            child_owner = node.name
        child_depth = translated_depth
        if isinstance(node, ast.With) and any(
            isinstance(item.context_expr, ast.Call)
            and isinstance(item.context_expr.func, ast.Name)
            and item.context_expr.func.id == "translated"
            for item in node.items
        ):
            child_depth += 1
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"execute", "executemany"}
            and not (
                path.name == "connection.py" and child_owner == "_rollback_quietly"
            )
            and child_depth == 0
        ):
            sites.append(node.lineno)
        for child in ast.iter_child_nodes(node):
            descend(child, child_owner, child_depth)

    descend(_tree(path), None, 0)
    return sites


def _wide_translation_sites(path: Path) -> list[tuple[int, int]]:
    sites: list[tuple[int, int]] = []

    def count(node: ast.AST, *, root: ast.With) -> int:
        if isinstance(node, ast.With) and node is not root and any(
            isinstance(item.context_expr, ast.Call)
            and isinstance(item.context_expr.func, ast.Name)
            and item.context_expr.func.id == "translated"
            for item in node.items
        ):
            return 0
        own = int(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"execute", "executemany"}
        )
        return own + sum(count(child, root=root) for child in ast.iter_child_nodes(node))

    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.With) and any(
            isinstance(item.context_expr, ast.Call)
            and isinstance(item.context_expr.func, ast.Name)
            and item.context_expr.func.id == "translated"
            for item in node.items
        ):
            executions = count(node, root=node)
            if executions != 1:
                sites.append((node.lineno, executions))
    return sites


@pytest.mark.parametrize("path", SOURCES, ids=lambda path: path.name)
def test_every_sqlite_execution_is_inside_narrow_translation(path):
    assert not (sites := _untranslated_execute_sites(path)), (
        f"{path.name} has untranslated SQLite execution at lines {sites}"
    )
    assert not (sites := _wide_translation_sites(path)), (
        f"{path.name} has translated scopes with execute counts other than one: {sites}"
    )


def test_no_statement_in_the_package_attaches_or_vacuums():
    from atoms.store.schema import SCHEMA_STATEMENTS

    inventory = dict(_sql_constants())
    inventory.update(
        (f"schema.SCHEMA_STATEMENTS[{index}]", text)
        for index, text in enumerate(SCHEMA_STATEMENTS)
    )
    for label, text in inventory.items():
        for banned in ("ATTACH", "DETACH", "VACUUM"):
            assert not re.search(rf"\b{banned}\b", text, re.IGNORECASE), (
                f"{label} spells {banned}"
            )


def _is_docstring(statement: ast.stmt) -> bool:
    return (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Constant)
        and isinstance(statement.value.value, str)
    )


def test_every_public_transaction_method_poisons_on_failure():
    tree = _tree(PACKAGE / "connection.py")
    body = next(
        node.body
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == "_StoreTransaction"
    )
    methods = {node.name: node for node in body if isinstance(node, FunctionScope)}

    def opens_mutating(node: FunctionScope) -> bool:
        statements = [line for line in node.body if not _is_docstring(line)]
        if len(statements) != 1 or not isinstance(statements[0], ast.With):
            return False
        guarded = statements[0]
        return (
            len(guarded.items) == 1
            and isinstance(guarded.items[0].context_expr, ast.Call)
            and not guarded.items[0].context_expr.args
            and not guarded.items[0].context_expr.keywords
            and isinstance(guarded.items[0].context_expr.func, ast.Attribute)
            and guarded.items[0].context_expr.func.attr == "_mutating"
            and isinstance(guarded.items[0].context_expr.func.value, ast.Name)
            and guarded.items[0].context_expr.func.value.id == "self"
        )

    for name, node in sorted(methods.items()):
        if name.startswith("_"):
            continue
        assert opens_mutating(node), (
            f"_StoreTransaction.{name} does not run inside _mutating(), so a failure "
            "inside it would not poison the transaction"
        )


_SQL_TOKEN = re.compile(
    r"(?:\s+|--[^\n]*(?:\n|$)|/\*.*?\*/)"
    r"|('(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|`(?:``|[^`])*`|\[[^]]*\]"
    r"|[A-Za-z_][A-Za-z0-9_]*|[(),;.]|\S)",
    re.DOTALL,
)


def _sql_tokens(text: str) -> list[str]:
    return [match.group(1) for match in _SQL_TOKEN.finditer(text) if match.group(1)]


def _after_parenthesized(tokens: list[str], start: int) -> int:
    assert tokens[start] == "(", "expected a parenthesized SQL clause"
    depth = 0
    for index in range(start, len(tokens)):
        if tokens[index] == "(":
            depth += 1
        elif tokens[index] == ")":
            depth -= 1
            if depth == 0:
                return index + 1
    raise AssertionError("unterminated parenthesized SQL clause")


def _effective_verb_index(tokens: list[str]) -> int | None:
    if not tokens:
        return None
    if tokens[0].upper() != "WITH":
        return 0
    index = 1
    if index < len(tokens) and tokens[index].upper() == "RECURSIVE":
        index += 1
    while True:
        assert index < len(tokens), "WITH has no common-table expression"
        index += 1  # CTE name
        if index < len(tokens) and tokens[index] == "(":
            index = _after_parenthesized(tokens, index)
        assert index < len(tokens) and tokens[index].upper() == "AS", (
            "CTE has no AS clause"
        )
        index += 1
        if index < len(tokens) and tokens[index].upper() == "NOT":
            index += 1
            assert index < len(tokens) and tokens[index].upper() == "MATERIALIZED"
            index += 1
        elif index < len(tokens) and tokens[index].upper() == "MATERIALIZED":
            index += 1
        assert index < len(tokens) and tokens[index] == "(", "CTE has no body"
        index = _after_parenthesized(tokens, index)
        if index >= len(tokens) or tokens[index] != ",":
            return index
        index += 1


def _statement_kind(text: str) -> str | None:
    tokens = _sql_tokens(text)
    index = _effective_verb_index(tokens)
    if index is None or index >= len(tokens):
        return None
    words = [token.upper() for token in tokens[index:]]
    verb = words[0]
    if verb not in ("INSERT", "REPLACE", "UPDATE", "DELETE"):
        return None
    if verb == "INSERT" and len(words) > 2 and words[1] == "OR":
        return f"INSERT OR {words[2]}"
    if verb == "INSERT" and "CONFLICT" in words:
        after = words[words.index("CONFLICT") :]
        if "DO" in after and after[after.index("DO") + 1 : after.index("DO") + 2] == [
            "UPDATE"
        ]:
            return "INSERT ON CONFLICT DO UPDATE"
    return verb


def _identifier(token: str) -> str:
    if token.startswith('"') and token.endswith('"'):
        return token[1:-1].replace('""', '"')
    if token.startswith("`") and token.endswith("`"):
        return token[1:-1].replace("``", "`")
    if token.startswith("[") and token.endswith("]"):
        return token[1:-1]
    return token


def _statement_target(text: str) -> str | None:
    tokens = _sql_tokens(text)
    index = _effective_verb_index(tokens)
    if index is None or index >= len(tokens):
        return None
    verb = tokens[index].upper()
    index += 1
    if (
        verb in ("INSERT", "UPDATE")
        and index < len(tokens)
        and tokens[index].upper() == "OR"
    ):
        index += 2
    if verb in ("INSERT", "REPLACE"):
        if index < len(tokens) and tokens[index].upper() == "INTO":
            index += 1
    elif (
        verb == "DELETE"
        and index < len(tokens)
        and tokens[index].upper() == "FROM"
    ):
        index += 1
    if verb not in ("INSERT", "REPLACE", "UPDATE", "DELETE"):
        return None
    assert index < len(tokens), f"{verb} has no target"
    if index + 2 < len(tokens) and tokens[index + 1] == ".":
        index += 2
    return _identifier(tokens[index])


def _blob_writers() -> list[tuple[str, str]]:
    writers = []
    for label, text in _sql_constants().items():
        kind = _statement_kind(text)
        if kind is None:
            continue
        target = _statement_target(text)
        if target and target.lower() == "blob":
            writers.append((label, kind))
    return writers


def test_exactly_one_statement_in_the_package_writes_blob():
    assert _blob_writers() == [("blobs.INSERT_BLOB", "INSERT")]


def test_the_store_attribute_set_is_exactly_the_documented_surface():
    from atoms.store import Store
    from tests.store_support import STORE_SURFACE

    assert {name for name in dir(Store) if not name.startswith("_")} == {
        name for name, _arguments in STORE_SURFACE
    } | {"close"}


def test_the_transaction_attribute_set_is_exactly_the_documented_surface():
    from atoms.store.connection import _StoreTransaction

    public = {
        name for name in dir(_StoreTransaction) if not name.startswith("_")
    }
    assert public == {
        "promote_staging",
        "insert_record",
        "set_transaction_state",
        "set_commit_decision",
        "set_journal_state",
        "set_rollback_result",
        "set_halt_diagnostic",
        "set_registration_digest",
        "set_settlement_digest",
        "set_assembly_halt",
        "set_active",
        "insert_root_operation",
        "insert_root_lifecycle",
        "set_root_lifecycle_state",
        "set_root_operation_phase",
        "set_root_operation_source_snapshot",
        "set_root_operation_tree_proof",
    }
    assert "insert_blobs" not in public


def test_the_workspace_attribute_set_is_exactly_the_documented_surface():
    from atoms.store import Workspace

    assert {name for name in dir(Workspace) if not name.startswith("_")} == {
        "txid",
        "staging_fd",
        "work_fd",
        "close",
    }


def _annotations(tree: ast.Module):
    for node in ast.walk(tree):
        annotation = None
        if isinstance(node, ast.AnnAssign | ast.arg):
            annotation = node.annotation
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            annotation = node.returns
        if annotation is not None:
            yield annotation


def _identifiers(tree: ast.Module) -> set[str]:
    names: set[str] = set()

    def collect(node: ast.AST) -> None:
        for inner in ast.walk(node):
            if isinstance(inner, ast.Name):
                names.add(inner.id)
            elif isinstance(inner, ast.Attribute):
                names.add(inner.attr)

    collect(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import | ast.ImportFrom):
            names.update(alias.name for alias in node.names)
            names.update(alias.asname for alias in node.names if alias.asname)
    for annotation in _annotations(tree):
        for inner in ast.walk(annotation):
            if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                try:
                    collect(ast.parse(inner.value, mode="eval"))
                except SyntaxError:
                    continue
    return names


@pytest.mark.parametrize("path", SOURCES, ids=lambda path: path.name)
def test_no_module_accepts_a_project_approved_spec(path):
    used = _identifiers(_tree(path))
    assert "ProjectApprovedSpec" not in used
    assert "approve_for_project" not in used


@pytest.mark.parametrize("path", SOURCES, ids=lambda path: path.name)
def test_no_module_catches_the_whole_sqlite_hierarchy(path):
    tree = _tree(path)
    modules, _database_errors, errors = _sqlite_exception_aliases(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler) or node.type is None:
            continue
        assert not _catches_sqlite_exception(
            node.type, modules, errors, "Error"
        ), (
            f"{path.name} catches {ast.unparse(node.type)}; "
            "catch sqlite3.DatabaseError or narrower"
        )


def test_the_package_has_exactly_one_swallowed_database_error():
    swallows = []
    for path in SOURCES:
        tree = _tree(path)
        modules, database_errors, _errors = _sqlite_exception_aliases(tree)
        owners = _handler_owners(tree)
        for handler in ast.walk(tree):
            if not isinstance(handler, ast.ExceptHandler):
                continue
            if not _catches_sqlite_exception(
                handler.type, modules, database_errors, "DatabaseError"
            ):
                continue
            if not _handler_always_reraises(handler):
                owner = owners[handler]
                swallows.append(f"{path.stem}.{owner or '<module>'}")
    assert swallows == ["connection._rollback_quietly"], swallows


def _trigger_blob_writers() -> list[str]:
    writers = []
    for statement in _sql_constants().values():
        tokens = _sql_tokens(statement)
        words = [token.upper() for token in tokens]
        if not words or words[0] != "CREATE":
            continue
        trigger_index = 2 if len(words) > 1 and words[1] in ("TEMP", "TEMPORARY") else 1
        if trigger_index >= len(words) or words[trigger_index] != "TRIGGER":
            continue
        begin = words.index("BEGIN", trigger_index + 1)
        end = len(words) - 1 - words[::-1].index("END")
        body: list[str] = []
        for token in tokens[begin + 1 : end] + [";"]:
            if token != ";":
                body.append(token)
                continue
            target = _statement_target(" ".join(body)) if body else None
            if target is not None and target.lower() == "blob":
                writers.append(statement)
            body = []
    return writers


def test_no_trigger_body_writes_blob():
    assert _trigger_blob_writers() == []


def test_the_store_fixture_registry_covers_every_test_argument():
    registered = fixture_names(TESTS / "conftest.py")
    assert unregistered_test_arguments(TESTS, registered, "test_store_*.py") == set()
    misplaced = sorted(
        f"{path.name}::{node.name}"
        for path in TESTS.glob("test_store_*.py")
        for node in ast.walk(_tree(path))
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and any(
            decorator_name(decorator) == "fixture"
            for decorator in node.decorator_list
        )
    )
    assert misplaced == [], f"fixtures must be declared in conftest.py: {misplaced}"


def test_the_fixture_guard_understands_parametrized_arguments():
    tree = ast.parse(
        "@pytest.mark.parametrize(('left', 'right'), [(1, 2)])\n"
        "def test_pair(left, right): pass\n"
    )
    test = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef))
    assert parametrize_names(test) == {"left", "right"}


def _plant_store_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    modules: dict[str, str],
) -> dict[str, Path]:
    package = tmp_path / "atoms" / "store"
    package.mkdir(parents=True)
    paths = {}
    modules.setdefault("records", "SAFE = 'SELECT 1'\n")
    for name, source in modules.items():
        path = package / f"{name}.py"
        path.write_text(source, encoding="utf-8")
        paths[name] = path
    monkeypatch.setattr("tests.test_store_architecture.PACKAGE", package)
    monkeypatch.setattr(
        "tests.test_store_architecture.SOURCES", sorted(paths.values())
    )
    return paths


def test_sql_guard_accepts_static_fstrings_of_module_constants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    paths = _plant_store_package(
        tmp_path,
        monkeypatch,
        {
            "schema": "SCHEMA_VERSION = 1\n",
            "connection": (
                "from atoms.store.schema import SCHEMA_VERSION\n"
                "SET_VERSION = f'PRAGMA user_version = {SCHEMA_VERSION}'\n"
                "def initialize(connection):\n"
                "    connection.execute(SET_VERSION)\n"
            ),
        },
    )
    test_every_execute_argument_resolves_to_a_module_level_constant(
        paths["connection"]
    )
    assert _sql_constants()["connection.SET_VERSION"] == "PRAGMA user_version = 1"


def test_sql_guard_accepts_imported_store_constants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    paths = _plant_store_package(
        tmp_path,
        monkeypatch,
        {
            "records": "SELECT_RECORD = 'SELECT * FROM transaction_record'\n",
            "connection": (
                "from atoms.store.records import SELECT_RECORD as QUERY\n"
                "def read(connection):\n"
                "    connection.execute(QUERY)\n"
            ),
        },
    )
    test_every_execute_argument_resolves_to_a_module_level_constant(
        paths["connection"]
    )


@pytest.mark.parametrize(
    "function",
    ["hostile", "_rollback_quietly"],
    ids=["ordinary", "counterfeit-rollback"],
)
def test_translation_guard_rejects_a_planted_execute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, function: str
):
    path = _plant_store_package(
        tmp_path,
        monkeypatch,
        {
            "hostile": (
                "QUERY = 'SELECT 1'\n"
                f"def {function}(connection):\n"
                "    connection.execute(QUERY)\n"
            )
        },
    )["hostile"]
    with pytest.raises(AssertionError):
        test_every_sqlite_execution_is_inside_narrow_translation(path)


@pytest.mark.parametrize(
    "source",
    [
        "def hostile(connection, holder):\n    connection.execute(holder.sql)\n",
        (
            "SAFE = 'SELECT 1'\n"
            "def hostile(connection):\n"
            "    statement = SAFE\n"
            "    connection.execute(statement)\n"
        ),
        "def hostile(connection, build):\n    connection.execute(build())\n",
        "def hostile(connection, cursor):\n    for row in cursor:\n        connection.execute(row)\n",
        (
            "SCHEMA_STATEMENTS = ('CREATE TABLE safe(id INTEGER)',)\n"
            "def hostile(connection, SCHEMA_STATEMENTS):\n"
            "    for statement in SCHEMA_STATEMENTS:\n"
            "        connection.execute(statement)\n"
        ),
        (
            "SCHEMA_STATEMENTS = ('CREATE TABLE safe(id INTEGER)',)\n"
            "def hostile(connection, runtime):\n"
            "    SCHEMA_STATEMENTS = runtime\n"
            "    for statement in SCHEMA_STATEMENTS:\n"
            "        connection.execute(statement)\n"
        ),
        (
            "_CONNECTION_PRAGMAS = (('safe', 'PRAGMA x', 'PRAGMA x', 1),)\n"
            "def hostile(connection, _CONNECTION_PRAGMAS):\n"
            "    for _, setter, reader, _ in _CONNECTION_PRAGMAS:\n"
            "        connection.execute(setter)\n"
            "        connection.execute(reader)\n"
        ),
        (
            "SAFE = 'SELECT 1'\n"
            "def run(connection, statement):\n"
            "    connection.execute(statement)\n"
            "def caller(connection, runtime):\n"
            "    run(connection, SAFE)\n"
            "    run(connection, runtime)\n"
        ),
        (
            "from elsewhere import SQL\n"
            "def hostile(connection):\n"
            "    connection.execute(SQL)\n"
        ),
        (
            "SAFE = 'SELECT 1'\n"
            "def hostile(connection, SAFE):\n"
            "    connection.execute(SAFE)\n"
        ),
        (
            "from atoms.store.records import SAFE\n"
            "def hostile(connection, SAFE):\n"
            "    connection.execute(SAFE)\n"
        ),
        (
            "SAFE = 'SELECT 1'\n"
            "def run(connection, statement):\n"
            "    connection.execute(statement)\n"
            "def caller(holder, connection):\n"
            "    holder.run(connection, SAFE)\n"
        ),
        (
            "VERSION = runtime_version()\n"
            "SQL = f'PRAGMA user_version = {VERSION}'\n"
            "def hostile(connection):\n"
            "    connection.execute(SQL)\n"
        ),
        (
            "SCHEMA_STATEMENTS = build_statements()\n"
            "def hostile(connection):\n"
            "    for statement in SCHEMA_STATEMENTS:\n"
            "        connection.execute(statement)\n"
        ),
        (
            "SAFE = 'SELECT 1'\n"
            "async def hostile(connection, SAFE):\n"
            "    connection.execute(SAFE)\n"
        ),
        (
            "SAFE = 'SELECT 1'\n"
            "def run(connection, statement=runtime_statement()):\n"
            "    connection.execute(statement)\n"
            "def caller(connection):\n"
            "    run(connection, SAFE)\n"
            "    run(connection)\n"
        ),
        (
            "SAFE = 'SELECT 1'\n"
            "SAFE = runtime_statement()\n"
            "def hostile(connection):\n"
            "    connection.execute(SAFE)\n"
        ),
        (
            "SCHEMA_STATEMENTS = (runtime_statement(),)\n"
            "def hostile(connection):\n"
            "    for statement in SCHEMA_STATEMENTS:\n"
            "        connection.execute(statement)\n"
        ),
        (
            "SCHEMA_STATEMENTS = ['SELECT 1']\n"
            "def hostile(connection):\n"
            "    for statement in SCHEMA_STATEMENTS:\n"
            "        connection.execute(statement)\n"
        ),
        (
            "SAFE = 'SELECT 1'\n"
            "def run(connection, statement):\n"
            "    connection.execute(statement)\n"
            "def caller(connection, other):\n"
            "    run(connection, SAFE)\n"
            "    other.run(connection, runtime_statement())\n"
        ),
    ],
    ids=(
        "attribute",
        "local-laundering",
        "runtime-call",
        "runtime-loop",
        "shadowed-schema-loop",
        "locally-rebound-schema-loop",
        "shadowed-pragma-loop",
        "mixed-helper-sites",
        "non-store-import",
        "shadowed-local-constant",
        "shadowed-imported-constant",
        "attribute-helper-call",
        "runtime-fstring",
        "runtime-schema-loop",
        "async-shadow",
        "omitted-runtime-default",
        "reassigned-module-constant",
        "runtime-tuple-element",
        "mutable-schema-loop",
        "non-self-helper-call",
    ),
)
def test_sql_guard_rejects_hostile_resolution_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str
):
    path = _plant_store_package(
        tmp_path, monkeypatch, {"hostile": source}
    )["hostile"]
    with pytest.raises(BaseException) as caught:
        test_every_execute_argument_resolves_to_a_module_level_constant(path)
    assert isinstance(caught.value, (AssertionError, pytest.fail.Exception))


@pytest.mark.parametrize(
    ("name", "statement"),
    [
        ("replace", "REPLACE INTO blob(digest, byte_len) VALUES (?, ?)"),
        ("insert-or-replace", "INSERT OR REPLACE INTO blob VALUES (?, ?)"),
        ("update", "UPDATE blob SET byte_len = ? WHERE digest = ?"),
        ("delete", "DELETE FROM blob WHERE digest = ?"),
        (
            "upsert",
            "INSERT INTO blob VALUES (?, ?) ON CONFLICT(digest) DO UPDATE SET byte_len = ?",
        ),
        (
            "cte-update",
            "WITH chosen AS (SELECT 1) UPDATE blob SET byte_len = 1",
        ),
        (
            "line-comment-prefix",
            "-- harmless heading\nUPDATE blob SET byte_len = 1",
        ),
        (
            "block-comment-prefix",
            "/* harmless heading */ DELETE FROM blob",
        ),
        (
            "qualified-update",
            "UPDATE main.blob SET byte_len = 1",
        ),
        (
            "quoted-qualified-insert",
            'INSERT INTO "main"."blob" VALUES (?, ?)',
        ),
    ],
)
def test_blob_inventory_rejects_every_second_writer_kind(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    statement: str,
):
    del name
    _plant_store_package(
        tmp_path,
        monkeypatch,
        {
            "blobs": "INSERT_BLOB = 'INSERT INTO blob VALUES (?, ?)'\n",
            "hostile": f"HOSTILE = {statement!r}\n",
        },
    )
    with pytest.raises(AssertionError):
        test_exactly_one_statement_in_the_package_writes_blob()


def test_blob_inventory_rejects_a_hostile_trigger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _plant_store_package(
        tmp_path,
        monkeypatch,
        {
            "blobs": "INSERT_BLOB = 'INSERT INTO blob VALUES (?, ?)'\n",
            "schema": (
                "SCHEMA_STATEMENTS = (\"CREATE TRIGGER hostile AFTER INSERT ON "
                "transaction_record BEGIN DELETE FROM blob; END\",)\n"
            ),
        },
    )
    with pytest.raises(AssertionError):
        test_no_trigger_body_writes_blob()


@pytest.mark.parametrize(
    "prefix", ["-- harmless heading\n", "/* harmless heading */ "]
)
def test_blob_inventory_rejects_a_comment_prefixed_hostile_trigger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    prefix: str,
):
    _plant_store_package(
        tmp_path,
        monkeypatch,
        {
            "blobs": "INSERT_BLOB = 'INSERT INTO blob VALUES (?, ?)'\n",
            "schema": (
                "SCHEMA_STATEMENTS = ("
                f"{prefix + 'CREATE TRIGGER hostile AFTER INSERT ON transaction_record BEGIN DELETE FROM blob; END'!r},"
                ")\n"
            ),
        },
    )
    with pytest.raises(AssertionError):
        test_no_trigger_body_writes_blob()


@pytest.mark.parametrize(
    "source",
    [
        "import os\ndef hostile(fd):\n    os.fsync(fd)\n",
        "def hostile():\n    try:\n        pass\n    except:\n        pass\n",
        "import sqlite3\ndef hostile():\n    try:\n        pass\n    except sqlite3.Error:\n        pass\n",
        (
            "import sqlite3\n"
            "def hostile():\n"
            "    try:\n"
            "        pass\n"
            "    except sqlite3.DatabaseError:\n"
            "        return\n"
        ),
    ],
    ids=("raw-fsync", "bare-except", "sqlite-error", "swallowed-database-error"),
)
def test_error_and_flush_guards_reject_hostile_modules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str
):
    path = _plant_store_package(
        tmp_path, monkeypatch, {"hostile": source}
    )["hostile"]
    guards = (
        test_no_raw_fsync_appears_in_the_package,
        test_no_blanket_oserror_handler,
        test_no_module_catches_the_whole_sqlite_hierarchy,
        test_every_database_error_handler_contains_a_bare_raise,
    )
    failures = 0
    for guard in guards:
        try:
            guard(path)
        except (AssertionError, pytest.fail.Exception):
            failures += 1
    assert failures >= 1


def test_oserror_guard_rejects_message_laundering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    path = _plant_store_package(
        tmp_path,
        monkeypatch,
        {
            "hostile": (
                "def hostile():\n"
                "    try:\n"
                "        pass\n"
                "    except OSError as caught:\n"
                "        caught.errno\n"
                "        raise RuntimeError('errno')\n"
            )
        },
    )["hostile"]
    with pytest.raises(AssertionError):
        test_no_blanket_oserror_handler(path)


def test_database_error_guard_ignores_a_nested_bare_raise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    path = _plant_store_package(
        tmp_path,
        monkeypatch,
        {
            "hostile": (
                "import sqlite3\n"
                "def hostile():\n"
                "    try:\n"
                "        pass\n"
                "    except sqlite3.DatabaseError:\n"
                "        def unrelated():\n"
                "            raise\n"
                "        return\n"
            )
        },
    )["hostile"]
    with pytest.raises(AssertionError):
        test_every_database_error_handler_contains_a_bare_raise(path)


ROLLBACK_EXEMPTION = (
    "import sqlite3\n"
    "def _rollback_quietly():\n"
    "    try:\n"
    "        pass\n"
    "    except sqlite3.DatabaseError:\n"
    "        return\n"
)


@pytest.mark.parametrize(
    "source",
    [
        (
            "import sqlite3\n"
            "try:\n"
            "    pass\n"
            "except sqlite3.DatabaseError:\n"
            "    pass\n"
        ),
        (
            "import sqlite3\n"
            "async def swallow():\n"
            "    try:\n"
            "        pass\n"
            "    except sqlite3.DatabaseError:\n"
            "        return\n"
        ),
        (
            "from sqlite3 import DatabaseError as Alias\n"
            "def swallow():\n"
            "    try:\n"
            "        pass\n"
            "    except Alias:\n"
            "        return\n"
        ),
        (
            "import sqlite3\n"
            "def _rollback_quietly():\n"
            "    async def nested():\n"
            "        try:\n"
            "            pass\n"
            "        except sqlite3.DatabaseError:\n"
            "            return\n"
        ),
    ],
    ids=("module", "async", "imported-alias", "nested-owner"),
)
def test_database_error_guards_reject_every_nonexempt_lexical_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: str,
):
    modules = {"connection": ROLLBACK_EXEMPTION, "hostile": source}
    if "def _rollback_quietly" in source:
        modules = {"connection": source}
    paths = _plant_store_package(tmp_path, monkeypatch, modules)
    hostile = paths.get("hostile", paths["connection"])
    with pytest.raises(AssertionError):
        test_every_database_error_handler_contains_a_bare_raise(hostile)
    with pytest.raises(AssertionError):
        test_the_package_has_exactly_one_swallowed_database_error()


def test_sqlite_error_guard_resolves_a_module_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    path = _plant_store_package(
        tmp_path,
        monkeypatch,
        {
            "hostile": (
                "import sqlite3 as database\n"
                "def swallow():\n"
                "    try:\n"
                "        pass\n"
                "    except database.Error:\n"
                "        return\n"
            )
        },
    )["hostile"]
    with pytest.raises(AssertionError):
        test_no_module_catches_the_whole_sqlite_hierarchy(path)


@pytest.mark.parametrize(
    "aliases",
    [
        "DBError = sqlite3.DatabaseError\n",
        (
            "DBError, WholeError = (\n"
            "    sqlite3.DatabaseError, sqlite3.Error,\n"
            ")\n"
        ),
    ],
    ids=("assigned", "tuple-assigned"),
)
def test_database_error_guards_resolve_assigned_exception_aliases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    aliases: str,
):
    paths = _plant_store_package(
        tmp_path,
        monkeypatch,
        {
            "connection": ROLLBACK_EXEMPTION,
            "hostile": (
                "import sqlite3\n"
                f"{aliases}"
                "def swallow():\n"
                "    try:\n"
                "        pass\n"
                "    except DBError:\n"
                "        return\n"
            ),
        },
    )
    with pytest.raises(AssertionError):
        test_every_database_error_handler_contains_a_bare_raise(paths["hostile"])
    with pytest.raises(AssertionError):
        test_the_package_has_exactly_one_swallowed_database_error()


@pytest.mark.parametrize(
    "source",
    [
        (
            "import sqlite3\n"
            "database = sqlite3\n"
            "def swallow():\n"
            "    try:\n"
            "        pass\n"
            "    except database.DatabaseError:\n"
            "        return\n"
        ),
        (
            "import sqlite3\n"
            "def swallow(condition):\n"
            "    try:\n"
            "        pass\n"
            "    except sqlite3.DatabaseError:\n"
            "        if condition:\n"
            "            raise\n"
            "        return\n"
        ),
    ],
    ids=("assigned-module-alias", "path-sensitive-swallow"),
)
def test_database_error_guards_reject_alias_and_partial_reraise(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: str,
):
    paths = _plant_store_package(
        tmp_path,
        monkeypatch,
        {"connection": ROLLBACK_EXEMPTION, "hostile": source},
    )
    with pytest.raises(AssertionError):
        test_every_database_error_handler_contains_a_bare_raise(paths["hostile"])
    with pytest.raises(AssertionError):
        test_the_package_has_exactly_one_swallowed_database_error()


@pytest.mark.parametrize(
    "source",
    [
        "from atoms.fs import ProjectApprovedSpec\n",
        "def hostile(spec: 'ProjectApprovedSpec') -> None:\n    pass\n",
        "import atoms.fs.approval as approval\nX = approval.ProjectApprovedSpec\n",
        "from atoms.fs import approve_for_project\n",
    ],
)
def test_project_approval_guard_rejects_all_identifier_shapes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str
):
    path = _plant_store_package(
        tmp_path, monkeypatch, {"hostile": source}
    )["hostile"]
    with pytest.raises(AssertionError):
        test_no_module_accepts_a_project_approved_spec(path)


def test_dependency_guard_rejects_fs_and_core_store_imports(tmp_path: Path):
    for directory in ("fs", "core"):
        path = tmp_path / "atoms" / directory / "hostile.py"
        path.parent.mkdir(parents=True)
        path.write_text("from atoms.store import Store\n", encoding="utf-8")
        with pytest.raises(AssertionError):
            test_neither_fs_nor_core_imports_the_store(path)


def test_dependency_guard_rejects_a_relative_store_import(tmp_path: Path):
    path = tmp_path / "atoms" / "fs" / "hostile.py"
    path.parent.mkdir(parents=True)
    path.write_text("from ..store import Store\n", encoding="utf-8")
    with pytest.raises(AssertionError):
        test_neither_fs_nor_core_imports_the_store(path)


def test_transaction_guard_rejects_a_public_method_before_mutating(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _plant_store_package(
        tmp_path,
        monkeypatch,
        {
            "connection": (
                "class _StoreTransaction:\n"
                "    def _mutating(self): pass\n"
                "    def hostile(self):\n"
                "        prepare()\n"
                "        with self._mutating():\n"
                "            write()\n"
            )
        },
    )
    with pytest.raises(AssertionError):
        test_every_public_transaction_method_poisons_on_failure()


def test_transaction_guard_rejects_delegation_to_another_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _plant_store_package(
        tmp_path,
        monkeypatch,
        {
            "connection": (
                "class _StoreTransaction:\n"
                "    def _mutating(self): pass\n"
                "    def _safe(self):\n"
                "        with self._mutating():\n"
                "            write()\n"
                "    def hostile(self, other):\n"
                "        other._safe()\n"
            )
        },
    )
    with pytest.raises(AssertionError):
        test_every_public_transaction_method_poisons_on_failure()


@pytest.mark.parametrize(
    "method",
    [
        (
            "    def hostile(self, other):\n"
            "        with other._mutating():\n"
            "            write()\n"
        ),
        (
            "    def hostile(self):\n"
            "        with self._mutating():\n"
            "            write()\n"
            "        unguarded_write()\n"
        ),
    ],
    ids=("non-self-context", "write-after-context"),
)
def test_transaction_guard_requires_one_complete_self_mutating_body(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
):
    _plant_store_package(
        tmp_path,
        monkeypatch,
        {"connection": "class _StoreTransaction:\n    def _mutating(self): pass\n" + method},
    )
    with pytest.raises(AssertionError):
        test_every_public_transaction_method_poisons_on_failure()


def test_transaction_guard_rejects_an_unguarded_async_public_method(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _plant_store_package(
        tmp_path,
        monkeypatch,
        {
            "connection": (
                "class _StoreTransaction:\n"
                "    def _mutating(self): pass\n"
                "    async def hostile(self):\n"
                "        write()\n"
            )
        },
    )
    with pytest.raises(AssertionError):
        test_every_public_transaction_method_poisons_on_failure()


def test_executescript_guard_rejects_a_planted_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    path = _plant_store_package(
        tmp_path,
        monkeypatch,
        {"hostile": "def hostile(connection):\n    connection.executescript('SELECT 1')\n"},
    )["hostile"]
    with pytest.raises(AssertionError):
        test_executescript_is_never_called(path)


def test_executescript_guard_rejects_an_assigned_call_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    path = _plant_store_package(
        tmp_path,
        monkeypatch,
        {
            "hostile": (
                "def hostile(connection):\n"
                "    run = connection.executescript\n"
                "    run('SELECT 1')\n"
            )
        },
    )["hostile"]
    with pytest.raises(AssertionError):
        test_executescript_is_never_called(path)


def test_raw_fsync_guard_rejects_an_imported_call_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    path = _plant_store_package(
        tmp_path,
        monkeypatch,
        {"hostile": "from os import fsync as flush\ndef hostile(fd): flush(fd)\n"},
    )["hostile"]
    with pytest.raises(AssertionError):
        test_no_raw_fsync_appears_in_the_package(path)


@pytest.mark.parametrize("statement", ["ATTACH 'other.db' AS other", "VACUUM"])
def test_statement_inventory_rejects_attach_and_vacuum(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    statement: str,
):
    _plant_store_package(
        tmp_path, monkeypatch, {"hostile": f"HOSTILE = {statement!r}\n"}
    )
    with pytest.raises(AssertionError):
        test_no_statement_in_the_package_attaches_or_vacuums()


def test_fixture_guard_rejects_a_fixture_declared_outside_conftest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    (tmp_path / "conftest.py").write_text("", encoding="utf-8")
    (tmp_path / "test_store_hostile.py").write_text(
        "import pytest\n"
        "@pytest.fixture\n"
        "def hidden_fixture(): return 1\n"
        "def test_uses_hidden_fixture(hidden_fixture): pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("tests.test_store_architecture.TESTS", tmp_path)
    with pytest.raises(AssertionError):
        test_the_store_fixture_registry_covers_every_test_argument()


def test_fixture_guard_rejects_an_unused_fixture_outside_conftest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    (tmp_path / "conftest.py").write_text("", encoding="utf-8")
    (tmp_path / "test_store_hostile.py").write_text(
        "import pytest\n"
        "@pytest.fixture\n"
        "def hidden_fixture(): return 1\n"
        "def test_uses_nothing(): pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("tests.test_store_architecture.TESTS", tmp_path)
    with pytest.raises(AssertionError):
        test_the_store_fixture_registry_covers_every_test_argument()


def test_fixture_guard_rejects_a_shadowing_fixture_outside_conftest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    (tmp_path / "conftest.py").write_text(
        "import pytest\n@pytest.fixture\ndef shared(): return 1\n",
        encoding="utf-8",
    )
    (tmp_path / "test_store_hostile.py").write_text(
        "import pytest\n"
        "@pytest.fixture\n"
        "def shared(): return 2\n"
        "def test_uses_nothing(): pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("tests.test_store_architecture.TESTS", tmp_path)
    with pytest.raises(AssertionError):
        test_the_store_fixture_registry_covers_every_test_argument()


def test_surface_guard_rejects_a_new_public_store_method(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(atoms.store.Store, "escape", lambda self: None, raising=False)
    with pytest.raises(AssertionError):
        test_the_store_attribute_set_is_exactly_the_documented_surface()
