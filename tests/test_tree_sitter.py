"""Tests for tree-sitter indexer."""

import tempfile
from pathlib import Path

import pytest

from codekb.storage.sqlite_store import SqliteStore
from codekb.indexers.tree_sitter import (
    TreeSitterIndexer,
    PythonStrategy,
    JavaScriptStrategy,
    JavaStrategy,
    KotlinStrategy,
    SwiftStrategy,
    TypeScriptStrategy,
)
from codekb.core.module_detector import ModuleInfo


@pytest.fixture
def store(tmp_path):
    return SqliteStore(tmp_path / "index")


@pytest.fixture
def indexer(store):
    return TreeSitterIndexer(store)


@pytest.fixture
def python_repo(tmp_path):
    """Create a small Python repo for testing."""
    repo = tmp_path / "test-repo"
    repo.mkdir()

    (repo / "main.py").write_text('''"""Main module."""

import os
from pathlib import Path


def greet(name: str) -> str:
    """Say hello."""
    return f"Hello {name}"


class Greeter:
    """A greeter class."""

    def __init__(self, greeting: str):
        self.greeting = greeting

    def greet(self, name: str) -> str:
        """Greet someone."""
        message = self.format_message(name)
        return message

    def format_message(self, name: str) -> str:
        return f"{self.greeting}, {name}!"


if __name__ == "__main__":
    g = Greeter("Hi")
    print(g.greet("World"))
''')

    (repo / "utils.py").write_text('''"""Utility functions."""

from typing import Optional


def add(a: int, b: int) -> int:
    return a + b


def multiply(a: int, b: int) -> int:
    return a * b
''')

    return repo


@pytest.fixture
def js_repo(tmp_path):
    """Create a small JavaScript repo for testing."""
    repo = tmp_path / "js-repo"
    repo.mkdir()

    (repo / "index.js").write_text('''const express = require("express");

function greet(name) {
    return `Hello ${name}`;
}

class Greeter {
    constructor(greeting) {
        this.greeting = greeting;
    }

    greet(name) {
        console.log(this.formatMessage(name));
    }

    formatMessage(name) {
        return `${this.greeting}, ${name}!`;
    }
}

module.exports = { greet, Greeter };
''')

    return repo


class TestPythonStrategy:
    def test_extract_function_symbols(self):
        strategy = PythonStrategy()
        import tree_sitter as ts
        lang = strategy.language()
        parser = ts.Parser(lang)

        source = b'def hello(name: str) -> str:\n    """Docstring."""\n    return f"Hello {name}"\n'
        tree = parser.parse(source)
        symbols = strategy.extract_symbols(tree, source, "test.py", "test", "python")

        assert len(symbols) == 1
        assert symbols[0].name == "hello"
        assert symbols[0].kind == "function"
        assert "hello" in symbols[0].signature
        assert "Docstring" in symbols[0].docstring
        assert symbols[0].start_line == 1

    def test_extract_class_with_methods(self):
        strategy = PythonStrategy()
        import tree_sitter as ts
        lang = strategy.language()
        parser = ts.Parser(lang)

        source = b'''class MyClass:
    """My class."""

    def __init__(self, name):
        self.name = name

    def greet(self):
        return f"Hello {self.name}"
'''
        tree = parser.parse(source)
        symbols = strategy.extract_symbols(tree, source, "test.py", "test", "python")

        assert len(symbols) == 3
        class_sym = symbols[0]
        assert class_sym.name == "MyClass"
        assert class_sym.kind == "class"

        method_names = [s.name for s in symbols[1:]]
        assert "__init__" in method_names
        assert "greet" in method_names
        for s in symbols[1:]:
            assert s.kind == "method"
            assert s.parent == "MyClass"

    def test_extract_calls(self):
        strategy = PythonStrategy()
        import tree_sitter as ts
        lang = strategy.language()
        parser = ts.Parser(lang)

        source = b'''def main():
    result = add(1, 2)
    print(result)
'''
        tree = parser.parse(source)
        calls = strategy.extract_calls(tree, source, "test.py", "test")

        callee_names = [c.callee_name for c in calls]
        assert "add" in callee_names
        assert "print" in callee_names

    def test_extract_imports(self):
        strategy = PythonStrategy()
        import tree_sitter as ts
        lang = strategy.language()
        parser = ts.Parser(lang)

        source = b'''import os
from pathlib import Path
from typing import Optional, List
'''
        tree = parser.parse(source)
        imports = strategy.extract_imports(tree, source, "test.py", "test")

        assert len(imports) == 3
        assert imports[0].module == "os"
        assert imports[1].module == "pathlib"
        assert imports[2].module == "typing"


