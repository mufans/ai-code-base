"""Tree-sitter based code structure indexer."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Protocol

import tree_sitter as ts
import tree_sitter_python as tspython
import tree_sitter_javascript as tsjs
import tree_sitter_java as tsjava
import tree_sitter_kotlin as tskotlin
import tree_sitter_swift as tsswift
import tree_sitter_typescript as tstypescript

try:
    import tree_sitter_arkts as tsarkts
except ImportError:
    tsarkts = None  # type: ignore[assignment]

from codekb.storage.sqlite_store import (
    SqliteStore,
    Symbol,
    CallRelation,
    ImportRecord,
    FileEntry,
    EdgeRelation,
)

from codekb.core.module_detector import ModuleInfo, file_to_module


# --- Language Strategy Protocol ---

class LanguageStrategy(Protocol):
    """Per-language extraction patterns."""

    def language(self) -> ts.Language:
        ...

    def file_extensions(self) -> set[str]:
        ...

    def extract_symbols(
        self, tree: ts.Tree, source: bytes, file_path: str, repo_name: str, language_name: str
    ) -> list[Symbol]:
        ...

    def extract_calls(
        self, tree: ts.Tree, source: bytes, file_path: str, repo_name: str
    ) -> list[CallRelation]:
        ...

    def extract_imports(
        self, tree: ts.Tree, source: bytes, file_path: str, repo_name: str
    ) -> list[ImportRecord]:
        ...

    def extract_inheritance(
        self, tree: ts.Tree, source: bytes, file_path: str, repo_name: str
    ) -> list[EdgeRelation]:
        ...


# --- Python Strategy ---

class PythonStrategy:
    def language(self) -> ts.Language:
        return ts.Language(tspython.language())

    def file_extensions(self) -> set[str]:
        return {".py"}

    def extract_symbols(
        self, tree: ts.Tree, source: bytes, file_path: str, repo_name: str, language_name: str
    ) -> list[Symbol]:
        symbols: list[Symbol] = []
        root = tree.root_node
        self._walk_for_symbols(root, source, file_path, repo_name, language_name, "", symbols)
        return symbols

    def _walk_for_symbols(
        self, node: ts.Node, source: bytes, file_path: str,
        repo_name: str, language_name: str, parent: str, symbols: list[Symbol]
    ):
        if node.type == "function_definition":
            name = self._get_field_text(node, "name", source)
            params = self._get_field_text(node, "parameters", source)
            signature = f"def {name}{params}"
            docstring = self._extract_docstring(node, source)
            symbols.append(Symbol(
                repo_name=repo_name,
                file_path=file_path,
                name=name,
                kind="method" if parent else "function",
                signature=signature,
                docstring=docstring,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                parent=parent,
                language=language_name,
                source=bytes(node.text).decode("utf-8", errors="replace"),
            ))

        elif node.type == "class_definition":
            name = self._get_field_text(node, "name", source)
            superclasses = self._get_field_text(node, "superclasses", source)
            sig = f"class {name}"
            if superclasses:
                sig += f"({superclasses})"
            docstring = self._extract_docstring(node, source)
            symbols.append(Symbol(
                repo_name=repo_name,
                file_path=file_path,
                name=name,
                kind="class",
                signature=sig,
                docstring=docstring,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                parent=parent,
                language=language_name,
                source=bytes(node.text).decode("utf-8", errors="replace"),
            ))
            # Walk into class body for methods
            body = node.child_by_field_name("body")
            if body:
                for child in body.children:
                    self._walk_for_symbols(child, source, file_path, repo_name, language_name, name, symbols)

        else:
            for child in node.children:
                self._walk_for_symbols(child, source, file_path, repo_name, language_name, parent, symbols)

    def _get_field_text(self, node: ts.Node, field: str, source: bytes) -> str:
        child = node.child_by_field_name(field)
        if child is None:
            return ""
        return bytes(child.text).decode("utf-8", errors="replace")

    def _extract_docstring(self, node: ts.Node, source: bytes) -> str:
        body = node.child_by_field_name("body")
        if body is None or not body.children:
            return ""
        first = body.children[0]
        if first.type == "expression_statement" and first.children:
            expr = first.children[0]
            if expr.type == "string":
                text = bytes(expr.text).decode("utf-8", errors="replace")
                # Strip triple quotes
                for q in ('"""', "'''"):
                    if text.startswith(q) and text.endswith(q):
                        return text[len(q):-len(q)].strip()
                return text
        return ""

    def extract_calls(
        self, tree: ts.Tree, source: bytes, file_path: str, repo_name: str
    ) -> list[CallRelation]:
        calls: list[CallRelation] = []
        # Find all function/method definitions, then find calls within them
        self._walk_for_calls(tree.root_node, source, file_path, repo_name, calls)
        return calls

    def _walk_for_calls(
        self, node: ts.Node, source: bytes, file_path: str,
        repo_name: str, calls: list[CallRelation]
    ):
        if node.type == "function_definition":
            func_name = self._get_field_text(node, "name", source)
            self._collect_calls_in_node(node, func_name, file_path, repo_name, calls)
        elif node.type == "class_definition":
            body = node.child_by_field_name("body")
            if body:
                for child in body.children:
                    self._walk_for_calls(child, source, file_path, repo_name, calls)
        else:
            for child in node.children:
                self._walk_for_calls(child, source, file_path, repo_name, calls)

    def _collect_calls_in_node(
        self, node: ts.Node, caller_name: str, file_path: str,
        repo_name: str, calls: list[CallRelation]
    ):
        if node.type == "call":
            func = node.child_by_field_name("function")
            if func:
                callee = bytes(func.text).decode("utf-8", errors="replace")
                calls.append(CallRelation(
                    repo_name=repo_name,
                    caller_file=file_path,
                    caller_name=caller_name,
                    callee_name=callee,
                    line_number=node.start_point[0] + 1,
                ))
        for child in node.children:
            self._collect_calls_in_node(child, caller_name, file_path, repo_name, calls)

    def extract_imports(
        self, tree: ts.Tree, source: bytes, file_path: str, repo_name: str
    ) -> list[ImportRecord]:
        imports: list[ImportRecord] = []
        self._walk_for_imports(tree.root_node, source, file_path, repo_name, imports)
        return imports

    def _walk_for_imports(
        self, node: ts.Node, source: bytes, file_path: str,
        repo_name: str, imports: list[ImportRecord]
    ):
        if node.type == "import_statement":
            # import X, Y
            for child in node.children:
                if child.type == "dotted_name":
                    module = bytes(child.text).decode("utf-8", errors="replace")
                    imports.append(ImportRecord(
                        repo_name=repo_name,
                        file_path=file_path,
                        module=module,
                        line_number=node.start_point[0] + 1,
                    ))
                elif child.type == "aliased_import":
                    name_node = child.child_by_field_name("name")
                    if name_node:
                        module = bytes(name_node.text).decode("utf-8", errors="replace")
                        imports.append(ImportRecord(
                            repo_name=repo_name,
                            file_path=file_path,
                            module=module,
                            line_number=node.start_point[0] + 1,
                        ))
        elif node.type == "import_from_statement":
            # from X import Y, Z
            module_name = node.child_by_field_name("module_name")
            module = bytes(module_name.text).decode("utf-8", errors="replace") if module_name else ""
            names = []
            for child in node.children:
                if child.type == "dotted_name" and child != module_name:
                    continue
                if child.type == "identifier" and child != module_name:
                    names.append(bytes(child.text).decode("utf-8", errors="replace"))
                elif child.type == "aliased_import":
                    name_node = child.child_by_field_name("name")
                    if name_node:
                        names.append(bytes(name_node.text).decode("utf-8", errors="replace"))
                elif child.type == "wildcard_import":
                    names.append("*")
            imports.append(ImportRecord(
                repo_name=repo_name,
                file_path=file_path,
                module=module,
                imported_names=",".join(names),
                line_number=node.start_point[0] + 1,
                is_relative=module.startswith("."),
            ))
        else:
            for child in node.children:
                self._walk_for_imports(child, source, file_path, repo_name, imports)

    def extract_inheritance(
        self, tree: ts.Tree, source: bytes, file_path: str, repo_name: str
    ) -> list[EdgeRelation]:
        """Extract extends/implements edges from class definitions.

        Python convention: class Foo(Bar) → extends Bar
        For Python ABC-based patterns, we check if any base name suggests an interface
        (contains 'Interface', 'Protocol', 'ABC', 'Mixin' or starts with 'I').
        """
        edges: list[EdgeRelation] = []
        self._walk_for_inheritance(tree.root_node, source, file_path, repo_name, edges)
        return edges

    def _walk_for_inheritance(
        self, node: ts.Node, source: bytes, file_path: str,
        repo_name: str, edges: list[EdgeRelation]
    ):
        if node.type == "class_definition":
            class_name = self._get_field_text(node, "name", source)
            superclasses_node = node.child_by_field_name("superclasses")
            if superclasses_node:
                for child in superclasses_node.children:
                    base_name = bytes(child.text).decode("utf-8", errors="replace").strip()
                    if not base_name or base_name in ("(", ")", ","):
                        continue
                    # Determine if it's extends or implements
                    kind = self._classify_python_base(base_name)
                    edges.append(EdgeRelation(
                        repo_name=repo_name,
                        source_symbol=class_name,
                        source_file=file_path,
                        target_symbol=base_name,
                        kind=kind,
                        line_number=node.start_point[0] + 1,
                    ))
        for child in node.children:
            self._walk_for_inheritance(child, source, file_path, repo_name, edges)

    def _classify_python_base(self, base_name: str) -> str:
        """Classify a Python base class as extends or implements."""
        interface_indicators = ("Interface", "Protocol", "ABC", "Mixin", "Abstract")
        if any(ind in base_name for ind in interface_indicators):
            return "implements"
        if base_name.startswith("I") and len(base_name) > 1 and base_name[1].isupper():
            return "implements"
        return "extends"

