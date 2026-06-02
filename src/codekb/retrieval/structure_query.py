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
        repo_module: Optional[str] = None,
    ) -> list[dict]:
        """Get code structure (classes, functions, signatures).

        Returns a nested structure if no path specified, or flat list for a specific file.
        """
        symbols = self.store.get_symbols(repo_name, file_path=path, repo_module=repo_module)

        if path:
            # Return flat list for a specific file
            return [self._symbol_to_dict(s) for s in symbols]

        # Build nested structure grouped by file
        result: dict[str, list[dict]] = {}
        for sym in symbols:
            if sym.file_path not in result:
                result[sym.file_path] = []
            result[sym.file_path].append(
                self._symbol_to_dict(sym, include_file=False, include_module=False)
            )

        return [{"file": fp, "symbols": syms} for fp, syms in sorted(result.items())]

    def get_symbol_detail(
        self,
        repo_name: str,
        symbol_name: str,
        repo_module: Optional[str] = None,
    ) -> Optional[dict]:
        """Get full symbol definition and references."""
        symbols = self.store.get_symbol_by_name(repo_name, symbol_name, repo_module=repo_module)
        if not symbols:
            return None

        # Use the first match as primary
        primary = symbols[0]

        # Get callers and callees
        callers = self.store.get_calls_to(repo_name, symbol_name, repo_module=repo_module)
        callees = self.store.get_calls_from(repo_name, symbol_name, repo_module=repo_module)

        # Get imports for the symbol's file
        imports = self.store.get_imports(repo_name, primary.file_path, repo_module=repo_module)

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
            ".ets": "typescript", ".java": "java", ".go": "go", ".rs": "rust",
            ".rb": "ruby", ".php": "php", ".c": "c", ".cpp": "cpp", ".cs": "csharp",
        }

        return {
            "content": content,
            "language": lang_map.get(ext, ""),
            "file_path": file_path,
            "total_lines": len(lines),
        }

    def get_readme(self, repo_name: str, module: Optional[str] = None) -> Optional[str]:
        """Get README content from the repo, optionally for a specific module."""
        repo = self.store.get_repo(repo_name)
        if repo is None:
            return None

        repo_path = Path(repo.local_path)

        # Module-level README: resolve module path and look for README
        if module:
            module_path = self._resolve_module_path(repo_name, module)
            if module_path:
                module_dir = repo_path / module_path
                for name in ["README.md", "README.rst", "README.txt", "README"]:
                    readme = module_dir / name
                    if readme.exists():
                        return readme.read_text(encoding="utf-8", errors="replace")
            return None

        # Root README (default behavior)
        for name in ["README.md", "README.rst", "README.txt", "README"]:
            readme = repo_path / name
            if readme.exists():
                return readme.read_text(encoding="utf-8", errors="replace")
        return None

    def _resolve_module_path(self, repo_name: str, module: str) -> Optional[str]:
        """Resolve module name to its filesystem path using repo modules data."""
        modules_data = self.store.get_repo_modules(repo_name)
        for mod in modules_data:
            if mod["name"] == module:
                return mod.get("path", "")
        return None

    def get_file_tree(self, repo_name: str, repo_module: Optional[str] = None) -> list[dict]:
        """Get file tree for a repo."""
        entries = self.store.get_file_tree(repo_name, repo_module=repo_module)
        return [
            {
                "path": e.path,
                "language": e.language,
                "is_entry_point": e.is_entry_point,
                "symbol_count": e.symbol_count,
                "repo_module": e.repo_module,
            }
            for e in entries
        ]

    def get_stats(self, repo_name: str, repo_module: Optional[str] = None) -> dict:
        """Get structure stats for a repo."""
        symbols = self.store.get_symbols(repo_name, repo_module=repo_module)
        files = self.store.get_file_tree(repo_name, repo_module=repo_module)

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

    def list_modules(self, repo_name: str) -> list[str]:
        """List all modules in a repo."""
        return self.store.list_modules(repo_name)

    def find_symbol(self, symbol_name: str) -> list[dict]:
        """Find symbol across all repos, returning归属 and basic info."""
        symbols = self.store.find_symbol_across_repos(symbol_name)
        return [
            {
                "repo_name": s.repo_name,
                "module": s.repo_module,
                "file_path": s.file_path,
                "name": s.name,
                "kind": s.kind,
                "signature": s.signature,
                "line": s.start_line,
                "end_line": s.end_line,
                "docstring": s.docstring,
            }
            for s in symbols
        ]

    def resolve_symbol(self, name: str) -> Optional[tuple[str, Optional[str]]]:
        """Resolve a symbol name to (repo_name, module).

        Returns None if zero or multiple repos match.
        If multiple symbols exist within the same repo, returns first match.
        """
        symbols = self.store.find_symbol_across_repos(name)
        if not symbols:
            return None
        repos = {s.repo_name for s in symbols}
        if len(repos) > 1:
            return None
        first = symbols[0]
        return (first.repo_name, first.repo_module or None)

    def get_module_dependencies(self, repo_name: str) -> list[dict]:
        """Analyze cross-module dependencies based on import statements.

        Returns a list of dependency edges between modules.
        """
        from codekb.core.module_detector import file_to_module, ModuleInfo

        modules_data = self.store.get_repo_modules(repo_name)
        if not modules_data:
            return []

        modules = [ModuleInfo(name=m["name"], path=m.get("path", "")) for m in modules_data]
        imports = self.store.get_imports(repo_name)

        # Build module-level dependency map
        deps: dict[tuple[str, str], list[str]] = {}
        for imp in imports:
            if not imp.repo_module:
                continue
            # Check if the import targets another module
            for mod in modules:
                if mod.name == imp.repo_module:
                    continue
                if mod.path and imp.module.startswith(mod.path.replace("/", ".")):
                    key = (imp.repo_module, mod.name)
                    if key not in deps:
                        deps[key] = []
                    if imp.file_path not in deps[key]:
                        deps[key].append(imp.file_path)

        return [
            {"from": src, "to": dst, "files": files}
            for (src, dst), files in sorted(deps.items())
        ]

    def _symbol_to_dict(self, sym: Symbol, include_file: bool = True,
                        include_module: bool = True) -> dict:
        d: dict = {
            "name": sym.name,
            "type": sym.kind,
            "signature": sym.signature,
            "line": sym.start_line,
            "end_line": sym.end_line,
            "parent": sym.parent,
        }
        if include_file:
            d["file"] = sym.file_path
        if include_module:
            d["repo_module"] = sym.repo_module
        if sym.docstring:
            d["docstring"] = sym.docstring
        return d