class TestJavaScriptStrategy:
    def test_extract_function_symbols(self):
        strategy = JavaScriptStrategy()
        import tree_sitter as ts
        lang = strategy.language()
        parser = ts.Parser(lang)

        source = b'function greet(name) {\n    return `Hello ${name}`;\n}\n'
        tree = parser.parse(source)
        symbols = strategy.extract_symbols(tree, source, "test.js", "test", "javascript")

        assert len(symbols) >= 1
        func = next(s for s in symbols if s.name == "greet")
        assert func.kind == "function"

    def test_extract_class_with_methods(self):
        strategy = JavaScriptStrategy()
        import tree_sitter as ts
        lang = strategy.language()
        parser = ts.Parser(lang)

        source = b'''class Greeter {
    constructor(greeting) {
        this.greeting = greeting;
    }
    greet(name) {
        return `${this.greeting}, ${name}`;
    }
}
'''
        tree = parser.parse(source)
        symbols = strategy.extract_symbols(tree, source, "test.js", "test", "javascript")

        class_sym = next(s for s in symbols if s.kind == "class")
        assert class_sym.name == "Greeter"

        methods = [s for s in symbols if s.kind == "method"]
        method_names = [m.name for m in methods]
        assert "constructor" in method_names
        assert "greet" in method_names

    def test_extract_calls(self):
        strategy = JavaScriptStrategy()
        import tree_sitter as ts
        lang = strategy.language()
        parser = ts.Parser(lang)

        source = b'function test() {\n    console.log("hello");\n    greet("world");\n}\n'
        tree = parser.parse(source)
        calls = strategy.extract_calls(tree, source, "test.js", "test")

        callee_names = [c.callee_name for c in calls]
        assert "console.log" in callee_names
        assert "greet" in callee_names


