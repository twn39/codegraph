import logging
from pathlib import Path
import tree_sitter
import tree_sitter_ocaml
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


class OCamlVisitor(VisitorMixin):
    traverser: ASTVisitor

    def __init__(self, ctx: ASTParsingContext, parser):
        super().__init__(ctx, parser)
        self.file_node_id = ctx.rel_path

    def visit_module_definition(self, node: tree_sitter.Node) -> None:
        module_binding = None
        for child in node.children:
            if child.type == "module_binding":
                module_binding = child
                break

        if module_binding:
            name_node = None
            for child in module_binding.children:
                if child.type == "module_name":
                    name_node = child
                    break

            if name_node:
                module_name = self.get_text(name_node)
                parent_id = self.get_current_parent_id()
                module_id = (
                    f"{parent_id}.{module_name}"
                    if self.scope.current_type != "file"
                    else f"{self.rel_path}::{module_name}"
                )

                self.emit_symbol(
                    node=node,
                    name=module_name,
                    sym_type="class",
                    symbol_id=module_id,
                    parent_id=parent_id,
                    signature=f"module {module_name}",
                    docstring=self.parser._get_docstring(node, self.source),
                )

                with self.scope.push(module_id, "class"):
                    self.generic_visit(node)
                return

        self.generic_visit(node)

    def visit_type_definition(self, node: tree_sitter.Node) -> None:
        type_binding = None
        for child in node.children:
            if child.type == "type_binding":
                type_binding = child
                break

        if type_binding:
            name_node = None
            for child in type_binding.children:
                if child.type == "type_constructor":
                    name_node = child
                    break

            if name_node:
                type_name = self.get_text(name_node)
                parent_id = self.get_current_parent_id()
                type_id = (
                    f"{parent_id}.{type_name}"
                    if self.scope.current_type != "file"
                    else f"{self.rel_path}::{type_name}"
                )

                self.emit_symbol(
                    node=node,
                    name=type_name,
                    sym_type="struct",
                    symbol_id=type_id,
                    parent_id=parent_id,
                    signature=f"type {type_name}",
                    docstring=self.parser._get_docstring(node, self.source),
                )

                with self.scope.push(type_id, "struct"):
                    self.generic_visit(node)
                return

        self.generic_visit(node)

    def visit_value_definition(self, node: tree_sitter.Node) -> None:
        # If we are inside a function/method, this is a local variable/binding, not a global function.
        if self.scope.current_type in ("function", "method"):
            self.generic_visit(node)
            return

        let_binding = None
        for child in node.children:
            if child.type == "let_binding":
                let_binding = child
                break

        if let_binding:
            name_node = None
            for child in let_binding.children:
                if child.type == "value_name":
                    name_node = child
                    break

            if name_node:
                func_name = self.get_text(name_node)
                # Ignore unit pattern let bindings like `let () = ...`
                if func_name == "()":
                    self.generic_visit(node)
                    return

                parent_id = self.get_current_parent_id()
                func_id = (
                    f"{parent_id}.{func_name}"
                    if self.scope.current_type != "file"
                    else f"{self.rel_path}::{func_name}"
                )

                local_bindings = {}

                def collect_local_bindings(n):
                    if n.type == "let_binding":
                        v_name_node = None
                        for c in n.children:
                            if c.type == "value_name":
                                v_name_node = c
                                break
                        if v_name_node:
                            var_name = self.get_text(v_name_node)
                            rhs_node = None
                            found_eq = False
                            for c in n.children:
                                if c.type == "=":
                                    found_eq = True
                                    continue
                                if found_eq:
                                    rhs_node = c
                                    break
                            if rhs_node:
                                type_name = None
                                if rhs_node.type == "application_expression":
                                    first_c = (
                                        rhs_node.children[0]
                                        if rhs_node.children
                                        else None
                                    )
                                    if first_c:
                                        type_name = self.get_text(first_c)
                                elif rhs_node.type == "value_path":
                                    type_name = self.get_text(rhs_node)
                                if type_name:
                                    local_bindings[var_name] = type_name

                    for child in n.children:
                        collect_local_bindings(child)

                collect_local_bindings(node)

                self.emit_symbol(
                    node=node,
                    name=func_name,
                    sym_type="function",
                    symbol_id=func_id,
                    parent_id=parent_id,
                    signature=self.parser._get_signature(node, self.source),
                    docstring=self.parser._get_docstring(node, self.source),
                    local_bindings=local_bindings,
                )

                with self.scope.push(func_id, "function"):
                    self.generic_visit(node)
                return

        self.generic_visit(node)

    def visit_open_module(self, node: tree_sitter.Node) -> None:
        module_path = None
        for child in node.children:
            if child.type == "module_path":
                module_path = child
                break

        if module_path:
            module_name = self.get_text(module_path)
            self.emit_relation(self.file_node_id, module_name, "imports", import_map={"*": "*"})
        self.generic_visit(node)

    def visit_include_module(self, node: tree_sitter.Node) -> None:
        module_path = None
        for child in node.children:
            if child.type == "module_path":
                module_path = child
                break

        if module_path:
            module_name = self.get_text(module_path)
            self.emit_relation(self.file_node_id, module_name, "imports", import_map={"*": "*"})
        self.generic_visit(node)

    def visit_application_expression(self, node: tree_sitter.Node) -> None:
        if node.children:
            func_node = node.children[0]
            if func_node.type == "value_path":
                callee_name = self.get_text(func_node)
                caller_id = self.get_current_parent_id()
                self.emit_relation(caller_id, callee_name, "calls")
        self.generic_visit(node)


@register_parser("ocaml")
class OCamlParser(BaseParser):
    def __init__(self):
        self.language = tree_sitter.Language(tree_sitter_ocaml.language_ocaml())
        self.parser = tree_sitter.Parser(self.language)

    def _get_docstring(self, node, source: bytes) -> str:
        if node.type == "compilation_unit":
            first_comment = None
            for child in node.children:
                if child.type == "comment":
                    first_comment = child
                    break
                elif child.type not in ("comment",):
                    break
            if first_comment:
                comment_text = source[
                    first_comment.start_byte : first_comment.end_byte
                ].decode("utf-8", errors="replace")
                clean_text = comment_text.strip()
                if clean_text.startswith("(*") and clean_text.endswith("*)"):
                    clean_text = clean_text[2:-2].strip()
                return clean_text
            return ""

        docstring = ""
        prev = node.prev_sibling
        comments = []
        while prev and prev.type in ("comment",):
            comment_text = source[prev.start_byte : prev.end_byte].decode(
                "utf-8", errors="replace"
            )
            clean_text = comment_text.strip()
            if clean_text.startswith("(*") and clean_text.endswith("*)"):
                clean_text = clean_text[2:-2].strip()
            comments.append(clean_text)
            prev = prev.prev_sibling

        if comments:
            docstring = "\n".join(reversed(comments))
        return docstring

    def _get_signature(self, node, source: bytes) -> str:
        text = (
            source[node.start_byte : node.end_byte]
            .decode("utf-8", errors="replace")
            .strip()
        )
        first_line = text.split("\n")[0]
        if first_line.endswith("="):
            first_line = first_line[:-1].strip()
        return first_line

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
        handler = OCamlVisitor(ctx, self)
        visitor = ASTVisitor(handler, ctx)
        visitor.visit(root)
        return result
