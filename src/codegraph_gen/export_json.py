"""Machine-readable graph export helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import networkx as nx

from codegraph_gen.analyzer import AnalysisResult


def graph_to_export_dict(
    G: nx.DiGraph,
    components: dict[int, list[str]],
    cohesion_scores: dict[int, float],
    component_names: dict[int, str],
    analysis: AnalysisResult,
) -> dict[str, Any]:
    """Serialize the knowledge graph and analysis into a JSON-friendly dict."""
    nodes = []
    for nid, data in G.nodes(data=True):
        nodes.append(
            {
                "id": nid,
                "label": data.get("label", nid),
                "type": data.get("type"),
                "source_file": data.get("source_file"),
                "line_start": data.get("line_start"),
                "line_end": data.get("line_end"),
                "signature": data.get("signature", ""),
            }
        )

    edges = []
    for u, v, data in G.edges(data=True):
        edges.append(
            {
                "source": u,
                "target": v,
                "relation": data.get("relation"),
            }
        )

    comps = []
    for cid, members in components.items():
        comps.append(
            {
                "id": cid,
                "name": component_names.get(cid, f"Component {cid}"),
                "cohesion": cohesion_scores.get(cid, 0.0),
                "size": len(members),
                "members": members,
            }
        )

    resolution = None
    if analysis.resolution is not None:
        resolution = analysis.resolution.model_dump()

    return {
        "statistics": {
            "nodes": G.number_of_nodes(),
            "edges": G.number_of_edges(),
            "files": sum(1 for _, d in G.nodes(data=True) if d.get("type") == "file"),
            "symbols": sum(1 for _, d in G.nodes(data=True) if d.get("type") != "file"),
        },
        "nodes": nodes,
        "edges": edges,
        "components": comps,
        "god_nodes": analysis.god_nodes,
        "import_cycles": analysis.cycles,
        "call_cycles": analysis.call_cycles,
        "resolution": resolution,
        "metrics": analysis.metrics,
        "inter_component_dependencies": {
            str(k): {str(tk): tv for tk, tv in v.items()}
            for k, v in analysis.inter_comp_deps.items()
        },
    }


def write_graph_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
