import json
import logging
import os
from pathlib import Path
from typing import Optional
from pydantic import BaseModel, Field
from codegraph_gen.schema import ExtractionResult

logger = logging.getLogger(__name__)

PROJECT_CONFIG_FILE = ".codegraphrc"

# Default exclusions for files and directories we want to ignore
DEFAULT_EXCLUSIONS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "third_party",
    "dist",
    "build",
    ".build",
    "__pycache__",
    ".pytest_cache",
    ".codegraph",
    ".idea",
    ".vscode",
    "target",
    "out",
    "bin",
    "obj",
    "vendor",
    "Pods",
    "Carthage",
    "DerivedData",
    "build_output",
    ".next",
    ".nuxt",
    ".cache",
    "build_mac",
    "build_ios",
    "build_ios_sim",
}


# Mapping of supported languages to file extensions
LANGUAGE_EXTENSIONS = {
    "python": {".py"},
    "javascript": {".js", ".mjs", ".cjs"},
    "typescript": {".ts", ".tsx"},
    "kotlin": {".kt", ".kts"},
    "go": {".go"},
    "rust": {".rs"},
    "swift": {".swift"},
    "c": {".c", ".h"},
    "cpp": {".cpp", ".cc", ".cxx", ".hpp", ".hxx"},
    "ocaml": {".ml", ".mli"},
    "dart": {".dart"},
}

ALL_EXTENSIONS = {ext for exts in LANGUAGE_EXTENSIONS.values() for ext in exts}


class CacheEntry(BaseModel):
    mtime: float
    size: int
    hash: str
    result: ExtractionResult


class ProjectConfig(BaseModel):
    """Schema for the .codegraphrc project-level configuration file."""

    include: Optional[list[str]] = None
    """Subdirectory whitelist (relative to workspace root). None = scan entire workspace."""

    exclude: list[str] = []
    """Extra directory names/patterns to exclude (appended to DEFAULT_EXCLUSIONS)."""

    output: str = ".codegraph"
    """Output directory path (relative to workspace root)."""

    languages: Optional[list[str]] = None
    """Language whitelist. None = all supported languages."""

    workers: Optional[int] = None
    """Number of parallel worker processes. None = use CPU count."""

    cache: bool = True
    """Enable incremental parse cache."""

    exclude_tests_from_clustering: bool = True
    """When true, Louvain runs without test-path nodes (tests reattached after)."""

    component_naming: str = "hybrid"
    """Component name strategy: hybrid | package | symbol."""

    min_resolve_rate: Optional[float] = None
    """CI gate: fail build if overall edge resolve rate is below this (0.0–1.0)."""

    max_unresolved_edges: Optional[int] = None
    """CI gate: fail build if overall unresolved edge count exceeds this."""

    min_internal_resolve_rate: Optional[float] = None
    """CI gate: fail if *internal* edge resolve rate is below this (0.0–1.0)."""

    max_internal_unresolved_edges: Optional[int] = None
    """CI gate: fail if unresolved *internal* edge count exceeds this."""

    strict: bool = False
    """When true, fail the build if any source file fails to parse."""

    report_language: str = "en"
    """Language for AGENT_PROMPT / AI insights guidance: en | zh."""


def load_project_config(workspace_dir: Path) -> Optional[ProjectConfig]:
    """
    Loads .codegraphrc from the workspace root directory.
    Returns None (silently) if the file does not exist.
    Returns None (with a warning) if the file is malformed.
    No upward traversal — the file must be in workspace_dir itself.
    """
    config_path = workspace_dir / PROJECT_CONFIG_FILE
    if not config_path.is_file():
        return None
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        cfg = ProjectConfig.model_validate(data)
        logger.info(f"Loaded project config from {config_path}")
        return cfg
    except json.JSONDecodeError as e:
        logger.warning(
            f"{PROJECT_CONFIG_FILE}: JSON parse error — {e}. Using defaults."
        )
    except Exception as e:
        logger.warning(f"{PROJECT_CONFIG_FILE}: Failed to load — {e}. Using defaults.")
    return None


class CodegraphConfig(BaseModel):
    """Configuration class for codegraph parsing and exporting."""

    workspace_dir: Path
    output_dir: Path = Field(default_factory=lambda: Path(".codegraph"))
    exclusions: set[str] = Field(default_factory=lambda: DEFAULT_EXCLUSIONS)
    languages: set[str] = Field(default_factory=lambda: set(LANGUAGE_EXTENSIONS.keys()))
    max_workers: int = Field(default_factory=lambda: os.cpu_count() or 4)
    use_cache: bool = Field(default=True)
    include_dirs: Optional[list[Path]] = Field(default=None)
    """Absolute paths of subdirectories to scan. None = scan entire workspace_dir."""
    export_json: bool = Field(default=True)
    """Write graph.json machine-readable export alongside the Markdown vault."""
    exclude_tests_from_clustering: bool = Field(default=True)
    """Exclude test-path nodes from Louvain seed graph."""
    component_naming: str = Field(default="hybrid")
    """hybrid | package | symbol component naming strategy."""
    min_resolve_rate: Optional[float] = Field(default=None)
    """If set, pipeline raises when overall resolve_rate is below this threshold."""
    max_unresolved_edges: Optional[int] = Field(default=None)
    """If set, pipeline raises when overall unresolved edges exceed this count."""
    min_internal_resolve_rate: Optional[float] = Field(default=None)
    """If set, pipeline raises when internal_resolve_rate is below this threshold."""
    max_internal_unresolved_edges: Optional[int] = Field(default=None)
    """If set, pipeline raises when unresolved internal edges exceed this count."""
    strict: bool = Field(default=False)
    """If true, pipeline raises when any file fails to parse."""
    report_language: str = Field(default="en")
    """Agent prompt language: en | zh."""

    @property
    def absolute_output_dir(self) -> Path:
        if self.output_dir.is_absolute():
            return self.output_dir
        return self.workspace_dir / self.output_dir
