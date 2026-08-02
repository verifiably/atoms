"""Tier 6 -- properties of the package as a whole (design §11.6)."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

import atoms.store
from tests.architecture_support import (
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


def _imported_modules(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
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
        for module in _imported_modules(_tree(path))
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


@pytest.mark.parametrize("path", SOURCES, ids=lambda path: path.name)
def test_no_raw_fsync_appears_in_the_package(path):
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Attribute) and node.attr in ("fsync", "fdatasync"):
            pytest.fail(f"{path.name} calls os.{node.attr} directly")


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


@pytest.mark.parametrize("path", SOURCES, ids=lambda path: path.name)
def test_every_database_error_handler_contains_a_bare_raise(path):
    for node in ast.walk(_tree(path)):
        if not isinstance(node, ast.FunctionDef):
            continue
        if f"{path.stem}.{node.name}" == SWALLOW_EXEMPTION:
            continue
        for handler in ast.walk(node):
            if not isinstance(handler, ast.ExceptHandler):
                continue
            label = ast.unparse(handler.type) if handler.type else ""
            if "DatabaseError" not in label:
                continue
            assert any(
                isinstance(inner, ast.Raise) and inner.exc is None
                for inner in handler_nodes(handler)
            ), f"{path.name}::{node.name}'s DatabaseError handler has no bare raise"


@pytest.mark.parametrize("path", SOURCES, ids=lambda path: path.name)
def test_executescript_is_never_called(path):
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr != "executescript", f"{path.name} calls executescript"


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
        else:
            return None
    return "".join(parts)


def _static_values(path: Path, seen: frozenset[Path] = frozenset()) -> dict[str, object]:
    if path in seen:
        return {}
    tree = _tree(path)
    values: dict[str, object] = {}
    for name, node in _module_bindings(tree):
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
                if alias.name in imported
            }
        )
    pending = dict(_module_bindings(tree))
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


def _scope_of(tree: ast.Module) -> dict[ast.AST, ast.FunctionDef | None]:
    owner: dict[ast.AST, ast.FunctionDef | None] = {}

    def descend(node: ast.AST, current: ast.FunctionDef | None) -> None:
        for child in ast.iter_child_nodes(node):
            owner[child] = current
            descend(child, child if isinstance(child, ast.FunctionDef) else current)

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


def _names_bound_in(scope: ast.FunctionDef) -> set[str]:
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
    owner: dict[ast.AST, ast.FunctionDef | None],
    scope: ast.FunctionDef,
    module_level: set[str],
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
        if node.iter.id not in module_level or node.iter.id in shadowed:
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
        for name, _value in _module_bindings(_tree(path))
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
    by_name: dict[str, list[ast.FunctionDef]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            by_name.setdefault(node.name, []).append(node)
    calls: dict[str, list[ast.Call]] = {}
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
    resolved: dict[str, set[str]] = {}
    for name, definitions in by_name.items():
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
                        values.extend(
                            keyword.value
                            for keyword in call.keywords
                            if keyword.arg == parameter
                        )
                if values and all(
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
    module_level = _module_level_names(tree)
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
            allowed |= _literal_iterated_names(tree, owner, scope, module_level)
            allowed |= parameters.get(scope.name, set())
        where = "module level" if scope is None else scope.name
        assert first.id in allowed, (
            f"{path.name}: execute({first.id}) in {where} does not resolve to a "
            "module-level SQL constant of the package"
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
    methods = {
        node.name: node for node in body if isinstance(node, ast.FunctionDef)
    }

    def opens_mutating(node: ast.FunctionDef) -> bool:
        first = next((line for line in node.body if not _is_docstring(line)), None)
        return (
            isinstance(first, ast.With)
            and any(
                isinstance(item.context_expr, ast.Call)
                and isinstance(item.context_expr.func, ast.Attribute)
                and item.context_expr.func.attr == "_mutating"
                for item in first.items
            )
        )

    def delegates_to_a_mutating_helper(node: ast.FunctionDef) -> bool:
        statements = [line for line in node.body if not _is_docstring(line)]
        if len(statements) != 1:
            return False
        call = statements[0].value if isinstance(statements[0], ast.Expr) else None
        if (
            not isinstance(call, ast.Call)
            or not isinstance(call.func, ast.Attribute)
            or not isinstance(call.func.value, ast.Name)
            or call.func.value.id != "self"
        ):
            return False
        target = methods.get(call.func.attr)
        return target is not None and opens_mutating(target)

    for name, node in sorted(methods.items()):
        if name.startswith("_"):
            continue
        assert opens_mutating(node) or delegates_to_a_mutating_helper(node), (
            f"_StoreTransaction.{name} does not run inside _mutating(), so a failure "
            "inside it would not poison the transaction"
        )


def _statement_kind(text: str) -> str | None:
    words = [word.upper() for word in re.findall(r"[A-Za-z_]+", text)]
    if not words:
        return None
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


def _blob_writers() -> list[tuple[str, str]]:
    writers = []
    for label, text in _sql_constants().items():
        kind = _statement_kind(text)
        if kind is None:
            continue
        target = re.search(
            r"\b(?:INTO|UPDATE|FROM)\s+([A-Za-z_][A-Za-z0-9_]*)",
            text,
            re.IGNORECASE,
        )
        if target and target.group(1).lower() == "blob":
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
        "set_active",
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


def test_no_production_module_outside_the_package_imports_the_store():
    root = PACKAGE.parent
    offenders = sorted(
        str(path.relative_to(root))
        for path in root.rglob("*.py")
        if not str(path).startswith(str(PACKAGE))
        and any(
            name == "atoms.store" or name.startswith("atoms.store.")
            for name in _imported_modules(_tree(path))
        )
    )
    assert offenders == [], offenders


@pytest.mark.parametrize("path", SOURCES, ids=lambda path: path.name)
def test_no_module_catches_the_whole_sqlite_hierarchy(path):
    for node in ast.walk(_tree(path)):
        if not isinstance(node, ast.ExceptHandler) or node.type is None:
            continue
        label = ast.unparse(node.type)
        assert "sqlite3.Error" not in label, (
            f"{path.name} catches {label}; catch sqlite3.DatabaseError or narrower"
        )


def test_the_package_has_exactly_one_swallowed_database_error():
    swallows = []
    for path in SOURCES:
        for node in ast.walk(_tree(path)):
            if not isinstance(node, ast.FunctionDef):
                continue
            for handler in ast.walk(node):
                if not isinstance(handler, ast.ExceptHandler) or handler.type is None:
                    continue
                if "DatabaseError" not in ast.unparse(handler.type):
                    continue
                if not any(
                    isinstance(inner, ast.Raise) and inner.exc is None
                    for inner in handler_nodes(handler)
                ):
                    swallows.append(f"{path.stem}.{node.name}")
    assert swallows == ["connection._rollback_quietly"], swallows


def _trigger_blob_writers() -> list[str]:
    writers = []
    for statement in _sql_constants().values():
        if not re.match(
            r"^\s*CREATE(?:\s+TEMP(?:ORARY)?)?\s+TRIGGER\b",
            statement,
            re.IGNORECASE,
        ):
            continue
        body = re.split(r"\bBEGIN\b", statement, maxsplit=1, flags=re.IGNORECASE)[1]
        body = re.split(r"\bEND\b", body, maxsplit=1, flags=re.IGNORECASE)[0]
        for target in re.finditer(
            r"\b(?:INTO|UPDATE|FROM)\s+([A-Za-z_][A-Za-z0-9_]*)",
            body,
            re.IGNORECASE,
        ):
            if target.group(1).lower() == "blob":
                writers.append(statement)
    return writers


def test_no_trigger_body_writes_blob():
    assert _trigger_blob_writers() == []


def test_the_store_fixture_registry_covers_every_test_argument():
    registered = fixture_names(TESTS / "conftest.py")
    assert unregistered_test_arguments(TESTS, registered, "test_store_*.py") == set()


def test_the_fixture_guard_understands_parametrized_arguments():
    tree = ast.parse(
        "@pytest.mark.parametrize(('left', 'right'), [(1, 2)])\n"
        "def test_pair(left, right): pass\n"
    )
    test = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef))
    assert parametrize_names(test) == {"left", "right"}


def test_a5_status_is_synchronized_across_authority_documents():
    agents = (Path(__file__).parents[2] / "AGENTS.md").read_text(encoding="utf-8")
    assert "A5a" in agents
    assert "A5a designed and unimplemented" not in agents


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
        path = tmp_path / f"{directory}.py"
        path.write_text("from atoms.store import Store\n", encoding="utf-8")
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


def test_surface_guard_rejects_a_new_public_store_method(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(atoms.store.Store, "escape", lambda self: None, raising=False)
    with pytest.raises(AssertionError):
        test_the_store_attribute_set_is_exactly_the_documented_surface()
