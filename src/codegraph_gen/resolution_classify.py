"""Classify edge resolution attempts for quality metrics and CI gates.

Categories
----------
- ``internal``  — targets expected to resolve within the workspace graph
- ``external``  — stdlib / third-party / unknown modules or simple names
- ``builtin``   — language builtins or common method names (append, get, …)
- ``attribute`` — multi-part attribute / method chains that failed to bind
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from codegraph_gen.resolver_steps import COMMON_BUILTIN_METHODS

if TYPE_CHECKING:
    from codegraph_gen.resolver_strategy.base import LanguageResolverStrategy

ResolutionCategory = Literal["internal", "external", "builtin", "attribute"]

CATEGORIES: tuple[ResolutionCategory, ...] = (
    "internal",
    "external",
    "builtin",
    "attribute",
)

_PATH_LIKE_SUFFIXES = (
    ".py",
    ".go",
    ".rs",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".kt",
    ".kts",
    ".swift",
    ".c",
    ".h",
    ".cpp",
    ".cc",
    ".cxx",
    ".hpp",
    ".ml",
    ".mli",
)


def _empty_bucket() -> dict[str, int]:
    return {"attempted": 0, "resolved": 0, "unresolved": 0}


def empty_category_stats() -> dict[str, dict[str, int]]:
    return {cat: _empty_bucket() for cat in CATEGORIES}


def classify_edge_attempt(
    *,
    relation: str,
    target: str,
    resolved: bool,
    global_symbol_map: dict[str, list[str]],
    strategy: LanguageResolverStrategy | None = None,
) -> ResolutionCategory:
    """
    Assign a resolution category to a single edge resolution attempt.

    Resolved edges that land on workspace nodes are always ``internal``.
    Unresolved edges are classified by target shape and known symbol tables.
    """
    if resolved:
        return "internal"

    if relation == "contains":
        # Failed contains edges are graph integrity issues (always internal).
        return "internal"

    if relation == "imports":
        return _classify_import_target(target, strategy)

    return _classify_symbol_target(target, global_symbol_map, strategy)


def _classify_import_target(
    target: str,
    strategy: LanguageResolverStrategy | None,
) -> ResolutionCategory:
    if strategy is not None:
        root = target.split(".", 1)[0].split("/", 1)[0]
        if strategy.is_stdlib(root) or strategy.is_builtin(root):
            return "external"
        if strategy.is_path_target(target):
            return "internal"

    if (
        target.startswith(".")
        or "/" in target
        or "\\" in target
        or target.endswith(_PATH_LIKE_SUFFIXES)
    ):
        return "internal"

    return "external"


def _classify_symbol_target(
    target: str,
    global_symbol_map: dict[str, list[str]],
    strategy: LanguageResolverStrategy | None,
) -> ResolutionCategory:
    clean = target.replace("::", ".")
    parts = [p.strip() for p in clean.split(".") if p.strip()]
    if not parts:
        return "external"

    main = parts[0]
    last = parts[-1]

    if strategy is not None and strategy.is_builtin(main):
        return "builtin"
    if strategy is not None and strategy.is_stdlib(main):
        return "external"

    if len(parts) > 1:
        if last in COMMON_BUILTIN_METHODS and main not in global_symbol_map:
            return "builtin"
        return "attribute"

    # Simple name: known workspace label → internal miss; else external noise
    if main in global_symbol_map:
        return "internal"
    return "external"
