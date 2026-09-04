"""Named pipeline stages for CodegraphEngine.

Keeps orchestration readable: each stage is a small function operating on a
shared ``PipelineContext``. ``CodegraphEngine.run_pipeline`` sequences these
stages without embedding all stage logic in one method body.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import networkx as nx

from codegraph_gen.analyzer import AnalysisResult
from codegraph_gen.config import CacheEntry, CodegraphConfig
from codegraph_gen.schema import ExtractionResult

ProgressCallback = Callable[[Any, Any, int, int], None]


@dataclass
class PipelineContext:
    """Mutable bag of state passed between pipeline stages."""

    config: CodegraphConfig
    progress_callback: Optional[ProgressCallback] = None

    files: list[tuple[Path, str]] = field(default_factory=list)
    extractions: list[ExtractionResult] = field(default_factory=list)
    dirty_files: set[str] = field(default_factory=set)
    parse_errors: list[str] = field(default_factory=list)
    cache_entries: dict[str, CacheEntry] = field(default_factory=dict)
    cache_path: Path | None = None

    graph: nx.DiGraph = field(default_factory=nx.DiGraph)
    components: dict[int, list[str]] = field(default_factory=dict)
    cohesion_scores: dict[int, float] = field(default_factory=dict)
    component_names: dict[int, str] = field(default_factory=dict)
    analysis: AnalysisResult | None = None

    rendered_nodes: dict[str, str] = field(default_factory=dict)
    rendered_components: dict[str, str] = field(default_factory=dict)
    new_signatures: dict[str, str] = field(default_factory=dict)
    readme_content: str = ""
    prompt_content: str = ""


def notify(
    ctx: PipelineContext, stage: Any, item: Any = None, idx: int = 0, total: int = 0
) -> None:
    if ctx.progress_callback:
        ctx.progress_callback(stage, item, idx, total)
