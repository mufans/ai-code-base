"""Tests for file watcher mechanism (P0-3)."""

import time
from pathlib import Path

import pytest

from codekb.storage.sqlite_store import SqliteStore, RepoRecord
from codekb.watcher.file_watcher import (
    RepoFileHandler,
    _ContentHashCache,
    _SOURCE_EXTENSIONS,
    _IGNORED_DIRS,
)


class TestContentHashCache:
    def test_detects_new_file(self, tmp_path):
        cache = _ContentHashCache()
        test_file = tmp_path / "test.py"
        test_file.write_text("print('hello')\n")

        assert cache.has_changed(str(test_file)) is True

    def test_detects_no_change(self, tmp_path):
        cache = _ContentHashCache()
        test_file = tmp_path / "test.py"
        test_file.write_text("print('hello')\n")

        # First check: changed (new)
        cache.has_changed(str(test_file))
        # Second check: not changed
        assert cache.has_changed(str(test_file)) is False

    def test_detects_content_change(self, tmp_path):
        cache = _ContentHashCache()
        test_file = tmp_path / "test.py"
        test_file.write_text("v1\n")

        cache.has_changed(str(test_file))

        # Modify file
        test_file.write_text("v2\n")
        assert cache.has_changed(str(test_file)) is True

    def test_detects_deletion(self, tmp_path):
        cache = _ContentHashCache()
        test_file = tmp_path / "test.py"
        test_file.write_text("content\n")

        cache.has_changed(str(test_file))

        test_file.unlink()
        assert cache.has_changed(str(test_file)) is True


class TestSourceExtensions:
    def test_python_tracked(self):
        assert ".py" in _SOURCE_EXTENSIONS

    def test_typescript_tracked(self):
        assert ".ts" in _SOURCE_EXTENSIONS
        assert ".tsx" in _SOURCE_EXTENSIONS

    def test_java_tracked(self):
        assert ".java" in _SOURCE_EXTENSIONS

    def test_markdown_tracked(self):
        assert ".md" in _SOURCE_EXTENSIONS

    def test_non_source_not_tracked(self):
        assert ".png" not in _SOURCE_EXTENSIONS
        assert ".json" not in _SOURCE_EXTENSIONS


class TestIgnoredDirs:
    def test_git_ignored(self):
        assert ".git" in _IGNORED_DIRS

    def test_node_modules_ignored(self):
        assert "node_modules" in _IGNORED_DIRS


class TestRepoFileHandler:
    def test_should_track_source_file(self, tmp_path):
        changes = []
        handler = RepoFileHandler("test", tmp_path, lambda r, f: changes.extend(f))

        assert handler._should_track("main.py") is True
        assert handler._should_track("app.ts") is True
        assert handler._should_track("style.css") is False
        assert handler._should_track("image.png") is False

    def test_should_ignore_node_modules(self, tmp_path):
        handler = RepoFileHandler("test", tmp_path, lambda r, f: None)

        assert handler._should_track("node_modules/pkg/index.js") is False
        assert handler._should_track("src/main.ts") is True

    def test_get_rel_path(self, tmp_path):
        handler = RepoFileHandler("test", tmp_path, lambda r, f: None)

        rel = handler._get_rel_path(str(tmp_path / "src" / "main.py"))
        assert rel == "src/main.py"

    def test_on_modified_triggers_callback(self, tmp_path):
        """Test that modifying a file triggers the callback after debounce."""
        collected_changes = []
        handler = RepoFileHandler("test", tmp_path, lambda r, f: collected_changes.append((r, f)))

        # Create a file
        test_file = tmp_path / "test.py"
        test_file.write_text("v1\n")

        # Simulate modification event
        handler.on_modified(type('Event', (), {
            'is_directory': False,
            'src_path': str(test_file),
        })())

        # Wait for debounce
        time.sleep(2.5)

        assert len(collected_changes) == 1
        assert collected_changes[0][0] == "test"
        assert "test.py" in collected_changes[0][1]

    def test_on_created_triggers_callback(self, tmp_path):
        """Test that creating a file triggers the callback after debounce."""
        collected_changes = []
        handler = RepoFileHandler("test", tmp_path, lambda r, f: collected_changes.append((r, f)))

        # Create a new file
        test_file = tmp_path / "new.py"
        test_file.write_text("def new(): pass\n")

        handler.on_created(type('Event', (), {
            'is_directory': False,
            'src_path': str(test_file),
        })())

        time.sleep(2.5)

        assert len(collected_changes) == 1
        assert "new.py" in collected_changes[0][1]

    def test_ignores_non_source_files(self, tmp_path):
        """Non-source files should not trigger callback."""
        collected_changes = []
        handler = RepoFileHandler("test", tmp_path, lambda r, f: collected_changes.append((r, f)))

        test_file = tmp_path / "style.css"
        test_file.write_text("body { color: red; }\n")

        handler.on_created(type('Event', (), {
            'is_directory': False,
            'src_path': str(test_file),
        })())

        time.sleep(2.5)
        assert len(collected_changes) == 0

    def test_debounce_batches_changes(self, tmp_path):
        """Multiple rapid changes should be batched into one callback."""
        collected_changes = []
        handler = RepoFileHandler("test", tmp_path, lambda r, f: collected_changes.append((r, f)))

        # Create multiple files rapidly
        for i in range(3):
            f = tmp_path / f"file{i}.py"
            f.write_text(f"def func{i}(): pass\n")
            handler.on_created(type('Event', (), {
                'is_directory': False,
                'src_path': str(f),
            })())

        time.sleep(2.5)

        # Should be batched into a single callback
        assert len(collected_changes) == 1
        assert len(collected_changes[0][1]) == 3
