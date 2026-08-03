import logging
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
    found_files = []
    workspace = workspace_dir.resolve()

    # Map extension -> language
    ext_to_lang = {}
    for lang in languages:
        if lang in LANGUAGE_EXTENSIONS:
            for ext in LANGUAGE_EXTENSIONS[lang]:
                ext_to_lang[ext] = lang

    # Normalize exclusions to lowercase for case-insensitive matching
    exclusions_lower = {exc.lower() for exc in exclusions}

    def is_ignored(path: Path) -> bool:
        # Check if any part of the path is in exclusions_lower
        try:
            rel_parts = path.relative_to(workspace).parts
        except ValueError:
            # Not under workspace
            return True

        for part in rel_parts:
            if part.lower() in exclusions_lower:
                return True
        return False

    def scan_dir(directory: Path):
        try:
            for item in directory.iterdir():
                if is_ignored(item):
                    continue
                if item.is_dir():
                    scan_dir(item)
                elif item.is_file():
                    ext = item.suffix.lower()
                    if ext in ext_to_lang:
                        resolved_path = item.resolve()
                        try:
                            resolved_path.relative_to(workspace)
                            found_files.append((resolved_path, ext_to_lang[ext]))
                        except ValueError:
                            # Skip symlink target that lies outside workspace
                            continue
        except PermissionError:
            logger.warning(f"Permission denied: {directory}")
        except Exception as e:
            logger.error(f"Error scanning {directory}: {e}")

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
            scan_dir(root)
    else:
        scan_dir(workspace)

    return found_files