class TestTreeSitterIndexer:
    def test_index_python_repo(self, indexer, store, python_repo):
        stats = indexer.index_repo("test-repo", python_repo)

        assert stats["files_indexed"] == 2
        assert stats["symbols_found"] > 0
        assert stats["calls_found"] > 0

        # Check symbols in DB
        symbols = store.get_symbols("test-repo")
        symbol_names = [s.name for s in symbols]
        assert "greet" in symbol_names
        assert "Greeter" in symbol_names
        assert "add" in symbol_names
        assert "multiply" in symbol_names

        # Check file tree
        files = store.get_file_tree("test-repo")
        assert len(files) == 2
        file_paths = [f.path for f in files]
        assert "main.py" in file_paths
        assert "utils.py" in file_paths

        # Entry point detection
        main_file = next(f for f in files if f.path == "main.py")
        assert main_file.is_entry_point is True

    def test_index_js_repo(self, indexer, store, js_repo):
        stats = indexer.index_repo("js-repo", js_repo)

        assert stats["files_indexed"] == 1
        assert stats["symbols_found"] > 0

        symbols = store.get_symbols("js-repo")
        symbol_names = [s.name for s in symbols]
        assert "Greeter" in symbol_names

    def test_reindex_file(self, indexer, store, python_repo):
        # First full index
        indexer.index_repo("test-repo", python_repo)

        # Modify a file
        (python_repo / "utils.py").write_text('''"""Updated utils."""

def add(a, b):
    return a + b

def subtract(a, b):
    return a - b

def multiply(a, b):
    return a * b
''')

        # Re-index the changed file
        indexer.reindex_file("test-repo", python_repo / "utils.py", "utils.py")

        # Check updated symbols
        utils_symbols = store.get_symbols("test-repo", "utils.py")
        utils_names = [s.name for s in utils_symbols]
        assert "subtract" in utils_names
        assert "add" in utils_names
        assert "multiply" in utils_names

    def test_skip_excluded_dirs(self, indexer, store, tmp_path):
        repo = tmp_path / "skip-repo"
        repo.mkdir()
        (repo / "main.py").write_text("def main(): pass\n")

        # Create files in excluded dirs
        node_modules = repo / "node_modules"
        node_modules.mkdir()
        (node_modules / "lib.py").write_text("def lib_func(): pass\n")

        pycache = repo / "__pycache__"
        pycache.mkdir()
        (pycache / "cache.py").write_text("def cached(): pass\n")

        stats = indexer.index_repo("skip-repo", repo)
        assert stats["files_indexed"] == 1  # only main.py

    def test_class_method_parent(self, indexer, store, python_repo):
        indexer.index_repo("test-repo", python_repo)

        symbols = store.get_symbols("test-repo", "main.py")
        greeter_methods = [s for s in symbols if s.parent == "Greeter"]
        assert len(greeter_methods) >= 3  # __init__, greet, format_message
        for m in greeter_methods:
            assert m.kind == "method"

    def test_index_with_modules(self, indexer, store, tmp_path):
        """Test indexing a monorepo with module detection."""
        # Create a multi-module repo
        frontend = tmp_path / "frontend"
        frontend.mkdir()
        (frontend / "app.py").write_text('def render(): pass\n')
        (frontend / "package.json").write_text('{"name": "frontend"}')

        backend = tmp_path / "backend"
        backend.mkdir()
        (backend / "main.py").write_text('def serve(): pass\n')
        (backend / "pyproject.toml").write_text("[project]\nname = 'backend'")

        modules = [
            ModuleInfo(name="frontend", path="frontend", language="python"),
            ModuleInfo(name="backend", path="backend", language="python"),
        ]
        stats = indexer.index_repo("mono-repo", tmp_path, modules=modules)

        assert stats["files_indexed"] == 2

        # Check module assignment
        symbols = store.get_symbols("mono-repo")
        assert len(symbols) == 2

        frontend_symbols = store.get_symbols("mono-repo", repo_module="frontend")
        assert len(frontend_symbols) == 1
        assert frontend_symbols[0].name == "render"
        assert frontend_symbols[0].repo_module == "frontend"

        backend_symbols = store.get_symbols("mono-repo", repo_module="backend")
        assert len(backend_symbols) == 1
        assert backend_symbols[0].name == "serve"
        assert backend_symbols[0].repo_module == "backend"

    def test_reindex_file_with_module(self, indexer, store, python_repo):
        """Test reindex_file propagates repo_module."""
        modules = [
            ModuleInfo(name="test-repo", path=""),
        ]
        indexer.index_repo("test-repo", python_repo)

        # Reindex with module
        indexer.reindex_file("test-repo", python_repo / "utils.py", "utils.py",
                              repo_module="core")

        symbols = store.get_symbols("test-repo", "utils.py")
        assert all(s.repo_module == "core" for s in symbols)


