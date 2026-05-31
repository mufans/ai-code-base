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
