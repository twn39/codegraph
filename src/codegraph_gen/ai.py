import logging
import networkx as nx

logger = logging.getLogger(__name__)


def build_agent_prompt(
    G: nx.DiGraph,
    components: dict[int, list[str]],
    cohesion_scores: dict[int, float],
    component_names: dict[int, str],
    god_nodes: list[dict],
    cycles: list[list[str]],
    mermaid_graph: str,
    *,
    resolution_summary: str = "",
) -> str:
    """
    Constructs a detailed architectural prompt designed for external AI agents
    (such as Antigravity, Claude Code, or Codex) to read and perform deep
    architectural analysis.
    """
    logger.info("Generating agent analysis prompt based on graph structure...")

    # Format metadata lists
    files_count = sum(1 for _, d in G.nodes(data=True) if d.get("type") == "file")
    symbols_count = G.number_of_nodes() - files_count

    comp_list = []
    for cid, members in components.items():
        comp_list.append(
            f"- {component_names[cid]}: cohesion={cohesion_scores[cid]:.4f}, members_count={len(members)}"
        )
    comp_str = "\n".join(comp_list)

    god_list = []
    for node in god_nodes:
        sf = G.nodes[node["id"]].get("source_file", "")
        god_list.append(
            f"- {node['label']} ({node['type']}): degree={node['degree']}, file={sf}"
        )
    god_str = "\n".join(god_list)

    cycle_list = []
    for c in cycles:
        cycle_list.append(" -> ".join(c + [c[0]]))
    cycle_str = "\n".join(cycle_list) if cycle_list else "No circular dependencies"

    resolution_block = (
        f"\n{resolution_summary}\n" if resolution_summary.strip() else "\n"
    )

    prompt = f"""# Codebase Architecture Analysis Prompt

You are a senior software architecture expert. Based on the codebase knowledge graph metadata and relationships between major components provided below, write a profound "AI Architectural Insights Report" for this project (written in English).

[Codebase Graph Statistics]
- Number of physical files: {files_count}
- Number of symbols (classes/structs/functions/methods): {symbols_count}
- Total number of dependency and call edges: {G.number_of_edges()}
{resolution_block}
[Modularity Components]
{comp_str}

[Component Dependency Graph (Mermaid Flowchart)]
```mermaid
{mermaid_graph}
```

[God Nodes (degree represents the total number of connected symbols)]
{god_str}

[File-level Circular Import Dependencies (Import Cycles)]
{cycle_str}

Please provide deep architectural insights based on the codebase structure and component relationships, focusing on the following three aspects:
1. **System Architecture Evaluation**: Explain the design patterns, modularity level, and alignment between physical directories and logical components in the codebase.
2. **Core Abstractions & Boundary Evaluation**: Deeply analyze God Nodes to determine which ones are core support and which ones have excessive responsibilities (God Object / Fat Class) that may lead to high risk.
3. **Potential Bottlenecks & Architectural Refactoring Recommendations**: Point out high-coupling risk points and negative impacts of circular dependencies, and provide specific, actionable refactoring optimization plans (e.g., decoupling, extracting interfaces, dependency inversion). Prefer *internal* resolve-rate metrics over full resolve rate when judging graph quality (external/stdlib edges are expected to remain unresolved).

Please output in standard Markdown format, clear and professional, without code block wrapper markers like ```markdown and ``` at the beginning and end. Output the content directly.
"""
    return prompt
