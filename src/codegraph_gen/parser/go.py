import logging
from pathlib import Path
import tree_sitter
import tree_sitter_go
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


class GoVisitor(VisitorMixin):
    traverser: ASTVisitor

    def __init__(self, ctx: ASTParsingContext, parser):
        super().__init__(ctx, parser)
        self.file_node_id = ctx.rel_path

    def get_receiver_type(self, method_node) -> str | None:
        receiver = method_node.child_by_field_name("receiver")
        if receiver:
            for child in receiver.children:
                if child.type == "parameter_declaration":
                    type_node = child.child_by_field_name("type")
                    if type_node:
                        raw_type = self.get_text(type_node)
                        return raw_type.strip()
        return None

    def visit_type_declaration(self, node: tree_sitter.Node) -> None:
        for child in node.children:
            if child.type == "type_spec":
                name_node = child.child_by_field_name("name")
                if name_node:
                    type_name = self.get_text(name_node)
                    type_id = f"{self.rel_path}::{type_name}"

                    sym_type = "struct"
                    for tc in child.children:
                        if tc.type == "interface_type":
                            sym_type = "interface"
                            break

                    self.emit_symbol(
                        node=child,
                        name=type_name,
                        sym_type=sym_type,
                        symbol_id=type_id,
                        parent_id=self.file_node_id,
                        signature=f"type {type_name} {sym_type}",
                        docstring=self.parser._get_docstring(node, self.source),
                    )
        self.generic_visit(node)

    def visit_function_declaration(self, node: tree_sitter.Node) -> None:
        name_node = node.child_by_field_name("name")
        if name_node:
            func_name = self.get_text(name_node)
            func_id = f"{self.rel_path}::{func_name}"

            self.emit_symbol(
                node=node,
                name=func_name,
                sym_type="function",
                symbol_id=func_id,
                parent_id=self.file_node_id,
                signature=self.parser._get_signature(node, self.source),
                docstring=self.parser._get_docstring(node, self.source),
            )
        self.generic_visit(node)

    def visit_method_declaration(self, node: tree_sitter.Node) -> None:
        name_node = node.child_by_field_name("name")
        if name_node:
            method_name = self.get_text(name_node)
            receiver_type = self.get_receiver_type(node)

            if receiver_type:
                parent_id = f"{self.rel_path}::{receiver_type}"
                method_id = f"{parent_id}.{method_name}"
            else:
                parent_id = self.file_node_id
                method_id = f"{self.rel_path}::{method_name}"

            self.emit_symbol(
                node=node,
                name=method_name,
                sym_type="method",
                symbol_id=method_id,
                parent_id=parent_id,
                signature=self.parser._get_signature(node, self.source),
                docstring=self.parser._get_docstring(node, self.source),
            )
        self.generic_visit(node)

    def visit_import_spec(self, node: tree_sitter.Node) -> None:
        path_node = node.child_by_field_name("path")
        if path_node:
            import_path = self.get_text(path_node).strip("\"'")
            pkg_name = import_path.split("/")[-1]
            import_map = {}

            name_node = node.child_by_field_name("name")
            if name_node:
                local_name = self.get_text(name_node)
                if local_name == ".":
                    import_map["*"] = "*"
                else:
                    import_map[local_name] = pkg_name
            else:
                import_map[pkg_name] = pkg_name

            self.emit_relation(
                self.file_node_id, import_path, "imports", import_map=import_map
            )
        self.generic_visit(node)

    def visit_call_expression(self, node: tree_sitter.Node) -> None:
        func_node = node.child_by_field_name("function")
        if func_node:
            callee_name = self.get_text(func_node)
            caller_id = self.file_node_id
            curr = node.parent
            while curr:
                if curr.type in ("function_declaration", "method_declaration"):
                    c_name_node = curr.child_by_field_name("name")
                    if c_name_node:
                        c_name = self.get_text(c_name_node)
                        if curr.type == "method_declaration":
                            r_type = self.get_receiver_type(curr)
                            if r_type:
                                caller_id = f"{self.rel_path}::{r_type}.{c_name}"
                            else:
                                caller_id = f"{self.rel_path}::{c_name}"
                        else:
                            caller_id = f"{self.rel_path}::{c_name}"
                    break
                curr = curr.parent

            self.emit_relation(caller_id, callee_name, "calls")
        self.generic_visit(node)


@register_parser("go")
class GoParser(BaseParser):
    def __init__(self):
        self.language = tree_sitter.Language(tree_sitter_go.language())
        self.parser = tree_sitter.Parser(self.language)

    def _get_docstring(self, node, source: bytes) -> str:
        docstring = ""
        prev = node.prev_sibling
        comments = []
        while prev and prev.type in ("comment", "line_comment"):
            comment_text = source[prev.start_byte : prev.end_byte].decode(
                "utf-8", errors="replace"
            )
            clean_text = comment_text.strip().lstrip("//").strip()
            comments.append(clean_text)
            prev = prev.prev_sibling

        if comments:
            docstring = "\n".join(reversed(comments))
        return docstring

    def _get_signature(self, node, source: bytes) -> str:
        body = node.child_by_field_name("body")
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
                signature=f"package {file_path.parent.name or 'main'}",
                docstring=self._get_docstring(root, source),
            )
        )

        ctx = ASTParsingContext(source, rel_path, result)
        handler = GoVisitor(ctx, self)
        visitor = ASTVisitor(handler, ctx)
        visitor.visit(root)
        return result
