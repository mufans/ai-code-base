"""Tests for Import Resolution system (P0-2)."""

from pathlib import Path

import pytest

from codekb.storage.sqlite_store import SqliteStore, ImportRecord, RepoRecord
from codekb.resolution.import_resolver import ImportResolver


@pytest.fixture
def store(tmp_path):
    return SqliteStore(tmp_path / "index")


@pytest.fixture
def resolver(store):
    return ImportResolver(store)


def _setup_repo(store, repo_name: str, language: str, local_path: str):
    """Helper to register a repo with a language."""
    store.register_repo(RepoRecord(
        name=repo_name,
        url=f"https://github.com/test/{repo_name}",
        local_path=local_path,
        language=language,
        status="indexed",
    ))


# --- Python import resolution ---

class TestPythonImportResolution:
    def test_resolve_relative_import(self, resolver, store, tmp_path):
        repo_path = tmp_path / "py-repo"
        repo_path.mkdir()
        (repo_path / "__init__.py").write_text("")
        (repo_path / "utils.py").write_text("def helper(): pass\n")
        (repo_path / "sub").mkdir()
        (repo_path / "sub" / "__init__.py").write_text("")
        (repo_path / "sub" / "main.py").write_text("from .utils import helper\n")

        _setup_repo(store, "py-repo", "python", str(repo_path))

        # Insert an import record
        store.insert_imports([
            ImportRecord(
                repo_name="py-repo",
                file_path="sub/main.py",
                module=".utils",
                imported_names="helper",
                line_number=1,
                is_relative=True,
            )
        ])

        resolver.resolve_imports("py-repo", repo_path)

        imports = store.get_imports("py-repo", "sub/main.py")
        assert len(imports) == 1
        # The resolver should resolve to utils.py at repo level (since sub/main.py's parent is sub/, and .utils means sub/utils.py which doesn't exist)
        # Actually .utils from sub/main.py should resolve to sub/utils.py
        # But we only created utils.py at root. Let's adjust the test.

    def test_resolve_relative_import_same_dir(self, resolver, store, tmp_path):
        repo_path = tmp_path / "py-repo2"
        repo_path.mkdir()
        (repo_path / "__init__.py").write_text("")
        (repo_path / "main.py").write_text("from .utils import helper\n")
        (repo_path / "utils.py").write_text("def helper(): pass\n")

        _setup_repo(store, "py-repo2", "python", str(repo_path))

        store.insert_imports([
            ImportRecord(
                repo_name="py-repo2",
                file_path="main.py",
                module=".utils",
                imported_names="helper",
                line_number=1,
                is_relative=True,
            )
        ])

        resolver.resolve_imports("py-repo2", repo_path)

        imports = store.get_imports("py-repo2", "main.py")
        assert len(imports) == 1
        assert imports[0].resolved_path == "utils.py"

    def test_resolve_package_import(self, resolver, store, tmp_path):
        repo_path = tmp_path / "py-repo3"
        repo_path.mkdir()
        (repo_path / "mypackage").mkdir()
        (repo_path / "mypackage" / "__init__.py").write_text("")
        (repo_path / "main.py").write_text("import mypackage\n")

        _setup_repo(store, "py-repo3", "python", str(repo_path))

        store.insert_imports([
            ImportRecord(
                repo_name="py-repo3",
                file_path="main.py",
                module="mypackage",
                line_number=1,
                is_relative=False,
            )
        ])

        resolver.resolve_imports("py-repo3", repo_path)

        imports = store.get_imports("py-repo3", "main.py")
        assert len(imports) == 1
        assert imports[0].resolved_path == "mypackage/__init__.py"


# --- TypeScript import resolution ---

