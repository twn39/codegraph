from pathlib import Path
import click
from rich.console import Console
from rich.table import Table
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    MofNCompleteColumn,
)

from codegraph_gen.config import (
    CodegraphConfig,
    DEFAULT_EXCLUSIONS,
    LANGUAGE_EXTENSIONS,
    load_project_config,
    PROJECT_CONFIG_FILE,
)

console = Console()

try:
    from importlib.metadata import version

    __version__ = version("codegraph-gen")
except Exception:
    __version__ = "1.3.2"


@click.group()
@click.version_option(version=__version__, prog_name="codegraph")
def cli():
    """codegraph - Build a Markdown knowledge graph of your codebase for AI analysis."""
    pass


@cli.command()
@click.argument(
    "src_dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=".",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path),
    default=Path(".codegraph"),
    help="Directory where the Markdown vault will be written.",
)
@click.option(
    "--exclude",
    "-e",
    multiple=True,
    type=str,
    help="Additional folder names/patterns to exclude from scanning.",
)
@click.option(
    "--parallel/--no-parallel",
    default=True,
    help="Enable/disable parallel parsing (using multiprocessing).",
)
@click.option(
    "--workers",
    "-w",
    type=int,
    default=None,
    help="Number of worker processes to use for parallel parsing.",
)
@click.option(
    "--cache/--no-cache",
    default=True,
    help="Enable/disable incremental parsing cache.",
)
@click.option(
    "--export-json/--no-export-json",
    default=True,
    help="Write graph.json machine-readable export in the vault output directory.",
)
@click.option(
    "--exclude-tests-from-clustering/--include-tests-in-clustering",
    default=None,
    help="Exclude (default) or include test-path nodes in Louvain clustering.",
)
@click.option(
    "--component-naming",
    type=click.Choice(["hybrid", "package", "symbol"], case_sensitive=False),
    default=None,
    help="Component naming strategy: hybrid (default), package, or symbol.",
)
@click.option(
    "--min-resolve-rate",
    type=float,
    default=None,
    help="CI gate: fail if overall edge resolve rate is below this value (0.0–1.0).",
)
@click.option(
    "--max-unresolved-edges",
    type=int,
    default=None,
    help="CI gate: fail if overall unresolved edge count exceeds this value.",
)
@click.option(
    "--min-internal-resolve-rate",
    type=float,
    default=None,
    help="CI gate: fail if *internal* edge resolve rate is below this (0.0–1.0).",
)
@click.option(
    "--max-internal-unresolved-edges",
    type=int,
    default=None,
    help="CI gate: fail if unresolved *internal* edge count exceeds this value.",
)
def build(
    src_dir: Path,
    output: Path,
    exclude: list[str],
    parallel: bool,
    workers: int | None,
    cache: bool,
    export_json: bool,
    exclude_tests_from_clustering: bool | None,
    component_naming: str | None,
    min_resolve_rate: float | None,
    max_unresolved_edges: int | None,
    min_internal_resolve_rate: float | None,
    max_internal_unresolved_edges: int | None,
):
    """Parses the codebase in SRC_DIR and exports the Markdown graph vault."""
    console.print("[bold blue]Starting codegraph analysis...[/bold blue]")

    workspace = src_dir.resolve()

    # ── Load project config file (.codegraphrc) ────────────────────────────
    project_cfg = load_project_config(workspace)

    if project_cfg is not None:
        console.print(
            f"[dim]📄 Found project config: [underline]{workspace / PROJECT_CONFIG_FILE}[/underline][/dim]"
        )

    # ── Merge: CLI args take priority over .codegraphrc ────────────────────
    # Exclusions: default set + .codegraphrc extras + CLI --exclude (cumulative)
    exclusions = set(DEFAULT_EXCLUSIONS)
    if project_cfg and project_cfg.exclude:
        exclusions.update(project_cfg.exclude)
    if exclude:
        exclusions.update(exclude)

    # Output directory: CLI --output > .codegraphrc output > default
    if output != Path(".codegraph"):
        # User explicitly passed --output on CLI
        resolved_output = output.resolve()
    elif project_cfg and project_cfg.output != ".codegraph":
        resolved_output = (workspace / project_cfg.output).resolve()
    else:
        resolved_output = (workspace / ".codegraph").resolve()

    # Languages: CLI has no language filter option yet; use .codegraphrc if present
    all_languages = set(LANGUAGE_EXTENSIONS.keys())
    if project_cfg and project_cfg.languages:
        valid = {lang for lang in project_cfg.languages if lang in all_languages}
        invalid = set(project_cfg.languages) - valid
        if invalid:
            console.print(
                f"[yellow]⚠ .codegraphrc: unknown languages ignored: {', '.join(sorted(invalid))}[/yellow]"
            )
        languages = valid if valid else all_languages
    else:
        languages = all_languages

    # Workers: CLI --workers > .codegraphrc workers > CPU count
    import os

    if not parallel:
        max_workers = 1
    elif workers is not None:
        max_workers = workers
    elif project_cfg and project_cfg.workers is not None:
        max_workers = project_cfg.workers
    else:
        max_workers = os.cpu_count() or 4

    # Cache: CLI --cache/--no-cache flag always applies; .codegraphrc only when CLI default
    # (click default for cache is True, so we trust project_cfg when CLI wasn't explicitly set)
    effective_cache = (
        cache if cache is not None else (project_cfg.cache if project_cfg else True)
    )

    # Include dirs: from .codegraphrc include whitelist (no CLI equivalent)
    include_dirs = None
    if project_cfg and project_cfg.include:
        include_dirs = []
        for subdir in project_cfg.include:
            resolved = (workspace / subdir).resolve()
            if not resolved.exists():
                console.print(
                    f"[yellow]⚠ .codegraphrc include '{subdir}' does not exist, skipping.[/yellow]"
                )
            else:
                include_dirs.append(resolved)
        if not include_dirs:
            include_dirs = None  # All entries invalid → fall back to full scan

    # Print effective config summary when a project config was loaded
    if project_cfg is not None:
        parts = []
        if include_dirs:
            parts.append(f"include: {', '.join(p.name for p in include_dirs)}")
        if project_cfg.exclude:
            parts.append(f"extra exclude: {', '.join(project_cfg.exclude)}")
        if project_cfg.languages:
            parts.append(f"languages: {', '.join(sorted(languages))}")
        if parts:
            console.print(f"[dim]   {' | '.join(parts)}[/dim]")

    # Clustering / quality options: CLI overrides .codegraphrc, else defaults
    effective_exclude_tests = (
        exclude_tests_from_clustering
        if exclude_tests_from_clustering is not None
        else (
            project_cfg.exclude_tests_from_clustering
            if project_cfg is not None
            else True
        )
    )
    effective_naming = (
        component_naming
        if component_naming is not None
        else (project_cfg.component_naming if project_cfg is not None else "hybrid")
    ).lower()
    effective_min_rate = (
        min_resolve_rate
        if min_resolve_rate is not None
        else (project_cfg.min_resolve_rate if project_cfg is not None else None)
    )
    effective_max_unresolved = (
        max_unresolved_edges
        if max_unresolved_edges is not None
        else (project_cfg.max_unresolved_edges if project_cfg is not None else None)
    )
    effective_min_internal_rate = (
        min_internal_resolve_rate
        if min_internal_resolve_rate is not None
        else (
            project_cfg.min_internal_resolve_rate
            if project_cfg is not None
            else None
        )
    )
    effective_max_internal_unresolved = (
        max_internal_unresolved_edges
        if max_internal_unresolved_edges is not None
        else (
            project_cfg.max_internal_unresolved_edges
            if project_cfg is not None
            else None
        )
    )

    for label, rate in (
        ("--min-resolve-rate", effective_min_rate),
        ("--min-internal-resolve-rate", effective_min_internal_rate),
    ):
        if rate is not None and not (0.0 <= rate <= 1.0):
            console.print(
                f"[bold red]Error:[/bold red] {label} must be between 0.0 and 1.0"
            )
            raise SystemExit(2)

    config = CodegraphConfig(
        workspace_dir=workspace,
        output_dir=resolved_output,
        exclusions=exclusions,
        languages=languages,
        max_workers=max_workers,
        use_cache=effective_cache,
        include_dirs=include_dirs,
        export_json=export_json,
        exclude_tests_from_clustering=effective_exclude_tests,
        component_naming=effective_naming,
        min_resolve_rate=effective_min_rate,
        max_unresolved_edges=effective_max_unresolved,
        min_internal_resolve_rate=effective_min_internal_rate,
        max_internal_unresolved_edges=effective_max_internal_unresolved,
    )

    from codegraph_gen.engine import CodegraphEngine, PipelineStage

    engine = CodegraphEngine()

    # Run pipeline with click progress bar
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Initializing...", total=None)

        def progress_callback(stage: PipelineStage, current_item, idx, total):
            if stage == PipelineStage.DISCOVERING:
                progress.update(task, description="Discovering source files...")
            elif stage == PipelineStage.PARSING:
                if total > 0:
                    progress.update(task, total=total)
                progress.update(
                    task,
                    description=f"Parsing {current_item.name if current_item else ''}",
                    completed=idx,
                )
            elif stage == PipelineStage.BUILDING:
                progress.update(task, description="Building reference graph...")
            elif stage == PipelineStage.CLUSTERING:
                progress.update(task, description="Clustering components...")
            elif stage == PipelineStage.ANALYZING:
                progress.update(task, description="Analyzing graph metrics...")
            elif stage == PipelineStage.RENDERING:
                progress.update(task, description="Rendering Markdown vault...")
            elif stage == PipelineStage.WRITING:
                progress.update(task, description="Writing files to disk...")
            elif stage == PipelineStage.COMPLETED:
                progress.update(task, description="Done!")

        try:
            result = engine.run_pipeline(config, progress_callback=progress_callback)
        except RuntimeError as e:
            progress.update(task, description="Failed quality gate")
            console.print(f"[bold red]Quality gate failed:[/bold red] {e}")
            raise SystemExit(1) from e

    G = result.graph
    if G.number_of_nodes() == 0:
        console.print("[bold yellow]Completed build, but graph is empty.[/bold yellow]")
        return

    files_count = len(result.files)
    symbols_count = G.number_of_nodes() - files_count

    console.print(f"Found [green]{files_count}[/green] supported files to analyze.")
    console.print(
        f"Assembled graph with [green]{G.number_of_nodes()}[/green] nodes and [green]{G.number_of_edges()}[/green] edges."
    )
    console.print(f"  - Files: {files_count}")
    console.print(f"  - Symbols (Classes/Functions/Methods): {symbols_count}")

    if result.analysis.resolution is not None:
        res = result.analysis.resolution
        console.print(
            f"  - Edge resolution: [green]{res.resolved}[/green]/{res.attempted} "
            f"([cyan]{100.0 * res.resolve_rate:.1f}%[/cyan]), unresolved={res.unresolved}"
        )
        console.print(
            f"  - Internal resolution: [green]{res.internal_resolved}[/green]/"
            f"{res.internal_attempted} "
            f"([cyan]{100.0 * res.internal_resolve_rate:.1f}%[/cyan]), "
            f"internal_unresolved={res.internal_unresolved}"
        )
        console.print(
            f"  - Unresolved by category: external={res.category_unresolved('external')}, "
            f"builtin={res.category_unresolved('builtin')}, "
            f"attribute={res.category_unresolved('attribute')}, "
            f"internal={res.category_unresolved('internal')}"
        )

    write_stats = G.graph.get("vault_write_stats")
    if write_stats:
        console.print(
            f"  - Vault write: written={write_stats.get('written', 0)}, "
            f"skipped={write_stats.get('skipped', 0)}, removed={write_stats.get('removed', 0)}"
        )

    render_stats = G.graph.get("render_stats")
    if render_stats:
        console.print(
            f"  - Incremental render: nodes "
            f"{render_stats.get('nodes_rendered', 0)} rendered / "
            f"{render_stats.get('nodes_reused', 0)} reused; components "
            f"{render_stats.get('components_rendered', 0)} / "
            f"{render_stats.get('components_reused', 0)} "
            f"(dirty_files={render_stats.get('dirty_files', 0)})"
        )

    if export_json:
        console.print(
            f"  - JSON export: [underline]{config.absolute_output_dir / 'graph.json'}[/underline]"
        )

    console.print(
        "[bold green]Success! Codebase knowledge graph built successfully.[/bold green]"
    )

    table = Table(title="Logical Components Summary")
    table.add_column("Component Name", style="cyan", no_wrap=True)
    table.add_column("Cohesion (Density)", style="magenta")
    table.add_column("Size (Nodes)", style="green")

    for cid, members in result.components.items():
        table.add_row(
            result.component_names[cid],
            str(result.cohesion_scores[cid]),
            str(len(members)),
        )

    console.print(table)
    console.print(
        f"\nView the main graph entrypoint at: [bold underline]{config.absolute_output_dir}/README.md[/bold underline]"
    )
    console.print(
        f"💡 [bold yellow]AI Insight Tip:[/bold yellow] Ask your AI Agent (e.g. Antigravity, Claude Code, Codex) to read [bold]{config.absolute_output_dir}/AGENT_PROMPT.md[/bold] and write the architectural report directly to [bold]{config.absolute_output_dir}/README.md[/bold].\n"
    )


