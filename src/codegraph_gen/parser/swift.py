import logging
from pathlib import Path
import tree_sitter
import tree_sitter_swift
from codegraph_gen.parser.base import (
    BaseParser,
    ASTVisitor,
    ASTParsingContext,
    register_parser,
)
from codegraph_gen.parser.common import VisitorMixin
from codegraph_gen.schema import (
    ExtractionResult,
    NodeSchema,
)

logger = logging.getLogger(__name__)


class SwiftVisitor(VisitorMixin):
    traverser: ASTVisitor

    def __init__(self, ctx: ASTParsingContext, parser):
        super().__init__(ctx, parser)
        self.file_node_id = ctx.rel_path

    def visit_class_declaration(self, node: tree_sitter.Node) -> None:
        self._visit_type_declaration(node, "class_declaration")

    def visit_struct_declaration(self, node: tree_sitter.Node) -> None:
        self._visit_type_declaration(node, "struct_declaration")

    def visit_protocol_declaration(self, node: tree_sitter.Node) -> None:
        self._visit_type_declaration(node, "protocol_declaration")

    def visit_enum_declaration(self, node: tree_sitter.Node) -> None:
        self._visit_type_declaration(node, "enum_declaration")

    def _visit_type_declaration(self, node: tree_sitter.Node, node_type: str) -> None:
        name_node = node.child_by_field_name("name")
        if name_node:
            class_name = self.get_text(name_node)
            parent_id = self.get_current_parent_id()
            class_id = f"{self.rel_path}::{class_name}"

            sym_type = "class"
            if node_type == "struct_declaration":
                sym_type = "struct"
            elif node_type == "protocol_declaration":
                sym_type = "interface"
            elif node_type == "enum_declaration":
                sym_type = "enum"

            self.emit_symbol(
                node=node,
                name=class_name,
                sym_type=sym_type,
                symbol_id=class_id,
                parent_id=parent_id,
                signature=self.parser._get_signature(node, self.source),
                docstring=self.parser._get_docstring(node, self.source),
            )

            # Protocol conformances or subclassing
            for child in node.children:
                if child.type == "type_inheritance_clause":
                    for sub in child.children:
                        if sub.type == "type_identifier":
                            parent_name = self.get_text(sub)
                            self.emit_relation(class_id, parent_name, "inherits")

            with self.scope.push(class_id, sym_type):
                self.generic_visit(node)
        else:
            self.generic_visit(node)

    def visit_function_declaration(self, node: tree_sitter.Node) -> None:
        self._visit_function_or_init(node, "function_declaration")

    def visit_init_declaration(self, node: tree_sitter.Node) -> None:
        self._visit_function_or_init(node, "init_declaration")

    def visit_deinit_declaration(self, node: tree_sitter.Node) -> None:
        self._visit_function_or_init(node, "deinit_declaration")

    def _visit_function_or_init(self, node: tree_sitter.Node, node_type: str) -> None:
        func_name = None
        if node_type == "function_declaration":
            name_node = node.child_by_field_name("name")
            if name_node:
                func_name = self.get_text(name_node)
        elif node_type == "init_declaration":
            func_name = "init"
        elif node_type == "deinit_declaration":
            func_name = "deinit"

        if func_name:
            parent_id = self.get_current_parent_id()
            parent_type = self.scope.current_type

            if parent_type in ("class", "struct", "interface", "enum"):
                func_id = f"{parent_id}.{func_name}"
                sym_type = "method"
            else:
                func_id = f"{self.rel_path}::{func_name}"
                sym_type = "function"

            local_bindings = {}

            def extract_type_id(tc):
                if tc.type == "type_identifier":
                    return self.get_text(tc)
                for gc in tc.children:
                    res = extract_type_id(gc)
                    if res:
                        return res
                return None

            def collect_local_bindings(n):
                if n.type == "property_declaration":
                    var_name = None
                    for child in n.children:
                        if child.type == "pattern":
                            for gc in child.children:
                                if gc.type == "simple_identifier":
                                    var_name = self.get_text(gc)
                    if var_name:
                        type_name = None
                        for child in n.children:
                            if child.type == "type_annotation":
                                type_name = extract_type_id(child)
                        if not type_name:
                            for child in n.children:
                                if child.type == "call_expression":
                                    for gc in child.children:
                                        if gc.type == "simple_identifier":
                                            type_name = self.get_text(gc)
                        if type_name:
                            local_bindings[var_name] = type_name
                elif n.type == "parameter":
                    identifiers = []
                    type_name = None
                    seen_colon = False
                    for child in n.children:
                        if child.type == "simple_identifier" and not seen_colon:
                            identifiers.append(self.get_text(child))
                        elif child.type == ":":
                            seen_colon = True
                        elif seen_colon:
                            res = extract_type_id(child)
                            if res:
                                type_name = res
                                break
                    if identifiers and type_name:
                        var_name = identifiers[-1]
                        local_bindings[var_name] = type_name

                for child in n.children:
                    collect_local_bindings(child)

            collect_local_bindings(node)

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

    def visit_import_declaration(self, node: tree_sitter.Node) -> None:
        path_parts = []
        for child in node.children:
            if child.type in ("simple_identifier", "navigation_expression"):
                path_parts.append(self.get_text(child))
        if path_parts:
            import_path = ".".join(path_parts)
            self.emit_relation(self.file_node_id, import_path, "imports")
        self.generic_visit(node)

    def visit_call_expression(self, node: tree_sitter.Node) -> None:
        func_node = None
        for child in node.children:
            if child.type in ("simple_identifier", "navigation_expression"):
                func_node = child
                break
        if func_node:
            callee_name = self.get_text(func_node)
            caller_id = self.get_current_parent_id()
            self.emit_relation(caller_id, callee_name, "calls")
        self.generic_visit(node)