class JavaScriptStrategy:
    def language(self) -> ts.Language:
        return ts.Language(tsjs.language())

    def file_extensions(self) -> set[str]:
        return {".js", ".jsx", ".mjs", ".cjs"}

    def extract_symbols(
        self, tree: ts.Tree, source: bytes, file_path: str, repo_name: str, language_name: str
    ) -> list[Symbol]:
        symbols: list[Symbol] = []
        self._walk_for_symbols(tree.root_node, source, file_path, repo_name, language_name, "", symbols)
        return symbols

    def _walk_for_symbols(
        self, node: ts.Node, source: bytes, file_path: str,
        repo_name: str, language_name: str, parent: str, symbols: list[Symbol]
    ):
        if node.type == "function_declaration":
            name = self._get_field_text(node, "name", source)
            params = self._get_field_text(node, "parameters", source)
            symbols.append(Symbol(
                repo_name=repo_name,
                file_path=file_path,
                name=name,
                kind="method" if parent else "function",
                signature=f"function {name}{params}",
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                parent=parent,
                language=language_name,
                source=bytes(node.text).decode("utf-8", errors="replace"),
            ))
        elif node.type == "class_declaration":
            name = self._get_field_text(node, "name", source)
            symbols.append(Symbol(
                repo_name=repo_name,
                file_path=file_path,
                name=name,
                kind="class",
                signature=f"class {name}",
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                parent=parent,
                language=language_name,
                source=bytes(node.text).decode("utf-8", errors="replace"),
            ))
            body = node.child_by_field_name("body")
            if body:
                for child in body.children:
                    if child.type == "method_definition":
                        method_name = self._get_field_text(child, "name", source)
                        method_params = self._get_field_text(child, "parameters", source)
                        symbols.append(Symbol(
                            repo_name=repo_name,
                            file_path=file_path,
                            name=method_name,
                            kind="method",
                            signature=f"{method_name}{method_params}",
                            start_line=child.start_point[0] + 1,
                            end_line=child.end_point[0] + 1,
                            parent=name,
                            language=language_name,
                            source=bytes(child.text).decode("utf-8", errors="replace"),
                        ))
        elif node.type == "lexical_declaration" or node.type == "variable_declaration":
            for child in node.children:
                if child.type == "variable_declarator":
                    name_node = child.child_by_field_name("name")
                    value_node = child.child_by_field_name("value")
                    if name_node and value_node and value_node.type == "arrow_function":
                        name = bytes(name_node.text).decode("utf-8", errors="replace")
                        params = self._get_field_text(value_node, "parameters", source)
                        symbols.append(Symbol(
                            repo_name=repo_name,
                            file_path=file_path,
                            name=name,
                            kind="function",
                            signature=f"const {name} = {params} =>",
                            start_line=node.start_point[0] + 1,
                            end_line=node.end_point[0] + 1,
                            parent=parent,
                            language=language_name,
                            source=bytes(node.text).decode("utf-8", errors="replace"),
                        ))

        for child in node.children:
            if node.type != "class_declaration" or child.type != "method_definition":
                if node.type != "class_declaration" or child != node.child_by_field_name("body"):
                    pass  # already handled above
        # Walk deeper for non-handled cases
        if node.type not in ("class_declaration",):
            for child in node.children:
                self._walk_for_symbols(child, source, file_path, repo_name, language_name, parent, symbols)

    def _get_field_text(self, node: ts.Node, field: str, source: bytes) -> str:
        child = node.child_by_field_name(field)
        if child is None:
            return ""
        return bytes(child.text).decode("utf-8", errors="replace")

    def extract_calls(
        self, tree: ts.Tree, source: bytes, file_path: str, repo_name: str
    ) -> list[CallRelation]:
        calls: list[CallRelation] = []
        self._walk_for_calls(tree.root_node, source, file_path, repo_name, "<module>", calls)
        return calls

    def _walk_for_calls(
        self, node: ts.Node, source: bytes, file_path: str,
        repo_name: str, caller_name: str, calls: list[CallRelation]
    ):
        if node.type == "function_declaration":
            name = self._get_field_text(node, "name", source)
            self._collect_calls_in_node(node, name, file_path, repo_name, calls)
            return
        elif node.type == "method_definition":
            name = self._get_field_text(node, "name", source)
            self._collect_calls_in_node(node, name, file_path, repo_name, calls)
            return
        elif node.type == "call_expression":
            func = node.child_by_field_name("function")
            if func:
                callee = bytes(func.text).decode("utf-8", errors="replace")
                calls.append(CallRelation(
                    repo_name=repo_name,
                    caller_file=file_path,
                    caller_name=caller_name,
                    callee_name=callee,
                    line_number=node.start_point[0] + 1,
                ))
        for child in node.children:
            self._walk_for_calls(child, source, file_path, repo_name, caller_name, calls)

    def _collect_calls_in_node(
        self, node: ts.Node, caller_name: str, file_path: str,
        repo_name: str, calls: list[CallRelation]
    ):
        if node.type == "call_expression":
            func = node.child_by_field_name("function")
            if func:
                callee = bytes(func.text).decode("utf-8", errors="replace")
                calls.append(CallRelation(
                    repo_name=repo_name,
                    caller_file=file_path,
                    caller_name=caller_name,
                    callee_name=callee,
                    line_number=node.start_point[0] + 1,
                ))
        for child in node.children:
            self._collect_calls_in_node(child, caller_name, file_path, repo_name, calls)

    def extract_imports(
        self, tree: ts.Tree, source: bytes, file_path: str, repo_name: str
    ) -> list[ImportRecord]:
        imports: list[ImportRelation] = []
        self._walk_for_imports(tree.root_node, source, file_path, repo_name, imports)
        return imports

    def _walk_for_imports(
        self, node: ts.Node, source: bytes, file_path: str,
        repo_name: str, imports: list[ImportRecord]
    ):
        if node.type == "import_statement":
            source_str = bytes(node.text).decode("utf-8", errors="replace")
            # import 'module' or import default from 'module'
            for child in node.children:
                if child.type == "string":
                    module = bytes(child.text).decode("utf-8", errors="replace").strip("'\"")
                    imports.append(ImportRecord(
                        repo_name=repo_name,
                        file_path=file_path,
                        module=module,
                        line_number=node.start_point[0] + 1,
                    ))
        elif node.type == "import_clause":
            pass  # handled by parent
        for child in node.children:
            self._walk_for_imports(child, source, file_path, repo_name, imports)

    def extract_inheritance(
        self, tree: ts.Tree, source: bytes, file_path: str, repo_name: str
    ) -> list[EdgeRelation]:
        """Extract extends edges from class declarations."""
        edges: list[EdgeRelation] = []
        self._walk_for_inheritance(tree.root_node, source, file_path, repo_name, edges)
        return edges

    def _walk_for_inheritance(
        self, node: ts.Node, source: bytes, file_path: str,
        repo_name: str, edges: list[EdgeRelation]
    ):
        if node.type == "class_declaration":
            class_name = self._get_field_text(node, "name", source)
            # JS class extends: class Foo extends Bar
            for child in node.children:
                if child.type == "class_heritage":
                    for heritage_child in child.children:
                        if heritage_child.type not in (",", "extends"):
                            base_name = bytes(heritage_child.text).decode("utf-8", errors="replace")
                            edges.append(EdgeRelation(
                                repo_name=repo_name,
                                source_symbol=class_name,
                                source_file=file_path,
                                target_symbol=base_name,
                                kind="extends",
                                line_number=node.start_point[0] + 1,
                            ))
        for child in node.children:
            self._walk_for_inheritance(child, source, file_path, repo_name, edges)

