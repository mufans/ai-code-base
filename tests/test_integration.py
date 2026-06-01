"""Integration test: full pipeline from add repo to search to docs to skills."""

import asyncio
from pathlib import Path

import pytest

from codekb.core.config import CodekbYamlConfig, ensure_data_dir
from codekb.core.indexer import IndexOrchestrator
from codekb.core.module_detector import ModuleInfo, detect_modules, file_to_module
from codekb.core.repo_manager import RepoManager
from codekb.indexers.doc_generator import DocGenerator
from codekb.indexers.skill_generator import SkillGenerator
from codekb.retrieval.structure_query import StructureQuery
from codekb.storage.doc_store import DocStore
from codekb.storage.sqlite_store import SqliteStore
from codekb.storage.vector_store import VectorStore


CALCULATOR_PY = r'''# Calculator module


class Calculator:
    # A simple calculator class.

    def __init__(self, precision: int = 2):
        self.precision = precision
        self.history = []

    def add(self, a: float, b: float) -> float:
        result = round(a + b, self.precision)
        self.history.append(f"{a} + {b} = {result}")
        return result

    def subtract(self, a: float, b: float) -> float:
        result = round(a - b, self.precision)
        self.history.append(f"{a} - {b} = {result}")
        return result

    def multiply(self, a: float, b: float) -> float:
        result = round(a * b, self.precision)
        self.history.append(f"{a} * {b} = {result}")
        return result

    def get_history(self) -> list[str]:
        return self.history
'''

UTILS_PY = r'''# Utility functions


def format_result(value: float, precision: int = 2) -> str:
    return f"{value:.{precision}f}"


def parse_expression(expr: str) -> tuple[float, str, float]:
    parts = expr.split()
    if len(parts) != 3:
        raise ValueError(f"Invalid expression: {expr}")
    return float(parts[0]), parts[1], float(parts[2])
'''

INIT_PY = r'''# Example project package

from example.calculator import Calculator
from example.utils import format_result

__all__ = ["Calculator", "format_result"]
'''

TEST_CALC_PY = r'''# Tests for calculator

from example.calculator import Calculator


def test_add():
    calc = Calculator()
    assert calc.add(1, 2) == 3.0


def test_subtract():
    calc = Calculator()
    assert calc.subtract(5, 3) == 2.0


def test_history():
    calc = Calculator()
    calc.add(1, 2)
    calc.subtract(5, 3)
    assert len(calc.get_history()) == 2
'''

README_MD = """# Example Project

A sample Python project for testing codekb.

## Installation

```bash
pip install -e .
```

## Usage

```python
from example import Calculator
calc = Calculator()
result = calc.add(1, 2)
```
"""


@pytest.fixture
def test_repo(tmp_path):
    """Create a realistic test Python repo."""
    repo = tmp_path / "example-project"
    repo.mkdir()

    (repo / "README.md").write_text(README_MD)
    (repo / "example").mkdir()
    (repo / "example" / "__init__.py").write_text(INIT_PY)
    (repo / "example" / "calculator.py").write_text(CALCULATOR_PY)
    (repo / "example" / "utils.py").write_text(UTILS_PY)
    (repo / "tests").mkdir()
    (repo / "tests" / "test_calculator.py").write_text(TEST_CALC_PY)

    return repo


@pytest.fixture
def services(tmp_path):
    """Set up all services for integration testing."""
    config = CodekbYamlConfig(data_dir=str(tmp_path / "data"))
    data_dir = ensure_data_dir(config)

    store = SqliteStore(data_dir / "index")
    vector_store = VectorStore(data_dir / "index" / "vectors")
    doc_store = DocStore(data_dir / "generated")
    repo_manager = RepoManager(config, store)

    return config, store, vector_store, doc_store, repo_manager


