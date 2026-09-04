import logging
from pathlib import Path
import tree_sitter
import tree_sitter_rust
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


class RustVisitor(VisitorMixin):
    traverser: ASTVisitor

    def __init__(self, ctx: ASTParsingContext, parser):
        super().__init__(ctx, parser)
        self.file_node_id = ctx.rel_path
        self.current_impl_type = None

    def get_impl_type(self, impl_node) -> str | None:
        type_node = impl_node.child_by_field_name("type")
        if type_node:
            return self.get_text(type_node)
        return None

    def visit_struct_item(self, node: tree_sitter.Node) -> None:
        self._visit_item(node, "struct")

    def visit_enum_item(self, node: tree_sitter.Node) -> None:
        self._visit_item(node, "enum")

    def visit_trait_item(self, node: tree_sitter.Node) -> None:
        self._visit_item(node, "interface")

    def _visit_item(self, node: tree_sitter.Node, sym_type: str) -> None:
        name_node = node.child_by_field_name("name")
        if name_node:
            item_name = self.get_text(name_node)
            item_id = f"{self.rel_path}::{item_name}"

            self.emit_symbol(
                node=node,
                name=item_name,
                sym_type=sym_type,
                symbol_id=item_id,
                parent_id=self.file_node_id,
                signature=self.parser._get_signature(node, self.source),
                docstring=self.parser._get_docstring(node, self.source),
            )
        self.generic_visit(node)

    def visit_impl_item(self, node: tree_sitter.Node) -> None:
        impl_type = self.get_impl_type(node)
        pushed_impl = self.current_impl_type
        if impl_type:
            self.current_impl_type = impl_type
            type_id = f"{self.rel_path}::{impl_type}"

            trait_node = node.child_by_field_name("trait")
            if trait_node:
                trait_name = self.get_text(trait_node)
                self.emit_relation(type_id, trait_name, "implements")

        self.generic_visit(node)
        self.current_impl_type = pushed_impl

    def visit_function_item(self, node: tree_sitter.Node) -> None:
        name_node = node.child_by_field_name("name")
        if name_node:
            func_name = self.get_text(name_node)

            if self.current_impl_type:
                parent_id = f"{self.rel_path}::{self.current_impl_type}"
                func_id = f"{parent_id}.{func_name}"
                sym_type = "method"
            else:
                parent_id = self.file_node_id
                func_id = f"{self.rel_path}::{func_name}"
                sym_type = "function"

            local_bindings = {}

            def extract_rust_type(type_node) -> str | None:
                if type_node.type == "type_identifier":
                    return self.get_text(type_node)
                elif type_node.type in (
                    "pointer_type",
                    "reference_type",
                    "sliced_type",
                    "array_type",
                ):
                    for child in type_node.children:
                        if child.type not in ("&", "*", "mut", "const"):
                            res = extract_rust_type(child)
                            if res:
                                return res
                elif type_node.type == "generic_type":
                    type_id_node = type_node.child_by_field_name("type")
                    if type_id_node:
                        return extract_rust_type(type_id_node)
                return None

            def collect_local_bindings(n):
                if n.type == "parameter":
                    pattern_node = n.child_by_field_name("pattern")
                    type_node = n.child_by_field_name("type")
                    if pattern_node and type_node:
                        var_name = None
                        if pattern_node.type == "identifier":
                            var_name = self.get_text(pattern_node)
                        elif pattern_node.type == "mut_pattern":
                            inner = pattern_node.child_by_field_name("pattern")
                            if inner and inner.type == "identifier":
                                var_name = self.get_text(inner)
                        if var_name:
                            t_name = extract_rust_type(type_node)
                            if t_name:
                                local_bindings[var_name] = t_name

                elif n.type == "let_declaration":
                    pattern_node = n.child_by_field_name("pattern")
                    type_node = n.child_by_field_name("type")
                    value_node = n.child_by_field_name("value")

                    var_name = None
                    if pattern_node:
                        if pattern_node.type == "identifier":
                            var_name = self.get_text(pattern_node)
                        elif pattern_node.type == "mut_pattern":
                            inner = pattern_node.child_by_field_name("pattern")
                            if inner and inner.type == "identifier":
                                var_name = self.get_text(inner)

                    if var_name:
                        type_name = None
                        if type_node:
                            type_name = extract_rust_type(type_node)
                        elif value_node:
                            if value_node.type == "call_expression":
                                func = value_node.child_by_field_name("function")
                                if func and func.type == "scoped_identifier":
                                    path_node = func.child_by_field_name("path")
                                    if path_node:
                                        type_name = self.get_text(path_node)
                            elif value_node.type == "struct_expression":
                                name_node = value_node.child_by_field_name("name")
                                if name_node:
                                    type_name = extract_rust_type(name_node)
                            elif value_node.type == "match_expression":
                                subject_node = value_node.child_by_field_name("value")
                                if not subject_node:
                                    for child in value_node.children:
                                        if child.type in ("match_block", "{"):
                                            break
                                        if child.type != "match":
                                            subject_node = child
                                            break
                                if subject_node:
                                    sub_ids = []

                                    def collect_ids(sub_n):
                                        if sub_n.type == "identifier":
                                            id_str = self.get_text(sub_n)
                                            sub_ids.append(id_str)
                                        for c in sub_n.children:
                                            collect_ids(c)

                                    collect_ids(subject_node)
                                    for sub_id in sub_ids:
                                        if sub_id in local_bindings:
                                            type_name = local_bindings[sub_id]
                                            break
                        if type_name:
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
        self.generic_visit(node)

    def visit_use_declaration(self, node: tree_sitter.Node) -> None:
        def parse_use_item(n, prefix=""):
            if n.type == "use_path":
                parts = []
                use_list_node = None
                as_clause_node = None

                for child in n.children:
                    if child.type == "use_list":
                        use_list_node = child
                    elif child.type == "use_as_clause":
                        as_clause_node = child
                    elif child.type in (
                        "identifier",
                        "scoped_identifier",
                        "use_path",
                    ):
                        parts.append(self.get_text(child))

                current_path = "::".join(parts)
                full_path = f"{prefix}::{current_path}" if prefix else current_path

                if use_list_node:
                    for sub in use_list_node.children:
                        if sub.type in (
                            "use_path",
                            "identifier",
                            "scoped_identifier",
                            "use_as_clause",
                        ):
                            parse_use_item(sub, full_path)
                elif as_clause_node:
                    path_node = as_clause_node.child_by_field_name("path")
                    alias_node = as_clause_node.child_by_field_name("alias")
                    if path_node and alias_node:
                        sub_path = self.get_text(path_node)
                        alias_name = self.get_text(alias_node)
                        item_path = (
                            f"{full_path}::{sub_path}" if full_path else sub_path
                        )
                        last_symbol = item_path.split("::")[-1]
                        self.emit_relation(
                            self.file_node_id,
                            item_path,
                            "imports",
                            import_map={alias_name: last_symbol},
                        )
                else:
                    last_symbol = full_path.split("::")[-1]
                    self.emit_relation(
                        self.file_node_id,
                        full_path,
                        "imports",
                        import_map={last_symbol: last_symbol},
                    )

            elif n.type == "use_as_clause":
                path_node = n.child_by_field_name("path")
                alias_node = n.child_by_field_name("alias")
                if path_node and alias_node:
                    path_name = self.get_text(path_node)
                    alias_name = self.get_text(alias_node)
                    full_path = f"{prefix}::{path_name}" if prefix else path_name
                    last_symbol = full_path.split("::")[-1]
                    self.emit_relation(
                        self.file_node_id,
                        full_path,
                        "imports",
                        import_map={alias_name: last_symbol},
                    )
            elif n.type in ("identifier", "scoped_identifier"):
                name = self.get_text(n)
                full_path = f"{prefix}::{name}" if prefix else name
                last_symbol = full_path.split("::")[-1]
                self.emit_relation(
                    self.file_node_id,
                    full_path,
                    "imports",
                    import_map={last_symbol: last_symbol},
                )
            elif n.type == "self_literal":
                full_path = prefix
                last_symbol = full_path.split("::")[-1] if full_path else "self"
                self.emit_relation(
                    self.file_node_id,
                    full_path,
                    "imports",
                    import_map={last_symbol: last_symbol},
                )

        for child in node.children:
            if child.type in (
                "use_path",
                "use_list",
                "identifier",
                "scoped_identifier",
                "use_as_clause",
            ):
                parse_use_item(child)
        self.generic_visit(node)

    def visit_call_expression(self, node: tree_sitter.Node) -> None:
        self._visit_call(node, "call_expression")

    def visit_method_call_expression(self, node: tree_sitter.Node) -> None:
        self._visit_call(node, "method_call_expression")

    def _visit_call(self, node: tree_sitter.Node, node_type: str) -> None:
        callee_name = None
        if node_type == "call_expression":
            func_node = node.child_by_field_name("function")
            if func_node:
                callee_name = self.get_text(func_node)
        else:
            value_node = node.child_by_field_name("value")
            name_node = node.child_by_field_name("name")
            if value_node and name_node:
                receiver = self.get_text(value_node)
                method = self.get_text(name_node)
                callee_name = f"{receiver}.{method}"

        if callee_name:
            caller_id = self.file_node_id
            curr = node.parent
            while curr:
                if curr.type == "function_item":
                    c_name_node = curr.child_by_field_name("name")
                    if c_name_node:
                        c_name = self.get_text(c_name_node)
                        impl_node = curr.parent
                        while impl_node and impl_node.type != "impl_item":
                            impl_node = impl_node.parent
                        if impl_node:
                            r_type = self.get_impl_type(impl_node)
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


@register_parser("rust")
class RustParser(BaseParser):
    def __init__(self):
        self.language = tree_sitter.Language(tree_sitter_rust.language())
        self.parser = tree_sitter.Parser(self.language)

    def _get_docstring(self, node, source: bytes) -> str:
        docstring = ""
        prev = node.prev_sibling
        comments = []
        while prev and prev.type in ("line_comment", "block_comment"):
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
                signature=f"mod {file_path.stem}",
                docstring=self._get_docstring(root, source),
            )
        )

        ctx = ASTParsingContext(source, rel_path, result)
        handler = RustVisitor(ctx, self)
        visitor = ASTVisitor(handler, ctx)
        visitor.visit(root)
        return result