class JavaStrategy:
    def language(self) -> ts.Language:
        return ts.Language(tsjava.language())

    def file_extensions(self) -> set[str]:
        return {".java"}

    def extract_symbols(
        self, tree: ts.Tree, source: bytes, file_path: str, repo_name: str, language_name: str
    ) -> list[Symbol]:
        symbols: list[Symbol] = []
        self._walk_for_symbols(tree.root_node, source, file_path, repo_name, language_name, "", symbols)
        return symbols

    def _walk_for_symbols(
        self, node: ts.Node, source: bytes, file_path: str,
        repo_name: str, language_name: str, parent: str, symbols: list[Symbol]
    ):
        if node.type == "class_declaration":
            name = self._get_name(node, source)
            annotations = self._get_annotations(node, source)
            sig = f"class {name}"
            symbols.append(Symbol(
                repo_name=repo_name, file_path=file_path, name=name,
                kind="class", signature=sig, docstring=annotations,
                start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
                parent=parent, language=language_name,
                source=bytes(node.text).decode("utf-8", errors="replace"),
            ))
            body = node.child_by_field_name("body")
            if body:
                for child in body.children:
                    self._walk_for_symbols(child, source, file_path, repo_name, language_name, name, symbols)

        elif node.type == "interface_declaration":
            name = self._get_name(node, source)
            annotations = self._get_annotations(node, source)
            sig = f"interface {name}"
            symbols.append(Symbol(
                repo_name=repo_name, file_path=file_path, name=name,
                kind="interface", signature=sig, docstring=annotations,
                start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
                parent=parent, language=language_name,
                source=bytes(node.text).decode("utf-8", errors="replace"),
            ))

        elif node.type == "enum_declaration":
            name = self._get_name(node, source)
            annotations = self._get_annotations(node, source)
            sig = f"enum {name}"
            symbols.append(Symbol(
                repo_name=repo_name, file_path=file_path, name=name,
                kind="enum", signature=sig, docstring=annotations,
                start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
                parent=parent, language=language_name,
                source=bytes(node.text).decode("utf-8", errors="replace"),
            ))

        elif node.type == "method_declaration":
            name = self._get_name(node, source)
            params = self._get_child_text(node, "formal_parameters", source)
            sig = f"{name}{params}"
            annotations = self._get_annotations(node, source)
            symbols.append(Symbol(
                repo_name=repo_name, file_path=file_path, name=name,
                kind="method", signature=sig, docstring=annotations,
                start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
                parent=parent, language=language_name,
                source=bytes(node.text).decode("utf-8", errors="replace"),
            ))

        elif node.type == "constructor_declaration":
            params = self._get_child_text(node, "formal_parameters", source)
            name = parent if parent else "constructor"
            annotations = self._get_annotations(node, source)
            sig = f"{name}{params}"
            symbols.append(Symbol(
                repo_name=repo_name, file_path=file_path, name=name,
                kind="constructor", signature=sig, docstring=annotations,
                start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
                parent=parent, language=language_name,
                source=bytes(node.text).decode("utf-8", errors="replace"),
            ))

        else:
            for child in node.children:
                self._walk_for_symbols(child, source, file_path, repo_name, language_name, parent, symbols)

    def _get_name(self, node: ts.Node, source: bytes) -> str:
        for child in node.children:
            if child.type == "identifier":
                return bytes(child.text).decode("utf-8", errors="replace")
        return ""

    def _get_child_text(self, node: ts.Node, child_type: str, source: bytes) -> str:
        for child in node.children:
            if child.type == child_type:
                return bytes(child.text).decode("utf-8", errors="replace")
        return ""

    def _get_annotations(self, node: ts.Node, source: bytes) -> str:
        # In Java tree-sitter, modifiers is a child node (not a named field)
        modifiers = None
        for child in node.children:
            if child.type == "modifiers":
                modifiers = child
                break
        if modifiers is None:
            return ""
        anns: list[str] = []
        for child in modifiers.children:
            if child.type in ("marker_annotation", "annotation"):
                anns.append(bytes(child.text).decode("utf-8", errors="replace"))
        return "\n".join(anns)

    def extract_calls(
        self, tree: ts.Tree, source: bytes, file_path: str, repo_name: str
    ) -> list[CallRelation]:
        calls: list[CallRelation] = []
        self._walk_for_calls(tree.root_node, source, file_path, repo_name, "<module>", calls)
        return calls

    def _walk_for_calls(
        self, node: ts.Node, source: bytes, file_path: str,
        repo_name: str, caller_name: str, calls: list[CallRelation]
    ):
        if node.type == "method_declaration":
            name = self._get_name(node, source)
            self._collect_calls_in_node(node, name, file_path, repo_name, calls)
            return
        elif node.type == "constructor_declaration":
            name = self._get_name(node, source)
            self._collect_calls_in_node(node, name or "constructor", file_path, repo_name, calls)
            return
        elif node.type == "method_invocation":
            callee = bytes(node.text).decode("utf-8", errors="replace")
            # Extract just the method call part (without args)
            callee = callee.split("(")[0] if "(" in callee else callee
            calls.append(CallRelation(
                repo_name=repo_name, caller_file=file_path,
                caller_name=caller_name, callee_name=callee,
                line_number=node.start_point[0] + 1,
            ))
        for child in node.children:
            self._walk_for_calls(child, source, file_path, repo_name, caller_name, calls)

    def _collect_calls_in_node(
        self, node: ts.Node, caller_name: str, file_path: str,
        repo_name: str, calls: list[CallRelation]
    ):
        if node.type == "method_invocation":
            callee = bytes(node.text).decode("utf-8", errors="replace")
            callee = callee.split("(")[0] if "(" in callee else callee
            calls.append(CallRelation(
                repo_name=repo_name, caller_file=file_path,
                caller_name=caller_name, callee_name=callee,
                line_number=node.start_point[0] + 1,
            ))
        for child in node.children:
            self._collect_calls_in_node(child, caller_name, file_path, repo_name, calls)

    def extract_imports(
        self, tree: ts.Tree, source: bytes, file_path: str, repo_name: str
    ) -> list[ImportRecord]:
        imports: list[ImportRecord] = []
        self._walk_for_imports(tree.root_node, source, file_path, repo_name, imports)
        return imports

    def _walk_for_imports(
        self, node: ts.Node, source: bytes, file_path: str,
        repo_name: str, imports: list[ImportRecord]
    ):
        if node.type == "import_declaration":
            module = ""
            for child in node.children:
                if child.type == "scoped_identifier":
                    module = bytes(child.text).decode("utf-8", errors="replace")
            is_wildcard = node.text.find(b".*") != -1
            imports.append(ImportRecord(
                repo_name=repo_name, file_path=file_path,
                module=module,
                line_number=node.start_point[0] + 1,
            ))
        else:
            for child in node.children:
                self._walk_for_imports(child, source, file_path, repo_name, imports)

    def extract_inheritance(
        self, tree: ts.Tree, source: bytes, file_path: str, repo_name: str
    ) -> list[EdgeRelation]:
        """Extract extends/implements edges from Java class declarations."""
        edges: list[EdgeRelation] = []
        self._walk_for_inheritance(tree.root_node, source, file_path, repo_name, edges)
        return edges

    def _walk_for_inheritance(
        self, node: ts.Node, source: bytes, file_path: str,
        repo_name: str, edges: list[EdgeRelation]
    ):
        if node.type == "class_declaration":
            class_name = self._get_name(node, source)
            # Java: extends and implements clauses
            for child in node.children:
                if child.type == "superclass":
                    # extends Bar
                    for sc in child.children:
                        if sc.type in ("identifier", "scoped_identifier", "type_identifier"):
                            base = bytes(sc.text).decode("utf-8", errors="replace")
                            edges.append(EdgeRelation(
                                repo_name=repo_name,
                                source_symbol=class_name,
                                source_file=file_path,
                                target_symbol=base,
                                kind="extends",
                                line_number=node.start_point[0] + 1,
                            ))
                elif child.type == "super_interfaces":
                    # implements Foo, Bar
                    for iface_child in child.children:
                        if iface_child.type in ("identifier", "scoped_identifier", "type_identifier"):
                            iface_name = bytes(iface_child.text).decode("utf-8", errors="replace")
                            edges.append(EdgeRelation(
                                repo_name=repo_name,
                                source_symbol=class_name,
                                source_file=file_path,
                                target_symbol=iface_name,
                                kind="implements",
                                line_number=node.start_point[0] + 1,
                            ))
                        elif iface_child.type == "type_list":
                            for tl_child in iface_child.children:
                                if tl_child.type in ("identifier", "scoped_identifier", "type_identifier"):
                                    iface_name = bytes(tl_child.text).decode("utf-8", errors="replace")
                                    edges.append(EdgeRelation(
                                        repo_name=repo_name,
                                        source_symbol=class_name,
                                        source_file=file_path,
                                        target_symbol=iface_name,
                                        kind="implements",
                                        line_number=node.start_point[0] + 1,
                                    ))
        for child in node.children:
            self._walk_for_inheritance(child, source, file_path, repo_name, edges)

