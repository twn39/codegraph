import re
from pathlib import Path
import networkx as nx
from codegraph_gen.analyzer import AnalysisResult


def get_node_filename(node_id: str) -> str:
    """Safely converts a node ID into a clean markdown filename."""
    cleaned = re.sub(r'[\\/.: *?#^[\]"<>|]+', "_", node_id)
    return cleaned + ".md"


def get_component_filename(component_name: str) -> str:
    """Safely converts a component name into a clean markdown filename."""
    cleaned = re.sub(r'[\\/.: *?#^[\]"<>|()]+', "_", component_name)
    return cleaned + ".md"


class MarkdownRenderer:
    def __init__(self, workspace_dir: Path):
        self.workspace_dir = workspace_dir

    def _format_resolution_prompt_block(self, analysis: AnalysisResult) -> str:
        if analysis.resolution is None:
            return ""
        res = analysis.resolution
        lines = [
            f"- Edge resolution: {res.resolved}/{res.attempted} "
            f"({100.0 * res.resolve_rate:.1f}%)",
            f"- Unresolved edges: {res.unresolved}",
            f"- Internal resolution: {res.internal_resolved}/{res.internal_attempted} "
            f"({100.0 * res.internal_resolve_rate:.1f}%), "
            f"internal_unresolved={res.internal_unresolved}",
            f"- Unresolved by category: external={res.category_unresolved('external')}, "
            f"builtin={res.category_unresolved('builtin')}, "
            f"attribute={res.category_unresolved('attribute')}, "
            f"internal={res.category_unresolved('internal')}",
        ]
        if analysis.call_cycles:
            lines.append(
                f"- Multi-node call-cycle samples: {len(analysis.call_cycles)}"
            )
        return "\n".join(lines)

    def generate_architecture_description(self, G: nx.DiGraph) -> str:
        """Dynamically generates an overall architecture description in Chinese."""
        file_nodes = [n for n, d in G.nodes(data=True) if d.get("type") == "file"]

        # 1. Group files by top-level directory
        directories = {}
        for f in file_nodes:
            parts = Path(f).parts
            if len(parts) > 1:
                dir_name = parts[0]
                directories.setdefault(dir_name, []).append(f)
            else:
                directories.setdefault(".", []).append(f)

        dir_desc_lines = []
        if directories:
            dir_desc_lines.append("### Physical Structure & Layout")
            for d_name, f_list in sorted(directories.items(), key=lambda x: -len(x[1])):
                if d_name == ".":
                    dir_desc_lines.append(
                        f"- **Root directory `.`** : contains {len(f_list)} configuration or source files."
                    )
                else:
                    files_preview = ", ".join(f"`{Path(f).name}`" for f in f_list[:3])
                    suffix = " etc." if len(f_list) > 3 else ""
                    dir_desc_lines.append(
                        f"- **`{d_name}/`** : contains {len(f_list)} source files (e.g., {files_preview}{suffix})."
                    )

        # 2. Find core entry points
        entry_nodes = []
        for nid, ndata in G.nodes(data=True):
            if ndata.get("type") in ("function", "method"):
                name = ndata.get("label", "").lower()
                if any(k in name for k in ("main", "app", "run", "cli", "serve")):
                    entry_nodes.append((ndata.get("label"), ndata.get("source_file")))

        entry_desc_lines = []
        if entry_nodes:
            entry_desc_lines.append("### Core Execution Entrypoints")
            for name, sf in entry_nodes[:5]:
                entry_desc_lines.append(
                    f"- `{name}` (defined in [{sf}](nodes/{get_node_filename(sf)}))"
                )

        # 3. Compose architecture markdown
        arch_lines = [
            "## Architecture Overview",
            "This project is built using a modular software system design. The static parser performs a full scan of the codebase to generate the underlying dependency network, and the knowledge graph groups symbols into logical components using a community detection algorithm (Component-level clustering).",
            "",
        ]
        if dir_desc_lines:
            arch_lines.extend(dir_desc_lines)
            arch_lines.append("")
        if entry_desc_lines:
            arch_lines.extend(entry_desc_lines)
            arch_lines.append("")

        arch_lines.extend(
            [
                "### Control Flow & Data Dependencies",
                "Data flow and control logic in the system center around core classes and components. The main components and their logical topological call relationships are shown in the diagram below, visualizing the core component interaction model.",
            ]
        )

        return "\n".join(arch_lines)

    def render_node_page(
        self, nid: str, ndata: dict, G: nx.DiGraph, node_component_map: dict
    ) -> str:
        """Renders a single node's documentation page."""
        label = ndata.get("label", nid)
        ntype = ndata.get("type", "unknown")
        sf = ndata.get("source_file", "")
        line_start = ndata.get("line_start", 1)
        line_end = ndata.get("line_end", 1)
        sig = ndata.get("signature", "")
        doc = ndata.get("docstring", "")
        comp_name = node_component_map.get(nid, "None")

        # Determine language for signature codeblock
        lang_ext = Path(sf).suffix.lower() if sf else ""
        lang_map = {
            ".py": "python",
            ".js": "javascript",
            ".ts": "typescript",
            ".tsx": "typescript",
            ".go": "go",
            ".rs": "rust",
            ".swift": "swift",
            ".kt": "kotlin",
            ".kts": "kotlin",
            ".dart": "dart",
        }
        code_lang = lang_map.get(lang_ext, "")

        lines = [
            "---",
            f'id: "{nid}"',
            f'label: "{label}"',
            f'type: "{ntype}"',
            f'source_file: "{sf}"',
            f"line_start: {line_start}",
            f"line_end: {line_end}",
            f'component: "{comp_name}"',
            "---",
            "",
            f"# {label}",
            "",
            f"**Type:** `{ntype}`  ",
            f"**Defined in:** [{sf}](../nodes/{get_node_filename(sf)}) (Lines {line_start}-{line_end})  "
            if ntype != "file"
            else f"**File path:** `{sf}`  ",
            f"**Component:** {comp_name}  " if comp_name != "None" else "",
            "",
        ]

        if sig:
            lines += ["## Definition", f"```{code_lang}", sig, "```", ""]

        if doc:
            lines += ["## Description", doc, ""]

        # Edges classification
        incoming = []
        outgoing = []
        contains_children = []
        contained_in_parent = None
        inherits_list = []
        inherited_by_list = []
        imports_list = []
        imported_by_list = []

        mermaid_calls = []

        # Outgoing edges
        for _, target, edata in G.out_edges(nid, data=True):
            rel = edata.get("relation")
            target_data = G.nodes.get(target, {})
            target_label = target_data.get("label", target)
            target_file = get_node_filename(target)

            if rel == "contains":
                contains_children.append(
                    f"- [{target_label}]({target_file}) (`{target_data.get('type')}`)"
                )
            elif rel == "inherits":
                inherits_list.append(
                    f"- [{target_label}]({target_file}) (`{target_data.get('type')}`)"
                )
            elif rel == "implements":
                inherits_list.append(
                    f"- [{target_label}]({target_file}) (Implements `{target_label}`)"
                )
            elif rel == "imports":
                imports_list.append(f"- [{target_label}]({target_file})")
            elif rel == "calls":
                outgoing.append(
                    f"- Calls [{target_label}]({target_file}) (`{target_data.get('type')}`)"
                )
                src_m_id = re.sub(r"[^a-zA-Z0-9_]", "_", nid)
                tgt_m_id = re.sub(r"[^a-zA-Z0-9_]", "_", target)
                mermaid_calls.append(
                    f'  {src_m_id}["{label}"] --> {tgt_m_id}["{target_label}"]'
                )

        # Incoming edges
        for source, _, edata in G.in_edges(nid, data=True):
            rel = edata.get("relation")
            source_data = G.nodes.get(source, {})
            source_label = source_data.get("label", source)
            source_file = get_node_filename(source)

            if rel == "contains":
                contained_in_parent = (
                    f"[{source_label}]({source_file}) (`{source_data.get('type')}`)"
                )
            elif rel == "inherits":
                inherited_by_list.append(
                    f"- [{source_label}]({source_file}) (`{source_data.get('type')}`)"
                )
            elif rel == "implements":
                inherited_by_list.append(
                    f"- [{source_label}]({source_file}) (Implemented by `{source_label}`)"
                )
            elif rel == "imports":
                imported_by_list.append(f"- [{source_label}]({source_file})")
            elif rel == "calls":
                incoming.append(
                    f"- Called by [{source_label}]({source_file}) (`{source_data.get('type')}`)"
                )
                src_m_id = re.sub(r"[^a-zA-Z0-9_]", "_", source)
                tgt_m_id = re.sub(r"[^a-zA-Z0-9_]", "_", nid)
                mermaid_calls.append(
                    f'  {src_m_id}["{source_label}"] --> {tgt_m_id}["{label}"]'
                )

        if contained_in_parent:
            lines += [f"**Contained in parent:** {contained_in_parent}", ""]

        if inherits_list:
            lines += ["## Inherits / Implements", *inherits_list, ""]

        if inherited_by_list:
            lines += ["## Inherited / Implemented By", *inherited_by_list, ""]

        if contains_children:
            lines += ["## Contains Symbols", *contains_children, ""]

        if imports_list:
            lines += ["## Imports", *imports_list, ""]

        if imported_by_list:
            lines += ["## Imported By", *imported_by_list, ""]

        if outgoing or incoming:
            lines += ["## Call Graph"]
            if mermaid_calls:
                uniq_m_calls = sorted(list(set(mermaid_calls)))
                lines += ["```mermaid", "flowchart TD", *uniq_m_calls, "```", ""]
            if outgoing:
                lines += ["### Outgoing Calls", *outgoing, ""]
            if incoming:
                lines += ["### Incoming Calls (Backlinks)", *incoming, ""]

        return "\n".join(lines)

    def render_component_page(
        self,
        cid: int,
        members: list,
        G: nx.DiGraph,
        cohesion: float,
        name: str,
        inter_comp_deps: dict,
        component_names: dict,
    ) -> str:
        """Renders a logical component's detail page."""
        lines = [
            "---",
            'type: "component"',
            f"cohesion: {cohesion}",
            f"members_count: {len(members)}",
            "---",
            "",
            f"# {name}",
            "",
            f"**Internal Cohesion:** {cohesion} (density score)  ",
            f"**Members count:** {len(members)} nodes  ",
            "",
            "## Members",
        ]

        for nid in sorted(members, key=lambda n: G.nodes[n].get("label", n)):
            ndata = G.nodes[nid]
            label = ndata.get("label", nid)
            ntype = ndata.get("type", "unknown")
            sf = ndata.get("source_file", "")
            lines.append(
                f"- [{label}](../nodes/{get_node_filename(nid)}) (`{ntype}` in `{sf}`)"
            )

        lines.append("")

        deps = inter_comp_deps.get(cid, {})
        if deps:
            lines += ["## Component Dependencies", ""]
            for target_cid, count in sorted(deps.items(), key=lambda x: -x[1]):
                target_name = component_names.get(target_cid, f"Component {target_cid}")
                target_file = get_component_filename(target_name)
                lines.append(
                    f"- Depends on [{target_name}]({target_file}) ({count} calls/connections)"
                )
            lines.append("")

        return "\n".join(lines)

    def render_readme(
        self,
        G: nx.DiGraph,
        components: dict,
        cohesion_scores: dict,
        component_names: dict,
        analysis: AnalysisResult,
        ai_insights: str | None = None,
    ) -> str:
        """Renders the main README.md for the vault."""
        files_count = sum(1 for _, d in G.nodes(data=True) if d.get("type") == "file")
        symbols_count = G.number_of_nodes() - files_count

        arch_desc = self.generate_architecture_description(G)

        readme_lines = [
            "# Codebase Knowledge Graph",
            "",
            "Welcome to the codebase knowledge graph of this workspace. This index represents files, classes, structs, methods, and functions as nodes, with call and dependency relationships between them.",
            "",
            "## Statistics",
            f"- **Files Scanned:** {files_count}",
            f"- **Symbols Extracted:** {symbols_count}",
            f"- **Call/Relation Edges:** {G.number_of_edges()}",
        ]

        if analysis.resolution is not None:
            res = analysis.resolution
            rate_pct = 100.0 * res.resolve_rate
            int_pct = 100.0 * res.internal_resolve_rate
            readme_lines += [
                f"- **Edge Resolution:** {res.resolved}/{res.attempted} resolved ({rate_pct:.1f}%)",
                f"- **Unresolved Edges:** {res.unresolved}",
                f"- **Internal Resolution:** {res.internal_resolved}/{res.internal_attempted} "
                f"({int_pct:.1f}%), internal unresolved={res.internal_unresolved}",
                f"- **Unresolved by Category:** external={res.category_unresolved('external')}, "
                f"builtin={res.category_unresolved('builtin')}, "
                f"attribute={res.category_unresolved('attribute')}, "
                f"internal={res.category_unresolved('internal')}",
            ]

        if analysis.metrics.get("relation_counts"):
            rel_parts = ", ".join(
                f"{k}={v}" for k, v in sorted(analysis.metrics["relation_counts"].items())
            )
            readme_lines.append(f"- **Relation Mix:** {rel_parts}")

        readme_lines += [
            "",
            arch_desc,
            "",
            "## Component Dependency Graph",
        ]

        # Build Mermaid graph
        mermaid_lines = ["flowchart TD"]
        has_relations = False
        for src_cid, targets in analysis.inter_comp_deps.items():
            src_name = component_names[src_cid]
            src_id = f"comp_{src_cid}"
            for tgt_cid, weight in targets.items():
                tgt_name = component_names[tgt_cid]
                tgt_id = f"comp_{tgt_cid}"
                mermaid_lines.append(
                    f'  {src_id}["{src_name}"] -->|{weight}| {tgt_id}["{tgt_name}"]'
                )
                has_relations = True

        if not has_relations:
            for cid, name in component_names.items():
                cid_id = f"comp_{cid}"
                mermaid_lines.append(f'  {cid_id}["{name}"]')

        mermaid_graph = "\n".join(mermaid_lines)
        readme_lines += ["```mermaid", mermaid_graph, "```", ""]

        readme_lines += [
            "## Logical Components Summary",
            "The codebase has been automatically clustered into logical components based on coupling and call density:",
            "",
            "| Component | Cohesion (Density) | Size (Nodes) |",
            "|---|---|---|",
        ]

        for cid, members in components.items():
            comp_name = component_names[cid]
            cohesion = cohesion_scores[cid]
            comp_file = f"components/{get_component_filename(comp_name)}"
            readme_lines.append(
                f"| [{comp_name}]({comp_file}) | {cohesion} | {len(members)} |"
            )

        readme_lines += [
            "",
            "## God Nodes (Top Core Abstractions)",
            "These symbols have the highest degrees (most connections) in the codebase. Test paths are excluded. Modifying them may have wide-reaching effects:",
            "",
            "| Symbol | Type | Degree | In | Out | File |",
            "|---|---|---|---|---|---|",
        ]

        for node in analysis.god_nodes:
            nid = node["id"]
            ndata = G.nodes[nid]
            sf = ndata.get("source_file", "")
            readme_lines.append(
                f"| [{node['label']}](nodes/{get_node_filename(nid)}) | `{node['type']}` | {node['degree']} | {node.get('in_degree', '-')} | {node.get('out_degree', '-')} | `{sf}` |"
            )

        if analysis.resolution is not None and analysis.resolution.unresolved_samples:
            readme_lines += [
                "",
                "## Unresolved Edge Samples",
                "Representative edges that could not be bound to a graph symbol, "
                "classified as internal / external / builtin / attribute:",
                "",
                "| Category | Relation | Source | Target |",
                "|---|---|---|---|",
            ]
            for sample in analysis.resolution.unresolved_samples[:20]:
                readme_lines.append(
                    f"| `{sample.get('category', '?')}` | `{sample.get('relation')}` | "
                    f"`{sample.get('source')}` | `{sample.get('target')}` |"
                )

        readme_lines += [
            "",
            "## Circular Import Dependencies",
        ]

        if analysis.cycles:
            readme_lines += [
                "WARNING: The following circular import loops were detected at the file level. Consider refactoring to reduce coupling:",
                "",
            ]
            for idx, cycle in enumerate(analysis.cycles, start=1):
                cycle_str = " ➡️ ".join(
                    f"[{Path(f).name}](nodes/{get_node_filename(f)})"
                    for f in cycle + [cycle[0]]
                )
                readme_lines.append(f"{idx}. {cycle_str}")
        else:
            readme_lines.append("No circular imports detected. Perfect modularity!")

        if analysis.call_cycles:
            readme_lines += [
                "",
                "## Call Cycles (Sample)",
                "Multi-node mutual call cycles only (self-recursion is filtered):",
                "Short symbol-level call cycles (capped). These may indicate recursion or mutual coupling:",
                "",
            ]
            for idx, cycle in enumerate(analysis.call_cycles[:10], start=1):
                labels = []
                for nid in cycle + [cycle[0]]:
                    lab = G.nodes[nid].get("label", nid) if nid in G.nodes else nid
                    labels.append(f"`{lab}`")
                readme_lines.append(f"{idx}. {' ➡️ '.join(labels)}")

        readme_lines.append("")

        if not ai_insights:
            ai_insights = """> 💡 **Tip**: AI architectural insights have not been generated yet. Please use your AI Agent (e.g., Antigravity, Claude Code, Codex) to read the prompt in `.codegraph/AGENT_PROMPT.md` and fill in the analysis results here."""

        readme_lines += [
            "## AI Architectural Insights",
            ai_insights,
            "",
        ]

        return "\n".join(readme_lines)

    def render_agent_prompt(
        self,
        G: nx.DiGraph,
        components: dict,
        cohesion_scores: dict,
        component_names: dict,
        analysis: AnalysisResult,
        language: str = "en",
    ) -> str:
        """Renders the AGENT_PROMPT.md template.

        ``language`` selects the instruction language: ``en`` (default) or ``zh``.
        The report must always be written under the English section heading
        ``## AI Architectural Insights`` so rebuilds can preserve prior insights.
        """
        files_count = sum(1 for _, d in G.nodes(data=True) if d.get("type") == "file")
        symbols_count = G.number_of_nodes() - files_count

        comp_list = []
        for cid, members in components.items():
            comp_list.append(
                f"- {component_names[cid]}: cohesion={cohesion_scores[cid]:.4f}, members_count={len(members)}"
            )
        comp_str = "\n".join(comp_list)

        god_list = []
        for node in analysis.god_nodes:
            sf = G.nodes[node["id"]].get("source_file", "")
            god_list.append(
                f"- {node['label']} ({node['type']}): degree={node['degree']}, file={sf}"
            )
        god_str = "\n".join(god_list)

        cycle_list = []
        for c in analysis.cycles:
            cycle_list.append(" -> ".join(c + [c[0]]))
        lang = (language or "en").lower()
        if lang.startswith("zh"):
            cycle_str = "\n".join(cycle_list) if cycle_list else "无循环依赖"
        else:
            cycle_str = "\n".join(cycle_list) if cycle_list else "No circular imports"

        # Generate Mermaid component graph
        mermaid_lines = ["flowchart TD"]
        has_relations = False
        for src_cid, targets in analysis.inter_comp_deps.items():
            src_name = component_names[src_cid]
            src_id = f"comp_{src_cid}"
            for tgt_cid, weight in targets.items():
                tgt_name = component_names[tgt_cid]
                tgt_id = f"comp_{tgt_cid}"
                mermaid_lines.append(
                    f'  {src_id}["{src_name}"] -->|{weight}| {tgt_id}["{tgt_name}"]'
                )
                has_relations = True

        if not has_relations:
            for cid, name in component_names.items():
                cid_id = f"comp_{cid}"
                mermaid_lines.append(f'  {cid_id}["{name}"]')
        mermaid_graph = "\n".join(mermaid_lines)

        if lang.startswith("zh"):
            prompt = f"""# Codebase Architecture Analysis Prompt

你是一个资深的软件架构专家。根据下面提供的代码库知识图谱元数据和主要组件之间的关系，为该项目撰写一份深刻的“AI 架构深度洞察分析报告”（使用中文书写）。

**重要**：将完整报告写入 `.codegraph/README.md` 的 `## AI Architectural Insights` 章节（章节标题保持英文，便于工具保留历史洞察）。

【代码库图谱数据统计】
- 物理文件数: {files_count}
- 符号数 (类/结构体/函数/方法): {symbols_count}
- 依赖与调用边总数: {G.number_of_edges()}
{self._format_resolution_prompt_block(analysis)}

【逻辑组件划分 (Modularity Components)】
{comp_str}

【组件依赖拓扑图 (Mermaid Flowchart)】
```mermaid
{mermaid_graph}
```

【核心抽象 God Nodes (度数代表连接的其它符号总数)】
{god_str}

【文件级循环导入依赖 (Import Cycles)】
{cycle_str}

请基于上述代码结构和组件的关联关系，以及结合现有代码进行深入洞察分析，重点阐述以下三个方面：
1. **系统架构评估**：说明代码库的设计模式、模块化水准、物理目录与逻辑组件契合度。
2. **核心抽象与边界评估**：对 God Nodes 进行深入把脉，分析哪些是核心支撑，哪些职责过重（God Object / Fat Class）可能导致高风险。
3. **潜在瓶颈与架构重构建议**：指出高耦合风险点、循环依赖负面影响，并给出具体、可操作的重构优化方案（如解耦、提取接口、依赖倒置等）。

请以标准的 Markdown 格式输出，排版要清晰美观、专业严谨，不要包含首尾的 ```markdown 和 ``` 语法包裹标记，直接输出内容。
"""
        else:
            prompt = f"""# Codebase Architecture Analysis Prompt

You are a senior software architect. Using the knowledge-graph metadata and component relationships below, write a deep **AI Architectural Insights** report for this project **in English**.

**Important**: Write the full report into the `## AI Architectural Insights` section of `.codegraph/README.md` (keep this English heading so rebuilds can preserve prior insights).

## Graph statistics
- Files: {files_count}
- Symbols (classes/functions/methods): {symbols_count}
- Edges: {G.number_of_edges()}
{self._format_resolution_prompt_block(analysis)}

## Logical components (modularity)
{comp_str}

## Component dependency graph (Mermaid)
```mermaid
{mermaid_graph}
```

## God nodes (highest degree)
{god_str}

## File-level import cycles
{cycle_str}

Focus your analysis on:
1. **System architecture** — design patterns, modularity, physical vs logical alignment.
2. **Core abstractions & boundaries** — which god nodes are healthy hubs vs fat objects.
3. **Bottlenecks & refactoring** — coupling risks, cycle impact, concrete decoupling steps.

Output standard Markdown only (no surrounding ```markdown fences).
"""
        return prompt
