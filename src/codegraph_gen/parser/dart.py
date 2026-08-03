from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import tree_sitter
import tree_sitter_dart

from codegraph_gen.parser.base import (
    ASTParsingContext,
    ASTVisitor,
    BaseParser,
    register_parser,
)
from codegraph_gen.parser.common import VisitorMixin
from codegraph_gen.schema import ExtractionResult, NodeSchema

logger = logging.getLogger(__name__)


class DartVisitor(VisitorMixin):
    traverser: ASTVisitor

    def __init__(self, ctx: ASTParsingContext, parser: Any):
        super().__init__(ctx, parser)
        self.file_node_id = ctx.rel_path
        self.import_map: dict[str, str] = {}

    def visit_import_or_export(self, node: tree_sitter.Node) -> None:
        self._parse_import_or_export(node)
        self.generic_visit(node)

    def visit_library_import(self, node: tree_sitter.Node) -> None:
        self._parse_import_or_export(node)
        self.generic_visit(node)

    def _parse_import_or_export(self, node: tree_sitter.Node) -> None:
        uri_node = None
        def find_uri(n: tree_sitter.Node):
            nonlocal uri_node
            if n.type == "string_literal":
                uri_node = n
                return
            for child in n.children:
                if uri_node is None:
                    find_uri(child)

        find_uri(node)
        if uri_node:
            raw_uri = self.get_text(uri_node).strip("'\"")
            if raw_uri:
                self.import_map[raw_uri] = "*"
                self.emit_relation(self.file_node_id, raw_uri, "imports")

    def visit_class_definition(self, node: tree_sitter.Node) -> None:
        self._visit_class_like(node, "class")

    def visit_class_type_alias(self, node: tree_sitter.Node) -> None:
        self._visit_class_like(node, "class")

    def visit_extension_type_declaration(self, node: tree_sitter.Node) -> None:
        self._visit_class_like(node, "type")

    def _visit_class_like(self, node: tree_sitter.Node, default_sym_type: str) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            for child in node.children:
                if child.type == "identifier":
                    name_node = child
                    break
        if name_node:
            class_name = self.get_text(name_node)
            parent_id = self.get_current_parent_id()
            class_id = f"{self.rel_path}::{class_name}"

            is_abstract = any(c.type == "abstract" for c in node.children)
            sym_type = "interface" if is_abstract else default_sym_type

            self.emit_symbol(
                node=node,
                name=class_name,
                sym_type=sym_type,
                symbol_id=class_id,
                parent_id=parent_id,
                signature=self.parser._get_signature(node, self.source),
                docstring=self.parser._get_docstring(node, self.source),
            )

            # Process superclass / mixins / interfaces inheritance
            def emit_type_inherits(n: tree_sitter.Node):
                if n.type == "type_identifier":
                    target_name = self.get_text(n)
                    self.emit_relation(
                        class_id,
                        target_name,
                        "inherits",
                        import_map=self.import_map,
                    )
                for c in n.children:
                    emit_type_inherits(c)

            for child in node.children:
                if child.type in ("superclass", "mixins", "interfaces"):
                    emit_type_inherits(child)

            with self.scope.push(class_id, sym_type):
                self.generic_visit(node)
        else:
            self.generic_visit(node)

    def visit_enum_declaration(self, node: tree_sitter.Node) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            name_node = next((c for c in node.children if c.type == "identifier"), None)
        if name_node:
            enum_name = self.get_text(name_node)
            parent_id = self.get_current_parent_id()
            enum_id = f"{self.rel_path}::{enum_name}"

            self.emit_symbol(
                node=node,
                name=enum_name,
                sym_type="enum",
                symbol_id=enum_id,
                parent_id=parent_id,
                signature=self.parser._get_signature(node, self.source),
                docstring=self.parser._get_docstring(node, self.source),
            )

            with self.scope.push(enum_id, "enum"):
                self.generic_visit(node)
        else:
            self.generic_visit(node)

    def visit_enum_constant(self, node: tree_sitter.Node) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            name_node = next((c for c in node.children if c.type == "identifier"), None)
        if name_node:
            const_name = self.get_text(name_node)
            parent_id = self.get_current_parent_id()
            const_id = f"{parent_id}.{const_name}"
            self.emit_symbol(
                node=node,
                name=const_name,
                sym_type="field",
                symbol_id=const_id,
                parent_id=parent_id,
            )
        self.generic_visit(node)

    def visit_mixin_declaration(self, node: tree_sitter.Node) -> None:
        name_node = next((c for c in node.children if c.type == "identifier"), None)
        if name_node:
            mixin_name = self.get_text(name_node)
            parent_id = self.get_current_parent_id()
            mixin_id = f"{self.rel_path}::{mixin_name}"

            self.emit_symbol(
                node=node,
                name=mixin_name,
                sym_type="mixin",
                symbol_id=mixin_id,
                parent_id=parent_id,
                signature=self.parser._get_signature(node, self.source),
                docstring=self.parser._get_docstring(node, self.source),
            )

            with self.scope.push(mixin_id, "mixin"):
                self.generic_visit(node)
        else:
            self.generic_visit(node)

    def visit_extension_declaration(self, node: tree_sitter.Node) -> None:
        name_node = next((c for c in node.children if c.type == "identifier"), None)
        ext_name = self.get_text(name_node) if name_node else "extension"
        parent_id = self.get_current_parent_id()
        ext_id = f"{self.rel_path}::{ext_name}"

        self.emit_symbol(
            node=node,
            name=ext_name,
            sym_type="extension",
            symbol_id=ext_id,
            parent_id=parent_id,
            signature=self.parser._get_signature(node, self.source),
            docstring=self.parser._get_docstring(node, self.source),
        )

        with self.scope.push(ext_id, "extension"):
            self.generic_visit(node)

    def visit_method_signature(self, node: tree_sitter.Node) -> None:
        for child in node.children:
            if child.type == "factory_constructor_signature":
                self._visit_constructor(child)
                return
        self._visit_function_or_method(node)

    def visit_function_signature(self, node: tree_sitter.Node) -> None:
        # Check if function_signature is inside method_signature (avoid duplicate parsing)
        if node.parent and node.parent.type == "method_signature":
            self.generic_visit(node)
            return
        self._visit_function_or_method(node)

    def visit_constructor_signature(self, node: tree_sitter.Node) -> None:
        self._visit_constructor(node)

    def visit_factory_constructor_signature(self, node: tree_sitter.Node) -> None:
        self._visit_constructor(node)

    def _visit_constructor(self, node: tree_sitter.Node) -> None:
        parent_id = self.get_current_parent_id()
        identifiers = [self.get_text(c) for c in node.children if c.type == "identifier"]
        if len(identifiers) >= 2:
            ctor_name = f"{identifiers[0]}.{identifiers[1]}"
            ctor_suffix = identifiers[1]
        elif len(identifiers) == 1:
            ctor_name = identifiers[0]
            ctor_suffix = identifiers[0]
        else:
            ctor_name = "constructor"
            ctor_suffix = "new"

        ctor_id = f"{parent_id}.{ctor_suffix}"

        self.emit_symbol(
            node=node,
            name=ctor_name,
            sym_type="constructor",
            symbol_id=ctor_id,
            parent_id=parent_id,
            signature=self.parser._get_signature(node, self.source),
        )
        self.generic_visit(node)

    def _visit_function_or_method(self, node: tree_sitter.Node) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            def find_name(n: tree_sitter.Node):
                if n.type == "identifier":
                    return n
                for c in n.children:
                    res = find_name(c)
                    if res:
                        return res
                return None
            name_node = find_name(node)

        if name_node:
            func_name = self.get_text(name_node)
            parent_id = self.get_current_parent_id()
            parent_type = self.scope.current_type

            if parent_type in ("class", "interface", "enum", "mixin", "extension"):
                func_id = f"{parent_id}.{func_name}"
                sym_type = "method"
            else:
                func_id = f"{self.rel_path}::{func_name}"
                sym_type = "function"

            local_bindings: dict[str, str] = {}

            # Extract local variables and parameters inside function body
            func_body_node = None
            if node.parent and node.parent.type in ("function_body", "method_signature"):
                func_body_node = node.parent
            for sibling in (node.parent.children if node.parent else []):
                if sibling.type == "function_body":
                    func_body_node = sibling
                    break

            if func_body_node:
                def extract_vars(n: tree_sitter.Node):
                    if n.type in ("local_variable_declaration", "initialized_variable_definition"):
                        var_name = None
                        type_name = None
                        name_child = n.child_by_field_name("name")
                        if name_child:
                            var_name = self.get_text(name_child)
                        else:
                            for c in n.children:
                                if c.type in ("identifier", "initialized_identifier"):
                                    id_c = c.child_by_field_name("name") if c.type == "initialized_identifier" else c
                                    if id_c:
                                        var_name = self.get_text(id_c)
                                        break
                        # Find type
                        for c in n.children:
                            if c.type == "type_identifier":
                                type_name = self.get_text(c)
                                break
                            elif c.type == "value" or c.type == "identifier":
                                type_name = self.get_text(c)

                        if var_name and type_name:
                            local_bindings[var_name] = type_name
                    for child in n.children:
                        extract_vars(child)

                extract_vars(func_body_node)

            self.emit_symbol(
                node=node,
                name=func_name,
                sym_type=sym_type,
                symbol_id=func_id,
                parent_id=parent_id,
                signature=self.parser._get_signature(node, self.source),
                docstring=self.parser._get_docstring(node, self.source),
                local_bindings=local_bindings,
            )

            with self.scope.push(func_id, sym_type):
                self.generic_visit(node)
        else:
            self.generic_visit(node)

    def visit_expression_statement(self, node: tree_sitter.Node) -> None:
        self._extract_calls(node)
        self.generic_visit(node)

    def _extract_calls(self, node: tree_sitter.Node) -> None:
        parent_id = self.get_current_parent_id()
        if not parent_id or parent_id == self.rel_path:
            return

        # Simple identifier call or selector call
        for child in node.children:
            if child.type == "identifier":
                target = self.get_text(child)
                if target and target not in ("print", "identical", "assert"):
                    self.emit_relation(
                        parent_id,
                        target,
                        "calls",
                        import_map=self.import_map,
                    )
            elif child.type == "selector":
                for sub in child.children:
                    if sub.type in ("unconditional_assignable_selector", "identifier"):
                        for leaf in (sub.children if sub.children else [sub]):
                            if leaf.type == "identifier":
                                target = self.get_text(leaf)
                                if target and target not in ("print", "identical", "assert"):
                                    self.emit_relation(
                                        parent_id,
                                        target,
                                        "calls",
                                        import_map=self.import_map,
                                    )


