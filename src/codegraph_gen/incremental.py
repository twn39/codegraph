"""Incremental build helpers: parse cache, vault signature skip, pipeline snapshot.

Layers
------
1. **Parse cache** (``cache.json`` via engine) — skip re-parsing unchanged files.
2. **Pipeline snapshot** (``pipeline_snapshot.json``) — when *no* files are dirty
   and the workspace fingerprint matches, reuse the previous graph, components,
   and analysis (skip build/resolve/cluster/analyze).
3. **Vault signatures** (``node_signatures.json``) — skip re-rendering Markdown
   pages whose graph neighborhood is unchanged.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import networkx as nx

from codegraph_gen.analyzer import AnalysisResult, ResolutionStats

logger = logging.getLogger(__name__)

SIGNATURE_STORE_NAME = "node_signatures.json"
PIPELINE_SNAPSHOT_NAME = "pipeline_snapshot.json"
SNAPSHOT_VERSION = 1


def _stable_hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def node_neighborhood_signature(G: nx.DiGraph, nid: str, component_name: str) -> str:
    """Hash node attributes + incident edges + component label."""
    data = G.nodes[nid]
    attrs = {
        "label": data.get("label"),
        "type": data.get("type"),
        "source_file": data.get("source_file"),
        "line_start": data.get("line_start"),
        "line_end": data.get("line_end"),
        "signature": data.get("signature"),
        "docstring": data.get("docstring"),
        "component": component_name,
    }
    outs = sorted(
        (str(v), str(d.get("relation", ""))) for _, v, d in G.out_edges(nid, data=True)
    )
    ins = sorted(
        (str(u), str(d.get("relation", ""))) for u, _, d in G.in_edges(nid, data=True)
    )
    return _stable_hash({"attrs": attrs, "out": outs, "in": ins})


def component_signature(
    members: list[str],
    cohesion: float,
    comp_name: str,
    inter_deps: dict[int, int],
) -> str:
    return _stable_hash(
        {
            "name": comp_name,
            "cohesion": cohesion,
            "members": sorted(members),
            "deps": sorted((str(k), v) for k, v in inter_deps.items()),
        }
    )


def load_signature_store(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
    except Exception as e:
        logger.warning("Could not load signature store %s: %s", path, e)
    return {}


def save_signature_store(path: Path, store: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(store, indent=2, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )


def import_dependents(G: nx.DiGraph, dirty_files: set[str]) -> set[str]:
    """Expand dirty source files with direct importers / importees (closure seed)."""
    if not dirty_files:
        return set()
    affected = set(dirty_files)
    for u, v, d in G.edges(data=True):
        if d.get("relation") != "imports":
            continue
        # file -> file edges
        if u in dirty_files:
            affected.add(v)
        if v in dirty_files:
            affected.add(u)
    return affected


def files_touching_node(G: nx.DiGraph, nid: str) -> set[str]:
    data = G.nodes.get(nid, {})
    sf = data.get("source_file")
    out = set()
    if sf:
        out.add(sf)
    if data.get("type") == "file":
        out.add(nid)
    return out


def should_force_render_node(G: nx.DiGraph, nid: str, force_files: set[str]) -> bool:
    if not force_files:
        return False
    return bool(files_touching_node(G, nid) & force_files)


# ── Pipeline snapshot (no-dirty rebuild fast path) ──────────────────────────


def workspace_fingerprint(
    file_hashes: dict[str, str],
    *,
    languages: set[str] | frozenset[str] | list[str],
    exclusions: set[str] | frozenset[str] | list[str],
    include_dirs: list[str] | None,
    naming_mode: str,
    exclude_tests_from_clustering: bool,
) -> str:
    """Stable hash of inputs that affect graph structure / clustering."""
    payload = {
        "files": sorted((k, v) for k, v in file_hashes.items()),
        "languages": sorted(languages),
        "exclusions": sorted(exclusions),
        "include": sorted(include_dirs or []),
        "naming_mode": naming_mode,
        "exclude_tests": exclude_tests_from_clustering,
    }
    return _stable_hash(payload)


@dataclass
class PipelineSnapshot:
    fingerprint: str
    graph: nx.DiGraph
    components: dict[int, list[str]]
    cohesion_scores: dict[int, float]
    component_names: dict[int, str]
    analysis: AnalysisResult


def _int_keyed_dict(raw: dict[Any, Any]) -> dict[int, Any]:
    out: dict[int, Any] = {}
    for k, v in raw.items():
        out[int(k)] = v
    return out


def save_pipeline_snapshot(
    path: Path,
    *,
    fingerprint: str,
    G: nx.DiGraph,
    components: dict[int, list[str]],
    cohesion_scores: dict[int, float],
    component_names: dict[int, str],
    analysis: AnalysisResult,
) -> None:
    """Persist graph + analysis so a no-dirty rebuild can skip resolve/cluster."""
    # Drop non-serializable / ephemeral graph attrs before dump
    graph_attrs = {
        k: v
        for k, v in G.graph.items()
        if k
        not in (
            "_signature_store_path",
            "vault_write_stats",
            "render_stats",
            "parse_errors",
        )
    }
    # ResolutionStats lives on analysis; also keep on graph if present
    res = G.graph.get("resolution_stats")
    if res is not None and hasattr(res, "model_dump"):
        graph_attrs["resolution_stats"] = res.model_dump()

    link = nx.node_link_data(G)
    link["graph"] = graph_attrs

    payload = {
        "version": SNAPSHOT_VERSION,
        "fingerprint": fingerprint,
        "graph": link,
        "components": {str(k): v for k, v in components.items()},
        "cohesion_scores": {str(k): v for k, v in cohesion_scores.items()},
        "component_names": {str(k): v for k, v in component_names.items()},
        "analysis": analysis.model_dump(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    logger.info("Wrote pipeline snapshot → %s", path)


def load_pipeline_snapshot(
    path: Path, expected_fingerprint: str
) -> PipelineSnapshot | None:
    """Load snapshot when fingerprint matches; otherwise return None."""
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("Could not read pipeline snapshot %s: %s", path, e)
        return None

    if payload.get("version") != SNAPSHOT_VERSION:
        logger.info("Pipeline snapshot version mismatch; rebuilding.")
        return None
    if payload.get("fingerprint") != expected_fingerprint:
        logger.info("Pipeline snapshot fingerprint mismatch; rebuilding.")
        return None

    try:
        G = nx.node_link_graph(payload["graph"])
        # Restore ResolutionStats object on graph if serialized as dict
        res_raw = G.graph.get("resolution_stats")
        if isinstance(res_raw, dict):
            G.graph["resolution_stats"] = ResolutionStats.model_validate(res_raw)

        components = {int(k): list(v) for k, v in payload.get("components", {}).items()}
        cohesion_scores = {
            int(k): float(v) for k, v in payload.get("cohesion_scores", {}).items()
        }
        component_names = {
            int(k): str(v) for k, v in payload.get("component_names", {}).items()
        }

        analysis_raw = payload.get("analysis", {})
        # inter_comp_deps keys become strings in JSON
        if "inter_comp_deps" in analysis_raw and isinstance(
            analysis_raw["inter_comp_deps"], dict
        ):
            fixed: dict[int, dict[int, int]] = {}
            for sk, targets in analysis_raw["inter_comp_deps"].items():
                fixed[int(sk)] = {int(tk): int(tv) for tk, tv in targets.items()}
            analysis_raw["inter_comp_deps"] = fixed
        analysis = AnalysisResult.model_validate(analysis_raw)

        logger.info(
            "Reusing pipeline snapshot (nodes=%s, edges=%s).",
            G.number_of_nodes(),
            G.number_of_edges(),
        )
        return PipelineSnapshot(
            fingerprint=expected_fingerprint,
            graph=G,
            components=components,
            cohesion_scores=cohesion_scores,
            component_names=component_names,
            analysis=analysis,
        )
    except Exception as e:
        logger.warning("Failed to restore pipeline snapshot: %s", e)
        return None
