"""SQLite storage for metadata.db and structure.db."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar, Optional

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
    repo_module: str = ""
    is_exported: bool = False


class CallRelation(BaseModel):
    """A function call relationship."""
    id: Optional[int] = None
    repo_name: str
    caller_file: str
    caller_name: str
    callee_name: str
    callee_file: str = ""
    line_number: int = 0
    repo_module: str = ""


class EdgeRelation(BaseModel):
    """A typed edge between two symbols (extends, implements, references, overrides, type_of, calls)."""
    id: Optional[int] = None
    repo_name: str
    source_symbol: str  # fully-qualified or local name of the source symbol
    source_file: str = ""
    target_symbol: str  # name of the target symbol
    target_file: str = ""
    kind: str  # calls, extends, implements, references, overrides, type_of
    line_number: int = 0
    repo_module: str = ""


class ImportRecord(BaseModel):
    """An import statement."""
    id: Optional[int] = None
    repo_name: str
    file_path: str
    module: str
    imported_names: str = ""  # JSON list
    line_number: int = 0
    is_relative: bool = False
    repo_module: str = ""
    resolved_path: str = ""  # resolved file path (set by import resolver)
    resolved_symbol: str = ""  # resolved symbol name (set by import resolver)


class FileEntry(BaseModel):
    """A file in the repo tree."""
    id: Optional[int] = None
    repo_name: str
    path: str
    language: str = ""
    is_entry_point: bool = False
    symbol_count: int = 0
    last_modified: str = ""
    repo_module: str = ""


# --- SQLite Store ---

class SqliteStore:
    """Manages two SQLite databases: metadata.db and structure.db."""

    _IGNORED_DIRS: ClassVar[frozenset[str]] = frozenset({
        ".git", ".hg", ".svn",
        "node_modules", "build", "dist", "out",
        ".venv", "__pycache__",
        "oh_modules",
    })

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
                status TEXT DEFAULT 'registered',
                modules_json TEXT DEFAULT '[]'
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

        # Ensure doc_index table exists (idempotent migration)
        self._ensure_doc_index_table()

        # Ensure guide_cache table exists
        self._ensure_guide_cache_table()

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
                source TEXT DEFAULT '',
                repo_module TEXT NOT NULL DEFAULT '',
                is_exported INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo_name TEXT NOT NULL,
                caller_file TEXT NOT NULL,
                caller_name TEXT NOT NULL,
                callee_name TEXT NOT NULL,
                callee_file TEXT DEFAULT '',
                line_number INTEGER DEFAULT 0,
                repo_module TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS imports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo_name TEXT NOT NULL,
                file_path TEXT NOT NULL,
                module TEXT NOT NULL,
                imported_names TEXT DEFAULT '',
                line_number INTEGER DEFAULT 0,
                is_relative INTEGER DEFAULT 0,
                repo_module TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo_name TEXT NOT NULL,
                source_symbol TEXT NOT NULL,
                source_file TEXT DEFAULT '',
                target_symbol TEXT NOT NULL,
                target_file TEXT DEFAULT '',
                kind TEXT NOT NULL,
                line_number INTEGER DEFAULT 0,
                repo_module TEXT NOT NULL DEFAULT ''
            );

            CREATE INDEX IF NOT EXISTS idx_edges_repo ON edges(repo_name);
            CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(repo_name, source_symbol);
            CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(repo_name, target_symbol);
            CREATE INDEX IF NOT EXISTS idx_edges_kind ON edges(repo_name, kind);
            CREATE INDEX IF NOT EXISTS idx_edges_repo_module ON edges(repo_name, repo_module);

            CREATE TABLE IF NOT EXISTS file_tree (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo_name TEXT NOT NULL,
                path TEXT NOT NULL,
                language TEXT DEFAULT '',
                is_entry_point INTEGER DEFAULT 0,
                symbol_count INTEGER DEFAULT 0,
                last_modified TEXT DEFAULT '',
                repo_module TEXT NOT NULL DEFAULT ''
            );

            CREATE INDEX IF NOT EXISTS idx_symbols_repo_file ON symbols(repo_name, file_path);
            CREATE INDEX IF NOT EXISTS idx_symbols_repo_name ON symbols(repo_name, name);
            CREATE INDEX IF NOT EXISTS idx_symbols_repo_module ON symbols(repo_name, repo_module);
            CREATE INDEX IF NOT EXISTS idx_calls_repo ON calls(repo_name);
            CREATE INDEX IF NOT EXISTS idx_calls_repo_module ON calls(repo_name, repo_module);
            CREATE INDEX IF NOT EXISTS idx_imports_repo ON imports(repo_name);
            CREATE INDEX IF NOT EXISTS idx_imports_repo_module ON imports(repo_name, repo_module);
            CREATE INDEX IF NOT EXISTS idx_file_tree_repo ON file_tree(repo_name);
            CREATE INDEX IF NOT EXISTS idx_file_tree_repo_module ON file_tree(repo_name, repo_module);
        """)
        conn.commit()
        conn.close()

        # Migrate existing databases
        self._migrate_add_repo_module()
        self._migrate_add_is_exported()
        self._migrate_add_import_resolution()

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
        # Delete dependent records first to satisfy FOREIGN KEY constraints
        conn.execute("DELETE FROM index_status WHERE repo_name = ?", (name,))
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

    def get_index_status(self, repo_name: str) -> list[dict]:
        """Get index status records for a repo. Returns empty list if never indexed."""
        conn = self._connect(self._meta_path)
        cursor = conn.execute(
            "SELECT phase, status FROM index_status WHERE repo_name = ?",
            (repo_name,),
        )
        results = [{"phase": row[0], "status": row[1]} for row in cursor.fetchall()]
        conn.close()
        return results

    # --- Symbol operations ---

    def insert_symbols(self, symbols: list[Symbol]):
        if not symbols:
            return
        conn = self._connect(self._struct_path)
        conn.executemany(
            """INSERT INTO symbols (repo_name, file_path, name, kind, signature, docstring,
               start_line, end_line, parent, language, source, repo_module, is_exported)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [(s.repo_name, s.file_path, s.name, s.kind, s.signature, s.docstring,
              s.start_line, s.end_line, s.parent, s.language, s.source, s.repo_module,
              int(s.is_exported)) for s in symbols],
        )
        conn.commit()
        conn.close()

    def get_symbols(self, repo_name: str, file_path: Optional[str] = None,
                    repo_module: Optional[str] = None,
                    exported_only: bool = False) -> list[Symbol]:
        conn = self._connect(self._struct_path)
        query = "SELECT * FROM symbols WHERE repo_name = ?"
        params: list = [repo_name]

        if file_path:
            # Support both exact file match and directory prefix match
            if '.' in Path(file_path).name:
                # Has extension → exact file match
                query += " AND file_path = ?"
                params.append(file_path)
            else:
                # No extension → directory prefix match
                query += " AND (file_path = ? OR file_path LIKE ?)"
                params.append(file_path)
                params.append(file_path + "/%")
        if repo_module is not None:
            query += " AND repo_module = ?"
            params.append(repo_module)
        if exported_only:
            query += " AND is_exported = 1"

        query += " ORDER BY file_path, start_line"
        rows = conn.execute(query, params).fetchall()
        conn.close()
        return [Symbol(**dict(r)) for r in rows]

    def get_symbol_by_name(self, repo_name: str, name: str,
                           repo_module: Optional[str] = None) -> list[Symbol]:
        conn = self._connect(self._struct_path)
        query = "SELECT * FROM symbols WHERE repo_name = ? AND name = ?"
        params: list = [repo_name, name]

        if repo_module is not None:
            query += " AND repo_module = ?"
            params.append(repo_module)

        rows = conn.execute(query, params).fetchall()
        conn.close()
        return [Symbol(**dict(r)) for r in rows]

    def find_symbol_across_repos(self, name: str) -> list[Symbol]:
        """Cross-repo exact symbol search by name."""
        conn = self._connect(self._struct_path)
        rows = conn.execute(
            "SELECT * FROM symbols WHERE name = ? ORDER BY repo_name, repo_module",
            (name,),
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
            """INSERT INTO calls (repo_name, caller_file, caller_name, callee_name, callee_file, line_number, repo_module)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [(c.repo_name, c.caller_file, c.caller_name, c.callee_name, c.callee_file, c.line_number, c.repo_module) for c in calls],
        )
        conn.commit()
        conn.close()

    def get_calls_from(self, repo_name: str, caller_name: str,
                       repo_module: Optional[str] = None) -> list[CallRelation]:
        conn = self._connect(self._struct_path)
        query = "SELECT * FROM calls WHERE repo_name = ? AND caller_name = ?"
        params: list = [repo_name, caller_name]

        if repo_module is not None:
            query += " AND repo_module = ?"
            params.append(repo_module)

        rows = conn.execute(query, params).fetchall()
        conn.close()
        return [CallRelation(**dict(r)) for r in rows]

    def get_calls_to(self, repo_name: str, callee_name: str,
                     repo_module: Optional[str] = None) -> list[CallRelation]:
        conn = self._connect(self._struct_path)
        query = "SELECT * FROM calls WHERE repo_name = ? AND callee_name = ?"
        params: list = [repo_name, callee_name]

        if repo_module is not None:
            query += " AND repo_module = ?"
            params.append(repo_module)

        rows = conn.execute(query, params).fetchall()
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
            """INSERT INTO imports (repo_name, file_path, module, imported_names, line_number, is_relative, repo_module, resolved_path, resolved_symbol)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [(i.repo_name, i.file_path, i.module, i.imported_names, i.line_number, int(i.is_relative), i.repo_module, i.resolved_path, i.resolved_symbol) for i in imports],
        )
        conn.commit()
        conn.close()

    def get_imports(self, repo_name: str, file_path: Optional[str] = None,
                    repo_module: Optional[str] = None) -> list[ImportRecord]:
        conn = self._connect(self._struct_path)
        query = "SELECT * FROM imports WHERE repo_name = ?"
        params: list = [repo_name]

        if file_path:
            query += " AND file_path = ?"
            params.append(file_path)
        if repo_module is not None:
            query += " AND repo_module = ?"
            params.append(repo_module)

        query += " ORDER BY file_path, line_number"
        rows = conn.execute(query, params).fetchall()
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

    # --- Edge operations ---

    def insert_edges(self, edges: list[EdgeRelation]):
        if not edges:
            return
        conn = self._connect(self._struct_path)
        conn.executemany(
            """INSERT INTO edges (repo_name, source_symbol, source_file, target_symbol, target_file, kind, line_number, repo_module)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [(e.repo_name, e.source_symbol, e.source_file, e.target_symbol, e.target_file, e.kind, e.line_number, e.repo_module) for e in edges],
        )
        conn.commit()
        conn.close()

    def get_edges_from(self, repo_name: str, source_symbol: str,
                       kind: Optional[str] = None,
                       repo_module: Optional[str] = None) -> list[EdgeRelation]:
        conn = self._connect(self._struct_path)
        query = "SELECT * FROM edges WHERE repo_name = ? AND source_symbol = ?"
        params: list = [repo_name, source_symbol]

        if kind is not None:
            query += " AND kind = ?"
            params.append(kind)
        if repo_module is not None:
            query += " AND repo_module = ?"
            params.append(repo_module)

        rows = conn.execute(query, params).fetchall()
        conn.close()
        return [EdgeRelation(**dict(r)) for r in rows]

    def get_edges_to(self, repo_name: str, target_symbol: str,
                     kind: Optional[str] = None,
                     repo_module: Optional[str] = None) -> list[EdgeRelation]:
        conn = self._connect(self._struct_path)
        query = "SELECT * FROM edges WHERE repo_name = ? AND target_symbol = ?"
        params: list = [repo_name, target_symbol]

        if kind is not None:
            query += " AND kind = ?"
            params.append(kind)
        if repo_module is not None:
            query += " AND repo_module = ?"
            params.append(repo_module)

        rows = conn.execute(query, params).fetchall()
        conn.close()
        return [EdgeRelation(**dict(r)) for r in rows]

    def delete_edges_for_file(self, repo_name: str, file_path: str):
        conn = self._connect(self._struct_path)
        conn.execute(
            "DELETE FROM edges WHERE repo_name = ? AND source_file = ?",
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
                """UPDATE file_tree SET language=?, is_entry_point=?, symbol_count=?, last_modified=?, repo_module=?
                   WHERE repo_name=? AND path=?""",
                (entry.language, int(entry.is_entry_point), entry.symbol_count, entry.last_modified,
                 entry.repo_module, entry.repo_name, entry.path),
            )
        else:
            conn.execute(
                """INSERT INTO file_tree (repo_name, path, language, is_entry_point, symbol_count, last_modified, repo_module)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (entry.repo_name, entry.path, entry.language, int(entry.is_entry_point),
                 entry.symbol_count, entry.last_modified, entry.repo_module),
            )
        conn.commit()
        conn.close()

    def get_file_tree(self, repo_name: str, repo_module: Optional[str] = None) -> list[FileEntry]:
        conn = self._connect(self._struct_path)
        query = "SELECT * FROM file_tree WHERE repo_name = ?"
        params: list = [repo_name]

        if repo_module is not None:
            query += " AND repo_module = ?"
            params.append(repo_module)

        query += " ORDER BY path"
        rows = conn.execute(query, params).fetchall()
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
        for table in ["symbols", "calls", "imports", "edges", "file_tree"]:
            conn.execute(f"DELETE FROM {table} WHERE repo_name = ?", (repo_name,))
        conn.commit()
        conn.close()

    # --- Doc index operations ---

    def _ensure_doc_index_table(self):
        """Create doc_index table if it doesn't exist (idempotent)."""
        conn = self._connect(self._struct_path)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS doc_index (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo_name TEXT NOT NULL,
                file_path TEXT NOT NULL,
                full_path TEXT NOT NULL,
                doc_type TEXT NOT NULL DEFAULT 'other',
                title TEXT DEFAULT '',
                size_bytes INTEGER DEFAULT 0,
                created_at TEXT DEFAULT '',
                UNIQUE(repo_name, file_path)
            );
            CREATE INDEX IF NOT EXISTS idx_doc_index_repo ON doc_index(repo_name);
            CREATE INDEX IF NOT EXISTS idx_doc_index_type ON doc_index(repo_name, doc_type);
        """)
        conn.commit()
        conn.close()

    def _ensure_guide_cache_table(self):
        """Create guide_cache table if it doesn't exist (idempotent)."""
        conn = self._connect(self._meta_path)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS guide_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo_name TEXT NOT NULL,
                tool_type TEXT NOT NULL,
                query_key TEXT NOT NULL,
                module TEXT DEFAULT '',
                result_json TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                UNIQUE(repo_name, tool_type, query_key, module)
            );
            CREATE INDEX IF NOT EXISTS idx_guide_cache_lookup
                ON guide_cache(repo_name, tool_type, query_key, module);
        """)
        conn.commit()
        conn.close()

    @staticmethod
    def _detect_doc_type(file_path: str) -> str:
        """Auto-detect doc type from file path."""
        basename = Path(file_path).name.upper()
        if basename in ("README.MD", "README.RST", "README.TXT", "README"):
            return "readme"
        if basename == "CLAUDE.MD":
            return "claude_md"
        if basename == "SKILL.MD":
            return "skill"
        if basename == "ARCHITECTURE.MD":
            return "architecture"
        parts = Path(file_path).parts
        if any(p.lower() == "docs" for p in parts):
            return "docs"
        return "other"

    @staticmethod
    def _extract_title(file_path: Path) -> str:
        """Extract title from first # heading in a markdown file."""
        try:
            with open(file_path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("#"):
                        return line.lstrip("#").strip()
                    if line:
                        break
        except (OSError, IOError):
            pass
        return ""

    def index_docs(self, repo_name: str, repo_path: Path) -> int:
        """Recursively scan and index md documents in a repo. Returns count of indexed docs."""
        conn = self._connect(self._struct_path)

        # Delete existing entries for this repo
        conn.execute("DELETE FROM doc_index WHERE repo_name = ?", (repo_name,))

        md_files: list[tuple[str, str, str, int, str]] = []

        for md_file in repo_path.rglob("*.md"):
            # Skip files inside ignored directories
            if any(part in self._IGNORED_DIRS for part in md_file.relative_to(repo_path).parts):
                continue
            rel_path = str(md_file.relative_to(repo_path))
            full_path = str(md_file)
            doc_type = self._detect_doc_type(rel_path)
            title = self._extract_title(md_file)
            size = md_file.stat().st_size
            md_files.append((rel_path, full_path, doc_type, size, title))

        now = datetime.now(timezone.utc).isoformat()
        conn.executemany(
            """INSERT OR REPLACE INTO doc_index
               (repo_name, file_path, full_path, doc_type, title, size_bytes, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [(repo_name, rel, full, dt, title, sz, now) for rel, full, dt, sz, title in md_files],
        )
        conn.commit()
        conn.close()
        return len(md_files)

    def get_doc_index(self, repo_name: str, doc_type: str | None = None) -> list[dict]:
        """Get doc index list for a repo, optionally filtered by type."""
        conn = self._connect(self._struct_path)
        if doc_type:
            rows = conn.execute(
                "SELECT * FROM doc_index WHERE repo_name = ? AND doc_type = ? ORDER BY file_path",
                (repo_name, doc_type),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM doc_index WHERE repo_name = ? ORDER BY file_path",
                (repo_name,),
            ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_doc_index_by_path(self, repo_name: str, file_path: str) -> dict | None:
        """Get a single doc index entry by repo and file path."""
        conn = self._connect(self._struct_path)
        row = conn.execute(
            "SELECT * FROM doc_index WHERE repo_name = ? AND file_path = ?",
            (repo_name, file_path),
        ).fetchone()
        conn.close()
        return dict(row) if row else None

    # --- Migration ---

    def _migrate_add_repo_module(self):
        """Add repo_module column to existing tables (idempotent)."""
        for db_path in [self._meta_path, self._struct_path]:
            conn = self._connect(db_path)
            if db_path == self._meta_path:
                # Add modules_json to repos table
                try:
                    conn.execute("ALTER TABLE repos ADD COLUMN modules_json TEXT DEFAULT '[]'")
                except sqlite3.OperationalError:
                    pass  # Column already exists
            else:
                for table in ["symbols", "calls", "imports", "file_tree"]:
                    try:
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN repo_module TEXT NOT NULL DEFAULT ''")
                    except sqlite3.OperationalError:
                        pass  # Column already exists
            conn.commit()
            conn.close()

    def _migrate_add_is_exported(self):
        """Add is_exported column to symbols table (idempotent)."""
        conn = self._connect(self._struct_path)
        try:
            conn.execute("ALTER TABLE symbols ADD COLUMN is_exported INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass  # Column already exists
        conn.commit()
        conn.close()

    def _migrate_add_import_resolution(self):
        """Add resolved_path and resolved_symbol columns to imports table (idempotent)."""
        conn = self._connect(self._struct_path)
        for col in ["resolved_path", "resolved_symbol"]:
            try:
                conn.execute(f"ALTER TABLE imports ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
            except sqlite3.OperationalError:
                pass  # Column already exists
        conn.commit()
        conn.close()

    def mark_symbols_exported(self, repo_name: str, updates: list[tuple[str, str]]):
        """Batch mark symbols as exported.

        updates: list of (file_path, symbol_name) tuples.
        """
        if not updates:
            return
        conn = self._connect(self._struct_path)
        conn.executemany(
            "UPDATE symbols SET is_exported = 1 WHERE repo_name = ? AND file_path = ? AND name = ?",
            [(repo_name, fp, name) for fp, name in updates],
        )
        conn.commit()
        conn.close()

    # --- Guide cache operations ---

    def get_guide_cache(self, repo_name: str, tool_type: str, query_key: str,
                        module: str = "") -> Optional[str]:
        """Get cached guide result. Returns result_json string or None."""
        conn = self._connect(self._meta_path)
        row = conn.execute(
            "SELECT result_json FROM guide_cache WHERE repo_name=? AND tool_type=? AND query_key=? AND module=?",
            (repo_name, tool_type, query_key, module),
        ).fetchone()
        conn.close()
        return row["result_json"] if row else None

    def set_guide_cache(self, repo_name: str, tool_type: str, query_key: str,
                        result_json: str, module: str = ""):
        """Insert or update a guide cache entry."""
        conn = self._connect(self._meta_path)
        conn.execute(
            """INSERT INTO guide_cache (repo_name, tool_type, query_key, module, result_json, updated_at)
               VALUES (?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(repo_name, tool_type, query_key, module)
               DO UPDATE SET result_json=excluded.result_json, updated_at=datetime('now')""",
            (repo_name, tool_type, query_key, module, result_json),
        )
        conn.commit()
        conn.close()

    def clear_guide_cache(self, repo_name: str):
        """Clear all guide cache entries for a repo."""
        conn = self._connect(self._meta_path)
        conn.execute("DELETE FROM guide_cache WHERE repo_name = ?", (repo_name,))
        conn.commit()
        conn.close()

    # --- Module operations ---

    def list_modules(self, repo_name: str) -> list[str]:
        """List unique module names for a repo."""
        conn = self._connect(self._struct_path)
        rows = conn.execute(
            "SELECT DISTINCT repo_module FROM symbols WHERE repo_name = ? ORDER BY repo_module",
            (repo_name,),
        ).fetchall()
        conn.close()
        return [r["repo_module"] for r in rows if r["repo_module"]]

    def save_repo_modules(self, repo_name: str, modules: list[dict]):
        """Save detected module info as JSON on the repos table."""
        conn = self._connect(self._meta_path)
        conn.execute(
            "UPDATE repos SET modules_json = ? WHERE name = ?",
            (json.dumps(modules, ensure_ascii=False), repo_name),
        )
        conn.commit()
        conn.close()

    def get_repo_modules(self, repo_name: str) -> list[dict]:
        """Get saved module info for a repo."""
        conn = self._connect(self._meta_path)
        row = conn.execute(
            "SELECT modules_json FROM repos WHERE name = ?",
            (repo_name,),
        ).fetchone()
        conn.close()
        if row is None or not row["modules_json"]:
            return []
        try:
            return json.loads(row["modules_json"])
        except (json.JSONDecodeError, TypeError):
            return []

    def find_module_across_repos(self, name: str) -> list[dict]:
        """Find a module by exact name across all repos."""
        conn = self._connect(self._meta_path)
        rows = conn.execute(
            "SELECT name, modules_json FROM repos WHERE modules_json IS NOT NULL AND modules_json != '[]'",
        ).fetchall()
        conn.close()

        results = []
        for row in rows:
            repo_name = row["name"]
            try:
                modules = json.loads(row["modules_json"])
            except (json.JSONDecodeError, TypeError):
                continue
            for mod in modules:
                if mod.get("name") == name:
                    results.append({
                        "repo_name": repo_name,
                        "module_name": mod["name"],
                        "module_path": mod.get("path", ""),
                        "language": mod.get("language", ""),
                    })
        return results