@register_parser("dart")
class DartParser(BaseParser):
    def __init__(self) -> None:
        self.ts_language = tree_sitter.Language(tree_sitter_dart.language())
        self.ts_parser = tree_sitter.Parser(self.ts_language)

    def parse_file(self, file_path: Path, workspace_dir: Path) -> ExtractionResult:
        rel_path = str(file_path.relative_to(workspace_dir))
        source = file_path.read_bytes()

        result = ExtractionResult()
        # Emit file symbol node
        result.nodes.append(
            NodeSchema(
                id=rel_path,
                label=rel_path,
                type="file",
                source_file=rel_path,
                line_start=1,
                line_end=len(source.splitlines()) or 1,
                signature=f"library {file_path.stem}",
            )
        )

        tree = self.ts_parser.parse(source)
        ctx = ASTParsingContext(source, rel_path, result)
        visitor_impl = DartVisitor(ctx, self)
        visitor = ASTVisitor(visitor_impl, ctx)
        visitor.visit(tree.root_node)

        return result

    def _get_signature(self, node: tree_sitter.Node, source: bytes) -> str:
        text = source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return lines[0] if lines else ""

    def _get_docstring(self, node: tree_sitter.Node, source: bytes) -> str:
        # Check previous sibling comments
        doc_lines = []
        prev = node.prev_sibling
        while prev and prev.type in ("comment", "line_comment", "block_comment"):
            comment_text = source[prev.start_byte : prev.end_byte].decode("utf-8", errors="replace").strip()
            if comment_text.startswith("///") or comment_text.startswith("/**"):
                doc_lines.insert(0, comment_text)
            prev = prev.prev_sibling
        return "\n".join(doc_lines)
