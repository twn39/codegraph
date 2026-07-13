import logging
from typing import Any

import networkx as nx
from pydantic import BaseModel, Field

from codegraph_gen.paths_util import is_test_path

logger = logging.getLogger(__name__)


class ResolutionStats(BaseModel):
    """Edge resolution quality metrics collected during TypeResolver.resolve_all_edges."""

    attempted: int = 0
    resolved: int = 0
    unresolved: int = 0
    by_relation: dict[str, dict[str, int]] = Field(default_factory=dict)
    by_category: dict[str, dict[str, int]] = Field(default_factory=dict)
    unresolved_samples: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def resolve_rate(self) -> float:
        if self.attempted == 0:
            return 1.0
        return self.resolved / self.attempted

    def _category_bucket(self, category: str) -> dict[str, int]:
        return self.by_category.get(category, {})

    @property
    def internal_attempted(self) -> int:
        return int(self._category_bucket("internal").get("attempted", 0))

    @property
    def internal_resolved(self) -> int:
        return int(self._category_bucket("internal").get("resolved", 0))

    @property
    def internal_unresolved(self) -> int:
        return int(self._category_bucket("internal").get("unresolved", 0))

    @property
    def internal_resolve_rate(self) -> float:
        att = self.internal_attempted
        if att == 0:
            return 1.0
        return self.internal_resolved / att

    def category_unresolved(self, category: str) -> int:
        return int(self._category_bucket(category).get("unresolved", 0))


class AnalysisResult(BaseModel):
    god_nodes: list[dict]
    cycles: list[list[str]]
    inter_comp_deps: dict[int, dict[int, int]]
    resolution: ResolutionStats | None = None
    call_cycles: list[list[str]] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)


def find_god_nodes(
    G: nx.DiGraph,
    top_n: int = 10,
    *,
    exclude_tests: bool = True,
) -> list[dict]:
    """
    Identifies the most connected nodes (highest degree) in the codebase.
    Optionally excludes symbols defined under test paths so integration tests
    do not dominate the architecture report.
    """
    degrees = dict(G.degree())
    candidates: list[tuple[str, int]] = []
    for nid, deg in degrees.items():
        if exclude_tests:
            sf = G.nodes[nid].get("source_file", "") or ""
            if is_test_path(sf) or (
                G.nodes[nid].get("type") == "file" and is_test_path(nid)
            ):
                continue
        candidates.append((nid, deg))

    candidates.sort(key=lambda item: item[1], reverse=True)

    god_nodes = []
    for nid, deg in candidates[:top_n]:
        node_data = G.nodes[nid]
        in_deg = G.in_degree(nid)
        out_deg = G.out_degree(nid)
        god_nodes.append(
            {
                "id": nid,
                "label": node_data.get("label", nid),
                "type": node_data.get("type", "unknown"),
                "degree": deg,
                "in_degree": int(in_deg),
                "out_degree": int(out_deg),
            }
        )

    return god_nodes


def find_import_cycles(G: nx.DiGraph) -> list[list[str]]:
    """Detects circular imports at the file level in the graph."""
    file_nodes = [n for n, d in G.nodes(data=True) if d.get("type") == "file"]
    file_subgraph = G.subgraph(file_nodes).copy()

    non_import_edges = [
        (u, v)
        for u, v, d in file_subgraph.edges(data=True)
        if d.get("relation") != "imports"
    ]
    file_subgraph.remove_edges_from(non_import_edges)

    try:
        cycles = list(nx.simple_cycles(file_subgraph))
        cycles.sort(key=len)
        return cycles
    except Exception as e:
        logger.error(f"Error finding import cycles: {e}")
        return []


def find_call_cycles(G: nx.DiGraph, max_cycles: int = 20) -> list[list[str]]:
    """Detect multi-node call cycles among non-file symbols (capped).

    Single-node self-recursion (``f → f``) is filtered out — those are
    legitimate recursive calls, not architectural mutual coupling.
    """
    symbol_nodes = [n for n, d in G.nodes(data=True) if d.get("type") != "file"]
    if not symbol_nodes:
        return []

    call_edges = [
        (u, v)
        for u, v, d in G.edges(data=True)
        if d.get("relation") == "calls"
        and u in symbol_nodes
        and v in symbol_nodes
        and u != v
    ]
    if not call_edges:
        return []

    H = nx.DiGraph()
    H.add_nodes_from({n for edge in call_edges for n in edge})
    H.add_edges_from(call_edges)

    try:
        cycles: list[list[str]] = []
        for cycle in nx.simple_cycles(H):
            # Only multi-node mutual cycles (length >= 2)
            if 2 <= len(cycle) <= 8:
                cycles.append(cycle)
            if len(cycles) >= max_cycles:
                break
        cycles.sort(key=len)
        return cycles
    except Exception as e:
        logger.error(f"Error finding call cycles: {e}")
        return []


def calculate_inter_component_dependencies(
    G: nx.DiGraph, components: dict[int, list[str]]
) -> dict[int, dict[int, int]]:
    """Computes dependencies between different components."""
    inter_comp_deps = {cid: {} for cid in components}

    member_to_comp = {}
    for cid, members in components.items():
        for member in members:
            member_to_comp[member] = cid

    for u, v in G.edges():
        u_comp = member_to_comp.get(u)
        v_comp = member_to_comp.get(v)
        if u_comp and v_comp and u_comp != v_comp:
            inter_comp_deps[u_comp][v_comp] = inter_comp_deps[u_comp].get(v_comp, 0) + 1

    return inter_comp_deps


def _compute_extra_metrics(G: nx.DiGraph) -> dict[str, Any]:
    file_count = sum(1 for _, d in G.nodes(data=True) if d.get("type") == "file")
    symbol_count = G.number_of_nodes() - file_count
    relation_counts: dict[str, int] = {}
    for _, _, d in G.edges(data=True):
        rel = d.get("relation", "unknown")
        relation_counts[rel] = relation_counts.get(rel, 0) + 1
    test_files = sum(
        1
        for n, d in G.nodes(data=True)
        if d.get("type") == "file" and is_test_path(n)
    )
    return {
        "file_count": file_count,
        "symbol_count": symbol_count,
        "edge_count": G.number_of_edges(),
        "relation_counts": relation_counts,
        "test_file_count": test_files,
    }


def analyze_graph(G: nx.DiGraph, components: dict[int, list[str]]) -> AnalysisResult:
    """Runs full architectural metric analysis on the graph."""
    logger.info("Analyzing codebase graph metrics...")
    god_nodes = find_god_nodes(G, 10, exclude_tests=True)
    cycles = find_import_cycles(G)
    call_cycles = find_call_cycles(G)
    inter_comp_deps = calculate_inter_component_dependencies(G, components)

    resolution = None
    raw_stats = G.graph.get("resolution_stats")
    if isinstance(raw_stats, ResolutionStats):
        resolution = raw_stats
    elif isinstance(raw_stats, dict):
        resolution = ResolutionStats(**raw_stats)

    metrics = _compute_extra_metrics(G)

    return AnalysisResult(
        god_nodes=god_nodes,
        cycles=cycles,
        inter_comp_deps=inter_comp_deps,
        resolution=resolution,
        call_cycles=call_cycles,
        metrics=metrics,
    )