class KotlinStrategy:
    def language(self) -> ts.Language:
        return ts.Language(tskotlin.language())

    def file_extensions(self) -> set[str]:
        return {".kt", ".kts"}

    def extract_symbols(
        self, tree: ts.Tree, source: bytes, file_path: str, repo_name: str, language_name: str
    ) -> list[Symbol]:
        symbols: list[Symbol] = []
        self._walk_for_symbols(tree.root_node, source, file_path, repo_name, language_name, "", symbols)
        return symbols

    def _walk_for_symbols(
        self, node: ts.Node, source: bytes, file_path: str,
        repo_name: str, language_name: str, parent: str, symbols: list[Symbol]
    ):
        if node.type == "class_declaration":
            name = self._get_name(node, source)
            sig = f"class {name}"
            symbols.append(Symbol(
                repo_name=repo_name, file_path=file_path, name=name,
                kind="class", signature=sig,
                start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
                parent=parent, language=language_name,
                source=bytes(node.text).decode("utf-8", errors="replace"),
            ))
            body = self._find_child(node, "class_body")
            if body:
                for child in body.children:
                    self._walk_for_symbols(child, source, file_path, repo_name, language_name, name, symbols)

        elif node.type == "object_declaration":
            name = self._get_name(node, source)
            sig = f"object {name}"
            symbols.append(Symbol(
                repo_name=repo_name, file_path=file_path, name=name,
                kind="object", signature=sig,
                start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
                parent=parent, language=language_name,
                source=bytes(node.text).decode("utf-8", errors="replace"),
            ))
            body = self._find_child(node, "class_body")
            if body:
                for child in body.children:
                    self._walk_for_symbols(child, source, file_path, repo_name, language_name, name, symbols)

        elif node.type == "function_declaration":
            name = self._get_name(node, source)
            params = self._get_child_text(node, "function_value_parameters", source)
            sig = f"fun {name}{params}"
            symbols.append(Symbol(
                repo_name=repo_name, file_path=file_path, name=name,
                kind="method" if parent else "function", signature=sig,
                start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
                parent=parent, language=language_name,
                source=bytes(node.text).decode("utf-8", errors="replace"),
            ))

        elif node.type == "property_declaration":
            var_decl = self._find_child(node, "variable_declaration")
            if var_decl:
                name = self._get_name(var_decl, source)
                keyword = "val" if any(c.type == "val" for c in node.children) else "var"
                sig = f"{keyword} {name}"
                symbols.append(Symbol(
                    repo_name=repo_name, file_path=file_path, name=name,
                    kind="property", signature=sig,
                    start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
                    parent=parent, language=language_name,
                    source=bytes(node.text).decode("utf-8", errors="replace"),
                ))

        else:
            for child in node.children:
                self._walk_for_symbols(child, source, file_path, repo_name, language_name, parent, symbols)

    def _get_name(self, node: ts.Node, source: bytes) -> str:
        for child in node.children:
            if child.type == "identifier":
                return bytes(child.text).decode("utf-8", errors="replace")
            if child.type == "simple_identifier":
                return bytes(child.text).decode("utf-8", errors="replace")
        return ""

    def _get_child_text(self, node: ts.Node, child_type: str, source: bytes) -> str:
        child = self._find_child(node, child_type)
        if child:
            return bytes(child.text).decode("utf-8", errors="replace")
        return ""

    def _find_child(self, node: ts.Node, child_type: str) -> ts.Node | None:
        for child in node.children:
            if child.type == child_type:
                return child
        return None

    def extract_calls(
        self, tree: ts.Tree, source: bytes, file_path: str, repo_name: str
    ) -> list[CallRelation]:
        calls: list[CallRelation] = []
        self._walk_for_calls(tree.root_node, source, file_path, repo_name, "<module>", calls)
        return calls

    def _walk_for_calls(
        self, node: ts.Node, source: bytes, file_path: str,
        repo_name: str, caller_name: str, calls: list[CallRelation]
    ):
        if node.type == "function_declaration":
            name = self._get_name(node, source)
            self._collect_calls_in_node(node, name, file_path, repo_name, calls)
            return
        elif node.type == "call_expression":
            callee = self._get_callee_name(node)
            if callee:
                calls.append(CallRelation(
                    repo_name=repo_name, caller_file=file_path,
                    caller_name=caller_name, callee_name=callee,
                    line_number=node.start_point[0] + 1,
                ))
        for child in node.children:
            self._walk_for_calls(child, source, file_path, repo_name, caller_name, calls)

    def _collect_calls_in_node(
        self, node: ts.Node, caller_name: str, file_path: str,
        repo_name: str, calls: list[CallRelation]
    ):
        if node.type == "call_expression":
            callee = self._get_callee_name(node)
            if callee:
                calls.append(CallRelation(
                    repo_name=repo_name, caller_file=file_path,
                    caller_name=caller_name, callee_name=callee,
                    line_number=node.start_point[0] + 1,
                ))
        for child in node.children:
            self._collect_calls_in_node(child, caller_name, file_path, repo_name, calls)

    def _get_callee_name(self, node: ts.Node) -> str:
        # call_expression has either identifier or navigation_expression as first child
        for child in node.children:
            if child.type == "identifier":
                return bytes(child.text).decode("utf-8", errors="replace")
            elif child.type == "navigation_expression":
                return bytes(child.text).decode("utf-8", errors="replace")
        return ""

    def extract_imports(
        self, tree: ts.Tree, source: bytes, file_path: str, repo_name: str
    ) -> list[ImportRecord]:
        imports: list[ImportRecord] = []
        self._walk_for_imports(tree.root_node, source, file_path, repo_name, imports)
        return imports

    def _walk_for_imports(
        self, node: ts.Node, source: bytes, file_path: str,
        repo_name: str, imports: list[ImportRecord]
    ):
        if node.type == "import":
            module = ""
            for child in node.children:
                if child.type == "qualified_identifier":
                    module = bytes(child.text).decode("utf-8", errors="replace")
            imports.append(ImportRecord(
                repo_name=repo_name, file_path=file_path,
                module=module,
                line_number=node.start_point[0] + 1,
            ))
        else:
            for child in node.children:
                self._walk_for_imports(child, source, file_path, repo_name, imports)

    def extract_inheritance(
        self, tree: ts.Tree, source: bytes, file_path: str, repo_name: str
    ) -> list[EdgeRelation]:
        """Extract extends/implements edges from Kotlin class declarations."""
        edges: list[EdgeRelation] = []
        self._walk_for_inheritance(tree.root_node, source, file_path, repo_name, edges)
        return edges

    def _walk_for_inheritance(
        self, node: ts.Node, source: bytes, file_path: str,
        repo_name: str, edges: list[EdgeRelation]
    ):
        if node.type == "class_declaration":
            class_name = self._get_name(node, source)
            for child in node.children:
                if child.type == "superclass":
                    # : ParentClass
                    for sc in child.children:
                        if sc.type in ("identifier", "simple_identifier", "user_type", "type_identifier"):
                            base = bytes(sc.text).decode("utf-8", errors="replace")
                            edges.append(EdgeRelation(
                                repo_name=repo_name,
                                source_symbol=class_name,
                                source_file=file_path,
                                target_symbol=base,
                                kind="extends",
                                line_number=node.start_point[0] + 1,
                            ))
                elif child.type == "superclass_call":
                    # : ParentClass()
                    base = bytes(child.text).decode("utf-8", errors="replace").split("(")[0]
                    if base:
                        edges.append(EdgeRelation(
                            repo_name=repo_name,
                            source_symbol=class_name,
                            source_file=file_path,
                            target_symbol=base,
                            kind="extends",
                            line_number=node.start_point[0] + 1,
                        ))
                elif child.type == "delegation_callers":
                    # : Interface by delegate
                    for dc in child.children:
                        if dc.type == "delegation_call":
                            iface_name = bytes(dc.text).decode("utf-8", errors="replace").split("by")[0].strip()
                            if iface_name:
                                edges.append(EdgeRelation(
                                    repo_name=repo_name,
                                    source_symbol=class_name,
                                    source_file=file_path,
                                    target_symbol=iface_name,
                                    kind="implements",
                                    line_number=node.start_point[0] + 1,
                                ))
                elif child.type in ("type_constraints",):
                    # Generic constraints - not inheritance
                    pass
            # Also check modifiers for 'data', 'sealed' etc.
            # Look for colon (:) children that indicate supertype specification
            for child in node.children:
                if child.type == ":" :
                    # The next sibling after : should be the supertype
                    pass
        for child in node.children:
            self._walk_for_inheritance(child, source, file_path, repo_name, edges)

class SwiftStrategy:
    def language(self) -> ts.Language:
        return ts.Language(tsswift.language())

    def file_extensions(self) -> set[str]:
        return {".swift"}

    def extract_symbols(
        self, tree: ts.Tree, source: bytes, file_path: str, repo_name: str, language_name: str
    ) -> list[Symbol]:
        symbols: list[Symbol] = []
        self._walk_for_symbols(tree.root_node, source, file_path, repo_name, language_name, "", symbols)
        return symbols

    def _walk_for_symbols(
        self, node: ts.Node, source: bytes, file_path: str,
        repo_name: str, language_name: str, parent: str, symbols: list[Symbol]
    ):
        if node.type == "class_declaration":
            name = self._get_type_name(node, source)
            # Determine if it's actually a struct/enum/extension based on keyword
            first_keyword = self._get_first_keyword(node)
            kind_map = {"class": "class", "struct": "struct", "enum": "enum", "extension": "extension"}
            kind = kind_map.get(first_keyword, "class")
            sig = f"{first_keyword} {name}" if name else first_keyword
            symbols.append(Symbol(
                repo_name=repo_name, file_path=file_path, name=name,
                kind=kind, signature=sig,
                start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
                parent=parent, language=language_name,
                source=bytes(node.text).decode("utf-8", errors="replace"),
            ))
            body = self._find_child(node, "class_body")
            if body:
                for child in body.children:
                    self._walk_for_symbols(child, source, file_path, repo_name, language_name, name, symbols)

        elif node.type == "protocol_declaration":
            name = self._get_type_name(node, source)
            sig = f"protocol {name}"
            symbols.append(Symbol(
                repo_name=repo_name, file_path=file_path, name=name,
                kind="protocol", signature=sig,
                start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
                parent=parent, language=language_name,
                source=bytes(node.text).decode("utf-8", errors="replace"),
            ))

        elif node.type == "function_declaration" or node.type == "protocol_function_declaration":
            name = self._get_function_name(node, source)
            sig = f"func {name}()"
            symbols.append(Symbol(
                repo_name=repo_name, file_path=file_path, name=name,
                kind="method" if parent else "function", signature=sig,
                start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
                parent=parent, language=language_name,
                source=bytes(node.text).decode("utf-8", errors="replace"),
            ))

        elif node.type == "init_declaration":
            sig = "init()"
            symbols.append(Symbol(
                repo_name=repo_name, file_path=file_path, name="init",
                kind="constructor", signature=sig,
                start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
                parent=parent, language=language_name,
                source=bytes(node.text).decode("utf-8", errors="replace"),
            ))

        elif node.type == "property_declaration":
            pattern = self._find_child(node, "pattern")
            if pattern:
                name = self._get_simple_identifier(pattern)
                if name:
                    binding = self._find_child(node, "value_binding_pattern")
                    keyword = "var"
                    if binding:
                        for c in binding.children:
                            if c.type == "let":
                                keyword = "let"
                            elif c.type == "var":
                                keyword = "var"
                    sig = f"{keyword} {name}"
                    symbols.append(Symbol(
                        repo_name=repo_name, file_path=file_path, name=name,
                        kind="property", signature=sig,
                        start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
                        parent=parent, language=language_name,
                        source=bytes(node.text).decode("utf-8", errors="replace"),
                    ))

        else:
            for child in node.children:
                self._walk_for_symbols(child, source, file_path, repo_name, language_name, parent, symbols)

    def _get_type_name(self, node: ts.Node, source: bytes) -> str:
        for child in node.children:
            if child.type == "type_identifier":
                return bytes(child.text).decode("utf-8", errors="replace")
            elif child.type == "user_type":
                # user_type contains type_identifier
                return bytes(child.text).decode("utf-8", errors="replace")
        return ""

    def _get_function_name(self, node: ts.Node, source: bytes) -> str:
        for child in node.children:
            if child.type == "simple_identifier":
                return bytes(child.text).decode("utf-8", errors="replace")
        return ""

    def _get_simple_identifier(self, node: ts.Node) -> str:
        for child in node.children:
            if child.type == "simple_identifier":
                return bytes(child.text).decode("utf-8", errors="replace")
        return ""

    def _get_first_keyword(self, node: ts.Node) -> str:
        for child in node.children:
            if child.type in ("class", "struct", "enum", "extension"):
                return child.type
        return "class"

    def _find_child(self, node: ts.Node, child_type: str) -> ts.Node | None:
        for child in node.children:
            if child.type == child_type:
                return child
        return None

    def extract_calls(
        self, tree: ts.Tree, source: bytes, file_path: str, repo_name: str
    ) -> list[CallRelation]:
        calls: list[CallRelation] = []
        self._walk_for_calls(tree.root_node, source, file_path, repo_name, "<module>", calls)
        return calls

    def _walk_for_calls(
        self, node: ts.Node, source: bytes, file_path: str,
        repo_name: str, caller_name: str, calls: list[CallRelation]
    ):
        if node.type == "function_declaration":
            name = self._get_function_name(node, source)
            self._collect_calls_in_node(node, name, file_path, repo_name, calls)
            return
        elif node.type == "init_declaration":
            self._collect_calls_in_node(node, "init", file_path, repo_name, calls)
            return
        elif node.type == "call_expression":
            callee = self._get_callee_name(node)
            if callee:
                calls.append(CallRelation(
                    repo_name=repo_name, caller_file=file_path,
                    caller_name=caller_name, callee_name=callee,
                    line_number=node.start_point[0] + 1,
                ))
        for child in node.children:
            self._walk_for_calls(child, source, file_path, repo_name, caller_name, calls)

    def _collect_calls_in_node(
        self, node: ts.Node, caller_name: str, file_path: str,
        repo_name: str, calls: list[CallRelation]
    ):
        if node.type == "call_expression":
            callee = self._get_callee_name(node)
            if callee:
                calls.append(CallRelation(
                    repo_name=repo_name, caller_file=file_path,
                    caller_name=caller_name, callee_name=callee,
                    line_number=node.start_point[0] + 1,
                ))
        for child in node.children:
            self._collect_calls_in_node(child, caller_name, file_path, repo_name, calls)

    def _get_callee_name(self, node: ts.Node) -> str:
        for child in node.children:
            if child.type == "simple_identifier":
                return bytes(child.text).decode("utf-8", errors="replace")
            elif child.type == "navigation_expression":
                return bytes(child.text).decode("utf-8", errors="replace")
        return ""

    def extract_imports(
        self, tree: ts.Tree, source: bytes, file_path: str, repo_name: str
    ) -> list[ImportRecord]:
        imports: list[ImportRecord] = []
        self._walk_for_imports(tree.root_node, source, file_path, repo_name, imports)
        return imports

    def _walk_for_imports(
        self, node: ts.Node, source: bytes, file_path: str,
        repo_name: str, imports: list[ImportRecord]
    ):
        if node.type == "import_declaration":
            module = ""
            ident = self._find_child(node, "identifier")
            if ident:
                for child in ident.children:
                    if child.type == "simple_identifier":
                        module = bytes(child.text).decode("utf-8", errors="replace")
            imports.append(ImportRecord(
                repo_name=repo_name, file_path=file_path,
                module=module,
                line_number=node.start_point[0] + 1,
            ))
        else:
            for child in node.children:
                self._walk_for_imports(child, source, file_path, repo_name, imports)

    def extract_inheritance(
        self, tree: ts.Tree, source: bytes, file_path: str, repo_name: str
    ) -> list[EdgeRelation]:
        """Extract extends/implements edges from Swift class/struct declarations."""
        edges: list[EdgeRelation] = []
        self._walk_for_inheritance(tree.root_node, source, file_path, repo_name, edges)
        return edges

    def _walk_for_inheritance(
        self, node: ts.Node, source: bytes, file_path: str,
        repo_name: str, edges: list[EdgeRelation]
    ):
        if node.type == "class_declaration":
            class_name = self._get_type_name(node, source)
            # Swift: class Foo: Bar, Protocol1, Protocol2
            # The inheritance clause comes after the type identifier
            found_name = False
            for child in node.children:
                if child.type == "type_identifier":
                    found_name = True
                    continue
                if found_name and child.type == "type_constraints":
                    break
                if found_name and child.type == "class_body":
                    break
                if found_name and child.type == "inheritance_clause":
                    for ic in child.children:
                        if ic.type == "class_body":
                            break
                        if ic.type in (",", "inheritance_clause"):
                            continue
                        base = bytes(ic.text).decode("utf-8", errors="replace").strip()
                        if base and base != ":":
                            # In Swift, first item after : is superclass, rest are protocols
                            edges.append(EdgeRelation(
                                repo_name=repo_name,
                                source_symbol=class_name,
                                source_file=file_path,
                                target_symbol=base,
                                kind="extends",  # simplified: treat all as extends
                                line_number=node.start_point[0] + 1,
                            ))
                    break
        for child in node.children:
            self._walk_for_inheritance(child, source, file_path, repo_name, edges)