class TestFullPipeline:
    def test_add_repo_and_index(self, test_repo, services):
        """Test: add repo, tree-sitter index, verify structure."""
        config, store, vector_store, doc_store, repo_manager = services

        # Add repo (local)
        repo = repo_manager.add_repo(str(test_repo), name="example-project", is_local=True)
        assert repo.name == "example-project"
        assert repo.language == "python"

        # Verify repo registered
        registered = store.get_repo("example-project")
        assert registered is not None
        assert registered.language == "python"

    def test_tree_sitter_index(self, test_repo, services):
        """Test: tree-sitter parsing extracts correct symbols."""
        config, store, vector_store, doc_store, repo_manager = services

        # Register repo
        repo_manager.add_repo(str(test_repo), name="example-project", is_local=True)

        # Index with tree-sitter
        from codekb.indexers.tree_sitter import TreeSitterIndexer
        ts_indexer = TreeSitterIndexer(store)
        stats = ts_indexer.index_repo("example-project", test_repo)

        assert stats["files_indexed"] >= 3
        assert stats["symbols_found"] > 0

        # Verify symbol extraction
        symbols = store.get_symbols("example-project")
        symbol_names = {s.name for s in symbols}

        assert "Calculator" in symbol_names
        assert "add" in symbol_names
        assert "subtract" in symbol_names
        assert "multiply" in symbol_names
        assert "format_result" in symbol_names
        assert "parse_expression" in symbol_names

        # Verify call graph
        calls = store.get_calls_from("example-project", "add")
        callee_names = [c.callee_name for c in calls]
        assert "round" in callee_names

    def test_full_index_pipeline(self, test_repo, services):
        """Test: full index with tree-sitter + embedding."""
        config, store, vector_store, doc_store, repo_manager = services

        # Register repo
        repo_manager.add_repo(str(test_repo), name="example-project", is_local=True)

        # Full index
        orchestrator = IndexOrchestrator(config, store, vector_store, doc_store, repo_manager)

        async def _index():
            return await orchestrator.full_index("example-project")

        result = asyncio.run(_index())
        assert result["files_indexed"] > 0
        assert result["symbols_found"] > 0
        assert result["code_chunks_embedded"] > 0

        # Verify repo status updated
        repo = store.get_repo("example-project")
        assert repo.status == "indexed"

    def test_structure_queries(self, test_repo, services):
        """Test: structure queries return correct data."""
        config, store, vector_store, doc_store, repo_manager = services
        repo_manager.add_repo(str(test_repo), name="example-project", is_local=True)

        from codekb.indexers.tree_sitter import TreeSitterIndexer
        ts = TreeSitterIndexer(store)
        ts.index_repo("example-project", test_repo)

        sq = StructureQuery(store)

        # Get structure
        structure = sq.get_structure("example-project")
        assert len(structure) > 0

        # Get symbol detail
        detail = sq.get_symbol_detail("example-project", "Calculator")
        assert detail is not None
        assert detail["definition"]["name"] == "Calculator"
        assert detail["definition"]["type"] == "class"

        # Get file content
        content = sq.get_file_content("example-project", "example/calculator.py")
        assert content is not None
        assert "Calculator" in content["content"]
        assert content["language"] == "python"

        # Get README
        readme = sq.get_readme("example-project")
        assert readme is not None
        assert "Example Project" in readme

    def test_doc_generation(self, test_repo, services):
        """Test: doc generation with coverage assessment."""
        config, store, vector_store, doc_store, repo_manager = services
        repo_manager.add_repo(str(test_repo), name="example-project", is_local=True)

        from codekb.indexers.tree_sitter import TreeSitterIndexer
        ts = TreeSitterIndexer(store)
        ts.index_repo("example-project", test_repo)

        # Generate docs (without LLM - template mode)
        generator = DocGenerator(store, doc_store, config)

        async def _generate():
            return await generator.generate_docs("example-project")

        result = asyncio.run(_generate())

        assert result["repo"] == "example-project"
        assert "coverage" in result

        # Verify docs written
        docs = doc_store.list_docs("example-project")
        assert "COVERAGE.json" in docs

    def test_skill_generation(self, test_repo, services):
        """Test: skill generation and verification."""
        config, store, vector_store, doc_store, repo_manager = services
        repo_manager.add_repo(str(test_repo), name="example-project", is_local=True)

        from codekb.indexers.tree_sitter import TreeSitterIndexer
        ts = TreeSitterIndexer(store)
        ts.index_repo("example-project", test_repo)

        # Generate skills (without LLM - template mode)
        sg = SkillGenerator(store, doc_store, config)

        async def _generate():
            return await sg.generate_skills("example-project")

        result = asyncio.run(_generate())
        assert len(result) > 0

        # Verify skills stored
        skills = doc_store.list_skills("example-project")
        assert len(skills) > 0

        # List skills
        listed = sg.list_skills("example-project")
        assert len(listed) > 0

        # Verify skills
        verified = sg.verify_skills("example-project")
        assert len(verified) > 0

    def test_reindex_changed_file(self, test_repo, services):
        """Test: incremental re-index when a file changes."""
        config, store, vector_store, doc_store, repo_manager = services
        repo_manager.add_repo(str(test_repo), name="example-project", is_local=True)

        from codekb.indexers.tree_sitter import TreeSitterIndexer
        ts = TreeSitterIndexer(store)
        ts.index_repo("example-project", test_repo)

        # Modify a file
        (test_repo / "example" / "utils.py").write_text(
            "# Updated utilities\n\n"
            "def new_function():\n"
            "    return 42\n\n"
            "def format_result(value):\n"
            "    return str(value)\n"
        )

        # Re-index the changed file
        ts.reindex_file("example-project", test_repo / "example" / "utils.py", "example/utils.py")

        # Verify new symbol found
        symbols = store.get_symbols("example-project", "example/utils.py")
        names = {s.name for s in symbols}
        assert "new_function" in names


