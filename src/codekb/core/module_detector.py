"""Module detection for monorepo / multi-package repositories."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# Directories that typically contain sub-packages in a monorepo
_MONOREPO_DIRS = {"packages", "libs", "modules", "services", "apps"}

# Manifest files that indicate a directory is a distinct package
_MANIFEST_FILES = {
    "package.json",
    "pyproject.toml",
    "setup.py",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "Gemfile",
    "composer.json",
    "oh-package.json5",
}

# Common monorepo top-level directory names (frontend/backend split etc.)
_COMMON_MODULE_DIRS = {"frontend", "backend", "server", "client", "web", "api", "admin", "mobile", "desktop"}

# Directories to skip during module scanning
_SKIP_DIRS = {
    "node_modules", "__pycache__", "dist", "build", "vendor",
    "target", ".git", ".github", "docs", "tests", "test", "scripts",
    "configs", "config", "data", "assets", "public", "static",
}


@dataclass
class ModuleInfo:
    """Information about a detected module within a repository."""

    name: str
    path: str = ""
    language: str = ""
    manifest_file: str = ""
    source: str = "auto"  # auto | manual


def detect_modules(
    repo_path: Path,
    configured_modules: Optional[list[dict]] = None,
) -> list[ModuleInfo]:
    """Detect modules in a repository.

    Priority:
    1. Manual configuration (configured_modules)
    2. Auto-detection from monorepo directory structure
    3. Single-module fallback (name="", path="")

    Returns a list of ModuleInfo. For single-module repos, returns
    [ModuleInfo(name="", path="")] for backward compatibility.
    """
    if configured_modules:
        modules = []
        for m in configured_modules:
            modules.append(ModuleInfo(
                name=m["name"],
                path=m.get("path", ""),
                language=m.get("language", ""),
                source="manual",
            ))
        return modules

    # Auto-detect
    modules = _auto_detect(repo_path)

    # If <=1 module found, return single-module (empty name) for compat
    if len(modules) <= 1:
        return [ModuleInfo(name="", path="")]

    return modules


def file_to_module(file_path: str, modules: list[ModuleInfo]) -> str:
    """Determine which module a file belongs to via longest-prefix match.

    Returns "" if no module matches (single-module repo).
    """
    if not modules:
        return ""

    best_match = ""
    best_len = 0
    for m in modules:
        if not m.path:
            continue
        # Normalize: ensure module path ends with /
        prefix = m.path if m.path.endswith("/") else m.path + "/"
        if (file_path + "/").startswith(prefix) and len(m.path) > best_len:
            best_match = m.name
            best_len = len(m.path)

    return best_match


def _auto_detect(repo_path: Path) -> list[ModuleInfo]:
    """Auto-detect modules by scanning directory structure."""
    modules: list[ModuleInfo] = []

    # Strategy 1: Check monorepo directories (packages/, libs/, etc.)
    for mono_dir_name in _MONOREPO_DIRS:
        mono_dir = repo_path / mono_dir_name
        if not mono_dir.is_dir():
            continue
        for child in sorted(mono_dir.iterdir()):
            if not child.is_dir():
                continue
            manifest = _find_manifest(child)
            if manifest:
                modules.append(ModuleInfo(
                    name=child.name,
                    path=str(child.relative_to(repo_path)),
                    language=_guess_language(manifest),
                    manifest_file=manifest.name,
                    source="auto",
                ))

    if modules:
        return modules

    # Strategy 2: Check root-level directories for manifest files
    for child in sorted(repo_path.iterdir()):
        if not child.is_dir():
            continue
        # Skip common non-module dirs
        if child.name.startswith(".") or child.name in _SKIP_DIRS:
            continue

        manifest = _find_manifest(child)
        if manifest:
            modules.append(ModuleInfo(
                name=child.name,
                path=str(child.relative_to(repo_path)),
                language=_guess_language(manifest),
                manifest_file=manifest.name,
                source="auto",
            ))
        else:
            # Strategy 2b: Scan one level deeper (e.g. bizCommon/biz_ui/oh-package.json5)
            for grandchild in sorted(child.iterdir()):
                if not grandchild.is_dir():
                    continue
                if grandchild.name.startswith(".") or grandchild.name in _SKIP_DIRS:
                    continue
                manifest = _find_manifest(grandchild)
                if manifest:
                    modules.append(ModuleInfo(
                        name=grandchild.name,
                        path=str(grandchild.relative_to(repo_path)),
                        language=_guess_language(manifest),
                        manifest_file=manifest.name,
                        source="auto",
                    ))

    if modules:
        return modules

    # Strategy 3: Check root-level common module dirs (frontend/, backend/ etc.)
    for child in sorted(repo_path.iterdir()):
        if not child.is_dir():
            continue
        if child.name in _COMMON_MODULE_DIRS:
            modules.append(ModuleInfo(
                name=child.name,
                path=str(child.relative_to(repo_path)),
                source="auto",
            ))

    return modules


def _find_manifest(directory: Path) -> Optional[Path]:
    """Find a manifest file in a directory."""
    for name in _MANIFEST_FILES:
        path = directory / name
        if path.exists():
            return path
    return None


def _guess_language(manifest: Path) -> str:
    """Guess the primary language from a manifest file."""
    name = manifest.name
    if name == "package.json":
        return "javascript"
    if name in ("pyproject.toml", "setup.py"):
        return "python"
    if name == "Cargo.toml":
        return "rust"
    if name == "go.mod":
        return "go"
    if name in ("pom.xml", "build.gradle"):
        return "java"
    if name == "Gemfile":
        return "ruby"
    if name == "composer.json":
        return "php"
    if name == "oh-package.json5":
        return "typescript"
    return ""
