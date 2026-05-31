"""SQLite storage for metadata.db and structure.db."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pydantic import BaseModel


# --- Data Models ---

class RepoRecord(BaseModel):
    """A registered repository."""
    id: Optional[int] = None
    name: str
    url: str
    local_path: str
    platform: str = ""
    branch: str = "main"
    language: str = ""
    framework: str = ""
    description: str = ""
    added_at: str = ""
    last_indexed_at: str = ""
    file_count: int = 0
    status: str = "registered"  # registered, indexing, indexed, error


class Symbol(BaseModel):
    """A code symbol (class, function, method, etc.)."""
    id: Optional[int] = None
    repo_name: str
    file_path: str
    name: str
    kind: str  # class, function, method, variable, constant, module
    signature: str = ""
    docstring: str = ""
    start_line: int = 0
    end_line: int = 0
    parent: str = ""
    language: str = ""
    source: str = ""  # the actual source code of the symbol


class CallRelation(BaseModel):
    """A function call relationship."""
    id: Optional[int] = None
    repo_name: str
    caller_file: str
    caller_name: str
    callee_name: str
    callee_file: str = ""
    line_number: int = 0


class ImportRecord(BaseModel):
    """An import statement."""
    id: Optional[int] = None
    repo_name: str
    file_path: str
    module: str
    imported_names: str = ""  # JSON list
    line_number: int = 0
    is_relative: bool = False


class FileEntry(BaseModel):
    """A file in the repo tree."""
    id: Optional[int] = None
    repo_name: str
    path: str
    language: str = ""
    is_entry_point: bool = False
    symbol_count: int = 0
    last_modified: str = ""


# --- SQLite Store ---

class SqliteStore:
    """Manages two SQLite databases: metadata.db and structure.db."""

    def __init__(self, index_dir: Path):
        self.index_dir = index_dir
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self._meta_path = index_dir / "metadata.db"
        self._struct_path = index_dir / "structure.db"
        self._init_databases()

    def _connect(self, db_path: Path) -> sqlite3.Connection:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_databases(self):
        """Create tables if they don't exist."""
        # metadata.db
        conn = self._connect(self._meta_path)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS repos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                url TEXT NOT NULL,
                local_path TEXT NOT NULL,
                platform TEXT DEFAULT '',
                branch TEXT DEFAULT 'main',
                language TEXT DEFAULT '',
                framework TEXT DEFAULT '',
                description TEXT DEFAULT '',
                added_at TEXT DEFAULT '',
                last_indexed_at TEXT DEFAULT '',
                file_count INTEGER DEFAULT 0,
                status TEXT DEFAULT 'registered'
            );

            CREATE TABLE IF NOT EXISTS index_status (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo_name TEXT NOT NULL,
                phase TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                started_at TEXT DEFAULT '',
                completed_at TEXT DEFAULT '',
                error_message TEXT DEFAULT '',
                items_processed INTEGER DEFAULT 0,
                FOREIGN KEY (repo_name) REFERENCES repos(name)
            );
        """)
        conn.commit()
        conn.close()

        # structure.db
        conn = self._connect(self._struct_path)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS symbols (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo_name TEXT NOT NULL,
                file_path TEXT NOT NULL,
                name TEXT NOT NULL,
                kind TEXT NOT NULL,
                signature TEXT DEFAULT '',
                docstring TEXT DEFAULT '',
                start_line INTEGER DEFAULT 0,
                end_line INTEGER DEFAULT 0,
                parent TEXT DEFAULT '',
                language TEXT DEFAULT '',
                source TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo_name TEXT NOT NULL,
                caller_file TEXT NOT NULL,
                caller_name TEXT NOT NULL,
                callee_name TEXT NOT NULL,
                callee_file TEXT DEFAULT '',
                line_number INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS imports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo_name TEXT NOT NULL,
                file_path TEXT NOT NULL,
                module TEXT NOT NULL,
                imported_names TEXT DEFAULT '',
                line_number INTEGER DEFAULT 0,
                is_relative INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS file_tree (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo_name TEXT NOT NULL,
                path TEXT NOT NULL,
                language TEXT DEFAULT '',
                is_entry_point INTEGER DEFAULT 0,
                symbol_count INTEGER DEFAULT 0,
                last_modified TEXT DEFAULT ''
            );

            CREATE INDEX IF NOT EXISTS idx_symbols_repo_file ON symbols(repo_name, file_path);
            CREATE INDEX IF NOT EXISTS idx_symbols_repo_name ON symbols(repo_name, name);
            CREATE INDEX IF NOT EXISTS idx_calls_repo ON calls(repo_name);
            CREATE INDEX IF NOT EXISTS idx_imports_repo ON imports(repo_name);
            CREATE INDEX IF NOT EXISTS idx_file_tree_repo ON file_tree(repo_name);
        """)
        conn.commit()
        conn.close()

    # --- Repo operations ---

    def register_repo(self, repo: RepoRecord) -> int:
        conn = self._connect(self._meta_path)
        now = datetime.now(timezone.utc).isoformat()
        cursor = conn.execute(
            """INSERT OR REPLACE INTO repos
               (name, url, local_path, platform, branch, language, framework,
                description, added_at, last_indexed_at, file_count, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (repo.name, repo.url, repo.local_path, repo.platform, repo.branch,
             repo.language, repo.framework, repo.description, now, "",
             repo.file_count, repo.status),
        )
        conn.commit()
        row_id = cursor.lastrowid
        conn.close()
        return row_id

    def get_repo(self, name: str) -> Optional[RepoRecord]:
        conn = self._connect(self._meta_path)
        row = conn.execute("SELECT * FROM repos WHERE name = ?", (name,)).fetchone()
        conn.close()
        if row is None:
            return None
        return RepoRecord(**dict(row))

    def list_repos(self) -> list[RepoRecord]:
        conn = self._connect(self._meta_path)
        rows = conn.execute("SELECT * FROM repos ORDER BY name").fetchall()
        conn.close()
        return [RepoRecord(**dict(r)) for r in rows]

    def update_repo(self, name: str, **kwargs) -> bool:
        if not kwargs:
            return False
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        values = list(kwargs.values()) + [name]
        conn = self._connect(self._meta_path)
        conn.execute(f"UPDATE repos SET {sets} WHERE name = ?", values)
        conn.commit()
        conn.close()
        return True

    def remove_repo(self, name: str) -> bool:
        conn = self._connect(self._meta_path)
        cursor = conn.execute("DELETE FROM repos WHERE name = ?", (name,))
        affected = cursor.rowcount
        conn.commit()
        conn.close()
        return affected > 0

    # --- Index status ---

    def set_index_status(self, repo_name: str, phase: str, status: str,
                         error_message: str = "", items_processed: int = 0):
        now = datetime.now(timezone.utc).isoformat()
        conn = self._connect(self._meta_path)
        conn.execute(
            """INSERT INTO index_status (repo_name, phase, status, started_at, completed_at, error_message, items_processed)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (repo_name, phase, status, now, now if status != "running" else "", error_message, items_processed),
        )
        conn.commit()
        conn.close()

    # --- Symbol operations ---

    def insert_symbols(self, symbols: list[Symbol]):
        if not symbols:
            return
        conn = self._connect(self._struct_path)
        conn.executemany(
            """INSERT INTO symbols (repo_name, file_path, name, kind, signature, docstring,
               start_line, end_line, parent, language, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [(s.repo_name, s.file_path, s.name, s.kind, s.signature, s.docstring,
              s.start_line, s.end_line, s.parent, s.language, s.source) for s in symbols],
        )
        conn.commit()
        conn.close()

    def get_symbols(self, repo_name: str, file_path: Optional[str] = None) -> list[Symbol]:
        conn = self._connect(self._struct_path)
        if file_path:
            rows = conn.execute(
                "SELECT * FROM symbols WHERE repo_name = ? AND file_path = ? ORDER BY start_line",
                (repo_name, file_path),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM symbols WHERE repo_name = ? ORDER BY file_path, start_line",
                (repo_name,),
            ).fetchall()
        conn.close()
        return [Symbol(**dict(r)) for r in rows]

    def get_symbol_by_name(self, repo_name: str, name: str) -> list[Symbol]:
        conn = self._connect(self._struct_path)
        rows = conn.execute(
            "SELECT * FROM symbols WHERE repo_name = ? AND name = ?",
            (repo_name, name),
        ).fetchall()
        conn.close()
        return [Symbol(**dict(r)) for r in rows]

    def delete_symbols_for_file(self, repo_name: str, file_path: str):
        conn = self._connect(self._struct_path)
        conn.execute(
            "DELETE FROM symbols WHERE repo_name = ? AND file_path = ?",
            (repo_name, file_path),
        )
        conn.commit()
        conn.close()

    # --- Call graph operations ---

    def insert_calls(self, calls: list[CallRelation]):
        if not calls:
            return
        conn = self._connect(self._struct_path)
        conn.executemany(
            """INSERT INTO calls (repo_name, caller_file, caller_name, callee_name, callee_file, line_number)
               VALUES (?, ?, ?, ?, ?, ?)""",
            [(c.repo_name, c.caller_file, c.caller_name, c.callee_name, c.callee_file, c.line_number) for c in calls],
        )
        conn.commit()
        conn.close()

    def get_calls_from(self, repo_name: str, caller_name: str) -> list[CallRelation]:
        conn = self._connect(self._struct_path)
        rows = conn.execute(
            "SELECT * FROM calls WHERE repo_name = ? AND caller_name = ?",
            (repo_name, caller_name),
        ).fetchall()
        conn.close()
        return [CallRelation(**dict(r)) for r in rows]

    def get_calls_to(self, repo_name: str, callee_name: str) -> list[CallRelation]:
        conn = self._connect(self._struct_path)
        rows = conn.execute(
            "SELECT * FROM calls WHERE repo_name = ? AND callee_name = ?",
            (repo_name, callee_name),
        ).fetchall()
        conn.close()
        return [CallRelation(**dict(r)) for r in rows]

    def delete_calls_for_file(self, repo_name: str, file_path: str):
        conn = self._connect(self._struct_path)
        conn.execute(
            "DELETE FROM calls WHERE repo_name = ? AND caller_file = ?",
            (repo_name, file_path),
        )
        conn.commit()
        conn.close()

    # --- Import operations ---

    def insert_imports(self, imports: list[ImportRecord]):
        if not imports:
            return
        conn = self._connect(self._struct_path)
        conn.executemany(
            """INSERT INTO imports (repo_name, file_path, module, imported_names, line_number, is_relative)
               VALUES (?, ?, ?, ?, ?, ?)""",
            [(i.repo_name, i.file_path, i.module, i.imported_names, i.line_number, int(i.is_relative)) for i in imports],
        )
        conn.commit()
        conn.close()

    def get_imports(self, repo_name: str, file_path: Optional[str] = None) -> list[ImportRecord]:
        conn = self._connect(self._struct_path)
        if file_path:
            rows = conn.execute(
                "SELECT * FROM imports WHERE repo_name = ? AND file_path = ? ORDER BY line_number",
                (repo_name, file_path),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM imports WHERE repo_name = ? ORDER BY file_path, line_number",
                (repo_name,),
            ).fetchall()
        conn.close()
        return [ImportRecord(**dict(r)) for r in rows]

    def delete_imports_for_file(self, repo_name: str, file_path: str):
        conn = self._connect(self._struct_path)
        conn.execute(
            "DELETE FROM imports WHERE repo_name = ? AND file_path = ?",
            (repo_name, file_path),
        )
        conn.commit()
        conn.close()

    # --- File tree operations ---

    def upsert_file_entry(self, entry: FileEntry):
        conn = self._connect(self._struct_path)
        existing = conn.execute(
            "SELECT id FROM file_tree WHERE repo_name = ? AND path = ?",
            (entry.repo_name, entry.path),
        ).fetchone()
        if existing:
            conn.execute(
                """UPDATE file_tree SET language=?, is_entry_point=?, symbol_count=?, last_modified=?
                   WHERE repo_name=? AND path=?""",
                (entry.language, int(entry.is_entry_point), entry.symbol_count, entry.last_modified,
                 entry.repo_name, entry.path),
            )
        else:
            conn.execute(
                """INSERT INTO file_tree (repo_name, path, language, is_entry_point, symbol_count, last_modified)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (entry.repo_name, entry.path, entry.language, int(entry.is_entry_point),
                 entry.symbol_count, entry.last_modified),
            )
        conn.commit()
        conn.close()

    def get_file_tree(self, repo_name: str) -> list[FileEntry]:
        conn = self._connect(self._struct_path)
        rows = conn.execute(
            "SELECT * FROM file_tree WHERE repo_name = ? ORDER BY path",
            (repo_name,),
        ).fetchall()
        conn.close()
        return [FileEntry(**{**dict(r), "is_entry_point": bool(r["is_entry_point"])}) for r in rows]

    def delete_file_entries(self, repo_name: str):
        conn = self._connect(self._struct_path)
        conn.execute("DELETE FROM file_tree WHERE repo_name = ?", (repo_name,))
        conn.commit()
        conn.close()

    def clear_repo_structure(self, repo_name: str):
        """Remove all structure data for a repo (for full re-index)."""
        conn = self._connect(self._struct_path)
        for table in ["symbols", "calls", "imports", "file_tree"]:
            conn.execute(f"DELETE FROM {table} WHERE repo_name = ?", (repo_name,))
        conn.commit()
        conn.close()
