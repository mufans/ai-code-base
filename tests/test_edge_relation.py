"""Tests for EdgeRelation model and inheritance extraction (P0-1)."""

import tempfile
from pathlib import Path

import pytest
import tree_sitter as ts

from codekb.storage.sqlite_store import SqliteStore, EdgeRelation, Symbol, ImportRecord
from codekb.indexers.tree_sitter import (
    TreeSitterIndexer,
    PythonStrategy,
    JavaScriptStrategy,
    JavaStrategy,
    KotlinStrategy,
    TypeScriptStrategy,
)
from codekb.core.module_detector import ModuleInfo


@pytest.fixture
def store(tmp_path):
    return SqliteStore(tmp_path / "index")


@pytest.fixture
def indexer(store):
    return TreeSitterIndexer(store)


# --- EdgeRelation model tests ---

class TestEdgeRelationModel:
    def test_create_edge(self):
        edge = EdgeRelation(
            repo_name="test",
            source_symbol="MyClass",
            source_file="main.py",
            target_symbol="BaseClass",
            kind="extends",
            line_number=10,
        )
        assert edge.source_symbol == "MyClass"
        assert edge.target_symbol == "BaseClass"
        assert edge.kind == "extends"

    def test_insert_and_query_edges(self, store):
        edges = [
            EdgeRelation(repo_name="test", source_symbol="Child", source_file="a.py",
                         target_symbol="Parent", kind="extends", line_number=5),
            EdgeRelation(repo_name="test", source_symbol="Child", source_file="a.py",
                         target_symbol="IRunnable", kind="implements", line_number=5),
        ]
        store.insert_edges(edges)

        # Query edges from source
        from_edges = store.get_edges_from("test", "Child")
        assert len(from_edges) == 2

        # Query edges to target
        to_edges = store.get_edges_to("test", "Parent")
        assert len(to_edges) == 1
        assert to_edges[0].kind == "extends"

    def test_query_edges_by_kind(self, store):
        edges = [
            EdgeRelation(repo_name="test", source_symbol="A", source_file="a.py",
                         target_symbol="B", kind="extends", line_number=1),
            EdgeRelation(repo_name="test", source_symbol="A", source_file="a.py",
                         target_symbol="I", kind="implements", line_number=1),
        ]
        store.insert_edges(edges)

        extends = store.get_edges_from("test", "A", kind="extends")
        assert len(extends) == 1
        assert extends[0].kind == "extends"

        implements = store.get_edges_from("test", "A", kind="implements")
        assert len(implements) == 1
        assert implements[0].kind == "implements"

    def test_delete_edges_for_file(self, store):
        edges = [
            EdgeRelation(repo_name="test", source_symbol="A", source_file="a.py",
                         target_symbol="B", kind="extends"),
            EdgeRelation(repo_name="test", source_symbol="C", source_file="c.py",
                         target_symbol="D", kind="extends"),
        ]
        store.insert_edges(edges)
        store.delete_edges_for_file("test", "a.py")

        remaining = store.get_edges_from("test", "C")
        assert len(remaining) == 1
        deleted = store.get_edges_from("test", "A")
        assert len(deleted) == 0

    def test_clear_repo_structure_deletes_edges(self, store):
        edges = [
            EdgeRelation(repo_name="test", source_symbol="A", source_file="a.py",
                         target_symbol="B", kind="extends"),
        ]
        store.insert_edges(edges)
        store.clear_repo_structure("test")
        assert store.get_edges_from("test", "A") == []

    def test_edges_with_repo_module(self, store):
        edges = [
            EdgeRelation(repo_name="test", source_symbol="A", source_file="a.py",
                         target_symbol="B", kind="extends", repo_module="frontend"),
            EdgeRelation(repo_name="test", source_symbol="C", source_file="c.py",
                         target_symbol="D", kind="extends", repo_module="backend"),
        ]
        store.insert_edges(edges)

        frontend = store.get_edges_from("test", "A", repo_module="frontend")
        assert len(frontend) == 1
        assert frontend[0].repo_module == "frontend"


# --- Python inheritance extraction ---

class TestPythonInheritance:
    def test_simple_inheritance(self):
        strategy = PythonStrategy()
        lang = strategy.language()
        parser = ts.Parser(lang)

        source = b'''class Animal:
    pass

class Dog(Animal):
    pass
'''
        tree = parser.parse(source)
        edges = strategy.extract_inheritance(tree, source, "test.py", "test")

        assert len(edges) == 1
        assert edges[0].source_symbol == "Dog"
        assert edges[0].target_symbol == "Animal"
        assert edges[0].kind == "extends"

    def test_interface_detection(self):
        strategy = PythonStrategy()
        lang = strategy.language()
        parser = ts.Parser(lang)

        source = b'''class MyInterface:
    pass

class MyABC(ABC):
    pass

class MyProtocol(Protocol):
    pass

class MyMixin(Mixin):
    pass

class Concrete(MyInterface):
    pass
'''
        tree = parser.parse(source)
        edges = strategy.extract_inheritance(tree, source, "test.py", "test")

        # MyABC, MyProtocol, MyMixin extend their bases
        # Concrete implements MyInterface
        assert len(edges) == 4

        # Check kind classification
        concrete_edge = next(e for e in edges if e.source_symbol == "Concrete")
        assert concrete_edge.kind == "implements"

        abc_edge = next(e for e in edges if e.source_symbol == "MyABC")
        assert abc_edge.kind == "implements"

    def test_multiple_inheritance(self):
        strategy = PythonStrategy()
        lang = strategy.language()
        parser = ts.Parser(lang)

        source = b'class Child(Parent1, Parent2):\n    pass\n'
        tree = parser.parse(source)
        edges = strategy.extract_inheritance(tree, source, "test.py", "test")

        assert len(edges) == 2
        targets = {e.target_symbol for e in edges}
        assert targets == {"Parent1", "Parent2"}


