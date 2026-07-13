import logging
import os
from collections import Counter

import networkx as nx
from networkx.algorithms.community import louvain_communities

from codegraph_gen.paths_util import is_test_path

logger = logging.getLogger(__name__)


def _node_is_test(G: nx.DiGraph, nid: str) -> bool:
    data = G.nodes[nid]
    sf = data.get("source_file") or ""
    if is_test_path(sf):
        return True
    if data.get("type") == "file" and is_test_path(nid):
        return True
    return False


def _prefer_production_central(
    G: nx.DiGraph, members: list[str]
) -> str:
    """Pick naming hub preferring non-test, non-file symbols with high degree."""
    degrees = dict(G.degree(members))

    def sort_key(n: str) -> tuple:
        data = G.nodes[n]
        is_test = 1 if _node_is_test(G, n) else 0
        is_file = 1 if data.get("type") == "file" else 0
        # Prefer: production symbols > production files > tests; higher degree first
        return (is_test, is_file, -degrees.get(n, 0), n)

    return sorted(members, key=sort_key)[0]


def _package_hint(G: nx.DiGraph, members: list[str]) -> str:
    """Longest common directory among member source files."""
    paths: list[str] = []
    for m in members:
        sf = G.nodes[m].get("source_file")
        if sf:
            dir_path = os.path.dirname(sf)
            if dir_path:
                paths.append(dir_path)
    if not paths:
        return ""
    try:
        common_dir = os.path.commonpath(paths)
        if common_dir in (".", "", "/"):
            return ""
        return common_dir
    except ValueError:
        return ""


def _short_package(common_dir: str) -> str:
    parts = common_dir.replace("\\", "/").split("/")
    return "/".join(parts[-2:]) if len(parts) > 2 else common_dir


def _candidate_name(
    clean_name: str,
    common_dir: str,
    *,
    naming_mode: str,
) -> str:
    """
    naming_mode:
      - package: prefer directory path
      - symbol: prefer central symbol label
      - hybrid: package when present, else symbol (default)
    """
    mode = (naming_mode or "hybrid").lower()
    if mode == "symbol":
        return clean_name
    if mode == "package":
        return _short_package(common_dir) if common_dir else clean_name
    # hybrid
    if common_dir:
        return _short_package(common_dir)
    return clean_name


def detect_components(
    G: nx.DiGraph,
    *,
    exclude_tests_from_clustering: bool = True,
    naming_mode: str = "hybrid",
) -> tuple[dict[int, list[str]], dict[int, float], dict[int, str]]:
    """
    Detects logical components using modularity clustering.

    When *exclude_tests_from_clustering* is True, test-path nodes are removed
    from the Louvain graph and later assigned to the production component they
    couple to most strongly (or a dedicated tests component).
    """
    if G.number_of_nodes() == 0:
        return {}, {}, {}

    test_nodes: list[str] = []
    cluster_nodes: list[str] = []
    for nid in G.nodes:
        if exclude_tests_from_clustering and _node_is_test(G, nid):
            test_nodes.append(nid)
        else:
            cluster_nodes.append(nid)

    if not cluster_nodes:
        # Degenerate: only tests — cluster everything
        cluster_nodes = list(G.nodes)
        test_nodes = []

    U = nx.Graph()
    U.add_nodes_from(cluster_nodes)

    cluster_set = set(cluster_nodes)

    def _package_dir(nid: str) -> str:
        sf = G.nodes[nid].get("source_file") or ""
        if not sf and G.nodes[nid].get("type") == "file":
            sf = nid
        return os.path.dirname(sf.replace("\\", "/")) if sf else ""

    for u, v, d in G.edges(data=True):
        if u not in cluster_set or v not in cluster_set:
            continue
        relation = d.get("relation")
        if relation == "contains":
            weight = 10.0
        elif relation == "imports":
            weight = 2.0
        elif relation == "calls":
            weight = 1.0
        else:
            weight = 1.0

        # Package affinity: same-directory symbols prefer the same community
        pu, pv = _package_dir(u), _package_dir(v)
        if pu and pu == pv:
            weight *= 1.5

        if U.has_edge(u, v):
            U[u][v]["weight"] += weight
        else:
            U.add_edge(u, v, weight=weight)

    communities = list(louvain_communities(U, weight="weight", seed=42))
    communities.sort(key=lambda s: (-len(s), sorted(list(s))))

    components: dict[int, list[str]] = {}
    cohesion_scores: dict[int, float] = {}
    component_names: dict[int, str] = {}
    raw_components: list[tuple[int, str, str]] = []
    member_to_comp: dict[str, int] = {}

    for idx, member_set in enumerate(communities, start=1):
        members = list(member_set)
        components[idx] = members
        for m in members:
            member_to_comp[m] = idx

        subgraph = G.subgraph(members)
        cohesion_scores[idx] = round(nx.density(subgraph), 2)

        central = _prefer_production_central(G, members)
        node_label = G.nodes[central].get("label", central)
        clean_name = str(node_label).replace("()", "").split(".")[0]
        common_dir = _package_hint(G, members)
        raw_components.append((idx, clean_name, common_dir))

    # Assign test nodes to best-matching production component
    if test_nodes:
        unassigned: list[str] = []
        for tn in test_nodes:
            scores: Counter[int] = Counter()
            for nbr in set(G.successors(tn)) | set(G.predecessors(tn)):
                cid = member_to_comp.get(nbr)
                if cid is not None:
                    scores[cid] += 1
            if scores:
                best = scores.most_common(1)[0][0]
                components[best].append(tn)
                member_to_comp[tn] = best
            else:
                unassigned.append(tn)

        if unassigned:
            new_id = max(components.keys(), default=0) + 1
            components[new_id] = unassigned
            cohesion_scores[new_id] = round(nx.density(G.subgraph(unassigned)), 2)
            raw_components.append((new_id, "tests", "tests"))

        # Recompute cohesion after attaching tests (density may change)
        for idx, members in components.items():
            cohesion_scores[idx] = round(nx.density(G.subgraph(members)), 2)

    candidate_names = []
    for idx, clean_name, common_dir in raw_components:
        cand = _candidate_name(clean_name, common_dir, naming_mode=naming_mode)
        candidate_names.append((idx, clean_name, common_dir, cand))

    name_counts = Counter(c[3] for c in candidate_names)

    for idx, clean_name, common_dir, cand in candidate_names:
        if name_counts[cand] == 1:
            component_names[idx] = cand
        elif common_dir and naming_mode != "symbol":
            component_names[idx] = f"{cand} ({clean_name})"
        elif naming_mode == "symbol" and common_dir:
            component_names[idx] = f"{clean_name} ({_short_package(common_dir)})"
        else:
            component_names[idx] = f"{clean_name} (Component {idx})"

    return components, cohesion_scores, component_names
