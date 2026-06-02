"""Import resolver: resolves ImportRecord.module to actual file paths and symbols.

Supports:
- Python: relative imports → package paths via __init__.py
- TypeScript/JavaScript: path aliases via tsconfig.json paths
- Java/Kotlin: package names → directory structure
- Go: module path → go.mod resolution
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from codekb.storage.sqlite_store import SqliteStore, ImportRecord


class ImportResolver:
    """Resolves import module strings to file paths and symbol names."""

    def __init__(self, store: SqliteStore):
        self.store = store
        self._tsconfig_cache: dict[str, Optional[dict]] = {}
        self._gomod_cache: dict[str, Optional[str]] = {}

    def resolve_imports(self, repo_name: str, repo_path: Path):
        """Post-process all imports for a repo, resolving module strings.

        Updates resolved_path and resolved_symbol on each ImportRecord.
        """
        repo = self.store.get_repo(repo_name)
        if repo is None:
            return

        language = repo.language.lower()
        imports = self.store.get_imports(repo_name)

        for imp in imports:
            resolved_path = ""
            resolved_symbol = ""

            if language in ("python",):
                resolved_path, resolved_symbol = self._resolve_python(
                    imp, repo_path,
                )
            elif language in ("typescript", "tsx", "javascript", "arkts"):
                resolved_path, resolved_symbol = self._resolve_ts_js(
                    imp, repo_path,
                )
            elif language in ("java", "kotlin"):
                resolved_path, resolved_symbol = self._resolve_java_kotlin(
                    imp, repo_path,
                )
            elif language == "go":
                resolved_path, resolved_symbol = self._resolve_go(
                    imp, repo_path,
                )

            if resolved_path:
                self._update_import_resolution(imp, resolved_path, resolved_symbol)

    def _update_import_resolution(self, imp: ImportRecord, resolved_path: str, resolved_symbol: str):
        """Update a single import record with resolved path/symbol."""
        conn = self.store._connect(self.store._struct_path)
        conn.execute(
            "UPDATE imports SET resolved_path = ?, resolved_symbol = ? "
            "WHERE repo_name = ? AND file_path = ? AND module = ? AND line_number = ?",
            (resolved_path, resolved_symbol, imp.repo_name, imp.file_path, imp.module, imp.line_number),
        )
        conn.commit()
        conn.close()

    # --- Python ---

    def _resolve_python(self, imp: ImportRecord, repo_path: Path) -> tuple[str, str]:
        """Resolve Python imports to file paths.

        Handles:
        - from .utils import foo → utils.py or utils/__init__.py
        - from ..package import Bar → ../package/__init__.py
        - import os.path → (stdlib, skip)
        """
        module = imp.module
        if not module:
            return "", ""

        # Skip stdlib / third-party (heuristic: no match in repo)
        if not imp.is_relative and "." not in module:
            # Try to find as a top-level module in the repo
            pass

        # Build candidate paths
        if imp.is_relative:
            # Relative import: resolve relative to the importing file's directory
            import_dir = repo_path / Path(imp.file_path).parent
            parts = module.lstrip(".").split(".")
            dot_count = len(module) - len(module.lstrip("."))

            # Go up dot_count - 1 levels (single dot = same dir)
            if dot_count > 1:
                for _ in range(dot_count - 1):
                    import_dir = import_dir.parent

            # Build path from remaining parts
            candidate = import_dir
            for part in parts:
                if part:
                    candidate = candidate / part
        else:
            # Absolute import: resolve from repo root
            parts = module.split(".")
            candidate = repo_path
            for part in parts:
                candidate = candidate / part

        return self._resolve_python_candidate(candidate, repo_path)

    def _resolve_python_candidate(self, candidate: Path, repo_path: Path) -> tuple[str, str]:
        """Try to resolve a Python module path candidate to a file."""
        # Try as file: candidate.py
        py_file = Path(str(candidate) + ".py")
        if py_file.exists():
            try:
                return str(py_file.relative_to(repo_path)), ""
            except ValueError:
                return str(py_file), ""

        # Try as package: candidate/__init__.py
        init_file = candidate / "__init__.py"
        if init_file.exists():
            try:
                return str(init_file.relative_to(repo_path)), ""
            except ValueError:
                return str(init_file), ""

        return "", ""

    # --- TypeScript / JavaScript ---

    def _resolve_ts_js(self, imp: ImportRecord, repo_path: Path) -> tuple[str, str]:
        """Resolve TS/JS imports to file paths.

        Handles:
        - ./relative/path → path.ts / path.js / path/index.ts
        - @alias/path → via tsconfig.json paths
        - bare module → node_modules or skip
        """
        module = imp.module
        if not module:
            return "", ""

        if module.startswith("."):
            # Relative import
            import_dir = repo_path / Path(imp.file_path).parent
            target = (import_dir / module).resolve()
            return self._resolve_ts_candidate(target, repo_path)

        # Check tsconfig paths
        tsconfig = self._load_tsconfig(repo_path)
        if tsconfig:
            paths = tsconfig.get("compilerOptions", {}).get("paths", {})
            for pattern, targets in paths.items():
                base_pattern = pattern.rstrip("*")
                if module.startswith(base_pattern):
                    suffix = module[len(base_pattern):]
                    for target_pattern in targets:
                        target_base = target_pattern.rstrip("*")
                        target_path = repo_path / (target_base + suffix)
                        resolved, _ = self._resolve_ts_candidate(target_path.resolve(), repo_path)
                        if resolved:
                            return resolved, ""

        return "", ""

    def _resolve_ts_candidate(self, candidate: Path, repo_path: Path) -> tuple[str, str]:
        """Try to resolve a TS/JS module path candidate to a file."""
        # Exact match
        if candidate.is_file():
            try:
                return str(candidate.relative_to(repo_path)), ""
            except ValueError:
                return str(candidate), ""

        # Try with extensions
        for ext in [".ts", ".tsx", ".js", ".jsx", ".d.ts"]:
            f = Path(str(candidate) + ext)
            if f.exists():
                try:
                    return str(f.relative_to(repo_path)), ""
                except ValueError:
                    return str(f), ""

        # Try as directory with index
        if candidate.is_dir():
            for name in ["index.ts", "index.tsx", "index.js"]:
                f = candidate / name
                if f.exists():
                    try:
                        return str(f.relative_to(repo_path)), ""
                    except ValueError:
                        return str(f), ""

        return "", ""

    def _load_tsconfig(self, repo_path: Path) -> Optional[dict]:
        """Load and cache tsconfig.json."""
        key = str(repo_path)
        if key not in self._tsconfig_cache:
            tsconfig_path = repo_path / "tsconfig.json"
            if tsconfig_path.exists():
                try:
                    self._tsconfig_cache[key] = json.loads(tsconfig_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    self._tsconfig_cache[key] = None
            else:
                self._tsconfig_cache[key] = None
        return self._tsconfig_cache[key]

    # --- Java / Kotlin ---

    def _resolve_java_kotlin(self, imp: ImportRecord, repo_path: Path) -> tuple[str, str]:
        """Resolve Java/Kotlin imports to file paths.

        Converts package.ClassName → src/.../ClassName.java or .kt
        """
        module = imp.module
        if not module:
            return "", ""

        # java.util.List → List, java.util → package
        parts = module.split(".")
        if not parts:
            return "", ""

        # The last part is typically the class name
        class_name = parts[-1]

        # Convert dots to directory separators
        path_parts = parts[:-1]  # package path
        # Search in common source dirs
        for src_dir in ["src/main/java", "src/main/kotlin", "src", ""]:
            base = repo_path / src_dir
            if path_parts:
                candidate_dir = base / "/".join(path_parts)
            else:
                candidate_dir = base

            if not candidate_dir.exists():
                continue

            for ext in [".java", ".kt"]:
                candidate = candidate_dir / (class_name + ext)
                if candidate.exists():
                    try:
                        return str(candidate.relative_to(repo_path)), class_name
                    except ValueError:
                        return str(candidate), class_name

        return "", ""

    # --- Go ---

    def _resolve_go(self, imp: ImportRecord, repo_path: Path) -> tuple[str, str]:
        """Resolve Go imports to file paths.

        Uses go.mod to determine module prefix, then maps import path to directory.
        """
        module = imp.module
        if not module:
            return "", ""

        go_mod_path = self._find_go_mod(repo_path)
        if go_mod_path is None:
            return "", ""

        module_prefix = self._parse_go_module(go_mod_path)
        if not module_prefix:
            return "", ""

        # Strip module prefix to get relative path
        if module.startswith(module_prefix):
            rel = module[len(module_prefix):].strip("/")
            if not rel:
                # Import of the module root
                return "", ""
            # Go convention: directory = package
            pkg_dir = repo_path / rel
            if pkg_dir.is_dir():
                # Find .go files in the directory
                for go_file in pkg_dir.glob("*.go"):
                    if not go_file.name.endswith("_test.go"):
                        try:
                            return str(go_file.relative_to(repo_path)), ""
                        except ValueError:
                            return str(go_file), ""

        return "", ""

    def _find_go_mod(self, repo_path: Path) -> Optional[Path]:
        """Find go.mod in the repo root."""
        gomod = repo_path / "go.mod"
        return gomod if gomod.exists() else None

    def _parse_go_module(self, go_mod_path: Path) -> str:
        """Extract module path from go.mod."""
        key = str(go_mod_path)
        if key not in self._gomod_cache:
            try:
                content = go_mod_path.read_text(encoding="utf-8")
                for line in content.splitlines():
                    line = line.strip()
                    if line.startswith("module "):
                        self._gomod_cache[key] = line.split(None, 1)[1].strip()
                        break
                else:
                    self._gomod_cache[key] = ""
            except OSError:
                self._gomod_cache[key] = ""
        return self._gomod_cache[key] or ""
