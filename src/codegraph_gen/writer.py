from __future__ import annotations

import concurrent.futures
import logging
import os
import shutil
import threading
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class WriteStats:
    written: int = 0
    skipped: int = 0
    removed: int = 0
    paths_written: list[str] = field(default_factory=list)


class VaultWriter:
    def clear_directory(self, path: Path):
        """Clears the output directory before exporting."""
        if path.exists():
            try:
                shutil.rmtree(path)
                logger.info(f"Cleared directory: {path}")
            except Exception as e:
                logger.warning(f"Could not fully clear output directory {path}: {e}")

    def write_file(
        self,
        path: Path,
        content: str,
        stats: WriteStats | None = None,
        lock: threading.Lock | None = None,
    ) -> bool:
        """Write content if changed. Returns True when a write occurred."""
        try:
            if not path.parent.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                try:
                    if path.read_text(encoding="utf-8") == content:
                        if stats is not None:
                            if lock:
                                with lock:
                                    stats.skipped += 1
                            else:
                                stats.skipped += 1
                        return False
                except Exception:
                    pass
            path.write_text(content, encoding="utf-8")
            if stats is not None:
                if lock:
                    with lock:
                        stats.written += 1
                        stats.paths_written.append(str(path))
                else:
                    stats.written += 1
                    stats.paths_written.append(str(path))
            return True
        except Exception as e:
            logger.error(f"Failed to write file at {path}: {e}")
            raise

    def write_vault(
        self,
        output_dir: Path,
        rendered_nodes: dict[str, str],
        rendered_components: dict[str, str],
        readme_content: str,
        prompt_content: str,
        skipped_nodes: set[str] | None = None,
        skipped_components: set[str] | None = None,
    ) -> WriteStats:
        """Writes all rendered markdown pages; skips unchanged files."""
        stats = WriteStats()
        nodes_dir = output_dir / "nodes"
        comps_dir = output_dir / "components"

        nodes_dir.mkdir(parents=True, exist_ok=True)
        comps_dir.mkdir(parents=True, exist_ok=True)

        skip_nodes = skipped_nodes or set()
        skip_comps = skipped_components or set()

        # Smart cleanup of obsolete files to avoid deleting active cache.json or graph.html
        expected_nodes = set(rendered_nodes.keys()) | skip_nodes
        if nodes_dir.exists():
            for p in nodes_dir.glob("*.md"):
                if p.name not in expected_nodes:
                    try:
                        p.unlink()
                        stats.removed += 1
                        logger.info(f"Removed obsolete node file: {p.name}")
                    except Exception as e:
                        logger.warning(
                            f"Could not remove obsolete node file {p.name}: {e}"
                        )

        expected_components = set(rendered_components.keys()) | skip_comps
        if comps_dir.exists():
            for p in comps_dir.glob("*.md"):
                if p.name not in expected_components:
                    try:
                        p.unlink()
                        stats.removed += 1
                        logger.info(f"Removed obsolete component file: {p.name}")
                    except Exception as e:
                        logger.warning(
                            f"Could not remove obsolete component file {p.name}: {e}"
                        )

        # Account for skipped files with zero disk I/O
        stats.skipped += len(skip_nodes) + len(skip_comps)

        total_files_to_write = len(rendered_nodes) + len(rendered_components)
        if total_files_to_write > 16:
            max_workers = min(16, (os.cpu_count() or 1) * 2)
            lock = threading.Lock()
            tasks = [
                (nodes_dir / fn, content) for fn, content in rendered_nodes.items()
            ] + [
                (comps_dir / fn, content) for fn, content in rendered_components.items()
            ]

            def _worker_task(item: tuple[Path, str]) -> None:
                self.write_file(item[0], item[1], stats, lock=lock)

            with concurrent.futures.ThreadPoolExecutor(
                max_workers=max_workers
            ) as executor:
                list(executor.map(_worker_task, tasks))
        else:
            for fname, content in rendered_nodes.items():
                self.write_file(nodes_dir / fname, content, stats)
            for fname, content in rendered_components.items():
                self.write_file(comps_dir / fname, content, stats)

        self.write_file(output_dir / "README.md", readme_content, stats)
        self.write_file(output_dir / "AGENT_PROMPT.md", prompt_content, stats)

        # 4. Write AGENTS.md to project root
        project_root = output_dir.parent
        agents_file = project_root / "AGENTS.md"

        agents_rule_body = """## codegraph-gen

This project maintains a codebase knowledge graph at `.codegraph/`.

### Guidelines for AI Agents (Antigravity, Claude Code, Cursor, Roo Code, etc.)

You MUST follow these rules when working in this codebase:

1. **Prioritize the Knowledge Graph**:
   - Before answering architecture, design, or codebase structure questions, you **MUST** read [.codegraph/README.md](.codegraph/README.md) to understand the system overview, god nodes, and logical community structure.
   - Use [.codegraph/components/](.codegraph/components/) and [.codegraph/nodes/](.codegraph/nodes/) to navigate component boundaries, file relationships, and symbol definitions. This is much faster and more token-efficient than reading raw source files directly.

2. **AI Architectural Insights**:
   - Check [.codegraph/README.md](.codegraph/README.md) for a section titled `AI Architectural Insights`.
   - If this section is missing, incomplete, or contains placeholders, read [.codegraph/AGENT_PROMPT.md](.codegraph/AGENT_PROMPT.md), perform a deep architectural analysis of the project, and write your report into that section. Do not overwrite other sections.

3. **Keep Graph Sync'd**:
   - Whenever you create, delete, or modify code files, you **SHOULD** remind the user to run `codegraph build .` to rebuild the knowledge graph and keep it current.
   - When running the build command, exclude irrelevant or generated directories (e.g., third-party dependencies, build folders, or documentation) using the `-e`/`--exclude` flag to keep the graph focused and clean (e.g., `codegraph build . -e third_party/`).
"""

        if agents_file.exists():
            try:
                content = agents_file.read_text(encoding="utf-8")
                if "## codegraph-gen" not in content:
                    new_content = content.rstrip() + "\n\n" + agents_rule_body
                    self.write_file(agents_file, new_content, stats)
                    logger.info(
                        "Appended codegraph-gen rules to existing AGENTS.md at root"
                    )
            except Exception as e:
                logger.warning(f"Could not read or append to existing AGENTS.md: {e}")
        else:
            try:
                self.write_file(agents_file, agents_rule_body, stats)
                logger.info("Created new AGENTS.md with codegraph rules at root")
            except Exception as e:
                logger.warning(f"Could not create AGENTS.md at root: {e}")

        logger.info(
            "Vault write: %s written, %s unchanged, %s removed",
            stats.written,
            stats.skipped,
            stats.removed,
        )
        return stats