# --- JavaScript inheritance extraction ---

class TestJavaScriptInheritance:
    def test_extends(self):
        strategy = JavaScriptStrategy()
        lang = strategy.language()
        parser = ts.Parser(lang)

        source = b'''class Animal {}
class Dog extends Animal {}
'''
        tree = parser.parse(source)
        edges = strategy.extract_inheritance(tree, source, "test.js", "test")

        assert len(edges) == 1
        assert edges[0].source_symbol == "Dog"
        assert edges[0].target_symbol == "Animal"
        assert edges[0].kind == "extends"


# --- Java inheritance extraction ---

class TestJavaInheritance:
    def test_extends_and_implements(self):
        strategy = JavaStrategy()
        lang = strategy.language()
        parser = ts.Parser(lang)

        source = b'''public class Dog extends Animal implements Runnable, Serializable {
    public void run() {}
}
'''
        tree = parser.parse(source)
        edges = strategy.extract_inheritance(tree, source, "Test.java", "test")

        extends_edges = [e for e in edges if e.kind == "extends"]
        implements_edges = [e for e in edges if e.kind == "implements"]

        assert len(extends_edges) == 1
        assert extends_edges[0].target_symbol == "Animal"

        assert len(implements_edges) >= 1  # at least Runnable


# --- TypeScript inheritance extraction ---

class TestTypeScriptInheritance:
    def test_extends_and_implements(self):
        strategy = TypeScriptStrategy()
        lang = strategy.language()
        parser = ts.Parser(lang)

        source = b'''interface Serializable {
    serialize(): string;
}

class Animal {}
class Dog extends Animal implements Serializable {
    serialize() { return ""; }
}
'''
        tree = parser.parse(source)
        edges = strategy.extract_inheritance(tree, source, "test.ts", "test")

        extends_edges = [e for e in edges if e.kind == "extends"]
        implements_edges = [e for e in edges if e.kind == "implements"]

        assert len(extends_edges) == 1
        assert extends_edges[0].source_symbol == "Dog"
        assert extends_edges[0].target_symbol == "Animal"

        assert len(implements_edges) >= 1
        impl_targets = {e.target_symbol for e in implements_edges}
        assert "Serializable" in impl_targets


# --- Integration: index_repo extracts edges ---

class TestEdgeIndexing:
    def test_index_python_repo_extracts_edges(self, indexer, store, tmp_path):
        repo = tmp_path / "test-repo"
        repo.mkdir()

        (repo / "animals.py").write_text('''
class Animal:
    def speak(self):
        pass

class Dog(Animal):
    def speak(self):
        return "Woof"

class Cat(Animal):
    def speak(self):
        return "Meow"
''')

        stats = indexer.index_repo("test-repo", repo)
        assert stats["files_indexed"] == 1

        # Check edges in DB
        dog_edges = store.get_edges_from("test-repo", "Dog")
        assert len(dog_edges) == 1
        assert dog_edges[0].target_symbol == "Animal"
        assert dog_edges[0].kind == "extends"

        cat_edges = store.get_edges_from("test-repo", "Cat")
        assert len(cat_edges) == 1

        # Query reverse: who extends Animal?
        animal_parents = store.get_edges_to("test-repo", "Animal")
        sources = {e.source_symbol for e in animal_parents}
        assert "Dog" in sources
        assert "Cat" in sources

    def test_structure_query_includes_inheritance(self, indexer, store, tmp_path):
        from codekb.retrieval.structure_query import StructureQuery

        repo = tmp_path / "test-repo"
        repo.mkdir()

        (repo / "main.py").write_text('''
class Base:
    pass

class Child(Base):
    pass
''')

        indexer.index_repo("test-repo", repo)
        sq = StructureQuery(store)
        detail = sq.get_symbol_detail("test-repo", "Child")

        assert detail is not None
        assert "extends" in detail
        assert len(detail["extends"]) == 1
        assert detail["extends"][0]["target"] == "Base"

    def test_structure_query_includes_extended_by(self, indexer, store, tmp_path):
        from codekb.retrieval.structure_query import StructureQuery

        repo = tmp_path / "test-repo"
        repo.mkdir()

        (repo / "main.py").write_text('''
class Base:
    pass

class ChildA(Base):
    pass

class ChildB(Base):
    pass
''')

        indexer.index_repo("test-repo", repo)
        sq = StructureQuery(store)
        detail = sq.get_symbol_detail("test-repo", "Base")

        assert detail is not None
        assert "extended_by" in detail
        sources = {e["source"] for e in detail["extended_by"]}
        assert "ChildA" in sources
        assert "ChildB" in sources
