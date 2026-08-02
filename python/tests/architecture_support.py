import ast
from pathlib import Path

PYTEST_BUILTINS = {
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


def decorator_name(decorator: ast.expr) -> str | None:
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return None


def fixture_names(conftest_path: Path) -> set[str]:
    tree = ast.parse(conftest_path.read_text(encoding="utf-8"))
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(
            decorator_name(decorator) == "fixture"
            for decorator in node.decorator_list
        )
    }


def parametrize_names(
    test: ast.FunctionDef | ast.AsyncFunctionDef,
) -> set[str]:
    names: set[str] = set()
    for decorator in test.decorator_list:
        if (
            not isinstance(decorator, ast.Call)
            or decorator_name(decorator) != "parametrize"
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


def collected_test_functions(
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


def unregistered_test_arguments(
    tests_root: Path,
    registered: set[str],
    pattern: str,
) -> set[str]:
    """Test arguments in `pattern` files that name no fixture in `registered`.

    `pattern` is a parameter because two suites now share this scanner. Subtracting
    parametrize_names first is not an optimization: a parametrized value is an
    argument that is deliberately NOT a fixture, and reporting it missing would make
    the guard fail on correct tests.
    """
    missing: set[str] = set()
    for test_path in tests_root.glob(pattern):
        tree = ast.parse(test_path.read_text(encoding="utf-8"))
        for node, receivers in collected_test_functions(tree):
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
                - parametrize_names(node)
                - registered
                - PYTEST_BUILTINS
                - receivers
            )
    return missing


def handler_nodes(handler: ast.ExceptHandler):
    """Walk a handler body without laundering evidence through a nested scope."""
    stack: list[ast.AST] = list(reversed(handler.body))
    while stack:
        node = stack.pop()
        yield node
        if isinstance(
            node,
            (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda),
        ):
            continue
        stack.extend(reversed(list(ast.iter_child_nodes(node))))


def oserror_handler_discriminates(handler: ast.ExceptHandler) -> bool:
    if handler.name is None:
        return False
    nodes = list(handler_nodes(handler))
    delegates = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_frontier_from"
        and any(
            isinstance(argument, ast.Name) and argument.id == handler.name
            for argument in node.args
        )
        for node in nodes
    )
    errno_controls_branch = any(
        isinstance(node, (ast.If, ast.IfExp))
        and isinstance(node.test, ast.Compare)
        and any(
            isinstance(part, ast.Attribute)
            and isinstance(part.value, ast.Name)
            and part.value.id == handler.name
            and part.attr == "errno"
            for part in ast.walk(node.test)
        )
        for node in nodes
    )
    return delegates or errno_controls_branch
