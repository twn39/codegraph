from types import MappingProxyType

from codegraph_gen.resolver_context import ResolutionContext, STOP
from codegraph_gen.scope import FileSymbolScope
from codegraph_gen.resolver_strategy import get_strategy_by_name
from codegraph_gen.resolver_steps import (
    guard_builtin,
    resolve_local_binding,
    resolve_self_reference,
    resolve_current_class,
    resolve_file_scope,
    resolve_package_siblings,
    resolve_explicit_imports,
    resolve_wildcard_imports,
    resolve_global_fallback,
)


def make_test_context(
    caller_id="main.py::MyClass.method",
    source_file="main.py",
    callee_name="x.bar",
    parts=("x", "bar"),
    strategy_name="python",
    declared_symbols=None,
    imported_symbols=None,
    wildcard_imports=None,
    local_bindings=None,
    node_ids=None,
    graph_nodes=None,
    global_symbol_map=None,
    return_types=None,
):
    strategy = get_strategy_by_name(strategy_name)
    scope = FileSymbolScope(source_file, strategy_name)
    if declared_symbols:
        scope.declared_symbols.update(declared_symbols)
    if imported_symbols:
        scope.imported_symbols.update(imported_symbols)
    if wildcard_imports:
        scope.wildcard_imports.extend(wildcard_imports)

    parts_tuple = tuple(parts)
    main_symbol = parts_tuple[0] if parts_tuple else ""
    rest_of_callee = ".".join(parts_tuple[1:]) if len(parts_tuple) > 1 else ""

    return ResolutionContext(
        caller_id=caller_id,
        source_file=source_file,
        callee_name=callee_name,
        parts=parts_tuple,
        main_symbol=main_symbol,
        rest_of_callee=rest_of_callee,
        strategy=strategy,
        scope=scope,
        local_bindings=MappingProxyType(local_bindings or {}),
        node_ids=frozenset(node_ids or set()),
        graph_nodes=graph_nodes or {},
        global_symbol_map=MappingProxyType(global_symbol_map or {}),
        return_types=MappingProxyType(return_types or {}),
    )


def test_guard_builtin():
    # Builtin Python function should return STOP
    ctx = make_test_context(
        callee_name="print", parts=("print",), strategy_name="python"
    )
    assert guard_builtin(ctx) is STOP

    # Non-builtin should return None
    ctx = make_test_context(
        callee_name="my_func", parts=("my_func",), strategy_name="python"
    )
    assert guard_builtin(ctx) is None


def test_resolve_local_binding():
    # Case 1: no local bindings
    ctx = make_test_context(callee_name="x.bar", parts=("x", "bar"))
    assert resolve_local_binding(ctx) is None

    # Case 2: local binding exists, class node exists, method node exists
    graph_nodes = {
        "main.py::OtherClass": {
            "type": "class",
            "label": "OtherClass",
            "source_file": "main.py",
        },
        "main.py::OtherClass.bar": {
            "type": "method",
            "label": "bar",
            "source_file": "main.py",
        },
    }
    ctx = make_test_context(
        callee_name="x.bar",
        parts=("x", "bar"),
        local_bindings={"x": "OtherClass"},
        node_ids=set(graph_nodes.keys()),
        graph_nodes=graph_nodes,
    )
    assert resolve_local_binding(ctx) == "main.py::OtherClass.bar"

    # Case 3: local binding exists but class not in graph -> should STOP
    ctx = make_test_context(
        callee_name="x.bar",
        parts=("x", "bar"),
        local_bindings={"x": "MissingClass"},
        node_ids=set(),
        graph_nodes={},
    )
    assert resolve_local_binding(ctx) is STOP


def test_resolve_self_reference():
    # self.method reference
    node_ids = {"main.py::MyClass.helper"}
    ctx = make_test_context(
        caller_id="main.py::MyClass.method",
        callee_name="self.helper",
        parts=("self", "helper"),
        node_ids=node_ids,
    )
    assert resolve_self_reference(ctx) == "main.py::MyClass.helper"

    # Non-self/this/cls reference
    ctx = make_test_context(
        caller_id="main.py::MyClass.method",
        callee_name="other.helper",
        parts=("other", "helper"),
        node_ids=node_ids,
    )
    assert resolve_self_reference(ctx) is None


def test_resolve_current_class():
    # helper method called within current class context without self.
    node_ids = {"main.py::MyClass.helper"}
    ctx = make_test_context(
        caller_id="main.py::MyClass.method",
        callee_name="helper",
        parts=("helper",),
        node_ids=node_ids,
    )
    assert resolve_current_class(ctx) == "main.py::MyClass.helper"


