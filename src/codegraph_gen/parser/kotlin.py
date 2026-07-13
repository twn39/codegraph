import logging
from pathlib import Path
import tree_sitter
import tree_sitter_kotlin
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


class KotlinVisitor(VisitorMixin):
    traverser: ASTVisitor

    def __init__(self, ctx: ASTParsingContext, parser):
        super().__init__(ctx, parser)
        self.file_node_id = ctx.rel_path

    def visit_class_declaration(self, node: tree_sitter.Node) -> None:
        self._visit_type(node, "class_declaration")

    def visit_object_declaration(self, node: tree_sitter.Node) -> None:
        self._visit_type(node, "object_declaration")

    def _visit_type(self, node: tree_sitter.Node, node_type: str) -> None:
        name_node = node.child_by_field_name("name")
        if name_node:
            class_name = self.get_text(name_node)
            parent_id = self.get_current_parent_id()
            class_id = f"{self.rel_path}::{class_name}"

            if node_type == "class_declaration":
                is_interface = any(c.type == "interface" for c in node.children)
                sym_type = "interface" if is_interface else "class"
            else:
                sym_type = "class"

            self.emit_symbol(
                node=node,
                name=class_name,
                sym_type=sym_type,
                symbol_id=class_id,
                parent_id=parent_id,
                signature=self.parser._get_signature(node, self.source),
                docstring=self.parser._get_docstring(node, self.source),
            )

            # Check inheritance / delegation specifiers
            for child in node.children:
                if child.type == "delegation_specifiers":
                    for spec in child.children:
                        if spec.type == "delegation_specifier":

                            def find_user_type(n):
                                if n.type == "user_type":
                                    return n
                                for c in n.children:
                                    res = find_user_type(c)
                                    if res:
                                        return res
                                return None

                            user_type_node = find_user_type(spec)
                            if user_type_node:
                                id_node = next(
                                    (
                                        c
                                        for c in user_type_node.children
                                        if c.type == "identifier"
                                    ),
                                    None,
                                )
                                if id_node:
                                    parent_name = self.get_text(id_node)
                                    self.emit_relation(class_id, parent_name, "inherits")

            with self.scope.push(class_id, sym_type):
                self.generic_visit(node)
        else:
            self.generic_visit(node)

    def visit_function_declaration(self, node: tree_sitter.Node) -> None:
        name_node = node.child_by_field_name("name")
        if name_node:
            func_name = self.get_text(name_node)
            parent_id = self.get_current_parent_id()
            parent_type = self.scope.current_type

            if parent_type in ("class", "interface"):
                func_id = f"{parent_id}.{func_name}"
                sym_type = "method"
            else:
                func_id = f"{self.rel_path}::{func_name}"
                sym_type = "function"

            local_bindings = {}

            def extract_type_from_kt_node(kt_node):
                if kt_node.type == "user_type":
                    id_node = next(
                        (c for c in kt_node.children if c.type == "identifier"),
                        None,
                    )
                    if id_node:
                        return self.get_text(id_node)
                elif kt_node.type == "call_expression":
                    callee = kt_node.child_by_field_name("constructor") or next(
                        (c for c in kt_node.children if c.type == "identifier"),
                        None,
                    )
                    if callee:
                        return self.get_text(callee)
                for child in kt_node.children:
                    res = extract_type_from_kt_node(child)
                    if res:
                        return res
                return None

            def collect_local_bindings(n):
                if n.type == "parameter":
                    id_node = next(
                        (c for c in n.children if c.type == "identifier"), None
                    )
                    type_node = next(
                        (c for c in n.children if c.type == "user_type"), None
                    )
                    if id_node and type_node:
                        var_name = self.get_text(id_node)
                        t_name = extract_type_from_kt_node(type_node)
                        if t_name:
                            local_bindings[var_name] = t_name
                elif n.type == "property_declaration":
                    var_decl = next(
                        (c for c in n.children if c.type == "variable_declaration"),
                        None,
                    )
                    val_expr = next(
                        (c for c in n.children if c.type == "call_expression"),
                        None,
                    )
                    if var_decl:
                        id_node = next(
                            (c for c in var_decl.children if c.type == "identifier"),
                            None,
                        )
                        type_node = next(
                            (c for c in var_decl.children if c.type == "user_type"),
                            None,
                        )
                        if id_node:
                            var_name = self.get_text(id_node)
                            if type_node:
                                t_name = extract_type_from_kt_node(type_node)
                                if t_name:
                                    local_bindings[var_name] = t_name
                            elif val_expr:
                                t_name = extract_type_from_kt_node(val_expr)
                                if t_name:
                                    local_bindings[var_name] = t_name

                for child in n.children:
                    if child.type not in (
                        "function_declaration",
                        "class_declaration",
                        "object_declaration",
                    ):
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

    def visit_import(self, node: tree_sitter.Node) -> None:
        qual_id_node = next(
            (c for c in node.children if c.type == "qualified_identifier"), None
        )
        if qual_id_node:
            target = self.get_text(qual_id_node)
            is_wildcard = any(c.type == "*" for c in node.children)
            alias = None

            as_idx = next(
                (i for i, c in enumerate(node.children) if c.type == "as"), -1
            )
            if as_idx != -1 and as_idx + 1 < len(node.children):
                alias_node = node.children[as_idx + 1]
                if alias_node.type == "identifier":
                    alias = self.get_text(alias_node)

            if is_wildcard:
                import_map = {"*": "*"}
            elif alias:
                last_part = target.split(".")[-1]
                import_map = {alias: last_part}
            else:
                last_part = target.split(".")[-1]
                import_map = {last_part: last_part}

            self.emit_relation(self.file_node_id, target, "imports", import_map=import_map)
        self.generic_visit(node)

    def visit_call_expression(self, node: tree_sitter.Node) -> None:
        func_node = None
        for child in node.children:
            if child.type in ("identifier", "navigation_expression"):
                func_node = child
                break
        if func_node:
            callee_name = self.get_text(func_node)
            caller_id = self.get_current_parent_id()
            self.emit_relation(caller_id, callee_name, "calls")
        self.generic_visit(node)


@register_parser("kotlin")
class KotlinParser(BaseParser):
    def __init__(self):
        self.language = tree_sitter.Language(tree_sitter_kotlin.language())
        self.parser = tree_sitter.Parser(self.language)

    def _get_docstring(self, node, source: bytes) -> str:
        docstring = ""
        prev = node.prev_sibling
        comments = []
        while prev and prev.type in ("comment", "line_comment", "block_comment"):
            comment_text = source[prev.start_byte : prev.end_byte].decode(
                "utf-8", errors="replace"
            )
            clean_text = (
                comment_text.strip()
                .lstrip("/*")
                .rstrip("*/")
                .lstrip("*")
                .lstrip("/")
                .strip()
            )
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
                "function_body",
                "block",
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
                signature=f"package {file_path.stem}",
                docstring=self._get_docstring(root, source),
            )
        )

        ctx = ASTParsingContext(source, rel_path, result)
        handler = KotlinVisitor(ctx, self)
        visitor = ASTVisitor(handler, ctx)
        visitor.visit(root)
        return result
