"""Path heuristics shared by clustering, analysis, and reporting."""

from __future__ import annotations

from pathlib import Path

# Directory segments that typically hold tests
_TEST_DIR_NAMES = frozenset(
    {
        "tests",
        "test",
        "__tests__",
        "spec",
        "specs",
        "testing",
    }
)

# File name suffixes/prefixes that mark test sources
_TEST_FILE_SUFFIXES = (
    "_test.py",
    "_test.go",
    "_test.rs",
    "_test.swift",
    "_test.kt",
    "_test.ts",
    "_test.tsx",
    "_test.js",
    ".test.ts",
    ".test.tsx",
    ".test.js",
    ".spec.ts",
    ".spec.tsx",
    ".spec.js",
    "Test.java",
    "Tests.swift",
)


def is_test_path(path: str) -> bool:
    """Return True if *path* looks like a test file or lives under a test directory."""
    if not path:
        return False
    normalized = path.replace("\\", "/")
    parts = [p for p in normalized.split("/") if p]
    if any(p.lower() in _TEST_DIR_NAMES for p in parts):
        return True
    name = Path(normalized).name
    lower = name.lower()
    if lower.startswith("test_") or lower.startswith("test-"):
        return True
    return any(
        name.endswith(suf) or lower.endswith(suf.lower()) for suf in _TEST_FILE_SUFFIXES
    )