@cli.command()
@click.option(
    "--platform",
    "-p",
    default="codex",
    type=click.Choice(["codex", "antigravity", "crush"]),
    help="The AI agent platform to integrate with.",
)
def install(platform: str):
    """Installs the codegraph slash command into your AI Agent's global config."""
    console.print(
        f"[bold blue]Installing codegraph integration for {platform}...[/bold blue]"
    )

    # 1. Resolve skills directory based on target platform
    if platform == "codex":
        skills_dir = Path.home() / ".codex" / "skills" / "codegraph"
    elif platform == "antigravity":
        skills_dir = Path.home() / ".gemini" / "config" / "skills" / "codegraph"
    elif platform == "crush":
        skills_dir = Path.home() / ".config" / "crush" / "skills" / "codegraph"
    else:
        skills_dir = Path.home() / ".codex" / "skills" / "codegraph"

    # 2. Skill file content
    skill_content = """---
name: codegraph
description: "Build a Markdown codebase knowledge graph using codegraph, perform logical component clustering, analyze god nodes/circular dependencies, and write deep architectural insights to .codegraph/README.md."
trigger: /codegraph
---

# /codegraph

Build a codebase knowledge graph using `codegraph` for any folder, cluster symbols into logical components, detect god nodes and cycles, and perform a deep architectural analysis to write insights directly to the `.codegraph/README.md` vault.

## Usage

```
/codegraph                                            # Run the full build & AI analysis pipeline on the current directory
/codegraph <path>                                     # Run the pipeline on a specific subfolder/path
/codegraph --exclude <pattern>                        # Build and exclude specific folders/patterns
```

### Project Configuration File (`.codegraphrc`)

Projects can place a `.codegraphrc` JSON file in their root directory to persist build settings. When present, `codegraph build` automatically loads it — no extra flags needed.

```json
{
  "include": ["src", "tests"],
  "exclude": ["dist", "third_party"],
  "output": ".codegraph",
  "languages": ["python", "typescript"],
  "workers": 4,
  "cache": true
}
```

| Field | Type | Description |
|---|---|---|
| `include` | `string[]` | Subdirectory whitelist — only these dirs are scanned. Omit to scan the entire workspace. |
| `exclude` | `string[]` | Extra directory names to exclude (appended to built-in defaults). |
| `output` | `string` | Output directory, relative to workspace root. Default: `.codegraph` |
| `languages` | `string[]` | Language whitelist. Omit to include all supported languages. |
| `workers` | `int` | Number of parallel worker processes. |
| `cache` | `bool` | Enable incremental parse cache. Default: `true` |

CLI flags always take priority over `.codegraphrc` values. `--exclude` is cumulative (appends to the config file list).

## What You Must Do When Invoked

### Check for a project config file first

Before deciding which directory or flags to use, check if a `.codegraphrc` file exists in the project root:
- If it exists, read it to understand which directories the project wants scanned (`include`) and excluded (`exclude`). Honor those settings — do NOT override them with your own guesses.
- If it does NOT exist, follow the fallback rules below.

If the user invoked `/codegraph` with no path and there is NO `.codegraphrc`, do not ask the user for a path. Instead, prioritize targeting the primary source directory (e.g. `src/`, `lib/`, `app/`) and test directory (e.g. `tests/`, `test/`).
- If specific source or test folders are found, run the build targeting those folders, or build the root `.` but exclude other non-code/non-test directories (e.g., `docs/`, `scripts/`, `examples/`) using the `--exclude` flag to keep the graph focused on code and tests.
- Otherwise, default to `.` (current directory).

Follow these steps in order. Do not skip any steps.

### Step 1 - Ensure codegraph is installed

Check and locate the `codegraph` executable. To support virtual environments, resolve the binary in the following priority order:
1. Local virtual environment: `.venv/bin/codegraph` or `venv/bin/codegraph`
2. Global command: `codegraph` (installed globally or via uv tool)

You can use this shell logic to resolve the executable:
```bash
if [ -f ".venv/bin/codegraph" ]; then
    CODEGRAPH_BIN=".venv/bin/codegraph"
elif [ -f "venv/bin/codegraph" ]; then
    CODEGRAPH_BIN="venv/bin/codegraph"
else
    if ! command -v codegraph >/dev/null 2>&1; then
        uv tool install codegraph-gen
    fi
    CODEGRAPH_BIN="codegraph"
fi
echo "Using codegraph binary: $CODEGRAPH_BIN"
```

### Step 2 - Build the Knowledge Graph

Run the resolved `$CODEGRAPH_BIN` on the specified directory. If a `.codegraphrc` is present in the project root, simply run without extra flags — the config file is picked up automatically:
```bash
$CODEGRAPH_BIN build INPUT_PATH
# Or with additional exclude arguments if provided by the user
```
*(Replace `INPUT_PATH` with the resolved target path, e.g. `.`)*

If the command fails or errors out, capture the terminal stderr/logs, display them to the user with a helpful explanation, and ask them if they want to exclude specific directories or fix the errors. Do not fail silently.

### Step 3 - Perform Deep Architectural Analysis

Once the graph is built successfully:
1. Read the newly generated `<path>/.codegraph/AGENT_PROMPT.md` file using your file reading tools.
2. Read the project statistics, communities, god nodes, and cycle warnings from it.
3. Perform a deep, professional architectural review of the codebase (using **English** as the report language), combined with deep insight analysis of the code implementation of existing features.
4. Focus your review on:
   - **System Architecture Evaluation**: Explain the design patterns, modularity level, and alignment between physical directories and logical components in the codebase.
   - **Core Abstractions & Boundary Evaluation**: Deeply analyze God Nodes to determine which ones are core support and which ones have excessive responsibilities (God Object / Fat Class) that may lead to high risk.
   - **Potential Bottlenecks & Architectural Refactoring Recommendations**: Point out high-coupling risk points and negative impacts of circular dependencies, and provide specific, actionable refactoring optimization plans (e.g., decoupling, extracting interfaces, dependency inversion).
5. Read the existing `<path>/.codegraph/README.md` first. If there's an existing `## AI Architectural Insights` section, merge your new findings with it rather than silently overwriting and discarding previous edits.
6. Write the completed report into `<path>/.codegraph/README.md` under the `## AI Architectural Insights` section, replacing any placeholder instructions.

### Step 4 - Present Summary to the User

Finally, reply to the user in English, summarizing:
- The graph statistics (number of files, symbols, edges).
- The logical component summary (with sizes and cohesion scores).
- A brief bulleted summary of your key architectural findings and recommendations.
- Clickable markdown links pointing to:
  - The main entrypoint: `[README.md](file:///<absolute_path_to_vault>/README.md)`
  - The agent guidelines: `[AGENTS.md](file:///<absolute_path_to_vault>/AGENTS.md)`
  - The detailed components folder: `[components/](file:///<absolute_path_to_vault>/components/)`
"""

    try:
        skills_dir.mkdir(parents=True, exist_ok=True)
        skill_file = skills_dir / "SKILL.md"
        skill_file.write_text(skill_content, encoding="utf-8")
        console.print(
            f"[bold green]Successfully installed /codegraph slash command to: [underline]{skill_file}[/underline][/bold green]"
        )
    except Exception as e:
        console.print(f"[bold red]Failed to write skill configuration: {e}[/bold red]")


