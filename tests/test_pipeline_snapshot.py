"""Pipeline snapshot reuse (no-dirty rebuild fast path)."""

from __future__ import annotations

from pathlib import Path

from codegraph_gen.analyzer import AnalysisResult, ResolutionStats
from codegraph_gen.builder import build_graph
from codegraph_gen.config import CodegraphConfig
from codegraph_gen.engine import CodegraphEngine
from codegraph_gen.incremental import (
    load_pipeline_snapshot,
    save_pipeline_snapshot,
    workspace_fingerprint,
)
from codegraph_gen.parser import get_parser


def test_workspace_fingerprint_stable():
    a = workspace_fingerprint(
        {"a.py": "h1", "b.py": "h2"},
        languages={"python"},
        exclusions={".git"},
        include_dirs=["src"],
        naming_mode="hybrid",
        exclude_tests_from_clustering=True,
    )
    b = workspace_fingerprint(
        {"b.py": "h2", "a.py": "h1"},
        languages={"python"},
        exclusions={".git"},
        include_dirs=["src"],
        naming_mode="hybrid",
        exclude_tests_from_clustering=True,
    )
    assert a == b
    c = workspace_fingerprint(
        {"a.py": "h1", "b.py": "CHANGED"},
        languages={"python"},
        exclusions={".git"},
        include_dirs=["src"],
        naming_mode="hybrid",
        exclude_tests_from_clustering=True,
    )
    assert a != c


def test_save_load_pipeline_snapshot_roundtrip(tmp_path: Path):
    src = tmp_path / "m.py"
    src.write_text(
        """
class A:
    def run(self):
        pass

def main():
    A().run()
""",
        encoding="utf-8",
    )
    ext = get_parser("python").parse_file(src, tmp_path)
    G = build_graph([ext], tmp_path)
    components = {0: list(G.nodes())}
    cohesion = {0: 0.5}
    names = {0: "main (A)"}
    analysis = AnalysisResult(
        god_nodes=[],
        cycles=[],
        inter_comp_deps={0: {0: 1}},
        resolution=G.graph.get("resolution_stats"),
    )
    if analysis.resolution is None:
        analysis.resolution = ResolutionStats()

    fp = "abc123"
    path = tmp_path / "pipeline_snapshot.json"
    save_pipeline_snapshot(
        path,
        fingerprint=fp,
        G=G,
        components=components,
        cohesion_scores=cohesion,
        component_names=names,
        analysis=analysis,
    )

    assert load_pipeline_snapshot(path, "wrong") is None
    snap = load_pipeline_snapshot(path, fp)
    assert snap is not None
    assert snap.graph.number_of_nodes() == G.number_of_nodes()
    assert snap.graph.number_of_edges() == G.number_of_edges()
    assert snap.components[0] == components[0]
    assert snap.component_names[0] == "main (A)"
    assert snap.analysis.inter_comp_deps[0][0] == 1
    assert snap.analysis.resolution is not None


def test_engine_reuses_snapshot_on_second_build(tmp_path: Path):
    src = tmp_path / "app.py"
    src.write_text("def hello():\n    return 1\n", encoding="utf-8")
    out = tmp_path / ".codegraph"
    config = CodegraphConfig(
        workspace_dir=tmp_path,
        output_dir=out,
        languages={"python"},
        use_cache=True,
        max_workers=1,
        export_json=False,
    )
    engine = CodegraphEngine()
    r1 = engine.run_pipeline(config)
    assert r1.graph.number_of_nodes() >= 2
    assert not r1.graph.graph.get("pipeline_reused_snapshot")
    assert (out / "pipeline_snapshot.json").is_file()

    r2 = engine.run_pipeline(config)
    assert r2.graph.graph.get("pipeline_reused_snapshot") is True
    assert r2.graph.number_of_nodes() == r1.graph.number_of_nodes()
    assert r2.components == r1.components


def test_engine_rebuilds_when_source_changes(tmp_path: Path):
    src = tmp_path / "app.py"
    src.write_text("def hello():\n    return 1\n", encoding="utf-8")
    out = tmp_path / ".codegraph"
    config = CodegraphConfig(
        workspace_dir=tmp_path,
        output_dir=out,
        languages={"python"},
        use_cache=True,
        max_workers=1,
        export_json=False,
    )
    engine = CodegraphEngine()
    engine.run_pipeline(config)

    src.write_text(
        "def hello():\n    return 1\n\ndef world():\n    return 2\n",
        encoding="utf-8",
    )
    r2 = engine.run_pipeline(config)
    assert not r2.graph.graph.get("pipeline_reused_snapshot")
    labels = {d.get("label") for _, d in r2.graph.nodes(data=True)}
    assert "world" in labels
