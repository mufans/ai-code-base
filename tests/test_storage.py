"""Tests for storage layer."""

import tempfile
from pathlib import Path

import pytest

from codekb.storage.sqlite_store import SqliteStore, RepoRecord, Symbol, CallRelation, ImportRecord, FileEntry
from codekb.storage.doc_store import DocStore


@pytest.fixture
def sqlite_store(tmp_path):
    return SqliteStore(tmp_path / "index")


@pytest.fixture
def doc_store(tmp_path):
    return DocStore(tmp_path / "generated")


class TestSqliteStore:
    def test_register_and_get_repo(self, sqlite_store):
        repo = RepoRecord(
            name="test-repo",
            url="https://github.com/org/test-repo",
            local_path="/tmp/repos/test-repo",
            platform="github",
            language="python",
        )
        sqlite_store.register_repo(repo)
        result = sqlite_store.get_repo("test-repo")
        assert result is not None
        assert result.name == "test-repo"
        assert result.url == "https://github.com/org/test-repo"
        assert result.language == "python"

    def test_list_repos(self, sqlite_store):
        for name in ["repo-a", "repo-b", "repo-c"]:
            sqlite_store.register_repo(RepoRecord(
                name=name, url=f"https://github.com/org/{name}",
                local_path=f"/tmp/repos/{name}",
            ))
        repos = sqlite_store.list_repos()
        assert len(repos) == 3
        names = [r.name for r in repos]
        assert names == ["repo-a", "repo-b", "repo-c"]

    def test_update_repo(self, sqlite_store):
        sqlite_store.register_repo(RepoRecord(
            name="test-repo", url="https://github.com/org/test",
            local_path="/tmp/repos/test",
        ))
        sqlite_store.update_repo("test-repo", language="javascript", status="indexed")
        repo = sqlite_store.get_repo("test-repo")
        assert repo.language == "javascript"
        assert repo.status == "indexed"

    def test_remove_repo(self, sqlite_store):
        sqlite_store.register_repo(RepoRecord(
            name="test-repo", url="https://github.com/org/test",
            local_path="/tmp/repos/test",
        ))
        assert sqlite_store.remove_repo("test-repo") is True
        assert sqlite_store.get_repo("test-repo") is None
        assert sqlite_store.remove_repo("nonexistent") is False

    def test_insert_and_get_symbols(self, sqlite_store):
        symbols = [
            Symbol(repo_name="test", file_path="main.py", name="hello",
                   kind="function", signature="def hello(name: str) -> str",
                   start_line=1, end_line=3, language="python", source="def hello(name: str) -> str:\n    return f'Hello {name}'"),
            Symbol(repo_name="test", file_path="main.py", name="MyClass",
                   kind="class", start_line=5, end_line=10, language="python"),
        ]
        sqlite_store.insert_symbols(symbols)
        result = sqlite_store.get_symbols("test", "main.py")
        assert len(result) == 2
        assert result[0].name == "hello"
        assert result[0].kind == "function"
        assert result[1].name == "MyClass"

    def test_get_symbol_by_name(self, sqlite_store):
        sqlite_store.insert_symbols([
            Symbol(repo_name="test", file_path="a.py", name="foo", kind="function",
                   start_line=1, end_line=1, language="python"),
            Symbol(repo_name="test", file_path="b.py", name="foo", kind="function",
                   start_line=5, end_line=5, language="python"),
        ])
        result = sqlite_store.get_symbol_by_name("test", "foo")
        assert len(result) == 2

    def test_insert_and_get_calls(self, sqlite_store):
        calls = [
            CallRelation(repo_name="test", caller_file="main.py",
                         caller_name="main", callee_name="hello", line_number=10),
            CallRelation(repo_name="test", caller_file="main.py",
                         caller_name="main", callee_name="goodbye", line_number=11),
        ]
        sqlite_store.insert_calls(calls)
        from_test = sqlite_store.get_calls_from("test", "main")
        assert len(from_test) == 2
        to_test = sqlite_store.get_calls_to("test", "hello")
        assert len(to_test) == 1

    def test_insert_and_get_imports(self, sqlite_store):
        imports = [
            ImportRecord(repo_name="test", file_path="main.py", module="os",
                         imported_names='["path"]', line_number=1),
            ImportRecord(repo_name="test", file_path="main.py", module="sys",
                         line_number=2, is_relative=False),
        ]
        sqlite_store.insert_imports(imports)
        result = sqlite_store.get_imports("test", "main.py")
        assert len(result) == 2
        assert result[0].module == "os"

    def test_file_tree(self, sqlite_store):
        entry = FileEntry(
            repo_name="test", path="src/main.py",
            language="python", symbol_count=5, is_entry_point=True,
        )
        sqlite_store.upsert_file_entry(entry)
        tree = sqlite_store.get_file_tree("test")
        assert len(tree) == 1
        assert tree[0].path == "src/main.py"
        assert tree[0].is_entry_point is True

    def test_delete_symbols_for_file(self, sqlite_store):
        sqlite_store.insert_symbols([
            Symbol(repo_name="test", file_path="a.py", name="foo", kind="function",
                   start_line=1, end_line=1, language="python"),
            Symbol(repo_name="test", file_path="b.py", name="bar", kind="function",
                   start_line=1, end_line=1, language="python"),
        ])
        sqlite_store.delete_symbols_for_file("test", "a.py")
        assert len(sqlite_store.get_symbols("test", "a.py")) == 0
        assert len(sqlite_store.get_symbols("test", "b.py")) == 1

    def test_clear_repo_structure(self, sqlite_store):
        sqlite_store.insert_symbols([
            Symbol(repo_name="test", file_path="a.py", name="foo", kind="function",
                   start_line=1, end_line=1, language="python"),
        ])
        sqlite_store.insert_calls([
            CallRelation(repo_name="test", caller_file="a.py",
                         caller_name="foo", callee_name="bar"),
        ])
        sqlite_store.clear_repo_structure("test")
        assert len(sqlite_store.get_symbols("test")) == 0
        assert len(sqlite_store.get_calls_from("test", "foo")) == 0

    def test_repo_module_field(self, sqlite_store):
        """Test that repo_module field is stored and retrieved."""
        symbols = [
            Symbol(repo_name="test", file_path="frontend/app.py", name="App",
                   kind="class", start_line=1, end_line=10, language="python",
                   repo_module="frontend"),
            Symbol(repo_name="test", file_path="backend/main.py", name="main",
                   kind="function", start_line=1, end_line=5, language="python",
                   repo_module="backend"),
        ]
        sqlite_store.insert_symbols(symbols)
        result = sqlite_store.get_symbols("test")
        assert len(result) == 2

        # Filter by module
        frontend = sqlite_store.get_symbols("test", repo_module="frontend")
        assert len(frontend) == 1
        assert frontend[0].repo_module == "frontend"
        assert frontend[0].name == "App"

        backend = sqlite_store.get_symbols("test", repo_module="backend")
        assert len(backend) == 1
        assert backend[0].repo_module == "backend"

    def test_imports_with_repo_module(self, sqlite_store):
        imports = [
            ImportRecord(repo_name="test", file_path="a.py", module="os",
                         line_number=1, repo_module="frontend"),
            ImportRecord(repo_name="test", file_path="b.py", module="sys",
                         line_number=1, repo_module="backend"),
        ]
        sqlite_store.insert_imports(imports)
        result = sqlite_store.get_imports("test", repo_module="frontend")
        assert len(result) == 1
        assert result[0].repo_module == "frontend"

    def test_file_tree_with_repo_module(self, sqlite_store):
        entry1 = FileEntry(
            repo_name="test", path="frontend/app.py",
            language="python", symbol_count=5, repo_module="frontend",
        )
        entry2 = FileEntry(
            repo_name="test", path="backend/main.py",
            language="python", symbol_count=3, repo_module="backend",
        )
        sqlite_store.upsert_file_entry(entry1)
        sqlite_store.upsert_file_entry(entry2)

        # Filter by module
        frontend_files = sqlite_store.get_file_tree("test", repo_module="frontend")
        assert len(frontend_files) == 1
        assert frontend_files[0].repo_module == "frontend"

    def test_list_modules(self, sqlite_store):
        sqlite_store.insert_symbols([
            Symbol(repo_name="test", file_path="a.py", name="foo", kind="function",
                   start_line=1, end_line=1, language="python", repo_module="frontend"),
            Symbol(repo_name="test", file_path="b.py", name="bar", kind="function",
                   start_line=1, end_line=1, language="python", repo_module="backend"),
            Symbol(repo_name="test", file_path="c.py", name="baz", kind="function",
                   start_line=1, end_line=1, language="python"),
        ])
        modules = sqlite_store.list_modules("test")
        assert set(modules) == {"frontend", "backend"}

    def test_save_and_get_repo_modules(self, sqlite_store):
        sqlite_store.register_repo(RepoRecord(
            name="test-repo", url="https://github.com/org/test",
            local_path="/tmp/repos/test",
        ))
        modules_data = [
            {"name": "frontend", "path": "frontend", "language": "javascript"},
            {"name": "backend", "path": "backend", "language": "python"},
        ]
        sqlite_store.save_repo_modules("test-repo", modules_data)
        result = sqlite_store.get_repo_modules("test-repo")
        assert len(result) == 2
        assert result[0]["name"] == "frontend"
        assert result[1]["name"] == "backend"

    def test_calls_with_repo_module(self, sqlite_store):
        calls = [
            CallRelation(repo_name="test", caller_file="a.py",
                         caller_name="foo", callee_name="bar",
                         repo_module="frontend"),
            CallRelation(repo_name="test", caller_file="b.py",
                         caller_name="baz", callee_name="qux",
                         repo_module="backend"),
        ]
        sqlite_store.insert_calls(calls)

        frontend_calls = sqlite_store.get_calls_from("test", "foo", repo_module="frontend")
        assert len(frontend_calls) == 1
        assert frontend_calls[0].repo_module == "frontend"

        backend_calls = sqlite_store.get_calls_to("test", "qux", repo_module="backend")
        assert len(backend_calls) == 1

    def test_find_symbol_across_repos(self, sqlite_store):
        """Test cross-repo symbol search."""
        sqlite_store.insert_symbols([
            Symbol(repo_name="repo-a", file_path="svc.py", name="QuoteService",
                   kind="class", signature="class QuoteService:",
                   start_line=1, end_line=10, language="python",
                   repo_module="quote_service"),
            Symbol(repo_name="repo-b", file_path="util.py", name="QuoteService",
                   kind="class", signature="class QuoteService:",
                   start_line=5, end_line=20, language="python"),
            Symbol(repo_name="repo-a", file_path="other.py", name="OtherClass",
                   kind="class", start_line=1, end_line=5, language="python"),
        ])
        results = sqlite_store.find_symbol_across_repos("QuoteService")
        assert len(results) == 2
        assert results[0].repo_name == "repo-a"
        assert results[0].repo_module == "quote_service"
        assert results[1].repo_name == "repo-b"

    def test_find_symbol_across_repos_no_match(self, sqlite_store):
        """Test cross-repo search with no matches."""
        sqlite_store.insert_symbols([
            Symbol(repo_name="repo-a", file_path="a.py", name="Foo",
                   kind="class", start_line=1, end_line=1, language="python"),
        ])
        results = sqlite_store.find_symbol_across_repos("NonExistent")
        assert results == []

    def test_find_symbol_cross_repo_via_structure_query(self, sqlite_store):
        """Test StructureQuery.find_symbol delegates correctly."""
        from codekb.retrieval.structure_query import StructureQuery
        sqlite_store.insert_symbols([
            Symbol(repo_name="repo-a", file_path="svc.py", name="QuoteService",
                   kind="class", signature="class QuoteService:",
                   start_line=1, end_line=10, language="python",
                   repo_module="quote_service"),
        ])
        sq = StructureQuery(sqlite_store)
        results = sq.find_symbol("QuoteService")
        assert len(results) == 1
        assert results[0]["repo_name"] == "repo-a"
        assert results[0]["module"] == "quote_service"
        assert results[0]["kind"] == "class"

    def test_resolve_symbol_unique_match(self, sqlite_store):
        """Test resolve_symbol with unique match returns (repo_name, module)."""
        from codekb.retrieval.structure_query import StructureQuery
        sqlite_store.insert_symbols([
            Symbol(repo_name="repo-a", file_path="svc.py", name="QuoteService",
                   kind="class", start_line=1, end_line=10, language="python",
                   repo_module="quote_service"),
        ])
        sq = StructureQuery(sqlite_store)
        result = sq.resolve_symbol("QuoteService")
        assert result == ("repo-a", "quote_service")

    def test_resolve_symbol_no_match(self, sqlite_store):
        """Test resolve_symbol with no match returns None."""
        from codekb.retrieval.structure_query import StructureQuery
        sq = StructureQuery(sqlite_store)
        result = sq.resolve_symbol("NonExistent")
        assert result is None

    def test_resolve_symbol_multiple_repos(self, sqlite_store):
        """Test resolve_symbol with matches in multiple repos returns None."""
        from codekb.retrieval.structure_query import StructureQuery
        sqlite_store.insert_symbols([
            Symbol(repo_name="repo-a", file_path="a.py", name="Foo",
                   kind="class", start_line=1, end_line=5, language="python"),
            Symbol(repo_name="repo-b", file_path="b.py", name="Foo",
                   kind="class", start_line=1, end_line=5, language="python"),
        ])
        sq = StructureQuery(sqlite_store)
        result = sq.resolve_symbol("Foo")
        assert result is None

    def test_resolve_symbol_same_repo_different_modules(self, sqlite_store):
        """Test resolve_symbol with same repo but different modules returns first match."""
        from codekb.retrieval.structure_query import StructureQuery
        sqlite_store.insert_symbols([
            Symbol(repo_name="repo-a", file_path="a.py", name="Foo",
                   kind="class", start_line=1, end_line=5, language="python",
                   repo_module="mod_a"),
            Symbol(repo_name="repo-a", file_path="b.py", name="Foo",
                   kind="class", start_line=1, end_line=5, language="python",
                   repo_module="mod_b"),
        ])
        sq = StructureQuery(sqlite_store)
        result = sq.resolve_symbol("Foo")
        assert result == ("repo-a", "mod_a")


