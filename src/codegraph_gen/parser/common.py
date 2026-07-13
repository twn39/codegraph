"""Shared helpers for language-specific AST visitors."""

from __future__ import annotations

from typing import Any

import tree_sitter

from codegraph_gen.parser.base import (
    ASTParsingContext,
    ASTVisitor,
    get_line_range,
    get_node_text,
)
from codegraph_gen.schema import EdgeSchema, NodeSchema


class VisitorMixin:
    """Boilerplate shared by language visitors (ctx, scope, emit helpers)."""

    traverser: ASTVisitor
    ctx: ASTParsingContext
    parser: Any

    def __init__(self, ctx: ASTParsingContext, parser: Any):
        self.ctx = ctx
        self.parser = parser

    def get_text(self, node: tree_sitter.Node) -> str:
        return get_node_text(node, self.ctx.source)

    def get_line_range(self, node: tree_sitter.Node) -> tuple[int, int]:
        return get_line_range(node)

    def get_current_parent_id(self) -> str:
        return self.ctx.scope.current_id

    def add_node(self, node: NodeSchema) -> None:
        self.ctx.add_node(node)

    def add_edge(self, edge: EdgeSchema) -> None:
        self.ctx.add_edge(edge)

    @property
    def scope(self):
        return self.ctx.scope

    @property
    def source(self) -> bytes:
        return self.ctx.source

    @property
    def rel_path(self) -> str:
        return self.ctx.rel_path

    def generic_visit(self, node: tree_sitter.Node) -> None:
        self.traverser.generic_visit(node)

    def visit(self, node: tree_sitter.Node) -> None:
        self.traverser.visit(node)

    def emit_symbol(
        self,
        *,
        node: tree_sitter.Node,
        name: str,
        sym_type: str,
        symbol_id: str | None = None,
        parent_id: str | None = None,
        signature: str = "",
        docstring: str = "",
        local_bindings: dict[str, str] | None = None,
        contains: bool = True,
    ) -> str:
        """Create a symbol node (+ optional contains edge) and return its id."""
        parent = parent_id if parent_id is not None else self.get_current_parent_id()
        sid = symbol_id or f"{self.rel_path}::{name}"
        start_line, end_line = self.get_line_range(node)
        self.add_node(
            NodeSchema(
                id=sid,
                label=name,
                type=sym_type,
                source_file=self.rel_path,
                line_start=start_line,
                line_end=end_line,
                signature=signature,
                docstring=docstring,
                local_bindings=local_bindings or {},
            )
        )
        if contains and parent:
            self.add_edge(EdgeSchema(source=parent, target=sid, relation="contains"))
        return sid

    def emit_relation(
        self,
        source: str,
        target: str,
        relation: str,
        *,
        import_map: dict[str, str] | None = None,
    ) -> None:
        self.add_edge(
            EdgeSchema(
                source=source,
                target=target,
                relation=relation,
                import_map=import_map or {},
            )
        )


def extract_type_name(node: tree_sitter.Node, get_text) -> str | None:
    """Best-effort type name from identifier / attribute / call / type wrappers."""
    if node is None:
        return None
    if node.type == "identifier":
        return get_text(node)
    if node.type == "attribute":
        attr_node = node.child_by_field_name("attribute")
        if attr_node:
            return get_text(attr_node)
        return get_text(node)
    if node.type == "type":
        for child in node.children:
            res = extract_type_name(child, get_text)
            if res:
                return res
    if node.type == "call":
        func_node = node.child_by_field_name("function")
        if func_node:
            return extract_type_name(func_node, get_text)
    for child in node.children:
        res = extract_type_name(child, get_text)
        if res:
            return res
    return None