class TypeScriptStrategy:
    """Strategy for both TypeScript (.ts) and TSX (.tsx) files."""

    def __init__(self, is_tsx: bool = False):
        self._is_tsx = is_tsx

    def language(self) -> ts.Language:
        if self._is_tsx:
            return ts.Language(tstypescript.language_tsx())
        return ts.Language(tstypescript.language_typescript())

    def file_extensions(self) -> set[str]:
        return {".tsx"} if self._is_tsx else {".ts"}

    def extract_symbols(
        self, tree: ts.Tree, source: bytes, file_path: str, repo_name: str, language_name: str
    ) -> list[Symbol]:
        symbols: list[Symbol] = []
        self._walk_for_symbols(tree.root_node, source, file_path, repo_name, language_name, "", symbols)
        return symbols

    def _walk_for_symbols(
        self, node: ts.Node, source: bytes, file_path: str,
        repo_name: str, language_name: str, parent: str, symbols: list[Symbol]
    ):
        if node.type == "function_declaration":
            name = self._get_name(node, source)
            params = self._get_child_text(node, "formal_parameters", source)
            sig = f"function {name}{params}"
            symbols.append(Symbol(
                repo_name=repo_name, file_path=file_path, name=name,
                kind="method" if parent else "function", signature=sig,
                start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
                parent=parent, language=language_name,
                source=bytes(node.text).decode("utf-8", errors="replace"),
            ))

        elif node.type == "class_declaration":
            name = self._get_name(node, source)
            sig = f"class {name}"
            symbols.append(Symbol(
                repo_name=repo_name, file_path=file_path, name=name,
                kind="class", signature=sig,
                start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
                parent=parent, language=language_name,
                source=bytes(node.text).decode("utf-8", errors="replace"),
            ))
            body = node.child_by_field_name("body")
            if body:
                for child in body.children:
                    if child.type == "method_definition":
                        method_name = self._get_method_name(child, source)
                        method_params = self._get_child_text(child, "formal_parameters", source)
                        symbols.append(Symbol(
                            repo_name=repo_name, file_path=file_path, name=method_name,
                            kind="method", signature=f"{method_name}{method_params}",
                            start_line=child.start_point[0] + 1, end_line=child.end_point[0] + 1,
                            parent=name, language=language_name,
                            source=bytes(child.text).decode("utf-8", errors="replace"),
                        ))
                    elif child.type == "public_field_definition":
                        prop_name = self._get_property_name(child, source)
                        if prop_name:
                            symbols.append(Symbol(
                                repo_name=repo_name, file_path=file_path, name=prop_name,
                                kind="property", signature=prop_name,
                                start_line=child.start_point[0] + 1, end_line=child.end_point[0] + 1,
                                parent=name, language=language_name,
                                source=bytes(child.text).decode("utf-8", errors="replace"),
                            ))

        elif node.type == "interface_declaration":
            name = self._get_name(node, source)
            sig = f"interface {name}"
            symbols.append(Symbol(
                repo_name=repo_name, file_path=file_path, name=name,
                kind="interface", signature=sig,
                start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
                parent=parent, language=language_name,
                source=bytes(node.text).decode("utf-8", errors="replace"),
            ))

        elif node.type == "type_alias_declaration":
            name = self._get_name(node, source)
            sig = f"type {name}"
            symbols.append(Symbol(
                repo_name=repo_name, file_path=file_path, name=name,
                kind="type", signature=sig,
                start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
                parent=parent, language=language_name,
                source=bytes(node.text).decode("utf-8", errors="replace"),
            ))

        elif node.type == "enum_declaration":
            name = self._get_name(node, source)
            sig = f"enum {name}"
            symbols.append(Symbol(
                repo_name=repo_name, file_path=file_path, name=name,
                kind="enum", signature=sig,
                start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
                parent=parent, language=language_name,
                source=bytes(node.text).decode("utf-8", errors="replace"),
            ))

        elif node.type in ("lexical_declaration", "variable_declaration"):
            is_export = self._is_exported(node)
            for child in node.children:
                if child.type == "variable_declarator":
                    name_node = child.child_by_field_name("name")
                    value_node = child.child_by_field_name("value")
                    if name_node:
                        name = bytes(name_node.text).decode("utf-8", errors="replace")
                        if is_export or (value_node and value_node.type == "arrow_function"):
                            keyword = "const" if node.type == "lexical_declaration" else "var"
                            sig = f"{keyword} {name}"
                            symbols.append(Symbol(
                                repo_name=repo_name, file_path=file_path, name=name,
                                kind="function" if value_node and value_node.type == "arrow_function" else "variable",
                                signature=sig,
                                start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
                                parent=parent, language=language_name,
                                source=bytes(node.text).decode("utf-8", errors="replace"),
                            ))

        else:
            for child in node.children:
                self._walk_for_symbols(child, source, file_path, repo_name, language_name, parent, symbols)

    def _get_name(self, node: ts.Node, source: bytes) -> str:
        for child in node.children:
            if child.type in ("identifier", "type_identifier"):
                return bytes(child.text).decode("utf-8", errors="replace")
        return ""

    def _get_method_name(self, node: ts.Node, source: bytes) -> str:
        for child in node.children:
            if child.type == "property_identifier":
                return bytes(child.text).decode("utf-8", errors="replace")
        return ""

    def _get_property_name(self, node: ts.Node, source: bytes) -> str:
        for child in node.children:
            if child.type == "property_identifier":
                return bytes(child.text).decode("utf-8", errors="replace")
        return ""

    def _get_child_text(self, node: ts.Node, child_type: str, source: bytes) -> str:
        for child in node.children:
            if child.type == child_type:
                return bytes(child.text).decode("utf-8", errors="replace")
        return ""

    def _is_exported(self, node: ts.Node) -> bool:
        parent = node.parent
        return parent is not None and parent.type == "export_statement"

    def extract_calls(
        self, tree: ts.Tree, source: bytes, file_path: str, repo_name: str
    ) -> list[CallRelation]:
        calls: list[CallRelation] = []
        self._walk_for_calls(tree.root_node, source, file_path, repo_name, "<module>", calls)
        return calls

    def _walk_for_calls(
        self, node: ts.Node, source: bytes, file_path: str,
        repo_name: str, caller_name: str, calls: list[CallRelation]
    ):
        if node.type == "function_declaration":
            name = self._get_name(node, source)
            self._collect_calls_in_node(node, name, file_path, repo_name, calls)
            return
        elif node.type == "method_definition":
            name = self._get_method_name(node, source)
            self._collect_calls_in_node(node, name, file_path, repo_name, calls)
            return
        elif node.type == "call_expression":
            func = node.child_by_field_name("function")
            if func:
                callee = bytes(func.text).decode("utf-8", errors="replace")
                calls.append(CallRelation(
                    repo_name=repo_name, caller_file=file_path,
                    caller_name=caller_name, callee_name=callee,
                    line_number=node.start_point[0] + 1,
                ))
        for child in node.children:
            self._walk_for_calls(child, source, file_path, repo_name, caller_name, calls)

    def _collect_calls_in_node(
        self, node: ts.Node, caller_name: str, file_path: str,
        repo_name: str, calls: list[CallRelation]
    ):
        if node.type == "call_expression":
            func = node.child_by_field_name("function")
            if func:
                callee = bytes(func.text).decode("utf-8", errors="replace")
                calls.append(CallRelation(
                    repo_name=repo_name, caller_file=file_path,
                    caller_name=caller_name, callee_name=callee,
                    line_number=node.start_point[0] + 1,
                ))
        for child in node.children:
            self._collect_calls_in_node(child, caller_name, file_path, repo_name, calls)

    def extract_imports(
        self, tree: ts.Tree, source: bytes, file_path: str, repo_name: str
    ) -> list[ImportRecord]:
        imports: list[ImportRecord] = []
        self._walk_for_imports(tree.root_node, source, file_path, repo_name, imports)
        return imports

    def _walk_for_imports(
        self, node: ts.Node, source: bytes, file_path: str,
        repo_name: str, imports: list[ImportRecord]
    ):
        if node.type == "import_statement":
            # Find the source string
            module = ""
            imported_names = ""
            for child in node.children:
                if child.type == "string":
                    module = bytes(child.text).decode("utf-8", errors="replace").strip("'\"")
                elif child.type == "import_clause":
                    imported_names = bytes(child.text).decode("utf-8", errors="replace")
            imports.append(ImportRecord(
                repo_name=repo_name, file_path=file_path,
                module=module, imported_names=imported_names,
                line_number=node.start_point[0] + 1,
            ))
        elif node.type == "export_statement":
            for child in node.children:
                if child.type == "export_clause":
                    # export { a, b }
                    pass
        else:
            for child in node.children:
                self._walk_for_imports(child, source, file_path, repo_name, imports)

    def extract_inheritance(
        self, tree: ts.Tree, source: bytes, file_path: str, repo_name: str
    ) -> list[EdgeRelation]:
        """Extract extends/implements edges from TypeScript class declarations."""
        edges: list[EdgeRelation] = []
        self._walk_for_inheritance(tree.root_node, source, file_path, repo_name, edges)
        return edges

    def _walk_for_inheritance(
        self, node: ts.Node, source: bytes, file_path: str,
        repo_name: str, edges: list[EdgeRelation]
    ):
        if node.type == "class_declaration":
            class_name = self._get_name(node, source)
            for child in node.children:
                if child.type == "class_heritage":
                    for heritage_child in child.children:
                        if heritage_child.type == "extends_clause":
                            for ext_child in heritage_child.children:
                                if ext_child.type in ("identifier", "type_identifier"):
                                    base = bytes(ext_child.text).decode("utf-8", errors="replace")
                                    edges.append(EdgeRelation(
                                        repo_name=repo_name,
                                        source_symbol=class_name,
                                        source_file=file_path,
                                        target_symbol=base,
                                        kind="extends",
                                        line_number=node.start_point[0] + 1,
                                    ))
                        elif heritage_child.type == "implements_clause":
                            for impl_child in heritage_child.children:
                                if impl_child.type in ("identifier", "type_identifier"):
                                    iface = bytes(impl_child.text).decode("utf-8", errors="replace")
                                    edges.append(EdgeRelation(
                                        repo_name=repo_name,
                                        source_symbol=class_name,
                                        source_file=file_path,
                                        target_symbol=iface,
                                        kind="implements",
                                        line_number=node.start_point[0] + 1,
                                    ))
        for child in node.children:
            self._walk_for_inheritance(child, source, file_path, repo_name, edges)