class TestDocIndex:
    def test_index_docs(self, sqlite_store, tmp_path):
        """Test scanning and indexing md files from a repo directory."""
        repo_dir = tmp_path / "test-repo"
        repo_dir.mkdir()

        # Create test md files
        (repo_dir / "README.md").write_text("# Test Project\n\nA test project.", encoding="utf-8")
        (repo_dir / "CLAUDE.md").write_text("# Claude Instructions\n\nDo stuff.", encoding="utf-8")
        (repo_dir / "ARCHITECTURE.md").write_text("# Architecture\n\nDetails.", encoding="utf-8")
        (repo_dir / "random.md").write_text("# Random\n\nSome notes.", encoding="utf-8")

        docs_dir = repo_dir / "docs"
        docs_dir.mkdir()
        (docs_dir / "guide.md").write_text("# User Guide\n\nHow to use.", encoding="utf-8")
        (docs_dir / "api.md").write_text("# API Reference\n\nEndpoints.", encoding="utf-8")

        # A non-md file should be ignored
        (repo_dir / "setup.py").write_text("print('hello')", encoding="utf-8")

        count = sqlite_store.index_docs("test-repo", repo_dir)
        assert count == 6  # 4 root + 2 docs/

        entries = sqlite_store.get_doc_index("test-repo")
        assert len(entries) == 6

        # Verify types
        types_map = {e["file_path"]: e["doc_type"] for e in entries}
        assert types_map["README.md"] == "readme"
        assert types_map["CLAUDE.md"] == "claude_md"
        assert types_map["ARCHITECTURE.md"] == "architecture"
        assert types_map["docs/guide.md"] == "docs"
        assert types_map["docs/api.md"] == "docs"
        assert types_map["random.md"] == "other"

    def test_get_doc_index(self, sqlite_store, tmp_path):
        """Test querying doc index with type filter."""
        repo_dir = tmp_path / "test-repo2"
        repo_dir.mkdir()
        (repo_dir / "README.md").write_text("# Readme", encoding="utf-8")
        (repo_dir / "CLAUDE.md").write_text("# Claude", encoding="utf-8")

        docs_dir = repo_dir / "docs"
        docs_dir.mkdir()
        (docs_dir / "guide.md").write_text("# Guide", encoding="utf-8")

        sqlite_store.index_docs("test-repo2", repo_dir)

        # Filter by type
        readme_entries = sqlite_store.get_doc_index("test-repo2", doc_type="readme")
        assert len(readme_entries) == 1
        assert readme_entries[0]["file_path"] == "README.md"

        docs_entries = sqlite_store.get_doc_index("test-repo2", doc_type="docs")
        assert len(docs_entries) == 1
        assert docs_entries[0]["file_path"] == "docs/guide.md"

    def test_doc_type_detection(self, sqlite_store):
        """Test automatic doc type detection from file paths."""
        assert sqlite_store._detect_doc_type("README.md") == "readme"
        assert sqlite_store._detect_doc_type("README.rst") == "readme"
        assert sqlite_store._detect_doc_type("README.txt") == "readme"
        assert sqlite_store._detect_doc_type("CLAUDE.md") == "claude_md"
        assert sqlite_store._detect_doc_type("SKILL.md") == "skill"
        assert sqlite_store._detect_doc_type("ARCHITECTURE.md") == "architecture"
        assert sqlite_store._detect_doc_type("docs/guide.md") == "docs"
        assert sqlite_store._detect_doc_type("docs/sub/deep.md") == "docs"
        assert sqlite_store._detect_doc_type("notes.md") == "other"
        assert sqlite_store._detect_doc_type("contributing.md") == "other"

    def test_get_doc_index_by_path(self, sqlite_store, tmp_path):
        """Test querying a single doc index entry by file path."""
        repo_dir = tmp_path / "test-repo3"
        repo_dir.mkdir()
        (repo_dir / "README.md").write_text("# My Project\n\nHello world.", encoding="utf-8")
        (repo_dir / "CONTRIBUTING.md").write_text("# Contributing\n\nPlease contribute.", encoding="utf-8")

        sqlite_store.index_docs("test-repo3", repo_dir)

        entry = sqlite_store.get_doc_index_by_path("test-repo3", "README.md")
        assert entry is not None
        assert entry["doc_type"] == "readme"
        assert entry["title"] == "My Project"
        assert entry["size_bytes"] > 0

        # Non-existent file
        assert sqlite_store.get_doc_index_by_path("test-repo3", "NONEXISTENT.md") is None

    def test_index_docs_reindex(self, sqlite_store, tmp_path):
        """Test that re-indexing replaces old entries."""
        repo_dir = tmp_path / "test-repo4"
        repo_dir.mkdir()
        (repo_dir / "README.md").write_text("# V1", encoding="utf-8")

        count1 = sqlite_store.index_docs("test-repo4", repo_dir)
        assert count1 == 1

        # Add a new file and re-index
        (repo_dir / "CLAUDE.md").write_text("# V2", encoding="utf-8")
        count2 = sqlite_store.index_docs("test-repo4", repo_dir)
        assert count2 == 2

        entries = sqlite_store.get_doc_index("test-repo4")
        assert len(entries) == 2

    def test_doc_title_extraction(self, sqlite_store, tmp_path):
        """Test title extraction from first # heading."""
        repo_dir = tmp_path / "test-repo5"
        repo_dir.mkdir()

        # File with heading
        (repo_dir / "a.md").write_text("# Hello World\n\nBody text.", encoding="utf-8")
        # File with multiple # headings (should pick first)
        (repo_dir / "b.md").write_text("### Sub Heading\n\nBody.", encoding="utf-8")
        # File with no heading
        (repo_dir / "c.md").write_text("Just plain text.\nNo heading.", encoding="utf-8")

        sqlite_store.index_docs("test-repo5", repo_dir)
        entries = {e["file_path"]: e["title"] for e in sqlite_store.get_doc_index("test-repo5")}

        assert entries["a.md"] == "Hello World"
        assert entries["b.md"] == "Sub Heading"
        assert entries["c.md"] == ""


