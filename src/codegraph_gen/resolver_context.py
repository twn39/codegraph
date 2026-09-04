"""
resolver_context.py
===================
Immutable value object (ResolutionContext) shared across all symbol
resolver functions, plus the STOP sentinel used to signal explicit
early termination of the resolver chain.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from codegraph_gen.scope import FileSymbolScope

if TYPE_CHECKING:
    from codegraph_gen.resolver_strategy import LanguageResolverStrategy


# ---------------------------------------------------------------------------
# STOP sentinel
# ---------------------------------------------------------------------------


class _StopResolution:
    """
    Singleton sentinel returned by a resolver function to signal that
    resolution has definitively failed and no further steps should be tried.

    Use this (instead of ``None``) when the caller's intent is unambiguous
    but the target cannot be resolved, e.g.:

    - A typed local variable (``foo: MyClass``) is found in ``local_bindings``
      but its class definition cannot be located → return ``STOP`` to avoid
      incorrect global-fallback guessing.
    - A builtin/stdlib symbol is detected → return ``STOP`` immediately.

    Returning ``None`` from a resolver means "I cannot handle this, try the
    next step."  Returning ``STOP`` means "I handled this decisively; abort."
    """

    _instance: "_StopResolution | None" = None

    def __new__(cls) -> "_StopResolution":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "STOP"

    def __bool__(self) -> bool:
        return False


#: The singleton sentinel instance.  Import this, not the class.
STOP = _StopResolution()


# ---------------------------------------------------------------------------
# ResolutionContext
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolutionContext:
    """
    Immutable value object that captures every input needed to resolve a
    single symbol reference.  Built once per ``resolve_symbol()`` call and
    passed (read-only) through the entire resolver chain.

    Using ``frozen=True`` enforces purity: each resolver function must not
    mutate shared state, keeping them side-effect-free and easily testable.
    """

    # ── call site ──────────────────────────────────────────────────────────
    caller_id: str
    """Graph node ID of the symbol that contains the reference (caller)."""

    source_file: str
    """Relative path of the file that owns ``caller_id``."""

    # ── parsed callee ──────────────────────────────────────────────────────
    callee_name: str
    """Raw callee string as extracted from the AST."""

    parts: tuple[str, ...]
    """``callee_name`` split on '.' after normalising '::' → '.'.
    Stored as a tuple so that the frozen dataclass is hashable."""

    main_symbol: str
    """``parts[0]`` — the first segment of the dotted callee."""

    rest_of_callee: str
    """Everything after the first '.' in the normalised callee, or ''."""

    # ── language strategy ──────────────────────────────────────────────────
    strategy: "LanguageResolverStrategy"
    """Language-specific resolver strategy for the caller's file."""

    # ── scope information ──────────────────────────────────────────────────
    scope: FileSymbolScope
    """File-level scope (imports, declared symbols) for ``source_file``."""

    local_bindings: MappingProxyType  # MappingProxyType[str, str]
    """Read-only view of the caller's typed local variable bindings
    (e.g. ``{"foo": "MyClass"}`` for ``foo: MyClass = ...``)."""

    # ── graph indexes (read-only references) ───────────────────────────────
    node_ids: frozenset  # frozenset[str]
    """Frozenset of all graph node IDs.  Used for fast membership tests."""

    graph_nodes: Any
    """``G.nodes`` proxy — already read-only; no copying needed."""

    global_symbol_map: MappingProxyType  # MappingProxyType[str, list[str]]
    """Read-only mapping from label → list of node IDs with that label."""

    return_types: MappingProxyType  # MappingProxyType[str, str]
    """Read-only map of function/method node id → declared return type name.
    Used for multi-hop attribute chains such as ``o.inner().value()``."""

    # ── fast precomputed indexes (read-only references) ────────────────────
    class_like_symbols: MappingProxyType = MappingProxyType({})
    """Read-only map of symbol label → list/tuple of class/struct/interface/enum node IDs."""

    methods_by_class: MappingProxyType = MappingProxyType({})
    """Read-only map of class node ID → {method_name: method_node_id}."""

    dir_to_symbols: MappingProxyType = MappingProxyType({})
    """Read-only map of directory path string → tuple of symbol node IDs (for package-level scope)."""

    file_to_nodes: MappingProxyType = MappingProxyType({})
    """Read-only map of source_file path string → tuple of contained node IDs."""