class TestMultiModulePipeline:
    """Integration tests for multi-module repository support."""

    @pytest.fixture
    def mono_repo(self, tmp_path):
        """Create a multi-module monorepo."""
        repo = tmp_path / "mono-repo"
        repo.mkdir()

        (repo / "README.md").write_text("# Mono Repo\n\nMulti-module test project.\n")

        # Frontend module
        frontend = repo / "packages" / "frontend"
        frontend.mkdir(parents=True)
        (frontend / "package.json").write_text('{"name": "frontend"}')
        (frontend / "app.js").write_text('''
function render(name) {
    return "Hello " + name;
}

class App {
    constructor() {
        this.name = "test";
    }
    start() {
        console.log(this.name);
    }
}
''')

        # Backend module
        backend = repo / "packages" / "backend"
        backend.mkdir(parents=True)
        (backend / "pyproject.toml").write_text("[project]\nname = 'backend'")
        (backend / "server.py").write_text('''
"""Backend server."""

from typing import Optional


def handle_request(path: str) -> dict:
    """Handle incoming request."""
    return {"path": path, "status": "ok"}


class Server:
    """HTTP server."""

    def __init__(self, port: int = 8080):
        self.port = port

    def start(self) -> None:
        """Start the server."""
        pass
''')

        return repo

    @pytest.fixture
    def services(self, tmp_path):
        """Set up all services for integration testing."""
        config = CodekbYamlConfig(data_dir=str(tmp_path / "data"))
        data_dir = ensure_data_dir(config)

        store = SqliteStore(data_dir / "index")
        vector_store = VectorStore(data_dir / "index" / "vectors")
        doc_store = DocStore(data_dir / "generated")
        repo_manager = RepoManager(config, store)

        return config, store, vector_store, doc_store, repo_manager

    def test_module_detection(self, mono_repo):
        """Test that modules are correctly detected in a monorepo."""
        modules = detect_modules(mono_repo)
        assert len(modules) == 2
        names = {m.name for m in modules}
        assert "frontend" in names
        assert "backend" in names

    def test_file_to_module_mapping(self, mono_repo):
        """Test file-to-module assignment."""
        modules = detect_modules(mono_repo)
        assert file_to_module("packages/frontend/app.js", modules) == "frontend"
        assert file_to_module("packages/backend/server.py", modules) == "backend"
        assert file_to_module("README.md", modules) == ""

    def test_index_with_modules(self, mono_repo, services):
        """Test tree-sitter indexing with module support."""
        config, store, vector_store, doc_store, repo_manager = services
        repo_manager.add_repo(str(mono_repo), name="mono-repo", is_local=True)

        from codekb.indexers.tree_sitter import TreeSitterIndexer
        ts = TreeSitterIndexer(store)

        modules = [
            ModuleInfo(name="frontend", path="packages/frontend"),
            ModuleInfo(name="backend", path="packages/backend"),
        ]
        stats = ts.index_repo("mono-repo", mono_repo, modules=modules)

        assert stats["files_indexed"] == 2
        assert stats["symbols_found"] > 0

        # Verify module assignment
        frontend_symbols = store.get_symbols("mono-repo", repo_module="frontend")
        backend_symbols = store.get_symbols("mono-repo", repo_module="backend")

        assert len(frontend_symbols) > 0
        assert len(backend_symbols) > 0

        # All frontend symbols should have correct module
        for s in frontend_symbols:
            assert s.repo_module == "frontend"
            assert "frontend" in s.file_path

        for s in backend_symbols:
            assert s.repo_module == "backend"
            assert "backend" in s.file_path

    def test_full_index_with_module_detection(self, mono_repo, services):
        """Test full index pipeline auto-detects modules."""
        config, store, vector_store, doc_store, repo_manager = services
        repo_manager.add_repo(str(mono_repo), name="mono-repo", is_local=True)

        orchestrator = IndexOrchestrator(config, store, vector_store, doc_store, repo_manager)

        async def _index():
            return await orchestrator.full_index("mono-repo")

        result = asyncio.run(_index())
        assert result["files_indexed"] > 0
        assert "modules" in result
        # Should detect 2 modules
        module_names = result["modules"]
        assert "frontend" in module_names
        assert "backend" in module_names

        # Verify modules saved
        saved_modules = store.get_repo_modules("mono-repo")
        assert len(saved_modules) == 2

    def test_structure_query_with_modules(self, mono_repo, services):
        """Test structure queries with module filtering."""
        config, store, vector_store, doc_store, repo_manager = services
        repo_manager.add_repo(str(mono_repo), name="mono-repo", is_local=True)

        from codekb.indexers.tree_sitter import TreeSitterIndexer
        ts = TreeSitterIndexer(store)
        modules = [
            ModuleInfo(name="frontend", path="packages/frontend"),
            ModuleInfo(name="backend", path="packages/backend"),
        ]
        ts.index_repo("mono-repo", mono_repo, modules=modules)

        sq = StructureQuery(store)

        # List modules
        mod_list = sq.list_modules("mono-repo")
        assert set(mod_list) == {"frontend", "backend"}

        # Stats per module
        frontend_stats = sq.get_stats("mono-repo", repo_module="frontend")
        assert frontend_stats["total_symbols"] > 0

        backend_stats = sq.get_stats("mono-repo", repo_module="backend")
        assert backend_stats["total_symbols"] > 0

    def test_doc_store_with_modules(self, services):
        """Test doc store module-level paths."""
        _, _, _, doc_store, _ = services

        # Write doc for a specific module
        doc_store.write_doc("test-repo", "ARCHITECTURE.md", "# Frontend Architecture",
                            repo_module="frontend")
        doc_store.write_doc("test-repo", "ARCHITECTURE.md", "# Backend Architecture",
                            repo_module="backend")

        # Read back
        frontend_doc = doc_store.read_doc("test-repo", "ARCHITECTURE.md",
                                           repo_module="frontend")
        assert "Frontend" in frontend_doc

        backend_doc = doc_store.read_doc("test-repo", "ARCHITECTURE.md",
                                          repo_module="backend")
        assert "Backend" in backend_doc

        # List docs per module
        frontend_docs = doc_store.list_docs("test-repo", repo_module="frontend")
        assert "ARCHITECTURE.md" in frontend_docs