class TestDocStore:
    def test_write_and_read_doc(self, doc_store):
        doc_store.write_doc("test-repo", "ARCHITECTURE.md", "# Architecture\n\nTest content.")
        content = doc_store.read_doc("test-repo", "ARCHITECTURE.md")
        assert content == "# Architecture\n\nTest content."

    def test_read_nonexistent_doc(self, doc_store):
        assert doc_store.read_doc("test-repo", "NONEXISTENT.md") is None

    def test_list_docs(self, doc_store):
        doc_store.write_doc("test-repo", "ARCHITECTURE.md", "content")
        doc_store.write_doc("test-repo", "CORE_CHAIN.md", "content")
        doc_store.write_coverage("test-repo", {"test": True})
        docs = doc_store.list_docs("test-repo")
        assert "ARCHITECTURE.md" in docs
        assert "CORE_CHAIN.md" in docs
        assert "COVERAGE.json" in docs

    def test_delete_doc(self, doc_store):
        doc_store.write_doc("test-repo", "test.md", "content")
        assert doc_store.delete_doc("test-repo", "test.md") is True
        assert doc_store.read_doc("test-repo", "test.md") is None

    def test_coverage(self, doc_store):
        coverage = {
            "total_dimensions": 8,
            "covered_by_readme": ["overview"],
            "generated": ["architecture"],
        }
        doc_store.write_coverage("test-repo", coverage)
        result = doc_store.read_coverage("test-repo")
        assert result["total_dimensions"] == 8
        assert "overview" in result["covered_by_readme"]

    def test_skill_operations(self, doc_store):
        doc_store.write_skill("test-repo", "add-cache", "---\nname: add-cache\n---\n# Content")
        skills = doc_store.list_skills("test-repo")
        assert skills == ["add-cache"]

        content = doc_store.read_skill("test-repo", "add-cache")
        assert "add-cache" in content

        assert doc_store.delete_skill("test-repo", "add-cache") is True
        assert doc_store.list_skills("test-repo") == []

    def test_delete_repo(self, doc_store):
        doc_store.write_doc("test-repo", "test.md", "content")
        doc_store.write_skill("test-repo", "skill1", "content")
        assert doc_store.delete_repo("test-repo") is True
        assert doc_store.list_docs("test-repo") == []