@register_parser("swift")
class SwiftParser(BaseParser):
    def __init__(self):
        self.language = tree_sitter.Language(tree_sitter_swift.language())
        self.parser = tree_sitter.Parser(self.language)

    def _get_docstring(self, node, source: bytes) -> str:
        docstring = ""
        prev = node.prev_sibling
        comments = []
        while prev and prev.type in ("comment", "line_comment", "block_comment"):
            comment_text = source[prev.start_byte : prev.end_byte].decode(
                "utf-8", errors="replace"
            )
            clean_text = comment_text.strip().lstrip("/").strip()
            comments.append(clean_text)
            prev = prev.prev_sibling

        if comments:
            docstring = "\n".join(reversed(comments))
        return docstring

    def _get_signature(self, node, source: bytes) -> str:
        body = None
        for child in node.children:
            if child.type in (
                "class_body",
                "struct_body",
                "brace_item_list",
            ):
                body = child
                break
        if body:
            end_byte = body.start_byte
            sig_bytes = source[node.start_byte : end_byte]
            sig = sig_bytes.decode("utf-8", errors="replace").strip()
            if sig.endswith("{"):
                sig = sig[:-1].strip()
            return sig
        return (
            source[node.start_byte : node.end_byte]
            .decode("utf-8", errors="replace")
            .split("\n")[0]
        )

    def parse_file(self, file_path: Path, workspace_dir: Path) -> ExtractionResult:
        try:
            source = file_path.read_bytes()
        except Exception as e:
            logger.error(f"Error reading file {file_path}: {e}")
            return ExtractionResult()

        tree = self.parser.parse(source)
        root = tree.root_node

        rel_path = str(file_path.relative_to(workspace_dir))
        result = ExtractionResult()

        # Add file node
        file_node_id = rel_path
        result.nodes.append(
            NodeSchema(
                id=file_node_id,
                label=file_path.name,
                type="file",
                source_file=rel_path,
                line_start=1,
                line_end=len(source.splitlines()) or 1,
                signature=f"module {file_path.stem}",
                docstring=self._get_docstring(root, source),
            )
        )

        ctx = ASTParsingContext(source, rel_path, result)
        handler = SwiftVisitor(ctx, self)
        visitor = ASTVisitor(handler, ctx)
        visitor.visit(root)
        return result
