"""Tests for resolution category classification and call-cycle filtering."""

from __future__ import annotations

from pathlib import Path

import networkx as nx

from codegraph_gen.analyzer import find_call_cycles
from codegraph_gen.builder import build_graph
from codegraph_gen.parser import get_parser
from codegraph_gen.resolution_classify import classify_edge_attempt
from codegraph_gen.resolver_strategy.python import PythonStrategy


def test_classify_external_import():
    cat = classify_edge_attempt(
        relation="imports",
        target="json",
        resolved=False,
        global_symbol_map={},
        strategy=PythonStrategy(),
    )
    assert cat == "external"


def test_classify_builtin_method():
    cat = classify_edge_attempt(
        relation="calls",
        target="items.append",
        resolved=False,
        global_symbol_map={},
        strategy=PythonStrategy(),
    )
    assert cat == "builtin"


def test_classify_attribute_chain():
    cat = classify_edge_attempt(
        relation="calls",
        target="client.api.ping",
        resolved=False,
        global_symbol_map={"client": ["a.py::client"]},
        strategy=PythonStrategy(),
    )
    assert cat == "attribute"


def test_classify_internal_simple_name_present():
    cat = classify_edge_attempt(
        relation="calls",
        target="helper",
        resolved=False,
        global_symbol_map={"helper": ["a.py::helper"]},
        strategy=PythonStrategy(),
    )
    assert cat == "internal"


def test_resolved_always_internal():
    cat = classify_edge_attempt(
        relation="calls",
        target="json.loads",
        resolved=True,
        global_symbol_map={},
        strategy=PythonStrategy(),
    )
    assert cat == "internal"


def test_build_graph_populates_category_stats(tmp_path: Path):
    src = tmp_path / "m.py"
    src.write_text(
        """
import json

class Client:
    def ping(self) -> str:
        return "ok"

def work():
    c: Client = Client()
    c.ping()
    json.dumps({})
    missing()
""",
        encoding="utf-8",
    )
    ext = get_parser("python").parse_file(src, tmp_path)
    G = build_graph([ext], tmp_path)
    stats = G.graph["resolution_stats"]
    assert stats.by_category
    assert stats.internal_attempted >= 1
    assert stats.internal_resolve_rate >= 0.0
    # External import noise should not zero out categories
    cats = {s.get("category") for s in stats.unresolved_samples}
    assert cats  # some samples present for unresolved edges


def test_find_call_cycles_filters_self_recursion():
    G = nx.DiGraph()
    for nid, ntype in [
        ("a.py", "file"),
        ("a.py::f", "function"),
        ("a.py::g", "function"),
        ("a.py::h", "function"),
    ]:
        G.add_node(nid, type=ntype, label=nid.split("::")[-1], source_file="a.py")
    G.add_edge("a.py::f", "a.py::f", relation="calls")  # self-recursion
    G.add_edge("a.py::g", "a.py::h", relation="calls")
    G.add_edge("a.py::h", "a.py::g", relation="calls")  # mutual
    cycles = find_call_cycles(G)
    assert all(len(c) >= 2 for c in cycles)
    assert any(set(c) == {"a.py::g", "a.py::h"} for c in cycles)
    assert not any(c == ["a.py::f"] for c in cycles)
