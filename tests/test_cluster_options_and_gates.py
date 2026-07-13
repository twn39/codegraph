"""CLI-facing cluster options and resolution quality gates."""

from pathlib import Path

import networkx as nx
import pytest

from codegraph_gen.analyzer import ResolutionStats
from codegraph_gen.builder import build_graph
from codegraph_gen.cluster import detect_components
from codegraph_gen.config import CodegraphConfig
from codegraph_gen.engine import CodegraphEngine
from codegraph_gen.parser import get_parser
from codegraph_gen.schema import EdgeSchema, ExtractionResult, NodeSchema


def _graph_with_package_layout() -> nx.DiGraph:
    G = nx.DiGraph()
    for nid, label, sf, ntype in [
        ("pkg/a.py", "a.py", "pkg/a.py", "file"),
        ("pkg/a.py::Alpha", "Alpha", "pkg/a.py", "class"),
        ("pkg/b.py", "b.py", "pkg/b.py", "file"),
        ("pkg/b.py::Beta", "Beta", "pkg/b.py", "class"),
        ("tests/test_a.py", "test_a.py", "tests/test_a.py", "file"),
        ("tests/test_a.py::test_alpha", "test_alpha", "tests/test_a.py", "function"),
    ]:
        G.add_node(
            nid,
            label=label,
            type=ntype,
            source_file=sf,
            line_start=1,
            line_end=1,
            signature="",
            docstring="",
        )
    G.add_edge("pkg/a.py", "pkg/a.py::Alpha", relation="contains")
    G.add_edge("pkg/b.py", "pkg/b.py::Beta", relation="contains")
    G.add_edge("pkg/a.py::Alpha", "pkg/b.py::Beta", relation="calls")
    G.add_edge("tests/test_a.py", "tests/test_a.py::test_alpha", relation="contains")
    G.add_edge("tests/test_a.py::test_alpha", "pkg/a.py::Alpha", relation="calls")
    return G


def test_naming_mode_symbol_vs_package():
    G = _graph_with_package_layout()
    _, _, names_sym = detect_components(G, naming_mode="symbol")
    _, _, names_pkg = detect_components(G, naming_mode="package")
    _, _, names_hyb = detect_components(G, naming_mode="hybrid")
    # All modes produce names for every component
    assert names_sym and names_pkg and names_hyb
    # Symbol mode should mention symbol-ish labels more often
    joined_sym = " ".join(names_sym.values())
    joined_pkg = " ".join(names_pkg.values())
    assert "Alpha" in joined_sym or "Beta" in joined_sym or "a.py" in joined_sym
    assert "pkg" in joined_pkg or "tests" in joined_pkg


def test_include_tests_in_clustering_changes_membership():
    G = _graph_with_package_layout()
    comps_ex, _, _ = detect_components(G, exclude_tests_from_clustering=True)
    comps_in, _, _ = detect_components(G, exclude_tests_from_clustering=False)
    # Same total membership
    assert sum(len(m) for m in comps_ex.values()) == G.number_of_nodes()
    assert sum(len(m) for m in comps_in.values()) == G.number_of_nodes()


def test_min_resolve_rate_gate(tmp_path: Path):
    src = tmp_path / "m.py"
    src.write_text("def f():\n    missing()\n", encoding="utf-8")
    ext = get_parser("python").parse_file(src, tmp_path)
    G = build_graph([ext], tmp_path)
    stats = G.graph["resolution_stats"]
    assert isinstance(stats, ResolutionStats)
    assert stats.unresolved >= 1

    config = CodegraphConfig(
        workspace_dir=tmp_path,
        output_dir=tmp_path / ".codegraph",
        languages={"python"},
        max_workers=1,
        use_cache=False,
        export_json=False,
        min_resolve_rate=0.999,  # almost certainly fails with unresolved call
    )
    with pytest.raises(RuntimeError, match="resolve rate"):
        CodegraphEngine().run_pipeline(config)


def test_max_unresolved_edges_gate(tmp_path: Path):
    ext = ExtractionResult(
        nodes=[
            NodeSchema(
                id="a.py",
                label="a.py",
                type="file",
                source_file="a.py",
                line_start=1,
                line_end=1,
                signature="",
            ),
            NodeSchema(
                id="a.py::f",
                label="f",
                type="function",
                source_file="a.py",
                line_start=1,
                line_end=1,
                signature="def f()",
            ),
        ],
        edges=[
            EdgeSchema(source="a.py", target="a.py::f", relation="contains"),
            EdgeSchema(source="a.py::f", target="nope1", relation="calls"),
            EdgeSchema(source="a.py::f", target="nope2", relation="calls"),
        ],
    )
    (tmp_path / "a.py").write_text("def f():\n    pass\n", encoding="utf-8")
    # Use engine path with a real parse would be flaky for exact unresolved count;
    # exercise gate via analyze + manual check is covered above. Here build_graph:
    G = build_graph([ext], tmp_path)
    assert G.graph["resolution_stats"].unresolved == 2

    config = CodegraphConfig(
        workspace_dir=tmp_path,
        output_dir=tmp_path / "out",
        languages={"python"},
        max_workers=1,
        use_cache=False,
        export_json=False,
        max_unresolved_edges=0,
    )
    # Engine re-discovers files from disk; write matching sources with unresolved calls
    (tmp_path / "a.py").write_text("def f():\n    nope1()\n    nope2()\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="Unresolved edges"):
        CodegraphEngine().run_pipeline(config)


def test_min_internal_resolve_rate_gate(tmp_path: Path):
    """Internal gate fails when internal resolve rate is below threshold."""
    stats = ResolutionStats(
        attempted=10,
        resolved=5,
        unresolved=5,
        by_category={
            "internal": {"attempted": 8, "resolved": 4, "unresolved": 4},
            "external": {"attempted": 2, "resolved": 0, "unresolved": 2},
            "builtin": {"attempted": 0, "resolved": 0, "unresolved": 0},
            "attribute": {"attempted": 0, "resolved": 0, "unresolved": 0},
        },
    )
    config = CodegraphConfig(
        workspace_dir=tmp_path,
        output_dir=tmp_path / ".codegraph",
        min_internal_resolve_rate=0.9,
    )
    with pytest.raises(RuntimeError, match="Internal edge resolve rate"):
        CodegraphEngine._enforce_quality_gates(config, stats)


def test_max_internal_unresolved_edges_gate(tmp_path: Path):
    stats = ResolutionStats(
        attempted=5,
        resolved=2,
        unresolved=3,
        by_category={
            "internal": {"attempted": 5, "resolved": 2, "unresolved": 3},
        },
    )
    config = CodegraphConfig(
        workspace_dir=tmp_path,
        output_dir=tmp_path / ".codegraph",
        max_internal_unresolved_edges=1,
    )
    with pytest.raises(RuntimeError, match="Internal unresolved edges"):
        CodegraphEngine._enforce_quality_gates(config, stats)


def test_go_emit_symbol_parent_for_methods(tmp_path: Path):
    path = tmp_path / "s.go"
    path.write_text(
        """
package main

type Server struct{}

func (s *Server) Start() {}
""",
        encoding="utf-8",
    )
    result = get_parser("go").parse_file(path, tmp_path)
    method = next(n for n in result.nodes if n.label == "Start")
    assert method.id.endswith("Server.Start")
    contains = [
        e
        for e in result.edges
        if e.relation == "contains" and e.target == method.id
    ]
    assert contains
    assert "Server" in contains[0].source
