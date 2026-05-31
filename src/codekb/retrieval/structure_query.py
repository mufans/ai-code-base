"""Structured queries against SQLite structure.db."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from codekb.storage.sqlite_store import SqliteStore, Symbol, CallRelation, ImportRecord, FileEntry


class StructureQuery:
    """Structured queries against the code structure index."""

    def __init__(self, store: SqliteStore):
        self.store = store

    def get_structure(
        self,
        repo_name: str,
        path: Optional[str] = None,
    ) -> list[dict]:
        """Get code structure (classes, functions, signatures).

        Returns a nested structure if no path specified, or flat list for a specific file.
        """
        symbols = self.store.get_symbols(repo_name, file_path=path)

        if path:
            # Return flat list for a specific file
            return [self._symbol_to_dict(s) for s in symbols]

        # Build nested structure grouped by file
        result: dict[str, list[dict]] = {}
        for sym in symbols:
            if sym.file_path not in result:
                result[sym.file_path] = []
            result[sym.file_path].append(self._symbol_to_dict(sym))

        return [{"file": fp, "symbols": syms} for fp, syms in sorted(result.items())]

    def get_symbol_detail(
        self,
        repo_name: str,
        symbol_name: str,
    ) -> Optional[dict]:
        """Get full symbol definition and references."""
        symbols = self.store.get_symbol_by_name(repo_name, symbol_name)
        if not symbols:
            return None

        # Use the first match as primary
        primary = symbols[0]

        # Get callers and callees
        callers = self.store.get_calls_to(repo_name, symbol_name)
        callees = self.store.get_calls_from(repo_name, symbol_name)

        # Get imports for the symbol's file
        imports = self.store.get_imports(repo_name, primary.file_path)

        return {
            "definition": self._symbol_to_dict(primary),
            "all_definitions": [self._symbol_to_dict(s) for s in symbols],
            "callers": [
                {"caller": c.caller_name, "file": c.caller_file, "line": c.line_number}
                for c in callers
            ],
            "callees": [
                {"callee": c.callee_name, "file": c.callee_file, "line": c.line_number}
                for c in callees
            ],
            "file_imports": [
                {"module": i.module, "names": i.imported_names, "line": i.line_number}
                for i in imports
            ],
        }

    def get_file_content(
        self,
        repo_name: str,
        file_path: str,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
    ) -> Optional[dict]:
        """Read file content from disk."""
        repo = self.store.get_repo(repo_name)
        if repo is None:
            return None

        full_path = Path(repo.local_path) / file_path
        if not full_path.exists():
            return None

        content = full_path.read_text(encoding="utf-8", errors="replace")
        lines = content.split("\n")

        if start_line is not None and end_line is not None:
            lines = lines[start_line - 1:end_line]
            content = "\n".join(lines)
        elif start_line is not None:
            lines = lines[start_line - 1:]
            content = "\n".join(lines)

        # Detect language from extension
        ext = Path(file_path).suffix.lower()
        lang_map = {
            ".py": "python", ".js": "javascript", ".ts": "typescript",
            ".java": "java", ".go": "go", ".rs": "rust", ".rb": "ruby",
            ".php": "php", ".c": "c", ".cpp": "cpp", ".cs": "csharp",
        }

        return {
            "content": content,
            "language": lang_map.get(ext, ""),
            "file_path": file_path,
            "total_lines": len(lines),
        }

    def get_readme(self, repo_name: str) -> Optional[str]:
        """Get README content from the repo."""
        repo = self.store.get_repo(repo_name)
        if repo is None:
            return None

        repo_path = Path(repo.local_path)
        for name in ["README.md", "README.rst", "README.txt", "README"]:
            readme = repo_path / name
            if readme.exists():
                return readme.read_text(encoding="utf-8", errors="replace")
        return None

    def get_file_tree(self, repo_name: str) -> list[dict]:
        """Get file tree for a repo."""
        entries = self.store.get_file_tree(repo_name)
        return [
            {
                "path": e.path,
                "language": e.language,
                "is_entry_point": e.is_entry_point,
                "symbol_count": e.symbol_count,
            }
            for e in entries
        ]

    def get_stats(self, repo_name: str) -> dict:
        """Get structure stats for a repo."""
        symbols = self.store.get_symbols(repo_name)
        files = self.store.get_file_tree(repo_name)

        kinds = {}
        for s in symbols:
            kinds[s.kind] = kinds.get(s.kind, 0) + 1

        languages = {}
        for f in files:
            if f.language:
                languages[f.language] = languages.get(f.language, 0) + 1

        return {
            "total_symbols": len(symbols),
            "total_files": len(files),
            "symbol_kinds": kinds,
            "languages": languages,
            "entry_points": [f.path for f in files if f.is_entry_point],
        }

    def _symbol_to_dict(self, sym: Symbol) -> dict:
        return {
            "name": sym.name,
            "type": sym.kind,
            "signature": sym.signature,
            "file": sym.file_path,
            "line": sym.start_line,
            "end_line": sym.end_line,
            "parent": sym.parent,
            "docstring": sym.docstring,
        }
