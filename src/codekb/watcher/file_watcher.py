"""File watcher using watchdog for automatic incremental re-indexing.

Features:
- 2000ms debounce to batch rapid changes
- content_hash change detection to skip no-op writes
- Watches all registered repos
- Triggers incremental_index on file changes
"""

from __future__ import annotations

import asyncio
import hashlib
import threading
import time
from pathlib import Path
from typing import Optional

from watchdog.events import FileSystemEventHandler, FileModifiedEvent, FileCreatedEvent, FileDeletedEvent
from watchdog.observers import Observer

from codekb.core.config import CodekbYamlConfig, ensure_data_dir, load_config, load_settings
from codekb.core.indexer import IndexOrchestrator
from codekb.core.module_detector import ModuleInfo
from codekb.core.repo_manager import RepoManager
from codekb.indexers.embedder import create_embedding_provider
from codekb.storage.doc_store import DocStore
from codekb.storage.sqlite_store import SqliteStore
from codekb.storage.vector_store import VectorStore


# File extensions that trigger re-indexing
_SOURCE_EXTENSIONS = {".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".kt", ".kts", ".swift", ".ets", ".go", ".rs", ".rb", ".php", ".md", ".rst"}
_IGNORED_DIRS = {".git", ".hg", ".svn", "node_modules", "__pycache__", "dist", "build", "out", ".venv", "vendor", "target", "oh_modules"}

# Debounce interval in seconds
DEBOUNCE_SECONDS = 2.0


class _ContentHashCache:
    """Tracks content hashes to detect actual changes vs touch-only events."""

    def __init__(self):
        self._hashes: dict[str, str] = {}

    def has_changed(self, file_path: str) -> bool:
        """Check if file content has changed since last check."""
        path = Path(file_path)
        if not path.exists():
            # File was deleted
            if file_path in self._hashes:
                del self._hashes[file_path]
                return True
            return False

        try:
            content = path.read_bytes()
            current_hash = hashlib.md5(content).hexdigest()
        except OSError:
            return False

        previous = self._hashes.get(file_path)
        self._hashes[file_path] = current_hash
        return previous != current_hash


class RepoFileHandler(FileSystemEventHandler):
    """Watchdog handler that collects changed files and debounces."""

    def __init__(self, repo_name: str, repo_path: Path, callback):
        super().__init__()
        self.repo_name = repo_name
        self.repo_path = repo_path
        self._callback = callback
        self._changed_files: set[str] = set()
        self._last_change_time: float = 0
        self._hash_cache = _ContentHashCache()
        self._lock = threading.Lock()
        self._debounce_timer: Optional[threading.Timer] = None

    def _should_track(self, path: str) -> bool:
        """Check if a file should be tracked for re-indexing."""
        p = Path(path)
        if p.suffix.lower() not in _SOURCE_EXTENSIONS:
            return False
        # Check if any parent dir is in ignored list
        for part in p.parts:
            if part in _IGNORED_DIRS:
                return False
        return True

    def _get_rel_path(self, path: str) -> Optional[str]:
        """Get relative path from repo root."""
        try:
            return str(Path(path).relative_to(self.repo_path))
        except ValueError:
            return None

    def on_modified(self, event):
        if event.is_directory:
            return
        self._handle_event(event.src_path)

    def on_created(self, event):
        if event.is_directory:
            return
        self._handle_event(event.src_path)

    def on_deleted(self, event):
        if event.is_directory:
            return
        rel_path = self._get_rel_path(event.src_path)
        if rel_path and self._should_track(rel_path):
            self._schedule_debounce(rel_path)

    def _handle_event(self, src_path: str):
        rel_path = self._get_rel_path(src_path)
        if rel_path is None or not self._should_track(rel_path):
            return
        # Content hash check
        if self._hash_cache.has_changed(src_path):
            self._schedule_debounce(rel_path)

    def _schedule_debounce(self, rel_path: str):
        """Add file to change set and reset debounce timer."""
        with self._lock:
            self._changed_files.add(rel_path)
            self._last_change_time = time.monotonic()

            # Cancel existing timer and start a new one
            if self._debounce_timer is not None:
                self._debounce_timer.cancel()
            self._debounce_timer = threading.Timer(DEBOUNCE_SECONDS, self._flush)
            self._debounce_timer.daemon = True
            self._debounce_timer.start()

    def _flush(self):
        """Flush accumulated changes to the callback."""
        with self._lock:
            if not self._changed_files:
                return
            files = sorted(self._changed_files)
            self._changed_files.clear()

        self._callback(self.repo_name, files)


class FileWatcher:
    """Watches registered repos for file changes and triggers incremental indexing."""

    def __init__(self, config: CodekbYamlConfig):
        self.config = config
        data_dir = ensure_data_dir(config)
        settings = load_settings()
        self.store = SqliteStore(data_dir / "index")
        self.vector_store = VectorStore(data_dir / "index" / "vectors")
        self.doc_store = DocStore(data_dir / "generated")
        self.repo_manager = RepoManager(config, self.store)
        self.orchestrator = IndexOrchestrator(
            config, self.store, self.vector_store, self.doc_store, self.repo_manager,
        )
        self._observer = Observer()
        self._handlers: list[RepoFileHandler] = []
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def start(self):
        """Start watching all registered repos."""
        repos = self.store.list_repos()
        for repo in repos:
            if repo.status != "indexed":
                continue
            repo_path = Path(repo.local_path)
            if not repo_path.exists():
                continue

            handler = RepoFileHandler(repo.name, repo_path, self._on_changes)
            self._handlers.append(handler)
            self._observer.schedule(handler, str(repo_path), recursive=True)

        self._observer.start()

    def stop(self):
        """Stop watching."""
        self._observer.stop()
        self._observer.join(timeout=5)

    def _on_changes(self, repo_name: str, changed_files: list[str]):
        """Callback invoked by debounce handler with batched file changes."""
        print(f"[codekb watch] {repo_name}: {len(changed_files)} files changed")

        # Run incremental index in a new event loop (called from watchdog thread)
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(
                self.orchestrator.incremental_index(repo_name, changed_files)
            )
            print(f"[codekb watch] {repo_name}: re-indexed {result}")
        except Exception as e:
            print(f"[codekb watch] {repo_name}: error during re-index: {e}")
        finally:
            loop.close()

    def watch_forever(self):
        """Start watching and block until interrupted."""
        self.start()
        try:
            while self._observer.is_alive():
                self._observer.join(timeout=1)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()
