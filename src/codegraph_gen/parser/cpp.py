import logging
from pathlib import Path
import tree_sitter
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


class CCppVisitor(VisitorMixin):
    traverser: ASTVisitor

    def __init__(self, ctx: ASTParsingContext, parser):
        super().__init__(ctx, parser)
        self.file_node_id = ctx.rel_path
        self.defined_ids = {ctx.rel_path}

    def visit_class_specifier(self, node: tree_sitter.Node) -> None:
        self._visit_specifier(node, "class_specifier")

    def visit_struct_specifier(self, node: tree_sitter.Node) -> None:
        self._visit_specifier(node, "struct_specifier")

    def visit_union_specifier(self, node: tree_sitter.Node) -> None:
        self._visit_specifier(node, "union_specifier")

    def visit_enum_specifier(self, node: tree_sitter.Node) -> None:
        self._visit_specifier(node, "enum_specifier")

    def visit_namespace_definition(self, node: tree_sitter.Node) -> None:
        self._visit_specifier(node, "namespace_definition")

    def _visit_specifier(self, node: tree_sitter.Node, node_type: str) -> None:
        if node_type != "namespace_definition":
            body_node = node.child_by_field_name("body")
            if not body_node:
                self.generic_visit(node)
                return

        name_node = node.child_by_field_name("name")
        name = ""
        if name_node:
            name = self.get_text(name_node)

        if not name:
            self.generic_visit(node)
            return

        parent_id = self.get_current_parent_id()
        if "::" in name:
            symbol_id = f"{self.rel_path}::{name}"
        else:
            parent_parts = parent_id.split("::", 1)
            if len(parent_parts) > 1:
                symbol_id = f"{self.rel_path}::{parent_parts[1]}.{name}"
            else:
                symbol_id = f"{self.rel_path}::{name}"

        sym_type = "class"
        if node_type == "struct_specifier":
            sym_type = "struct"
        elif node_type == "union_specifier":
            sym_type = "union"
        elif node_type == "enum_specifier":
            sym_type = "enum"
        elif node_type == "namespace_definition":
            sym_type = "namespace"

        self.emit_symbol(
            node=node,
            name=name,
            sym_type=sym_type,
            symbol_id=symbol_id,
            parent_id=parent_id,
            signature=self.parser._get_signature(node, self.source),
            docstring=self.parser._get_docstring(node, self.source),
        )
        self.defined_ids.add(symbol_id)

        # Handle base classes inheritance
        for child in node.children:
            if child.type == "base_class_clause":

                def extract_base_types(n):
                    if n.type in (
                        "type_identifier",
                        "qualified_identifier",
                        "template_type",
                    ):
                        return self.get_text(n)
                    for c in n.children:
                        bt = extract_base_types(c)
                        if bt:
                            return bt
                    return None

                for sub in child.children:
                    base_name = extract_base_types(sub)
                    if base_name:
                        self.emit_relation(symbol_id, base_name, "inherits")

        with self.scope.push(symbol_id, sym_type):
            self.generic_visit(node)

    def visit_function_definition(self, node: tree_sitter.Node) -> None:
        declarator = node.child_by_field_name("declarator")
        func_name = self.parser._get_declarator_name(declarator, self.source)

        if func_name:
            parent_id = self.get_current_parent_id()
            parent_type = self.scope.current_type

            if "::" in func_name:
                class_part, method_part = func_name.rsplit("::", 1)
                class_id = f"{self.rel_path}::{class_part.replace('::', '.')}"
                method_id = f"{class_id}.{method_part}"
                sym_type = "method"
                func_label = method_part
                contains_parent = (
                    class_id if class_id in self.defined_ids else self.file_node_id
                )
            elif parent_type in ("class", "struct", "union", "namespace"):
                method_id = f"{parent_id}.{func_name}"
                sym_type = "method" if parent_type != "namespace" else "function"
                func_label = func_name
                contains_parent = parent_id
            else:
                method_id = f"{self.rel_path}::{func_name}"
                sym_type = "function"
                func_label = func_name
                contains_parent = parent_id

            self.emit_symbol(
                node=node,
                name=func_label,
                sym_type=sym_type,
                symbol_id=method_id,
                parent_id=contains_parent,
                signature=self.parser._get_signature(node, self.source),
                docstring=self.parser._get_docstring(node, self.source),
            )
            self.defined_ids.add(method_id)

            with self.scope.push(method_id, sym_type):
                self.generic_visit(node)
        else:
            self.generic_visit(node)

    def visit_preproc_include(self, node: tree_sitter.Node) -> None:
        path_node = node.child_by_field_name("path")
        if not path_node:
            for child in node.children:
                if child.type in ("string_literal", "system_lib_string"):
                    path_node = child
                    break
        if path_node:
            include_path = self.get_text(path_node).strip('"<>')
            self.emit_relation(self.file_node_id, include_path, "imports")
        self.generic_visit(node)

    def visit_call_expression(self, node: tree_sitter.Node) -> None:
        func_node = node.child_by_field_name("function")
        if func_node:
            callee_name = self.get_text(func_node)
            caller_id = self.get_current_parent_id()
            self.emit_relation(caller_id, callee_name, "calls")
        self.generic_visit(node)