class TestJavaStrategy:
    def test_extract_class_with_methods(self):
        strategy = JavaStrategy()
        import tree_sitter as ts
        lang = strategy.language()
        parser = ts.Parser(lang)

        source = b'''import java.util.List;

public class Greeter {
    private String greeting;

    public Greeter(String greeting) {
        this.greeting = greeting;
    }

    public void greet(String name) {
        System.out.println("Hello " + name);
    }
}

interface MyInterface {
    void doSomething();
}

enum Color {
    RED, GREEN, BLUE
}
'''
        tree = parser.parse(source)
        symbols = strategy.extract_symbols(tree, source, "Test.java", "test", "java")

        # Should find class, constructor, method, interface, enum
        symbol_names = [s.name for s in symbols]
        assert "Greeter" in symbol_names
        assert "MyInterface" in symbol_names
        assert "Color" in symbol_names

        class_sym = next(s for s in symbols if s.name == "Greeter")
        assert class_sym.kind == "class"

        iface_sym = next(s for s in symbols if s.name == "MyInterface")
        assert iface_sym.kind == "interface"

        enum_sym = next(s for s in symbols if s.name == "Color")
        assert enum_sym.kind == "enum"

        # Methods under Greeter
        methods = [s for s in symbols if s.parent == "Greeter"]
        method_names = [m.name for m in methods]
        assert "Greeter" in method_names  # constructor
        assert "greet" in method_names
        assert methods[0].kind == "constructor" or any(m.kind == "constructor" for m in methods)

    def test_extract_calls(self):
        strategy = JavaStrategy()
        import tree_sitter as ts
        lang = strategy.language()
        parser = ts.Parser(lang)

        source = b'''public class Test {
    void run() {
        System.out.println("hello");
        foo.bar();
    }
}'''
        tree = parser.parse(source)
        calls = strategy.extract_calls(tree, source, "Test.java", "test")

        callee_names = [c.callee_name for c in calls]
        assert "System.out.println" in callee_names
        assert "foo.bar" in callee_names

    def test_extract_imports(self):
        strategy = JavaStrategy()
        import tree_sitter as ts
        lang = strategy.language()
        parser = ts.Parser(lang)

        source = b'''import java.util.List;
import java.util.ArrayList;
import static java.lang.Math.PI;
'''
        tree = parser.parse(source)
        imports = strategy.extract_imports(tree, source, "Test.java", "test")

        assert len(imports) == 3
        modules = [i.module for i in imports]
        assert "java.util.List" in modules
        assert "java.util.ArrayList" in modules

    def test_annotations_as_docstring(self):
        strategy = JavaStrategy()
        import tree_sitter as ts
        lang = strategy.language()
        parser = ts.Parser(lang)

        source = b'''public class Test {
    @Override
    @Deprecated(since="1.0")
    public void annotated() {}
}'''
        tree = parser.parse(source)
        symbols = strategy.extract_symbols(tree, source, "Test.java", "test", "java")

        method = next(s for s in symbols if s.name == "annotated")
        assert "@Override" in method.docstring
        assert "@Deprecated" in method.docstring

    def test_empty_file(self):
        strategy = JavaStrategy()
        import tree_sitter as ts
        lang = strategy.language()
        parser = ts.Parser(lang)

        tree = parser.parse(b"")
        symbols = strategy.extract_symbols(tree, b"", "Test.java", "test", "java")
        assert len(symbols) == 0


class TestKotlinStrategy:
    def test_extract_class_with_function(self):
        strategy = KotlinStrategy()
        import tree_sitter as ts
        lang = strategy.language()
        parser = ts.Parser(lang)

        source = b'''package com.example

import java.util.List

class MyClass(val name: String) {
    fun greet(msg: String): String {
        println("Hello $name")
        return msg
    }
}

object Singleton {
    val instance = "test"
}

fun topLevel(x: Int): Int {
    return x
}

var globalVar = 10
val globalVal = "hello"
'''
        tree = parser.parse(source)
        symbols = strategy.extract_symbols(tree, source, "Test.kt", "test", "kotlin")

        symbol_names = [s.name for s in symbols]
        assert "MyClass" in symbol_names
        assert "Singleton" in symbol_names
        assert "topLevel" in symbol_names

        class_sym = next(s for s in symbols if s.name == "MyClass")
        assert class_sym.kind == "class"

        obj_sym = next(s for s in symbols if s.name == "Singleton")
        assert obj_sym.kind == "object"

        func_sym = next(s for s in symbols if s.name == "topLevel")
        assert func_sym.kind == "function"

        # Method inside class
        methods = [s for s in symbols if s.parent == "MyClass"]
        assert "greet" in [m.name for m in methods]
        assert methods[0].kind == "method"

        # Properties
        props = [s for s in symbols if s.kind == "property"]
        assert len(props) >= 2

    def test_extract_calls(self):
        strategy = KotlinStrategy()
        import tree_sitter as ts
        lang = strategy.language()
        parser = ts.Parser(lang)

        source = b'''fun test() {
    println("hello")
    abs(-5)
    obj.greet()
}'''
        tree = parser.parse(source)
        calls = strategy.extract_calls(tree, source, "Test.kt", "test")

        callee_names = [c.callee_name for c in calls]
        assert "println" in callee_names
        assert "abs" in callee_names
        assert "obj.greet" in callee_names

    def test_extract_imports(self):
        strategy = KotlinStrategy()
        import tree_sitter as ts
        lang = strategy.language()
        parser = ts.Parser(lang)

        source = b'''import java.util.List
import kotlin.math.abs
'''
        tree = parser.parse(source)
        imports = strategy.extract_imports(tree, source, "Test.kt", "test")

        assert len(imports) == 2
        modules = [i.module for i in imports]
        assert "java.util.List" in modules
        assert "kotlin.math.abs" in modules

    def test_empty_file(self):
        strategy = KotlinStrategy()
        import tree_sitter as ts
        lang = strategy.language()
        parser = ts.Parser(lang)

        tree = parser.parse(b"")
        symbols = strategy.extract_symbols(tree, b"", "Test.kt", "test", "kotlin")
        assert len(symbols) == 0