@cli.command()
@click.argument(
    "src_dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=".",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path),
    default=Path(".codegraph"),
    help="Directory where the Markdown vault was written.",
)
@click.option(
    "--open/--no-open",
    "open_browser",
    default=True,
    help="Automatically open the interactive HTML page in the browser.",
)
def visualize(src_dir: Path, output: Path, open_browser: bool):
    """Generates an interactive Plotly-based HTML visualization of the graph."""
    import sys

    console.print(
        "[bold blue]Generating interactive graph visualization...[/bold blue]"
    )

    workspace = src_dir.resolve()

    # Resolve output directory using same logic as build
    project_cfg = load_project_config(workspace)
    if output != Path(".codegraph"):
        resolved_output = output.resolve()
    elif project_cfg and project_cfg.output != ".codegraph":
        resolved_output = (workspace / project_cfg.output).resolve()
    else:
        resolved_output = (workspace / ".codegraph").resolve()

    try:
        from codegraph_gen.visualizer import generate_visualization

        html_path = generate_visualization(
            workspace, resolved_output, open_browser=open_browser
        )
        console.print(
            f"[bold green]Success![/bold green] Interactive graph exported to: [bold underline]{html_path}[/bold underline]"
        )
    except ImportError as e:
        console.print(f"[bold red]Error: {e}[/bold red]")
        sys.exit(1)
    except Exception as e:
        console.print(f"[bold red]Error generating visualization: {e}[/bold red]")
        sys.exit(1)


@cli.command()
def info():
    """Prints tool info and supported languages."""
    console.print(f"[bold]codegraph v{__version__}[/bold]")
    console.print(
        "Supported languages: Python, JavaScript, TypeScript, Kotlin, Go, Rust, Swift, C, C++, OCaml"
    )


def main():
    cli()


if __name__ == "__main__":
    main()