class CCppParser(BaseParser):
    def __init__(self, lang_module):
        self.language = tree_sitter.Language(lang_module.language())
        self.parser = tree_sitter.Parser(self.language)

    def _get_declarator_name(self, node, source: bytes) -> str:
        if not node:
            return ""
        if node.type in ("identifier", "field_identifier", "destructor_name"):
            return source[node.start_byte : node.end_byte].decode(
                "utf-8", errors="replace"
            )
        elif node.type in ("qualified_identifier", "operator_name"):
            return source[node.start_byte : node.end_byte].decode(
                "utf-8", errors="replace"
            )
        elif node.type in (
            "pointer_declarator",
            "reference_declarator",
            "parenthesized_declarator",
            "array_declarator",
        ):
            decl = node.child_by_field_name("declarator")
            if decl:
                return self._get_declarator_name(decl, source)
        elif node.type == "function_declarator":
            decl = node.child_by_field_name("declarator")
            if decl:
                return self._get_declarator_name(decl, source)
        for child in node.children:
            name = self._get_declarator_name(child, source)
            if name:
                return name
        return ""

    def _get_docstring(self, node, source: bytes) -> str:
        docstring = ""
        prev = node.prev_sibling
        comments = []
        while prev and prev.type in ("comment", "line_comment", "block_comment"):
            comment_text = source[prev.start_byte : prev.end_byte].decode(
                "utf-8", errors="replace"
            )
            clean_text = comment_text.strip().lstrip("/").strip()
            if clean_text.endswith("*/"):
                clean_text = clean_text[:-2].strip()
            if clean_text.startswith("/*"):
                clean_text = clean_text[2:].strip()
            comments.append(clean_text)
            prev = prev.prev_sibling

        if comments:
            docstring = "\n".join(reversed(comments))
        return docstring

    def _get_signature(self, node, source: bytes) -> str:
        body = node.child_by_field_name("body")
        if body:
            end_byte = body.start_byte
            sig = (
                source[node.start_byte : end_byte]
                .decode("utf-8", errors="replace")
                .strip()
            )
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
                signature=f"file {file_path.name}",
                docstring=self._get_docstring(root, source),
            )
        )

        ctx = ASTParsingContext(source, rel_path, result)
        handler = CCppVisitor(ctx, self)
        visitor = ASTVisitor(handler, ctx)
        visitor.visit(root)
        return result


@register_parser("c")
class CParser(CCppParser):
    def __init__(self):
        import tree_sitter_c

        super().__init__(tree_sitter_c)


@register_parser("cpp")
class CppParser(CCppParser):
    def __init__(self):
        import tree_sitter_cpp

        super().__init__(tree_sitter_cpp)
