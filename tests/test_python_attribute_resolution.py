"""Python attribute / method-call resolution quality tests."""

from __future__ import annotations

from pathlib import Path

from codegraph_gen.builder import build_graph
from codegraph_gen.parser.python import PythonParser


def _build(workspace: Path, files: dict[str, str]):
    parser = PythonParser()
    extractions = []
    for rel, source in files.items():
        path = workspace / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source.strip() + "\n", encoding="utf-8")
        extractions.append(parser.parse_file(path, workspace))
    return build_graph(extractions, workspace)


def test_optional_and_union_type_bindings(tmp_path: Path):
    G = _build(
        tmp_path,
        {
            "a.py": """
from typing import Optional, Union

class Client:
    def ping(self) -> str:
        return "ok"

def a(c: Optional[Client]):
    c.ping()

def b(c: Client | None):
    c.ping()

def c(c: Union[Client, None]):
    c.ping()

def d(c: "Client"):
    c.ping()
""",
        },
    )
    for fn in ("a", "b", "c", "d"):
        assert G.has_edge(f"a.py::{fn}", "a.py::Client.ping"), fn
        bindings = G.nodes[f"a.py::{fn}"].get("local_bindings", {})
        assert "c" in bindings
        assert "Client" in bindings["c"] or bindings["c"].endswith("::Client")


def test_return_type_call_chain(tmp_path: Path):
    G = _build(
        tmp_path,
        {
            "a.py": """
class Inner:
    def value(self) -> int:
        return 1

class Outer:
    def inner(self) -> Inner:
        return Inner()

def work(o: Outer):
    return o.inner().value()
""",
        },
    )
    assert G.has_edge("a.py::work", "a.py::Outer.inner")
    assert G.has_edge("a.py::work", "a.py::Inner.value")


def test_cross_file_package_method_call(tmp_path: Path):
    G = _build(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/client.py": """
class Client:
    def ping(self) -> str:
        return "ok"
""",
            "app.py": """
from pkg.client import Client

def work():
    c = Client()
    return c.ping()
""",
        },
    )
    assert G.has_edge("app.py::work", "pkg/client.py::Client.ping")


def test_relative_import_method_call(tmp_path: Path):
    G = _build(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/client.py": """
class Client:
    def ping(self) -> str:
        return "ok"
""",
            "pkg/app.py": """
from .client import Client

def work():
    c = Client()
    return c.ping()
""",
        },
    )
    assert G.has_edge("pkg/app.py::work", "pkg/client.py::Client.ping")


def test_factory_and_constructor_inference(tmp_path: Path):
    G = _build(
        tmp_path,
        {
            "a.py": """
class Client:
    def ping(self) -> str:
        return "ok"

def make() -> Client:
    return Client()

def work():
    c = make()
    d = Client()
    return c.ping(), d.ping()
""",
        },
    )
    assert G.has_edge("a.py::work", "a.py::Client.ping")
