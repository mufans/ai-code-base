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

from codekb.storage.sqlite_store import (
    SqliteStore,
    Symbol,
    CallRelation,
    ImportRecord,
    FileEntry,
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


# --- JavaScript Strategy ---

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


# --- Java Strategy ---

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


# --- Kotlin Strategy ---

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


# --- Swift Strategy ---

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


# --- TypeScript Strategy ---

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


# --- Indexer ---

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

        # Clear existing structure data
        self.store.clear_repo_structure(repo_name)

        all_symbols: list[Symbol] = []
        all_calls: list[CallRelation] = []
        all_imports: list[ImportRecord] = []

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

                all_symbols.extend(file_stats["symbols"])
                all_calls.extend(file_stats["calls"])
                all_imports.extend(file_stats["imports"])

                # Record file entry
                is_entry = filepath.stem in ENTRY_POINT_NAMES
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

        return {"symbols": symbols, "calls": calls, "imports": imports}

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

        # Re-parse
        file_data = self.index_file(repo_name, file_path, rel_path, strategy, lang_name)

        # Set repo_module on all extracted entities
        for sym in file_data["symbols"]:
            sym.repo_module = repo_module
        for call in file_data["calls"]:
            call.repo_module = repo_module
        for imp in file_data["imports"]:
            imp.repo_module = repo_module

        # Insert new data
        self.store.insert_symbols(file_data["symbols"])
        self.store.insert_calls(file_data["calls"])
        self.store.insert_imports(file_data["imports"])

        # Update file entry
        self.store.upsert_file_entry(FileEntry(
            repo_name=repo_name,
            path=rel_path,
            language=lang_name,
            is_entry_point=file_path.stem in ENTRY_POINT_NAMES,
            symbol_count=len(file_data["symbols"]),
            repo_module=repo_module,
        ))