class TestGuideCache:
    def test_cache_miss_returns_none(self, sqlite_store):
        result = sqlite_store.get_guide_cache("test-repo", "component_guide", "Foo")
        assert result is None

    def test_cache_write_and_read(self, sqlite_store):
        sqlite_store.set_guide_cache("test-repo", "component_guide", "Foo", '{"name":"Foo"}')
        result = sqlite_store.get_guide_cache("test-repo", "component_guide", "Foo")
        assert result == '{"name":"Foo"}'

    def test_cache_upsert(self, sqlite_store):
        sqlite_store.set_guide_cache("test-repo", "component_guide", "Foo", '{"v":1}')
        sqlite_store.set_guide_cache("test-repo", "component_guide", "Foo", '{"v":2}')
        result = sqlite_store.get_guide_cache("test-repo", "component_guide", "Foo")
        assert result == '{"v":2}'

    def test_cache_clear_by_repo(self, sqlite_store):
        sqlite_store.set_guide_cache("repo-a", "component_guide", "Foo", '{"a":1}')
        sqlite_store.set_guide_cache("repo-b", "component_guide", "Bar", '{"b":2}')
        sqlite_store.clear_guide_cache("repo-a")
        assert sqlite_store.get_guide_cache("repo-a", "component_guide", "Foo") is None
        assert sqlite_store.get_guide_cache("repo-b", "component_guide", "Bar") == '{"b":2}'

    def test_cache_with_module(self, sqlite_store):
        sqlite_store.set_guide_cache("test-repo", "component_guide", "Foo", '{"x":1}', module="biz_ui")
        assert sqlite_store.get_guide_cache("test-repo", "component_guide", "Foo") is None
        assert sqlite_store.get_guide_cache("test-repo", "component_guide", "Foo", module="biz_ui") == '{"x":1}'
