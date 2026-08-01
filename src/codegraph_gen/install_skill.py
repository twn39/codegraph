"""Agent platform skill install helpers (paths + SKILL.md body)."""

from __future__ import annotations

from pathlib import Path

SUPPORTED_PLATFORMS = (
    "codex",
    "antigravity",
    "crush",
    "claude",
    "cursor",
    "gemini",
    "windsurf",
)

SKILL_MARKDOWN = """---
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
  "languages": ["python"],
  "workers": 4,
  "cache": true,
  "min_internal_resolve_rate": 0.95,
  "strict": false,
  "report_language": "en"
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
| `min_internal_resolve_rate` | `float` | CI gate for internal edge resolve rate (0.0–1.0). |
| `strict` | `bool` | Fail build when any file fails to parse. |
| `report_language` | `string` | Agent prompt language: `en` or `zh`. |

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
3. Perform a deep, professional architectural review of the codebase. Use English unless `.codegraphrc` / the user requests Chinese (`report_language: zh`).
4. Focus your review on:
   - **System Architecture Evaluation**: Explain the design patterns, modularity level, and alignment between physical directories and logical components in the codebase.
   - **Core Abstractions & Boundary Evaluation**: Deeply analyze God Nodes to determine which ones are core support and which ones have excessive responsibilities (God Object / Fat Class) that may lead to high risk.
   - **Potential Bottlenecks & Architectural Refactoring Recommendations**: Point out high-coupling risk points and negative impacts of circular dependencies, and provide specific, actionable refactoring optimization plans (e.g., decoupling, extracting interfaces, dependency inversion).
5. Read the existing `<path>/.codegraph/README.md` first. If there's an existing `## AI Architectural Insights` section, merge your new findings with it rather than silently overwriting and discarding previous edits.
6. Write the completed report into `<path>/.codegraph/README.md` under the `## AI Architectural Insights` section (keep this English heading), replacing any placeholder instructions.

### Step 4 - Present Summary to the User

Finally, reply to the user summarizing:
- The graph statistics (number of files, symbols, edges).
- The logical component summary (with sizes and cohesion scores).
- A brief bulleted summary of your key architectural findings and recommendations.
- Clickable markdown links pointing to:
  - The main entrypoint: `[README.md](file:///<absolute_path_to_vault>/README.md)`
  - The agent guidelines: `[AGENTS.md](file:///<absolute_path_to_vault>/AGENTS.md)`
  - The detailed components folder: `[components/](file:///<absolute_path_to_vault>/components/)`
"""


def skills_dir_for_platform(platform: str, *, home: Path | None = None) -> Path:
    """Map agent platform name to its global skills directory."""
    root = home if home is not None else Path.home()
    platform = platform.lower()
    mapping = {
        "codex": root / ".codex" / "skills" / "codegraph",
        "antigravity": root / ".gemini" / "config" / "skills" / "codegraph",
        "crush": root / ".config" / "crush" / "skills" / "codegraph",
        "claude": root / ".claude" / "skills" / "codegraph",
        "cursor": root / ".cursor" / "skills" / "codegraph",
        "gemini": root / ".gemini" / "config" / "skills" / "codegraph",
        "windsurf": root / ".codeium" / "windsurf" / "skills" / "codegraph",
    }
    return mapping.get(platform, root / ".codex" / "skills" / "codegraph")


def write_skill(skills_dir: Path, content: str = SKILL_MARKDOWN) -> Path:
    """Write SKILL.md under *skills_dir* and return the file path."""
    skills_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skills_dir / "SKILL.md"
    skill_file.write_text(content, encoding="utf-8")
    return skill_file
