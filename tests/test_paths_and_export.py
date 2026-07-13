"""Tests for path heuristics, resolution stats, clustering, and JSON export."""

from pathlib import Path

from codegraph_gen.analyzer import analyze_graph, find_god_nodes
from codegraph_gen.builder import build_graph
from codegraph_gen.cluster import detect_components
from codegraph_gen.export_json import graph_to_export_dict, write_graph_json
from codegraph_gen.parser import get_parser
from codegraph_gen.paths_util import is_test_path
from codegraph_gen.schema import EdgeSchema, ExtractionResult, NodeSchema


def _mini_graph(tmp_path: Path):
    prod = tmp_path / "src" / "app.py"
    test = tmp_path / "tests" / "test_app.py"
    prod.parent.mkdir(parents=True)
    test.parent.mkdir(parents=True)
    prod.write_text(
        """
class Core:
    def run(self):
        helper()

def helper():
    pass
""",
        encoding="utf-8",
    )
    test.write_text(
        """
from app import Core

def test_core():
    Core().run()
""",
        encoding="utf-8",
    )
    parser = get_parser("python")
    extractions = [
        parser.parse_file(prod, tmp_path),
        parser.parse_file(test, tmp_path),
    ]
    G = build_graph(extractions, tmp_path)
    return G


def test_god_nodes_exclude_tests(tmp_path: Path):
    G = _mini_graph(tmp_path)
    gods = find_god_nodes(G, top_n=20, exclude_tests=True)
    for g in gods:
        sf = G.nodes[g["id"]].get("source_file", "")
        assert not is_test_path(sf)


def test_cluster_assigns_tests(tmp_path: Path):
    G = _mini_graph(tmp_path)
    components, scores, names = detect_components(G, exclude_tests_from_clustering=True)
    all_members = [m for members in components.values() for m in members]
    assert len(all_members) == G.number_of_nodes()
    assert scores
    assert names


def test_resolution_stats_on_graph(tmp_path: Path):
    G = _mini_graph(tmp_path)
    assert "resolution_stats" in G.graph
    stats = G.graph["resolution_stats"]
    assert stats.attempted >= stats.resolved
    analysis = analyze_graph(G, {1: list(G.nodes)})
    assert analysis.resolution is not None
    assert "in_degree" in analysis.god_nodes[0] if analysis.god_nodes else True


def test_export_json(tmp_path: Path):
    G = _mini_graph(tmp_path)
    components, cohesion, names = detect_components(G)
    analysis = analyze_graph(G, components)
    payload = graph_to_export_dict(G, components, cohesion, names, analysis)
    out = tmp_path / "out" / "graph.json"
    write_graph_json(out, payload)
    assert out.exists()
    assert payload["statistics"]["nodes"] == G.number_of_nodes()
    assert "resolution" in payload


def test_contains_edge_stats_count():
    """contains edges between known IDs should count as resolved."""
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
            EdgeSchema(source="a.py::f", target="missing", relation="calls"),
        ],
    )
    import networkx as nx
    from codegraph_gen.resolver import TypeResolver

    G = nx.DiGraph()
    for n in ext.nodes:
        G.add_node(n.id, **n.model_dump())
    resolver = TypeResolver(G, [ext], Path("."))
    stats = resolver.resolve_all_edges()
    assert stats.attempted == 2
    assert stats.resolved == 1
    assert stats.unresolved == 1