class TestSwiftStrategy:
    def test_extract_class_with_methods(self):
        strategy = SwiftStrategy()
        import tree_sitter as ts
        lang = strategy.language()
        parser = ts.Parser(lang)

        source = b'''import Foundation

class MyClass {
    var name: String
    let age: Int

    init(name: String, age: Int) {
        self.name = name
        self.age = age
    }

    func greet() -> String {
        return name
    }
}

struct Point {
    var x: Double
    var y: Double

    func distance() -> Double {
        return 0.0
    }
}

enum Color {
    case red, green, blue
}

protocol Drawable {
    func draw()
}

extension MyClass {
    func describe() -> String {
        return "test"
    }
}

func topLevel() {
    print("hello")
}
'''
        tree = parser.parse(source)
        symbols = strategy.extract_symbols(tree, source, "Test.swift", "test", "swift")

        symbol_names = [s.name for s in symbols]
        assert "MyClass" in symbol_names
        assert "Point" in symbol_names
        assert "Color" in symbol_names
        assert "Drawable" in symbol_names

        class_sym = next(s for s in symbols if s.name == "MyClass" and s.kind == "class")
        assert class_sym.kind == "class"

        struct_sym = next(s for s in symbols if s.name == "Point")
        assert struct_sym.kind == "struct"

        enum_sym = next(s for s in symbols if s.name == "Color")
        assert enum_sym.kind == "enum"

        proto_sym = next(s for s in symbols if s.name == "Drawable")
        assert proto_sym.kind == "protocol"

        # Methods inside class
        methods = [s for s in symbols if s.parent == "MyClass"]
        method_names = [m.name for m in methods]
        assert "init" in method_names
        assert "greet" in method_names
        assert "describe" in method_names

        # Top-level function
        top_func = next(s for s in symbols if s.name == "topLevel")
        assert top_func.kind == "function"

    def test_extract_calls(self):
        strategy = SwiftStrategy()
        import tree_sitter as ts
        lang = strategy.language()
        parser = ts.Parser(lang)

        source = b'''func test() {
    print("hello")
    obj.greet()
}'''
        tree = parser.parse(source)
        calls = strategy.extract_calls(tree, source, "Test.swift", "test")

        callee_names = [c.callee_name for c in calls]
        assert "print" in callee_names
        assert "obj.greet" in callee_names

    def test_extract_imports(self):
        strategy = SwiftStrategy()
        import tree_sitter as ts
        lang = strategy.language()
        parser = ts.Parser(lang)

        source = b'''import Foundation
import UIKit
'''
        tree = parser.parse(source)
        imports = strategy.extract_imports(tree, source, "Test.swift", "test")

        assert len(imports) == 2
        modules = [i.module for i in imports]
        assert "Foundation" in modules
        assert "UIKit" in modules

    def test_empty_file(self):
        strategy = SwiftStrategy()
        import tree_sitter as ts
        lang = strategy.language()
        parser = ts.Parser(lang)

        tree = parser.parse(b"")
        symbols = strategy.extract_symbols(tree, b"", "Test.swift", "test", "swift")
        assert len(symbols) == 0