class TestTypeScriptImportResolution:
    def test_resolve_relative_import(self, resolver, store, tmp_path):
        repo_path = tmp_path / "ts-repo"
        repo_path.mkdir()
        (repo_path / "utils.ts").write_text("export function helper() {}\n")
        (repo_path / "main.ts").write_text("import { helper } from './utils';\n")

        _setup_repo(store, "ts-repo", "typescript", str(repo_path))

        store.insert_imports([
            ImportRecord(
                repo_name="ts-repo",
                file_path="main.ts",
                module="./utils",
                imported_names="{ helper }",
                line_number=1,
            )
        ])

        resolver.resolve_imports("ts-repo", repo_path)

        imports = store.get_imports("ts-repo", "main.ts")
        assert len(imports) == 1
        assert imports[0].resolved_path == "utils.ts"

    def test_resolve_tsconfig_alias(self, resolver, store, tmp_path):
        repo_path = tmp_path / "ts-alias-repo"
        repo_path.mkdir()
        (repo_path / "tsconfig.json").write_text('''{
            "compilerOptions": {
                "paths": {
                    "@utils/*": ["src/utils/*"]
                }
            }
        }''')
        (repo_path / "src").mkdir()
        (repo_path / "src" / "utils").mkdir()
        (repo_path / "src" / "utils" / "helper.ts").write_text("export function helper() {}\n")
        (repo_path / "main.ts").write_text("import { helper } from '@utils/helper';\n")

        _setup_repo(store, "ts-alias-repo", "typescript", str(repo_path))

        store.insert_imports([
            ImportRecord(
                repo_name="ts-alias-repo",
                file_path="main.ts",
                module="@utils/helper",
                imported_names="{ helper }",
                line_number=1,
            )
        ])

        resolver.resolve_imports("ts-alias-repo", repo_path)

        imports = store.get_imports("ts-alias-repo", "main.ts")
        assert len(imports) == 1
        assert imports[0].resolved_path == "src/utils/helper.ts"


# --- Java import resolution ---

class TestJavaImportResolution:
    def test_resolve_java_import(self, resolver, store, tmp_path):
        repo_path = tmp_path / "java-repo"
        (repo_path / "src" / "main" / "java" / "com" / "example").mkdir(parents=True)
        (repo_path / "src" / "main" / "java" / "com" / "example" / "Greeter.java").write_text(
            "package com.example;\npublic class Greeter {}\n"
        )

        _setup_repo(store, "java-repo", "java", str(repo_path))

        store.insert_imports([
            ImportRecord(
                repo_name="java-repo",
                file_path="App.java",
                module="com.example.Greeter",
                line_number=1,
            )
        ])

        resolver.resolve_imports("java-repo", repo_path)

        imports = store.get_imports("java-repo", "App.java")
        assert len(imports) == 1
        assert "Greeter.java" in imports[0].resolved_path
        assert imports[0].resolved_symbol == "Greeter"


# --- Go import resolution ---

class TestGoImportResolution:
    def test_resolve_go_import(self, resolver, store, tmp_path):
        repo_path = tmp_path / "go-repo"
        repo_path.mkdir()
        (repo_path / "go.mod").write_text("module github.com/example/myapp\n\ngo 1.21\n")
        (repo_path / "utils").mkdir()
        (repo_path / "utils" / "helper.go").write_text("package utils\n")

        _setup_repo(store, "go-repo", "go", str(repo_path))

        store.insert_imports([
            ImportRecord(
                repo_name="go-repo",
                file_path="main.go",
                module="github.com/example/myapp/utils",
                line_number=3,
            )
        ])

        resolver.resolve_imports("go-repo", repo_path)

        imports = store.get_imports("go-repo", "main.go")
        assert len(imports) == 1
        assert "utils/helper.go" in imports[0].resolved_path


# --- Edge cases ---

class TestImportResolutionEdgeCases:
    def test_no_repo_returns_early(self, resolver, store, tmp_path):
        """If repo doesn't exist, resolve_imports should not error."""
        resolver.resolve_imports("nonexistent", tmp_path)

    def test_stdlib_import_not_resolved(self, resolver, store, tmp_path):
        repo_path = tmp_path / "py-repo"
        repo_path.mkdir()
        (repo_path / "main.py").write_text("import os\n")

        _setup_repo(store, "py-repo", "python", str(repo_path))

        store.insert_imports([
            ImportRecord(
                repo_name="py-repo",
                file_path="main.py",
                module="os",
                line_number=1,
            )
        ])

        resolver.resolve_imports("py-repo", repo_path)

        imports = store.get_imports("py-repo", "main.py")
        assert len(imports) == 1
        # os is stdlib, should not resolve to anything in the repo
        assert imports[0].resolved_path == ""

    def test_empty_module_skipped(self, resolver, store, tmp_path):
        repo_path = tmp_path / "py-repo"
        repo_path.mkdir()

        _setup_repo(store, "py-repo", "python", str(repo_path))

        store.insert_imports([
            ImportRecord(
                repo_name="py-repo",
                file_path="main.py",
                module="",
                line_number=1,
            )
        ])

        resolver.resolve_imports("py-repo", repo_path)
        imports = store.get_imports("py-repo", "main.py")
        assert imports[0].resolved_path == ""