class ArkTSStrategy:
    """Strategy for ArkTS (.ets) files using tree-sitter-arkts grammar."""

    def language(self) -> ts.Language:
        return ts.Language(tsarkts.language())

    def file_extensions(self) -> set[str]:
        return {".ets"}

    # -- helpers --

    @staticmethod
    def _node_name(node: ts.Node, source: bytes) -> str:
        for child in node.children:
            if child.type in ("identifier", "type_identifier"):
                return bytes(child.text).decode("utf-8", errors="replace")
        return ""

    @staticmethod
    def _child_text(node: ts.Node, child_type: str, source: bytes) -> str:
        for child in node.children:
            if child.type == child_type:
                return bytes(child.text).decode("utf-8", errors="replace")
        return ""

    @staticmethod
    def _has_decorator(node: ts.Node, name: str) -> bool:
        """Check if node has a decorator with the given name (e.g. 'Component', 'Builder')."""
        for child in node.children:
            if child.type == "decorator":
                # decorator -> @ + identifier  OR  @ + call_expression
                for dc in child.children:
                    if dc.type == "identifier" and bytes(dc.text).decode() == name:
                        return True
                    if dc.type == "call_expression":
                        for ce in dc.children:
                            if ce.type == "identifier" and bytes(ce.text).decode() == name:
                                return True
        return False

    # -- symbol extraction --

    def extract_symbols(
        self, tree: ts.Tree, source: bytes, file_path: str, repo_name: str, language_name: str
    ) -> list[Symbol]:
        symbols: list[Symbol] = []
        self._walk_for_symbols(tree.root_node, source, file_path, repo_name, language_name, "", symbols)
        return symbols

    def _walk_for_symbols(
        self, node: ts.Node, source: bytes, file_path: str,
        repo_name: str, language_name: str, parent: str, symbols: list[Symbol]
    ):
        if node.type == "struct_declaration":
            name = self._node_name(node, source)
            kind = "component" if self._has_decorator(node, "Component") else "struct"
            sig = f"@Component struct {name}" if kind == "component" else f"struct {name}"
            symbols.append(Symbol(
                repo_name=repo_name, file_path=file_path, name=name,
                kind=kind, signature=sig,
                start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
                parent=parent, language=language_name,
                source=bytes(node.text).decode("utf-8", errors="replace"),
            ))
            # Walk struct body for members
            body = None
            for child in node.children:
                if child.type == "struct_body":
                    body = child
                    break
            if body:
                for child in body.children:
                    if child.type == "method_definition":
                        mname = self._child_text(child, "property_identifier", source)
                        mparams = self._child_text(child, "formal_parameters", source)
                        symbols.append(Symbol(
                            repo_name=repo_name, file_path=file_path, name=mname,
                            kind="method", signature=f"{mname}{mparams}",
                            start_line=child.start_point[0] + 1, end_line=child.end_point[0] + 1,
                            parent=name, language=language_name,
                            source=bytes(child.text).decode("utf-8", errors="replace"),
                        ))
                    elif child.type == "public_field_definition":
                        pname = self._child_text(child, "property_identifier", source)
                        if pname:
                            symbols.append(Symbol(
                                repo_name=repo_name, file_path=file_path, name=pname,
                                kind="property", signature=pname,
                                start_line=child.start_point[0] + 1, end_line=child.end_point[0] + 1,
                                parent=name, language=language_name,
                                source=bytes(child.text).decode("utf-8", errors="replace"),
                            ))

        elif node.type == "class_declaration":
            name = self._node_name(node, source)
            sig = f"class {name}"
            symbols.append(Symbol(
                repo_name=repo_name, file_path=file_path, name=name,
                kind="class", signature=sig,
                start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
                parent=parent, language=language_name,
                source=bytes(node.text).decode("utf-8", errors="replace"),
            ))
            body = node.child_by_field_name("body")
            if body:
                for child in body.children:
                    if child.type == "method_definition":
                        mname = self._child_text(child, "property_identifier", source)
                        mparams = self._child_text(child, "formal_parameters", source)
                        symbols.append(Symbol(
                            repo_name=repo_name, file_path=file_path, name=mname,
                            kind="method", signature=f"{mname}{mparams}",
                            start_line=child.start_point[0] + 1, end_line=child.end_point[0] + 1,
                            parent=name, language=language_name,
                            source=bytes(child.text).decode("utf-8", errors="replace"),
                        ))
                    elif child.type == "public_field_definition":
                        pname = self._child_text(child, "property_identifier", source)
                        if pname:
                            symbols.append(Symbol(
                                repo_name=repo_name, file_path=file_path, name=pname,
                                kind="property", signature=pname,
                                start_line=child.start_point[0] + 1, end_line=child.end_point[0] + 1,
                                parent=name, language=language_name,
                                source=bytes(child.text).decode("utf-8", errors="replace"),
                            ))

        elif node.type == "function_declaration":
            name = self._node_name(node, source)
            params = self._child_text(node, "formal_parameters", source)
            is_builder = self._has_decorator(node, "Builder")
            is_extend = self._has_decorator(node, "Extend")
            if is_builder:
                kind = "builder"
                sig = f"@Builder function {name}{params}"
            elif is_extend:
                kind = "extend"
                sig = f"@Extend function {name}{params}"
            else:
                kind = "function"
                sig = f"function {name}{params}"
            symbols.append(Symbol(
                repo_name=repo_name, file_path=file_path, name=name,
                kind=kind, signature=sig,
                start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
                parent=parent, language=language_name,
                source=bytes(node.text).decode("utf-8", errors="replace"),
            ))

        elif node.type == "interface_declaration":
            name = self._node_name(node, source)
            sig = f"interface {name}"
            symbols.append(Symbol(
                repo_name=repo_name, file_path=file_path, name=name,
                kind="interface", signature=sig,
                start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
                parent=parent, language=language_name,
                source=bytes(node.text).decode("utf-8", errors="replace"),
            ))

        elif node.type == "enum_declaration":
            name = self._node_name(node, source)
            sig = f"enum {name}"
            symbols.append(Symbol(
                repo_name=repo_name, file_path=file_path, name=name,
                kind="enum", signature=sig,
                start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
                parent=parent, language=language_name,
                source=bytes(node.text).decode("utf-8", errors="replace"),
            ))

        elif node.type == "type_alias_declaration":
            name = self._node_name(node, source)
            sig = f"type {name}"
            symbols.append(Symbol(
                repo_name=repo_name, file_path=file_path, name=name,
                kind="type", signature=sig,
                start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
                parent=parent, language=language_name,
                source=bytes(node.text).decode("utf-8", errors="replace"),
            ))

        else:
            for child in node.children:
                self._walk_for_symbols(child, source, file_path, repo_name, language_name, parent, symbols)

    # -- call extraction (reuse TS pattern) --

    def extract_calls(
        self, tree: ts.Tree, source: bytes, file_path: str, repo_name: str
    ) -> list[CallRelation]:
        calls: list[CallRelation] = []
        self._walk_for_calls(tree.root_node, source, file_path, repo_name, "<module>", calls)
        return calls

    def _walk_for_calls(
        self, node: ts.Node, source: bytes, file_path: str,
        repo_name: str, caller_name: str, calls: list[CallRelation]
    ):
        if node.type == "struct_declaration":
            name = self._node_name(node, source)
            self._collect_calls_in_node(node, name, file_path, repo_name, calls)
            return
        elif node.type == "function_declaration":
            name = self._node_name(node, source)
            self._collect_calls_in_node(node, name, file_path, repo_name, calls)
            return
        elif node.type == "method_definition":
            name = self._child_text(node, "property_identifier", source)
            self._collect_calls_in_node(node, name, file_path, repo_name, calls)
            return
        elif node.type == "class_declaration":
            name = self._node_name(node, source)
            self._collect_calls_in_node(node, name, file_path, repo_name, calls)
            return
        for child in node.children:
            self._walk_for_calls(child, source, file_path, repo_name, caller_name, calls)

    def _collect_calls_in_node(
        self, node: ts.Node, caller_name: str, file_path: str,
        repo_name: str, calls: list[CallRelation]
    ):
        if node.type == "call_expression":
            func = node.child_by_field_name("function")
            if func:
                callee = bytes(func.text).decode("utf-8", errors="replace")
                calls.append(CallRelation(
                    repo_name=repo_name, caller_file=file_path,
                    caller_name=caller_name, callee_name=callee,
                    line_number=node.start_point[0] + 1,
                ))
        for child in node.children:
            self._collect_calls_in_node(child, caller_name, file_path, repo_name, calls)

    # -- import extraction (reuse TS pattern) --

    def extract_imports(
        self, tree: ts.Tree, source: bytes, file_path: str, repo_name: str
    ) -> list[ImportRecord]:
        imports: list[ImportRecord] = []
        self._walk_for_imports(tree.root_node, source, file_path, repo_name, imports)
        return imports

    def _walk_for_imports(
        self, node: ts.Node, source: bytes, file_path: str,
        repo_name: str, imports: list[ImportRecord]
    ):
        if node.type == "import_statement":
            module = ""
            imported_names = ""
            for child in node.children:
                if child.type == "string":
                    module = bytes(child.text).decode("utf-8", errors="replace").strip("'\"")
                elif child.type == "import_clause":
                    imported_names = bytes(child.text).decode("utf-8", errors="replace")
            imports.append(ImportRecord(
                repo_name=repo_name, file_path=file_path,
                module=module, imported_names=imported_names,
                line_number=node.start_point[0] + 1,
            ))
        else:
            for child in node.children:
                self._walk_for_imports(child, source, file_path, repo_name, imports)

    def extract_inheritance(
        self, tree: ts.Tree, source: bytes, file_path: str, repo_name: str
    ) -> list[EdgeRelation]:
        """Extract extends/implements edges from ArkTS class declarations."""
        edges: list[EdgeRelation] = []
        self._walk_for_inheritance(tree.root_node, source, file_path, repo_name, edges)
        return edges

    def _walk_for_inheritance(
        self, node: ts.Node, source: bytes, file_path: str,
        repo_name: str, edges: list[EdgeRelation]
    ):
        if node.type == "class_declaration":
            class_name = self._node_name(node, source)
            for child in node.children:
                if child.type == "class_heritage":
                    for heritage_child in child.children:
                        if heritage_child.type == "extends_clause":
                            for ext_child in heritage_child.children:
                                if ext_child.type in ("identifier", "type_identifier"):
                                    base = bytes(ext_child.text).decode("utf-8", errors="replace")
                                    edges.append(EdgeRelation(
                                        repo_name=repo_name,
                                        source_symbol=class_name,
                                        source_file=file_path,
                                        target_symbol=base,
                                        kind="extends",
                                        line_number=node.start_point[0] + 1,
                                    ))
                        elif heritage_child.type == "implements_clause":
                            for impl_child in heritage_child.children:
                                if impl_child.type in ("identifier", "type_identifier"):
                                    iface = bytes(impl_child.text).decode("utf-8", errors="replace")
                                    edges.append(EdgeRelation(
                                        repo_name=repo_name,
                                        source_symbol=class_name,
                                        source_file=file_path,
                                        target_symbol=iface,
                                        kind="implements",
                                        line_number=node.start_point[0] + 1,
                                    ))
        for child in node.children:
            self._walk_for_inheritance(child, source, file_path, repo_name, edges)

