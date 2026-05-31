"""Tests for tree-sitter indexer."""

import tempfile
from pathlib import Path

import pytest

from codekb.storage.sqlite_store import SqliteStore
from codekb.indexers.tree_sitter import TreeSitterIndexer, PythonStrategy, JavaScriptStrategy


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
