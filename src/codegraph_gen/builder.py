import logging
from pathlib import Path
import networkx as nx
from codegraph_gen.schema import ExtractionResult
from codegraph_gen.resolver import TypeResolver

logger = logging.getLogger(__name__)


def build_graph(extractions: list[ExtractionResult], workspace_dir: Path) -> nx.DiGraph:
    """
    Assembles a list of ExtractionResults into a single directed graph
    and resolves call, inherit, and import edges using a two-pass scope resolver.
    """
    G = nx.DiGraph()

    # 1. Add all nodes to the graph in batch
    G.add_nodes_from(
        (node.id, node.__dict__) for ext in extractions for node in ext.nodes
    )

    # 2. Run Type Resolver (Two-pass type inference & scope/edge resolution)
    resolver = TypeResolver(G, extractions, workspace_dir)
    resolver.propagate_types()
    resolver.resolve_all_edges()

    return G