class TestTypeScriptStrategy:
    def test_extract_class_with_methods(self):
        strategy = TypeScriptStrategy()
        import tree_sitter as ts
        lang = strategy.language()
        parser = ts.Parser(lang)

        source = b'''import { useState } from 'react';

export interface User {
    name: string;
}

export type Result<T> = {
    data: T;
};

export enum Status {
    Active = "active",
}

export function greet(name: string): string {
    return `Hello ${name}`;
}

export class Greeter {
    private greeting: string;

    constructor(greeting: string) {
        this.greeting = greeting;
    }

    greet(name: string): string {
        return `${this.greeting}, ${name}!`;
    }
}

export const add = (a: number, b: number): number => a + b;
export let counter = 0;
'''
        tree = parser.parse(source)
        symbols = strategy.extract_symbols(tree, source, "test.ts", "test", "typescript")

        symbol_names = [s.name for s in symbols]
        assert "User" in symbol_names
        assert "Result" in symbol_names
        assert "Status" in symbol_names
        assert "greet" in symbol_names
        assert "Greeter" in symbol_names
        assert "add" in symbol_names
        assert "counter" in symbol_names

        iface = next(s for s in symbols if s.name == "User")
        assert iface.kind == "interface"

        type_alias = next(s for s in symbols if s.name == "Result")
        assert type_alias.kind == "type"

        enum_sym = next(s for s in symbols if s.name == "Status")
        assert enum_sym.kind == "enum"

        class_sym = next(s for s in symbols if s.name == "Greeter")
        assert class_sym.kind == "class"

        # Methods inside class
        methods = [s for s in symbols if s.parent == "Greeter"]
        method_names = [m.name for m in methods]
        assert "greet" in method_names

        # Arrow function
        add_sym = next(s for s in symbols if s.name == "add")
        assert add_sym.kind == "function"

    def test_extract_calls(self):
        strategy = TypeScriptStrategy()
        import tree_sitter as ts
        lang = strategy.language()
        parser = ts.Parser(lang)

        source = b'''function test() {
    console.log("hello");
    greet("world");
}'''
        tree = parser.parse(source)
        calls = strategy.extract_calls(tree, source, "test.ts", "test")

        callee_names = [c.callee_name for c in calls]
        assert "console.log" in callee_names
        assert "greet" in callee_names

    def test_extract_imports(self):
        strategy = TypeScriptStrategy()
        import tree_sitter as ts
        lang = strategy.language()
        parser = ts.Parser(lang)

        source = b'''import { useState, useEffect } from 'react';
import type { Config } from './types';
import * as utils from './utils';
import express from 'express';
'''
        tree = parser.parse(source)
        imports = strategy.extract_imports(tree, source, "test.ts", "test")

        assert len(imports) == 4
        modules = [i.module for i in imports]
        assert "react" in modules
        assert "./types" in modules
        assert "./utils" in modules
        assert "express" in modules

    def test_tsx_strategy(self):
        strategy = TypeScriptStrategy(is_tsx=True)
        assert ".tsx" in strategy.file_extensions()
        assert ".ts" not in strategy.file_extensions()

        import tree_sitter as ts
        lang = strategy.language()
        parser = ts.Parser(lang)

        source = b'''export function Component() {
    return <div>Hello</div>;
}'''
        tree = parser.parse(source)
        symbols = strategy.extract_symbols(tree, source, "test.tsx", "test", "tsx")
        assert len(symbols) >= 1
        assert symbols[0].name == "Component"

    def test_empty_file(self):
        strategy = TypeScriptStrategy()
        import tree_sitter as ts
        lang = strategy.language()
        parser = ts.Parser(lang)

        tree = parser.parse(b"")
        symbols = strategy.extract_symbols(tree, b"", "test.ts", "test", "typescript")
        assert len(symbols) == 0
