"""AGENT_PROMPT report language selection."""

from pathlib import Path

import networkx as nx

from codegraph_gen.analyzer import AnalysisResult, ResolutionStats
from codegraph_gen.renderer import MarkdownRenderer


def _minimal_graph() -> tuple:
    G = nx.DiGraph()
    G.add_node(
        "a.py",
        label="a.py",
        type="file",
        source_file="a.py",
        line_start=1,
        line_end=1,
        signature="",
        docstring="",
    )
    G.add_node(
        "a.py::f",
        label="f",
        type="function",
        source_file="a.py",
        line_start=1,
        line_end=2,
        signature="def f():",
        docstring="",
    )
    G.add_edge("a.py", "a.py::f", relation="contains")
    components = {0: ["a.py", "a.py::f"]}
    cohesion = {0: 1.0}
    names = {0: "a.py (f)"}
    analysis = AnalysisResult(
        god_nodes=[],
        cycles=[],
        inter_comp_deps={},
        resolution=ResolutionStats(attempted=1, resolved=1, unresolved=0),
    )
    return G, components, cohesion, names, analysis


def test_agent_prompt_english_and_chinese(tmp_path: Path):
    G, components, cohesion, names, analysis = _minimal_graph()
    renderer = MarkdownRenderer(tmp_path)

    en = renderer.render_agent_prompt(
        G, components, cohesion, names, analysis, language="en"
    )
    assert "You are a senior software architect" in en
    assert "## AI Architectural Insights" in en
    assert "No circular imports" in en

    zh = renderer.render_agent_prompt(
        G, components, cohesion, names, analysis, language="zh"
    )
    assert "资深的软件架构专家" in zh
    assert "## AI Architectural Insights" in zh
    assert "无循环依赖" in zh
