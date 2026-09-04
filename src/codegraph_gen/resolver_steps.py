"""
resolver_steps.py
=================
Pure, stateless resolver functions implementing each step of the symbol
resolution fallback chain.

Each function has the same signature::

    def resolve_xxx(ctx: ResolutionContext) -> str | _StopResolution | None

Return semantics
----------------
- ``str``             — a graph node ID was found; resolution succeeds.
- ``None``            — this step cannot handle the symbol; try the next step.
- ``STOP`` sentinel   — resolution has definitively failed; abort the chain
                        (even if later steps might produce a guess).

The default chain is exported as ``DEFAULT_RESOLVER_CHAIN``, an ordered
list of callables.  It replaces the 9-step ``if/elif/return`` sequence
that was previously embedded inside ``TypeResolver.resolve_symbol()``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from codegraph_gen.resolver_context import ResolutionContext, STOP, _StopResolution

# Common builtin/standard library method names to avoid incorrect resolution during global fallback
COMMON_BUILTIN_METHODS: set[str] = {
    "append",
    "decode",
    "encode",
    "insert",
    "remove",
    "contains",
    "push",
    "pop",
    "split",
    "join",
    "map",
    "filter",
    "reduce",
    "forEach",
    "sorted",
    "count",
    "length",
    "size",
    "isEmpty",
    "resume",
    "cancel",
    "suspend",
    "start",
    "stop",
    "send",
    "receive",
    "len",
    "new",
    "is_empty",
    "clone",
    "default",
    "parse",
    "format",
    "read",
    "write",
    "close",
    "flush",
    "to_string",
    "to_str",
    "as_str",
    "as_ref",
    "as_mut",
    "unwrap",
    "expect",
    "iter",
    "iter_mut",
    "into_iter",
    "next",
    "into",
    "from",
    "ok",
    "err",
    "clear",
    "get",
    "set",
    "add",
    "keys",
    "values",
    "items",
    "update",
    "copy",
    "find",
    "index",
    "last",
    "first",
}


logger = logging.getLogger(__name__)

# Type alias for a single resolution step
ResolverFn = Callable[[ResolutionContext], "str | _StopResolution | None"]


# ---------------------------------------------------------------------------
# Step 1 — Builtin / Stdlib guard
# ---------------------------------------------------------------------------


def guard_builtin(ctx: ResolutionContext) -> str | _StopResolution | None:
    """
    Reject symbols that are builtins or stdlib identifiers for the caller's
    language.  Returns ``STOP`` immediately to prevent any further guessing.
    """
    if ctx.strategy.is_builtin(ctx.main_symbol):
        return STOP
    return None


# ---------------------------------------------------------------------------
# Step 2 — Local binding (typed variable)
# ---------------------------------------------------------------------------


def resolve_local_binding(ctx: ResolutionContext) -> str | _StopResolution | None:
    """
    Resolve ``foo.bar()`` where ``foo`` is a typed local variable
    (i.e. ``foo`` appears in ``local_bindings`` with a type annotation).

    If ``foo`` is in ``local_bindings`` but the class cannot be located,
    returns ``STOP`` to prevent incorrect global-fallback guessing — the
    intent of the call is unambiguous even if the class is missing.
    """
    if len(ctx.parts) <= 1 or ctx.main_symbol not in ctx.local_bindings:
        return None

    result = _resolve_local_binding_impl(ctx)
    # Explicit short-circuit: typed var was found but class is unresolvable
    return result if result else STOP


def _normalize_type_name(type_name: str) -> str:
    """Strip constructor call syntax and generic noise from a binding type."""
    name = type_name.strip()
    if name.endswith("()"):
        name = name[:-2].strip()
    # Drop simple generic args: List[Foo] → keep as-is for id lookup; Foo[T] → Foo
    if "[" in name:
        name = name.split("[", 1)[0].strip()
    # Prefer trailing segment: pkg.mod.Client → Client for label search
    if "." in name and name not in ("",):
        # Keep full string for node-id match first; callers try both
        pass
    return name


def _lookup_class_id(
    receiver_type: str,
    *,
    source_file: str,
    scope,
    node_ids,
    graph_nodes,
    strategy,
    class_like_symbols=None,
) -> str | None:
    """Resolve a type name / node id to a class-like graph node id."""
    candidates = [receiver_type, _normalize_type_name(receiver_type)]
    short = _normalize_type_name(receiver_type).rsplit(".", 1)[-1]
    if short not in candidates:
        candidates.append(short)

    for cand in candidates:
        if cand in node_ids:
            return cand
        file_local = f"{source_file}::{cand}"
        if file_local in node_ids:
            return file_local

    for cand in candidates:
        if cand in scope.imported_symbols:
            target_file_id, original_name = scope.imported_symbols[cand]
            class_id = f"{target_file_id}::{original_name}"
            if class_id in node_ids:
                return class_id

    caller_dir = Path(source_file).parent
    for cand in candidates:
        if class_like_symbols is not None and cand in class_like_symbols:
            matched_nids = class_like_symbols[cand]
        else:
            matched_nids = [
                nid
                for nid in node_ids
                if graph_nodes[nid].get("type")
                in ("class", "struct", "interface", "enum")
                and graph_nodes[nid].get("label") == cand
            ]

        if strategy.has_package_sibling_scope():
            for nid in matched_nids:
                node_file = graph_nodes[nid].get("source_file", "")
                if node_file and Path(node_file).parent == caller_dir:
                    return nid

        if matched_nids:
            return matched_nids[0]

    return None


def _find_method_on_class(
    resolved_class_id: str,
    method_name: str,
    rest_of_callee: str,
    *,
    node_ids,
    graph_nodes,
    receiver_type: str,
    global_symbol_map=None,
    methods_by_class=None,
) -> str | None:
    """Locate a method/function node belonging to *resolved_class_id*."""
    for candidate in (
        f"{resolved_class_id}.{rest_of_callee}",
        f"{resolved_class_id}.{method_name}",
    ):
        if candidate in node_ids:
            return candidate

    if methods_by_class and resolved_class_id in methods_by_class:
        method_id = methods_by_class[resolved_class_id].get(method_name)
        if method_id:
            return method_id

    class_label = graph_nodes[resolved_class_id].get("label", "")
    type_norm = _normalize_type_name(receiver_type)
    short_type = type_norm.rsplit(".", 1)[-1]

    # Search candidates matching method_name from global_symbol_map (O(1) label index),
    # avoiding O(N) full graph scans!
    candidate_nids = (
        global_symbol_map.get(method_name, ())
        if global_symbol_map is not None
        else node_ids
    )

    for nid in candidate_nids:
        ndata = graph_nodes[nid]
        if ndata.get("type") not in ("method", "function"):
            continue
        if ndata.get("label") != method_name:
            continue
        parent_class_part = nid.rsplit(".", 1)[0] if "." in nid else ""
        if parent_class_part == resolved_class_id:
            return nid
        parent_class_name = (
            parent_class_part.rsplit("::", 1)[-1]
            if "::" in parent_class_part
            else parent_class_part
        )
        if parent_class_name in {class_label, type_norm, short_type, receiver_type}:
            return nid
        if parent_class_name.endswith(f".{short_type}") or parent_class_name.endswith(
            f".{class_label}"
        ):
            return nid
    return None


def _strip_call_segment(segment: str) -> str:
    """Normalize ``inner()`` / ``inner(x)`` segments to the bare name."""
    name = segment.strip()
    if "(" in name:
        name = name.split("(", 1)[0].strip()
    return name


def _transfer_return_type(
    method_id: str,
    ctx: ResolutionContext,
) -> str | None:
    """Map a method/function node to its return type class id when known."""
    ret_name = None
    if ctx.return_types:
        ret_name = ctx.return_types.get(method_id)
    if not ret_name:
        # Fall back to signature extraction via graph node fields is not available
        # here; TypeResolver pre-fills return_types for all methods.
        return None
    return _lookup_class_id(
        ret_name,
        source_file=ctx.graph_nodes[method_id].get("source_file", ctx.source_file),
        scope=ctx.scope,
        node_ids=ctx.node_ids,
        graph_nodes=ctx.graph_nodes,
        strategy=ctx.strategy,
        class_like_symbols=getattr(ctx, "class_like_symbols", None),
    )


def _resolve_local_binding_impl(ctx: ResolutionContext) -> str | None:
    """Resolve ``foo.bar`` / ``foo.bar().baz()`` chains via typed locals + return types."""
    receiver_type = ctx.local_bindings[ctx.main_symbol]
    parts = ctx.parts

    resolved_class_id = _lookup_class_id(
        receiver_type,
        source_file=ctx.source_file,
        scope=ctx.scope,
        node_ids=ctx.node_ids,
        graph_nodes=ctx.graph_nodes,
        strategy=ctx.strategy,
        class_like_symbols=getattr(ctx, "class_like_symbols", None),
    )
    if not resolved_class_id:
        return None

    segments = list(parts[1:])
    if not segments:
        return resolved_class_id

    current_class_id = resolved_class_id
    current_receiver_type = receiver_type
    last_method_id: str | None = None

    for index, segment in enumerate(segments):
        method_name = _strip_call_segment(segment)
        if not method_name:
            return None
        method_id = _find_method_on_class(
            current_class_id,
            method_name,
            method_name,
            node_ids=ctx.node_ids,
            graph_nodes=ctx.graph_nodes,
            receiver_type=current_receiver_type,
            global_symbol_map=getattr(ctx, "global_symbol_map", None),
            methods_by_class=getattr(ctx, "methods_by_class", None),
        )
        if not method_id:
            return last_method_id if index == 0 else None
        last_method_id = method_id
        is_last = index == len(segments) - 1
        if is_last:
            return method_id
        # Intermediate call/attribute: advance receiver via declared return type
        next_class = _transfer_return_type(method_id, ctx)
        if not next_class:
            return None
        current_class_id = next_class
        current_receiver_type = ctx.graph_nodes[next_class].get("label", next_class)

    return last_method_id


# ---------------------------------------------------------------------------
# Step 3 — self / this / cls reference
# ---------------------------------------------------------------------------


def resolve_self_reference(ctx: ResolutionContext) -> str | _StopResolution | None:
    """
    Resolve ``self.foo``, ``this.foo``, or ``cls.foo`` to a sibling member
    of the enclosing class.

    Returns ``None`` (not ``STOP``) on failure so that later steps can still
    attempt resolution via class context or file scope.
    """
    if ctx.main_symbol not in ("self", "this", "cls"):
        return None

    caller_id = ctx.caller_id
    parts = ctx.parts
    rest_of_callee = ctx.rest_of_callee
    node_ids = ctx.node_ids

    if "." in caller_id:
        parent_class_id = caller_id.rsplit(".", 1)[0]
        if rest_of_callee:
            target_candidate = f"{parent_class_id}.{rest_of_callee}"
            if target_candidate in node_ids:
                return target_candidate
            target_candidate = f"{parent_class_id}.{parts[-1]}"
            if target_candidate in node_ids:
                return target_candidate
    return None


# ---------------------------------------------------------------------------
# Step 4 — Current class context
# ---------------------------------------------------------------------------


def resolve_current_class(ctx: ResolutionContext) -> str | _StopResolution | None:
    """
    Resolve sibling members called without an explicit receiver, from within
    a method of the same class (e.g. calling ``helper()`` inside ``MyClass``).
    """
    caller_id = ctx.caller_id
    main_symbol = ctx.main_symbol
    rest_of_callee = ctx.rest_of_callee
    node_ids = ctx.node_ids

    if "." in caller_id:
        parent_class_id = caller_id.rsplit(".", 1)[0]
        target_candidate = f"{parent_class_id}.{main_symbol}"
        if target_candidate in node_ids:
            if rest_of_callee:
                sub_target = f"{target_candidate}.{rest_of_callee}"
                if sub_target in node_ids:
                    return sub_target
            return target_candidate
    return None


# ---------------------------------------------------------------------------
# Step 5 — File-level scope
# ---------------------------------------------------------------------------


def resolve_file_scope(ctx: ResolutionContext) -> str | _StopResolution | None:
    """
    Resolve symbols declared at the top level of the same file
    (e.g. module-level classes or functions).
    """
    source_file = ctx.source_file
    main_symbol = ctx.main_symbol
    rest_of_callee = ctx.rest_of_callee
    node_ids = ctx.node_ids

    file_candidate = f"{source_file}::{main_symbol}"
    if file_candidate in node_ids:
        if rest_of_callee:
            sub_target = f"{file_candidate}.{rest_of_callee}"
            if sub_target in node_ids:
                return sub_target
        return file_candidate
    return None


# ---------------------------------------------------------------------------
# Step 6 — Package / sibling scope (Go, Swift)
# ---------------------------------------------------------------------------


def resolve_package_siblings(ctx: ResolutionContext) -> str | _StopResolution | None:
    """
    Resolve symbols declared in sibling files of the same package directory.
    Only active for languages with package-level scope (Go, Swift).

    Self-guarding: returns ``None`` immediately for other languages,
    removing the need for an ``if strategy.has_package_sibling_scope()``
    check in the calling code.
    """
    if not ctx.strategy.has_package_sibling_scope():
        return None

    source_file = ctx.source_file
    main_symbol = ctx.main_symbol
    rest_of_callee = ctx.rest_of_callee
    node_ids = ctx.node_ids
    graph_nodes = ctx.graph_nodes

    caller_dir = str(Path(source_file).parent)
    candidate_nids = (
        ctx.dir_to_symbols.get(caller_dir, ())
        if getattr(ctx, "dir_to_symbols", None)
        else None
    )
    if candidate_nids is not None:
        target_suffix = f"::{main_symbol}"
        for nid in candidate_nids:
            if nid.endswith(target_suffix):
                if rest_of_callee:
                    sub_target = f"{nid}.{rest_of_callee}"
                    if sub_target in node_ids:
                        return sub_target
                return nid
    else:
        caller_dir_path = Path(source_file).parent
        for nid in node_ids:
            ndata = graph_nodes[nid]
            if ndata.get("type") == "file":
                continue
            node_file = ndata.get("source_file", "")
            if node_file and Path(node_file).parent == caller_dir_path:
                if nid.endswith(f"::{main_symbol}"):
                    if rest_of_callee:
                        sub_target = f"{nid}.{rest_of_callee}"
                        if sub_target in node_ids:
                            return sub_target
                    return nid
    return None


# ---------------------------------------------------------------------------
# Step 7 — Explicit imports & aliases
# ---------------------------------------------------------------------------


def resolve_explicit_imports(ctx: ResolutionContext) -> str | _StopResolution | None:
    """
    Resolve symbols that were explicitly imported in the caller's file,
    including aliased imports (e.g. ``import X as Y``, ``from A import B``).

    Self-guarding: returns ``None`` if ``main_symbol`` is not in the
    file's imported_symbols map.
    """
    scope = ctx.scope
    if ctx.main_symbol not in scope.imported_symbols:
        return None

    main_symbol = ctx.main_symbol
    rest_of_callee = ctx.rest_of_callee
    parts = ctx.parts
    node_ids = ctx.node_ids
    graph_nodes = ctx.graph_nodes

    target_file_id, original_name = scope.imported_symbols[main_symbol]
    if original_name == "*" or original_name == Path(target_file_id).stem:
        if rest_of_callee:
            target_candidate = f"{target_file_id}::{rest_of_callee}"
            if target_candidate in node_ids:
                return target_candidate
            file_candidates = (
                ctx.file_to_nodes.get(target_file_id, ())
                if getattr(ctx, "file_to_nodes", None)
                else None
            )
            if file_candidates is not None:
                leaf_suffix = f".{parts[-1]}"
                for nid in file_candidates:
                    if nid.endswith(leaf_suffix):
                        return nid
            else:
                for nid in node_ids:
                    if graph_nodes[nid].get(
                        "source_file"
                    ) == target_file_id and nid.endswith(f".{parts[-1]}"):
                        return nid
        else:
            target_candidate = f"{target_file_id}::{main_symbol}"
            if target_candidate in node_ids:
                return target_candidate
            return target_file_id
    else:
        target_candidate = f"{target_file_id}::{original_name}"
        if target_candidate in node_ids:
            if rest_of_callee:
                sub_target = f"{target_candidate}.{rest_of_callee}"
                if sub_target in node_ids:
                    return sub_target
            return target_candidate
        return target_candidate
    return None


# ---------------------------------------------------------------------------
# Step 8 — Wildcard imports
# ---------------------------------------------------------------------------


def resolve_wildcard_imports(ctx: ResolutionContext) -> str | _StopResolution | None:
    """
    Resolve symbols that may have been pulled in by a wildcard import
    (``from X import *``).
    """
    scope = ctx.scope
    main_symbol = ctx.main_symbol
    rest_of_callee = ctx.rest_of_callee
    node_ids = ctx.node_ids

    for target_file_id in scope.wildcard_imports:
        target_candidate = f"{target_file_id}::{main_symbol}"
        if target_candidate in node_ids:
            if rest_of_callee:
                sub_target = f"{target_candidate}.{rest_of_callee}"
                if sub_target in node_ids:
                    return sub_target
            return target_candidate
    return None


# ---------------------------------------------------------------------------
# Step 9 — Global symbol map fallback
# ---------------------------------------------------------------------------


def resolve_global_fallback(ctx: ResolutionContext) -> str | _StopResolution | None:
    """
    Last-resort lookup in the global symbol map (label → node IDs).

    Prefers unambiguous matches (exactly one candidate globally, or exactly
    one candidate in the same directory as the caller).

    Uses ``strategy.is_builtin()`` instead of a hardcoded cross-language
    blocklist, so it correctly rejects stdlib identifiers for every language.
    """
    main_symbol = ctx.main_symbol
    parts = ctx.parts
    source_file = ctx.source_file
    graph_nodes = ctx.graph_nodes

    # Prevent global guessing for built-ins, standard library modules, or imported symbols
    # that could not be resolved locally (i.e. external dependencies).
    if (
        ctx.strategy.is_builtin(main_symbol)
        or ctx.strategy.is_stdlib(main_symbol)
        or main_symbol in ctx.scope.imported_symbols
    ):
        return None

    search_label = parts[-1] if len(parts) > 1 else main_symbol
    if len(parts) > 1 and search_label in COMMON_BUILTIN_METHODS:
        return None

    candidates = ctx.global_symbol_map.get(search_label, [])
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        caller_parent_dir = Path(source_file).parent
        near_candidates = [
            c
            for c in candidates
            if Path(graph_nodes[c]["source_file"]).parent == caller_parent_dir
        ]
        if len(near_candidates) == 1:
            return near_candidates[0]
    return None


# ---------------------------------------------------------------------------
# Default resolver chain (ordered list — this IS the configuration)
# ---------------------------------------------------------------------------

DEFAULT_RESOLVER_CHAIN: list[ResolverFn] = [
    guard_builtin,  # Step 1: reject stdlib/builtins immediately
    resolve_local_binding,  # Step 2: typed local variable (foo: MyClass → foo.method())
    resolve_self_reference,  # Step 3: self.foo / this.foo / cls.foo
    resolve_current_class,  # Step 4: sibling members within current class
    resolve_file_scope,  # Step 5: file-level declarations
    resolve_package_siblings,  # Step 6: Go/Swift package-level siblings (self-guarding)
    resolve_explicit_imports,  # Step 7: explicitly imported symbols (self-guarding)
    resolve_wildcard_imports,  # Step 8: wildcard-imported symbols
    resolve_global_fallback,  # Step 9: last-resort global symbol map lookup
]