EXTENSION_TO_LANGUAGE: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".swift": "swift",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".ets": "arkts",
}

ENTRY_POINT_NAMES = {"main", "app", "index", "__main__", "server", "wsgi"}


class TreeSitterIndexer:
    """Indexes source code using tree-sitter to extract structure."""

    def __init__(self, store: SqliteStore):
        self.store = store
        self._strategies: dict[str, LanguageStrategy] = {
            "python": PythonStrategy(),
            "javascript": JavaScriptStrategy(),
            "java": JavaStrategy(),
            "kotlin": KotlinStrategy(),
            "swift": SwiftStrategy(),
            "typescript": TypeScriptStrategy(),
            "tsx": TypeScriptStrategy(is_tsx=True),
        }
        if tsarkts is not None:
            self._strategies["arkts"] = ArkTSStrategy()
        self._parsers: dict[str, ts.Parser] = {}
        for lang_name, strategy in self._strategies.items():
            parser = ts.Parser(strategy.language())
            self._parsers[lang_name] = parser

    def _get_strategy(self, ext: str) -> Optional[tuple[str, LanguageStrategy]]:
        lang = EXTENSION_TO_LANGUAGE.get(ext)
        if lang and lang in self._strategies:
            return lang, self._strategies[lang]
        return None

    def _should_skip(self, path: str, exclude_patterns: list[str] | None = None) -> bool:
        """Check if a file should be skipped based on exclude patterns."""
        parts = Path(path).parts
        skip_dirs = {".git", "node_modules", "__pycache__", "dist", "build", "vendor", "target"}
        if skip_dirs.intersection(parts):
            return True
        if exclude_patterns:
            for pattern in exclude_patterns:
                if pattern.startswith("*.") and path.endswith(pattern[1:]):
                    return True
                if pattern.endswith("/**") and pattern[:-3] in path:
                    return True
        return False

    def index_repo(
        self,
        repo_name: str,
        repo_path: Path,
        exclude_patterns: list[str] | None = None,
        modules: list[ModuleInfo] | None = None,
    ) -> dict:
        """Full index of a repository.

        Returns stats about the indexing.
        """
        stats = {"files_indexed": 0, "symbols_found": 0, "calls_found": 0, "imports_found": 0}

        # Clear existing structure data and guide cache
        self.store.clear_repo_structure(repo_name)
        self.store.clear_guide_cache(repo_name)

        all_symbols: list[Symbol] = []
        all_calls: list[CallRelation] = []
        all_imports: list[ImportRecord] = []
        all_edges: list[EdgeRelation] = []

        for root, dirs, files in os.walk(repo_path):
            # Skip non-source dirs
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in (
                "node_modules", "__pycache__", "dist", "build", "vendor", "target",
            )]

            for filename in files:
                filepath = Path(root) / filename
                rel_path = str(filepath.relative_to(repo_path))
                ext = filepath.suffix.lower()

                if self._should_skip(rel_path, exclude_patterns):
                    continue

                result = self._get_strategy(ext)
                if result is None:
                    continue

                lang_name, strategy = result

                # Determine module for this file
                mod_name = ""
                if modules:
                    mod_name = file_to_module(rel_path, modules)

                file_stats = self.index_file(
                    repo_name=repo_name,
                    file_path=filepath,
                    rel_path=rel_path,
                    strategy=strategy,
                    lang_name=lang_name,
                )

                # Set repo_module on all extracted entities
                for sym in file_stats["symbols"]:
                    sym.repo_module = mod_name
                for call in file_stats["calls"]:
                    call.repo_module = mod_name
                for imp in file_stats["imports"]:
                    imp.repo_module = mod_name
                for edge in file_stats.get("edges", []):
                    edge.repo_module = mod_name

                all_symbols.extend(file_stats["symbols"])
                all_calls.extend(file_stats["calls"])
                all_imports.extend(file_stats["imports"])
                all_edges.extend(file_stats.get("edges", []))

                # Record file entry
                is_entry = filepath.stem.lower() in ENTRY_POINT_NAMES
                self.store.upsert_file_entry(FileEntry(
                    repo_name=repo_name,
                    path=rel_path,
                    language=lang_name,
                    is_entry_point=is_entry,
                    symbol_count=len(file_stats["symbols"]),
                    repo_module=mod_name,
                ))

                stats["files_indexed"] += 1
                stats["symbols_found"] += len(file_stats["symbols"])
                stats["calls_found"] += len(file_stats["calls"])
                stats["imports_found"] += len(file_stats["imports"])

        # Batch insert
        self.store.insert_symbols(all_symbols)
        self.store.insert_calls(all_calls)
        self.store.insert_imports(all_imports)
        self.store.insert_edges(all_edges)

        # Resolve module exports to mark public API symbols
        self._resolve_exports(repo_name, repo_path, modules)

        return stats

    def index_file(
        self,
        repo_name: str,
        file_path: Path,
        rel_path: str,
        strategy: LanguageStrategy | None = None,
        lang_name: str | None = None,
    ) -> dict:
        """Index a single file. Returns extracted data (not yet inserted)."""
        if strategy is None or lang_name is None:
            ext = file_path.suffix.lower()
            result = self._get_strategy(ext)
            if result is None:
                return {"symbols": [], "calls": [], "imports": []}
            lang_name, strategy = result

        try:
            source = file_path.read_bytes()
        except (OSError, UnicodeDecodeError):
            return {"symbols": [], "calls": [], "imports": []}

        parser = self._parsers[lang_name]
        tree = parser.parse(source)

        symbols = strategy.extract_symbols(tree, source, rel_path, repo_name, lang_name)
        calls = strategy.extract_calls(tree, source, rel_path, repo_name)
        imports = strategy.extract_imports(tree, source, rel_path, repo_name)
        edges = strategy.extract_inheritance(tree, source, rel_path, repo_name)

        return {"symbols": symbols, "calls": calls, "imports": imports, "edges": edges}

    def reindex_file(
        self,
        repo_name: str,
        file_path: Path,
        rel_path: str,
        repo_module: str = "",
    ):
        """Re-index a single changed file (delete old data first)."""
        ext = file_path.suffix.lower()
        result = self._get_strategy(ext)
        if result is None:
            return

        lang_name, strategy = result

        # Delete old data for this file
        self.store.delete_symbols_for_file(repo_name, rel_path)
        self.store.delete_calls_for_file(repo_name, rel_path)
        self.store.delete_imports_for_file(repo_name, rel_path)
        self.store.delete_edges_for_file(repo_name, rel_path)

        # Re-parse
        file_data = self.index_file(repo_name, file_path, rel_path, strategy, lang_name)

        # Set repo_module on all extracted entities
        for sym in file_data["symbols"]:
            sym.repo_module = repo_module
        for call in file_data["calls"]:
            call.repo_module = repo_module
        for imp in file_data["imports"]:
            imp.repo_module = repo_module
        for edge in file_data.get("edges", []):
            edge.repo_module = repo_module

        # Insert new data
        self.store.insert_symbols(file_data["symbols"])
        self.store.insert_calls(file_data["calls"])
        self.store.insert_imports(file_data["imports"])
        self.store.insert_edges(file_data.get("edges", []))

        # Update file entry
        self.store.upsert_file_entry(FileEntry(
            repo_name=repo_name,
            path=rel_path,
            language=lang_name,
            is_entry_point=file_path.stem.lower() in ENTRY_POINT_NAMES,
            symbol_count=len(file_data["symbols"]),
            repo_module=repo_module,
        ))

    # --- Export resolution (public API marking) ---

    _ENTRY_FILE_CANDIDATES = ["Index.ets", "index.ets", "Index.ts", "index.ts", "index.js"]

    def _resolve_exports(
        self,
        repo_name: str,
        repo_path: Path,
        modules: list[ModuleInfo] | None,
    ):
        """Top-level entry: resolve exports for each module (or root)."""
        if modules:
            for mod in modules:
                module_dir = repo_path / mod.path if mod.path else repo_path
                self._resolve_exports_for_module(
                    repo_name, repo_path, mod.name, module_dir,
                )
        else:
            # Single-module repo: check root for entry file
            self._resolve_exports_for_module(repo_name, repo_path, "", repo_path)

    def _resolve_exports_for_module(
        self,
        repo_name: str,
        repo_path: Path,
        repo_module: str,
        module_dir: Path,
    ):
        """Find entry file, parse exports, mark exported symbols."""
        entry = self._find_entry_file(module_dir)
        if entry is None:
            return

        try:
            source = entry.read_bytes()
        except OSError:
            return

        # Determine parser for entry file
        ext = entry.suffix.lower()
        result = self._get_strategy(ext)
        if result is None:
            return
        lang_name, strategy = result
        parser = self._parsers[lang_name]
        tree = parser.parse(source)

        exports = self._extract_exports_from_entry(tree.root_node, source)
        if not exports:
            return

        updates = self._resolve_export_paths(exports, repo_path, module_dir)
        if updates:
            self.store.mark_symbols_exported(repo_name, updates)

    def _find_entry_file(self, module_dir: Path) -> Path | None:
        """Find module entry file (Index.ets, index.ts, etc.)."""
        for name in self._ENTRY_FILE_CANDIDATES:
            candidate = module_dir / name
            if candidate.exists():
                return candidate
        return None

    def _extract_exports_from_entry(
        self, node: ts.Node, source: bytes,
    ) -> list[dict]:
        """Parse export statements from an entry file.

        Returns list of:
          {"type": "named", "names": ["A","B"], "from": "./path"}
          {"type": "wildcard", "from": "./path"}
        """
        exports: list[dict] = []
        for child in node.children:
            if child.type != "export_statement":
                continue

            # Check for wildcard: export * from './path'
            has_asterisk = any(
                c.type == "*"
                or (c.type == "identifier" and bytes(c.text) == b"*")
                for c in child.children
            )

            # Extract from path
            from_path = ""
            for c in child.children:
                if c.type == "string":
                    raw = bytes(c.text).decode("utf-8", errors="replace")
                    # Strip quotes
                    from_path = raw.strip("\"'").strip()
                    break

            if has_asterisk:
                if from_path:
                    exports.append({"type": "wildcard", "from": from_path})
            else:
                # Named export: export { A, B } from './path'
                names: list[str] = []
                for c in child.children:
                    if c.type == "export_clause":
                        for spec in c.children:
                            if spec.type == "export_specifier":
                                # export_specifier has identifier children
                                for sc in spec.children:
                                    if sc.type == "identifier":
                                        names.append(
                                            bytes(sc.text).decode("utf-8", errors="replace")
                                        )
                                        break
                if names:
                    exports.append({"type": "named", "names": names, "from": from_path})

        return exports

    def _resolve_export_paths(
        self,
        exports: list[dict],
        repo_path: Path,
        module_dir: Path,
    ) -> list[tuple[str, str]]:
        """Resolve export from-paths to (rel_file_path, symbol_name) pairs."""
        updates: list[tuple[str, str]] = []

        for exp in exports:
            from_rel = exp.get("from", "")
            if not from_rel:
                continue

            # Resolve relative path to actual file
            target_dir = (module_dir / from_rel).resolve()
            target_file = self._resolve_target_file(target_dir)
            if target_file is None:
                continue

            rel_path = str(target_file.relative_to(repo_path))

            if exp["type"] == "named":
                for name in exp["names"]:
                    updates.append((rel_path, name))
            elif exp["type"] == "wildcard":
                # Resolve all exported symbols from the target file
                names = self._get_file_exported_symbols(target_file)
                for name in names:
                    updates.append((rel_path, name))

        return updates

    def _resolve_target_file(self, target_path: Path) -> Path | None:
        """Try to resolve a from-path to an actual file.

        Handles cases like './components/TitleBar' → TitleBar.ets / TitleBar.ts / TitleBar.js
        Also handles directory paths like './components' → components/Index.ets
        """
        # Try exact match first
        if target_path.is_file():
            return target_path

        # Try with extensions
        for ext in [".ets", ".ts", ".js", ".tsx", ".jsx"]:
            candidate = Path(str(target_path) + ext)
            if candidate.exists():
                return candidate

        # Try as directory (look for index file)
        if target_path.is_dir():
            for name in self._ENTRY_FILE_CANDIDATES:
                candidate = target_path / name
                if candidate.exists():
                    return candidate

        return None

    def _get_file_exported_symbols(self, file_path: Path) -> list[str]:
        """Parse a file to find all top-level exported symbol names.

        Handles:
          export function foo() {}
          export class Foo {}
          export interface Bar {}
          export enum Baz {}
          export { A, B }
          export struct Qux {}  (ArkTS)
        """
        ext = file_path.suffix.lower()
        result = self._get_strategy(ext)
        if result is None:
            return []
        lang_name, strategy = result

        try:
            source = file_path.read_bytes()
        except OSError:
            return []

        parser = self._parsers[lang_name]
        tree = parser.parse(source)
        names: list[str] = []

        for child in tree.root_node.children:
            if child.type == "export_statement":
                for sub in child.children:
                    # export function/class/interface/enum/struct
                    if sub.type in (
                        "function_declaration",
                        "class_declaration",
                        "interface_declaration",
                        "enum_declaration",
                        "struct_declaration",
                        "lexical_declaration",
                        "variable_declaration",
                    ):
                        name_node = sub.child_by_field_name("name")
                        if name_node:
                            names.append(
                                bytes(name_node.text).decode("utf-8", errors="replace")
                            )
                        elif sub.type in ("lexical_declaration", "variable_declaration"):
                            # export const foo = ...
                            for decl in sub.children:
                                if decl.type == "variable_declarator":
                                    vn = decl.child_by_field_name("name")
                                    if vn:
                                        names.append(
                                            bytes(vn.text).decode("utf-8", errors="replace")
                                        )
                    # export { A, B }
                    elif sub.type == "export_clause":
                        for spec in sub.children:
                            if spec.type == "export_specifier":
                                for sc in spec.children:
                                    if sc.type == "identifier":
                                        names.append(
                                            bytes(sc.text).decode("utf-8", errors="replace")
                                        )
                                        break

        return names
