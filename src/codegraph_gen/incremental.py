"""Incremental vault rendering helpers (signature-based skip).

Parse results are already cached per file (see ``engine`` + ``cache.json``).
Full graph assembly and symbol resolution still run on every build so
cross-file bindings stay correct.  This module avoids re-rendering Markdown
pages whose graph neighborhood and component membership are unchanged.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

import networkx as nx

logger = logging.getLogger(__name__)

SIGNATURE_STORE_NAME = "node_signatures.json"


def _stable_hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def node_neighborhood_signature(
    G: nx.DiGraph, nid: str, component_name: str
) -> str:
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
        (str(v), str(d.get("relation", "")))
        for _, v, d in G.out_edges(nid, data=True)
    )
    ins = sorted(
        (str(u), str(d.get("relation", "")))
        for u, _, d in G.in_edges(nid, data=True)
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


def should_force_render_node(
    G: nx.DiGraph, nid: str, force_files: set[str]
) -> bool:
    if not force_files:
        return False
    return bool(files_touching_node(G, nid) & force_files)
