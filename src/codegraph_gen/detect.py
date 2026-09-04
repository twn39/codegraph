import logging
import os
from pathlib import Path
from codegraph_gen.config import LANGUAGE_EXTENSIONS

logger = logging.getLogger(__name__)


def discover_files(
    workspace_dir: Path,
    languages: set[str],
    exclusions: set[str],
    include_dirs: list[Path] | None = None,
) -> list[tuple[Path, str]]:
    """
    Recursively discovers source files in the workspace directory.
    Filters by allowed languages and ignores files/directories in exclusions.

    Args:
        workspace_dir: Root of the workspace (used to compute relative paths / node IDs).
        languages: Set of language names to include.
        exclusions: Directory names/patterns to exclude.
        include_dirs: Optional whitelist of absolute directories to scan.
                      When provided, only these directories are scanned.
                      When None, the entire workspace_dir is scanned.

    Returns:
        List of tuples: (absolute_file_path, language_name)
    """
    found_files: list[tuple[Path, str]] = []
    workspace = workspace_dir.resolve()
    workspace_str = str(workspace)

    # Map extension -> language
    ext_to_lang: dict[str, str] = {}
    for lang in languages:
        if lang in LANGUAGE_EXTENSIONS:
            for ext in LANGUAGE_EXTENSIONS[lang]:
                ext_to_lang[ext.lower()] = lang

    name_exclusions: set[str] = set()
    path_exclusions: set[str] = set()
    for exc in exclusions:
        exc_norm = exc.strip("/\\").lower().replace("\\", "/")
        if "/" in exc_norm:
            path_exclusions.add(exc_norm)
        else:
            name_exclusions.add(exc_norm)

    def is_path_excluded(rel_parts: tuple[str, ...], rel_str: str) -> bool:
        for part in rel_parts:
            if part in name_exclusions:
                return True
        if path_exclusions:
            for pe in path_exclusions:
                if rel_str == pe or rel_str.startswith(pe + "/"):
                    return True
        return False

    def scan_dir(dir_path: str, rel_parts: tuple[str, ...]) -> None:
        try:
            with os.scandir(dir_path) as entries:
                for entry in entries:
                    name_lower = entry.name.lower()
                    if name_lower in name_exclusions:
                        continue
                    child_rel_parts = rel_parts + (name_lower,)
                    child_rel_str = "/".join(child_rel_parts)
                    if path_exclusions and any(
                        child_rel_str == pe or child_rel_str.startswith(pe + "/")
                        for pe in path_exclusions
                    ):
                        continue

                    try:
                        is_d = entry.is_dir()
                    except OSError:
                        continue

                    if is_d:
                        scan_dir(entry.path, child_rel_parts)
                    else:
                        try:
                            is_f = entry.is_file()
                        except OSError:
                            continue
                        if is_f:
                            dot_idx = name_lower.rfind(".")
                            if dot_idx != -1:
                                ext = name_lower[dot_idx:]
                                if ext in ext_to_lang:
                                    p = Path(entry.path).resolve()
                                    try:
                                        p.relative_to(workspace)
                                        found_files.append((p, ext_to_lang[ext]))
                                    except ValueError:
                                        continue
        except PermissionError:
            logger.warning(f"Permission denied: {dir_path}")
        except Exception as e:
            logger.error(f"Error scanning {dir_path}: {e}")

    # Determine which root directories to scan
    if include_dirs:
        for root in include_dirs:
            root = root.resolve()
            if not root.exists():
                logger.warning(f"include_dirs entry does not exist, skipping: {root}")
                continue
            if not root.is_dir():
                logger.warning(
                    f"include_dirs entry is not a directory, skipping: {root}"
                )
                continue
            try:
                init_rel = root.relative_to(workspace)
                init_parts = tuple(p.lower() for p in init_rel.parts)
            except ValueError:
                init_parts = ()
            if is_path_excluded(init_parts, "/".join(init_parts)):
                continue
            scan_dir(str(root), init_parts)
    else:
        scan_dir(workspace_str, ())

    return found_files
