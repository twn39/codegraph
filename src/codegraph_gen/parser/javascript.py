import logging
from pathlib import Path
import tree_sitter
import tree_sitter_javascript
import tree_sitter_typescript
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


class JavaScriptVisitor(VisitorMixin):
    traverser: ASTVisitor

    def __init__(self, ctx: ASTParsingContext, parser):
        super().__init__(ctx, parser)
        self.file_node_id = ctx.rel_path

    def visit_class_declaration(self, node: tree_sitter.Node) -> None:
        self._visit_class_or_interface(node, "class_declaration")

    def visit_interface_declaration(self, node: tree_sitter.Node) -> None:
        self._visit_class_or_interface(node, "interface_declaration")

    def _visit_class_or_interface(self, node: tree_sitter.Node, node_type: str) -> None:
        name_node = node.child_by_field_name("name")
        if name_node:
            class_name = self.get_text(name_node)
            parent_id = self.get_current_parent_id()
            class_id = f"{self.rel_path}::{class_name}"
            sym_type = "class" if node_type == "class_declaration" else "interface"

            self.emit_symbol(
                node=node,
                name=class_name,
                sym_type=sym_type,
                symbol_id=class_id,
                parent_id=parent_id,
                signature=self.parser._get_signature(node, self.source),
                docstring=self.parser._get_docstring(node, self.source),
            )

            # Inheritance
            for child in node.children:
                if child.type in ("class_heritage", "interface_heritage"):
                    for sub in child.children:
                        if sub.type in ("identifier", "nested_identifier"):
                            parent_class_name = self.get_text(sub)
                            self.emit_relation(class_id, parent_class_name, "inherits")

            with self.scope.push(class_id, sym_type):
                self.generic_visit(node)
        else:
            self.generic_visit(node)

    def visit_function_declaration(self, node: tree_sitter.Node) -> None:
        self._visit_function_or_method(node)

    def visit_method_definition(self, node: tree_sitter.Node) -> None:
        self._visit_function_or_method(node)

    def _visit_function_or_method(self, node: tree_sitter.Node) -> None:
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

            def extract_type_from_ts_node(ts_node):
                if ts_node.type == "type_identifier":
                    return self.get_text(ts_node)
                elif ts_node.type == "property_identifier":
                    return self.get_text(ts_node)
                elif ts_node.type == "nested_type_identifier":
                    for child in reversed(ts_node.children):
                        if child.type in ("type_identifier", "identifier"):
                            return extract_type_from_ts_node(child)
                elif ts_node.type == "generic_type":
                    type_node = ts_node.child_by_field_name("name") or (
                        ts_node.children[0] if ts_node.children else None
                    )
                    if type_node:
                        return extract_type_from_ts_node(type_node)
                elif ts_node.type == "new_expression":
                    constructor_node = ts_node.child_by_field_name("constructor")
                    if constructor_node:
                        if constructor_node.type == "identifier":
                            return self.get_text(constructor_node)
                        elif constructor_node.type == "member_expression":
                            prop = constructor_node.child_by_field_name("property")
                            if prop:
                                return self.get_text(prop)
                elif ts_node.type == "type_annotation":
                    for child in ts_node.children:
                        res = extract_type_from_ts_node(child)
                        if res:
                            return res
                for child in ts_node.children:
                    res = extract_type_from_ts_node(child)
                    if res:
                        return res
                return None

            def collect_local_bindings(n):
                if n.type in ("required_parameter", "optional_parameter"):
                    pattern = n.child_by_field_name("pattern")
                    type_node = n.child_by_field_name("type")
                    if pattern and pattern.type == "identifier" and type_node:
                        var_name = self.get_text(pattern)
                        t_name = extract_type_from_ts_node(type_node)
                        if t_name:
                            local_bindings[var_name] = t_name
                elif n.type == "variable_declarator":
                    name_node = n.child_by_field_name("name")
                    value_node = n.child_by_field_name("value")
                    type_node = n.child_by_field_name("type")
                    if name_node and name_node.type == "identifier":
                        var_name = self.get_text(name_node)
                        if type_node:
                            t_name = extract_type_from_ts_node(type_node)
                            if t_name:
                                local_bindings[var_name] = t_name
                        elif value_node and value_node.type == "new_expression":
                            t_name = extract_type_from_ts_node(value_node)
                            if t_name:
                                local_bindings[var_name] = t_name

                for child in n.children:
                    if child.type not in (
                        "function_declaration",
                        "method_definition",
                        "class_declaration",
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

    def visit_import_statement(self, node: tree_sitter.Node) -> None:
        source_node = node.child_by_field_name("source")
        if source_node:
            import_path = self.get_text(source_node).strip("\"'")
            import_map = {}
            clause_node = None
            for child in node.children:
                if child.type == "import_clause":
                    clause_node = child
                    break

            if clause_node:
                for c in clause_node.children:
                    if c.type == "identifier":
                        name = self.get_text(c)
                        import_map[name] = "default"
                    elif c.type == "namespace_import":
                        for sub in c.children:
                            if sub.type == "identifier":
                                name = self.get_text(sub)
                                import_map[name] = "*"
                                break
                    elif c.type == "named_imports":
                        for spec in c.children:
                            if spec.type == "import_specifier":
                                name_node = spec.child_by_field_name("name")
                                alias_node = spec.child_by_field_name("alias")
                                if name_node and alias_node:
                                    name = self.get_text(name_node)
                                    alias = self.get_text(alias_node)
                                    import_map[alias] = name
                                elif name_node:
                                    name = self.get_text(name_node)
                                    import_map[name] = name

            self.emit_relation(
                self.file_node_id, import_path, "imports", import_map=import_map
            )
        self.generic_visit(node)

    def visit_call_expression(self, node: tree_sitter.Node) -> None:
        func_node = node.child_by_field_name("function")
        if func_node:
            callee_name = self.get_text(func_node)
            caller_id = self.get_current_parent_id()
            self.emit_relation(caller_id, callee_name, "calls")
        self.generic_visit(node)

    def visit_new_expression(self, node: tree_sitter.Node) -> None:
        func_node = node.child_by_field_name("constructor")
        if func_node:
            callee_name = self.get_text(func_node)
            caller_id = self.get_current_parent_id()
            self.emit_relation(caller_id, callee_name, "calls")
        self.generic_visit(node)


@register_parser("javascript", "typescript")
class JavaScriptParser(BaseParser):
    def __init__(self):
        self.js_lang = tree_sitter.Language(tree_sitter_javascript.language())
        self.ts_lang = tree_sitter.Language(
            tree_sitter_typescript.language_typescript()
        )
        self.tsx_lang = tree_sitter.Language(tree_sitter_typescript.language_tsx())

        self.js_parser = tree_sitter.Parser(self.js_lang)
        self.ts_parser = tree_sitter.Parser(self.ts_lang)
        self.tsx_parser = tree_sitter.Parser(self.tsx_lang)

    def _get_docstring(self, node, source: bytes) -> str:
        docstring = ""
        prev = node.prev_sibling
        comments = []
        while prev and prev.type in ("comment", "line_comment", "block_comment"):
            comment_text = source[prev.start_byte : prev.end_byte].decode(
                "utf-8", errors="replace"
            )
            clean_text = (
                comment_text.strip().lstrip("/*").rstrip("*/").lstrip("*").strip()
            )
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

        ext = file_path.suffix.lower()
        if ext == ".tsx":
            parser = self.tsx_parser
        elif ext in (".ts", ".cts", ".mts"):
            parser = self.ts_parser
        else:
            parser = self.js_parser

        tree = parser.parse(source)
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
                signature=f"module {file_path.name}",
                docstring=self._get_docstring(root, source),
            )
        )

        ctx = ASTParsingContext(source, rel_path, result)
        handler = JavaScriptVisitor(ctx, self)
        visitor = ASTVisitor(handler, ctx)
        visitor.visit(root)
        return result