def test_resolve_file_scope():
    # Symbol declared at file level
    node_ids = {"main.py::my_global_var"}
    ctx = make_test_context(
        caller_id="main.py::MyClass.method",
        callee_name="my_global_var",
        parts=("my_global_var",),
        node_ids=node_ids,
    )
    assert resolve_file_scope(ctx) == "main.py::my_global_var"


def test_resolve_package_siblings():
    # Sibling file in same directory (requires Go or Swift strategy)
    graph_nodes = {
        "main.go": {"type": "file"},
        "helper.go": {"type": "file"},
        "helper.go::HelperFunc": {
            "type": "function",
            "label": "HelperFunc",
            "source_file": "helper.go",
        },
    }
    ctx = make_test_context(
        caller_id="main.go::main",
        source_file="main.go",
        callee_name="HelperFunc",
        parts=("HelperFunc",),
        strategy_name="go",
        node_ids=set(graph_nodes.keys()),
        graph_nodes=graph_nodes,
    )
    assert resolve_package_siblings(ctx) == "helper.go::HelperFunc"

    # Python strategy should return None (no package sibling scope)
    ctx = make_test_context(
        caller_id="main.py::main",
        source_file="main.py",
        callee_name="HelperFunc",
        parts=("HelperFunc",),
        strategy_name="python",
        node_ids=set(graph_nodes.keys()),
        graph_nodes=graph_nodes,
    )
    assert resolve_package_siblings(ctx) is None


def test_resolve_explicit_imports():
    node_ids = {"utils.py::helper"}
    ctx = make_test_context(
        callee_name="helper",
        parts=("helper",),
        imported_symbols={"helper": ("utils.py", "helper")},
        node_ids=node_ids,
    )
    assert resolve_explicit_imports(ctx) == "utils.py::helper"


def test_resolve_wildcard_imports():
    node_ids = {"utils.py::helper"}
    ctx = make_test_context(
        callee_name="helper",
        parts=("helper",),
        wildcard_imports=["utils.py"],
        node_ids=node_ids,
    )
    assert resolve_wildcard_imports(ctx) == "utils.py::helper"


def test_resolve_global_fallback():
    # Single unique candidate globally
    graph_nodes = {
        "other.py::UniqueSymbol": {
            "type": "class",
            "label": "UniqueSymbol",
            "source_file": "other.py",
        }
    }
    ctx = make_test_context(
        callee_name="UniqueSymbol",
        parts=("UniqueSymbol",),
        global_symbol_map={"UniqueSymbol": ["other.py::UniqueSymbol"]},
        node_ids=set(graph_nodes.keys()),
        graph_nodes=graph_nodes,
    )
    assert resolve_global_fallback(ctx) == "other.py::UniqueSymbol"


def test_resolve_builtin_vs_stdlib_shadowing():
    # Case 1: errors is a Go stdlib module. It should not be blocked by guard_builtin
    ctx = make_test_context(
        callee_name="errors.New", parts=("errors", "New"), strategy_name="go"
    )
    assert guard_builtin(ctx) is None

    # Case 2: Local shadowing of stdlib modules.
    # If errors is explicitly imported as a local path, resolve_explicit_imports should resolve it.
    graph_nodes = {
        "myerrors.go": {"type": "file"},
        "myerrors.go::New": {
            "type": "function",
            "label": "New",
            "source_file": "myerrors.go",
        },
    }
    ctx = make_test_context(
        callee_name="errors.New",
        parts=("errors", "New"),
        strategy_name="go",
        imported_symbols={"errors": ("myerrors.go", "*")},
        node_ids=set(graph_nodes.keys()),
        graph_nodes=graph_nodes,
    )
    assert resolve_explicit_imports(ctx) == "myerrors.go::New"


def test_resolve_global_fallback_stdlib_and_external_imports():
    # Case 1: errors is a Go stdlib. If it is not resolved locally, resolve_global_fallback should return None (intercepted)
    ctx = make_test_context(
        callee_name="errors.New",
        parts=("errors", "New"),
        strategy_name="go",
        global_symbol_map={"New": ["other.go::New"]},
    )
    assert resolve_global_fallback(ctx) is None

    # Case 2: requests is a Python third-party library.
    # It is in imported_symbols, but we have no local source file for it.
    # resolve_global_fallback should intercept and return None instead of guessing "get" from other.py
    graph_nodes = {
        "other.py::get": {"type": "function", "label": "get", "source_file": "other.py"}
    }
    ctx = make_test_context(
        callee_name="requests.get",
        parts=("requests", "get"),
        strategy_name="python",
        imported_symbols={"requests": ("requests", "requests")},
        global_symbol_map={"get": ["other.py::get"]},
        node_ids=set(graph_nodes.keys()),
        graph_nodes=graph_nodes,
    )
    assert resolve_global_fallback(ctx) is None
