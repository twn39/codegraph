import logging
from pathlib import Path

import tree_sitter
import tree_sitter_python

from codegraph_gen.parser.base import (
    ASTParsingContext,
    ASTVisitor,
    BaseParser,
    register_parser,
)
from codegraph_gen.parser.common import VisitorMixin, extract_type_name
from codegraph_gen.schema import ExtractionResult, NodeSchema

logger = logging.getLogger(__name__)


class PythonVisitor(VisitorMixin):
    traverser: ASTVisitor

    def visit_decorated_definition(self, node: tree_sitter.Node) -> None:
        """Unwrap @decorator stacks onto the underlying class/function definition."""
        definition = None
        for child in node.children:
            if child.type in ("class_definition", "function_definition"):
                definition = child
                break
        if definition is not None:
            self.visit(definition)
        else:
            self.generic_visit(node)

    def visit_class_definition(self, node: tree_sitter.Node) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            self.generic_visit(node)
            return

        class_name = self.get_text(name_node)
        class_id = f"{self.rel_path}::{class_name}"
        self.emit_symbol(
            node=node,
            name=class_name,
            sym_type="class",
            symbol_id=class_id,
            signature=self.parser._get_signature(node, self.source),
            docstring=self.parser._get_docstring(node, self.source),
        )

        superclasses = node.child_by_field_name("superclasses")
        if superclasses:
            for child in superclasses.children:
                if child.type in ("identifier", "attribute"):
                    parent_class_name = self.get_text(child)
                    self.emit_relation(class_id, parent_class_name, "inherits")

        with self.scope.push(class_id, "class"):
            self.generic_visit(node)

    def visit_function_definition(self, node: tree_sitter.Node) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            self.generic_visit(node)
            return

        func_name = self.get_text(name_node)
        parent_id = self.get_current_parent_id()
        parent_type = self.scope.current_type

        if parent_type == "class":
            func_id = f"{parent_id}.{func_name}"
            sym_type = "method"
        else:
            func_id = f"{self.rel_path}::{func_name}"
            sym_type = "function"

        local_bindings = self._collect_local_bindings(node)

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

    def _collect_local_bindings(self, node: tree_sitter.Node) -> dict[str, str]:
        local_bindings: dict[str, str] = {}

        def bind(var_name: str | None, type_node) -> None:
            if not var_name or type_node is None:
                return
            t_name = extract_type_name(type_node, self.get_text)
            if t_name:
                local_bindings[var_name] = t_name

        def walk(n: tree_sitter.Node) -> None:
            if n.type in ("typed_parameter", "typed_default_parameter"):
                var_name = None
                for child in n.children:
                    if child.type == "identifier":
                        var_name = self.get_text(child)
                        break
                type_node = n.child_by_field_name("type")
                bind(var_name, type_node)
            elif n.type == "assignment":
                left = n.child_by_field_name("left") or (
                    n.children[0] if n.children else None
                )
                type_ann = n.child_by_field_name("type")
                right = n.child_by_field_name("right") or (
                    n.children[-1] if len(n.children) > 1 else None
                )
                if left and left.type == "identifier":
                    var_name = self.get_text(left)
                    if type_ann is not None:
                        bind(var_name, type_ann)
                    elif right is not None and right.type == "call":
                        bind(var_name, right)
            elif n.type == "as_pattern":
                call_node = None
                target_node = None
                for child in n.children:
                    if child.type == "call":
                        call_node = child
                    elif child.type == "as_pattern_target":
                        for sub in child.children:
                            if sub.type == "identifier":
                                target_node = sub
                                break
                if call_node and target_node:
                    bind(self.get_text(target_node), call_node)

            for child in n.children:
                # Do not descend into nested function bodies for outer bindings
                if child.type not in ("function_definition", "decorated_definition"):
                    walk(child)

        walk(node)
        return local_bindings

    def visit_import_statement(self, node: tree_sitter.Node) -> None:
        file_node_id = self.rel_path
        for child in node.children:
            if child.type == "dotted_name":
                module_name = self.get_text(child)
                self.emit_relation(
                    file_node_id,
                    module_name,
                    "imports",
                    import_map={module_name: module_name},
                )
            elif child.type == "aliased_import":
                name_node = child.child_by_field_name("name")
                alias_node = child.child_by_field_name("alias")
                if name_node and alias_node:
                    module_name = self.get_text(name_node)
                    alias_name = self.get_text(alias_node)
                    self.emit_relation(
                        file_node_id,
                        module_name,
                        "imports",
                        import_map={alias_name: module_name},
                    )
        self.generic_visit(node)

    def visit_import_from_statement(self, node: tree_sitter.Node) -> None:
        file_node_id = self.rel_path
        module_node = node.child_by_field_name("module_name")
        module_name = self.get_text(module_node) if module_node else ""

        dots = ""
        for child in node.children:
            if child.type == "relative_source":
                dots = self.get_text(child)
                break

        target_module = dots + module_name
        import_map: dict[str, str] = {}
        import_items = []

        start_collecting = False
        for child in node.children:
            if (module_node and child == module_node) or (
                child.type == "relative_source" and not start_collecting
            ):
                start_collecting = True
                continue
            if start_collecting:
                if child.type == "wildcard_import":
                    import_items.append(child)
                elif child.type in ("dotted_name", "aliased_import", "identifier"):
                    import_items.append(child)
                elif child.type == "import_list":
                    for sub_child in child.children:
                        if sub_child.type in (
                            "dotted_name",
                            "aliased_import",
                            "identifier",
                        ):
                            import_items.append(sub_child)

        for item in import_items:
            if item.type == "wildcard_import":
                import_map["*"] = "*"
            elif item.type in ("dotted_name", "identifier"):
                name = self.get_text(item)
                import_map[name] = name
            elif item.type == "aliased_import":
                name_node = item.child_by_field_name("name")
                alias_node = item.child_by_field_name("alias")
                if name_node and alias_node:
                    name = self.get_text(name_node)
                    alias = self.get_text(alias_node)
                    import_map[alias] = name

        if target_module:
            self.emit_relation(
                file_node_id,
                target_module,
                "imports",
                import_map=import_map,
            )
        self.generic_visit(node)

    def visit_call(self, node: tree_sitter.Node) -> None:
        func_node = node.child_by_field_name("function")
        if func_node:
            callee_name = self.get_text(func_node)
            caller_id = self.get_current_parent_id()
            self.emit_relation(caller_id, callee_name, "calls")
        self.generic_visit(node)


@register_parser("python")
class PythonParser(BaseParser):
    def __init__(self):
        self.language = tree_sitter.Language(tree_sitter_python.language())
        self.parser = tree_sitter.Parser(self.language)

    def _get_docstring(self, node, source: bytes) -> str:
        body = node.child_by_field_name("body")
        if not body:
            body = node

        for child in body.children:
            if child.type == "expression_statement":
                for sub in child.children:
                    if sub.type in ("string", "concatenated_string"):
                        text = source[sub.start_byte : sub.end_byte].decode(
                            "utf-8", errors="replace"
                        )
                        return text.strip("\"'").strip()
            if child.type not in ("comment",):
                break
        return ""

    def _get_signature(self, node, source: bytes) -> str:
        body = node.child_by_field_name("body")
        if body:
            end_byte = body.start_byte
            sig_bytes = source[node.start_byte : end_byte]
            sig = sig_bytes.decode("utf-8", errors="replace").strip()
            if sig.endswith(":"):
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
        handler = PythonVisitor(ctx, self)
        visitor = ASTVisitor(handler, ctx)
        visitor.visit(root)
        return result
