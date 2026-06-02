"""Repository management: clone, register, metadata."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import git

from codekb.core.config import CodekbYamlConfig, ensure_data_dir
from codekb.storage.sqlite_store import RepoRecord, SqliteStore


# Language detection by file extension
EXTENSION_MAP: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".jsx": "javascript",
    ".tsx": "typescript",
    ".ets": "typescript",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".php": "php",
    ".c": "c",
    ".cpp": "cpp",
    ".h": "c",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".swift": "swift",
    ".kt": "kotlin",
    ".scala": "scala",
    ".r": "r",
    ".m": "objective-c",
    ".lua": "lua",
}

# Framework detection by config files
FRAMEWORK_DETECTORS: dict[str, str] = {
    "package.json": "node",
    "pyproject.toml": "python",
    "setup.py": "python",
    "requirements.txt": "python",
    "Cargo.toml": "rust",
    "go.mod": "go",
    "pom.xml": "java-maven",
    "build.gradle": "java-gradle",
    "Gemfile": "ruby",
    "composer.json": "php",
}


def parse_repo_url(url: str) -> dict:
    """Parse a repository URL into components.

    Supports:
    - https://github.com/org/repo
    - https://github.com/org/repo.git
    - https://gitlab.com/org/repo
    - https://gitee.com/org/repo
    - git@github.com:org/repo.git
    - /local/path/to/repo
    """
    url = url.strip()

    # Local path
    if url.startswith("/") or url.startswith("./") or url.startswith("~/"):
        path = Path(url).expanduser().resolve()
        return {
            "platform": "local",
            "host": "",
            "org": "",
            "repo": path.name,
            "url": str(path),
            "is_local": True,
        }

    # SSH format: git@host:org/repo.git
    ssh_match = re.match(r"git@([^:]+):(.+?)(?:\.git)?$", url)
    if ssh_match:
        host = ssh_match.group(1)
        path_parts = ssh_match.group(2).split("/")
        org = path_parts[-2] if len(path_parts) >= 2 else ""
        repo = path_parts[-1]
        platform = _detect_platform(host)
        return {
            "platform": platform,
            "host": host,
            "org": org,
            "repo": repo,
            "url": url,
            "is_local": False,
        }

    # HTTPS format
    parsed = urlparse(url)
    host = parsed.hostname or ""
    path = parsed.path.strip("/").removesuffix(".git")
    parts = path.split("/")
    org = parts[-2] if len(parts) >= 2 else ""
    repo = parts[-1] if parts else ""
    platform = _detect_platform(host)

    return {
        "platform": platform,
        "host": host,
        "org": org,
        "repo": repo,
        "url": url,
        "is_local": False,
    }


def _detect_platform(host: str) -> str:
    """Detect git platform from hostname."""
    host_lower = host.lower()
    if "github" in host_lower:
        return "github"
    elif "gitlab" in host_lower:
        return "gitlab"
    elif "gitee" in host_lower:
        return "gitee"
    return host_lower


def _repo_dir_name(parsed: dict) -> str:
    """Generate a directory name for a repo."""
    if parsed["is_local"]:
        return parsed["repo"]
    host = parsed.get("host", "unknown")
    org = parsed.get("org", "")
    repo = parsed["repo"]
    parts = [host]
    if org:
        parts.append(org)
    parts.append(repo)
    return "_".join(parts).replace(".", "_")


def detect_language(repo_path: Path) -> str:
    """Detect the primary language of a repo by file extension counting."""
    lang_counts: dict[str, int] = {}
    for root, dirs, files in os.walk(repo_path):
        # Skip hidden and common non-source dirs
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in (
            "node_modules", "__pycache__", "dist", "build", ".git", "vendor", "target",
        )]
        for f in files:
            ext = Path(f).suffix.lower()
            lang = EXTENSION_MAP.get(ext)
            if lang:
                lang_counts[lang] = lang_counts.get(lang, 0) + 1

    if not lang_counts:
        return ""
    return max(lang_counts, key=lang_counts.get)


def detect_framework(repo_path: Path) -> str:
    """Detect the framework/build system of a repo."""
    frameworks = []
    for config_file, framework in FRAMEWORK_DETECTORS.items():
        if (repo_path / config_file).exists():
            frameworks.append(framework)
    return ",".join(frameworks) if frameworks else ""


class RepoManager:
    """Manages repository lifecycle: clone, register, detect metadata."""

    def __init__(self, config: CodekbYamlConfig, store: SqliteStore):
        self.config = config
        self.store = store
        self.data_dir = ensure_data_dir(config)
        self.repos_dir = self.data_dir / "repos"

    def add_repo(
        self,
        url: str,
        branch: Optional[str] = None,
        name: Optional[str] = None,
        is_local: bool = False,
    ) -> RepoRecord:
        """Clone/register a repository and detect metadata."""
        parsed = parse_repo_url(url)
        dir_name = name or _repo_dir_name(parsed)
        local_path = self.repos_dir / dir_name

        if parsed["is_local"] or is_local:
            local_path = Path(url).expanduser().resolve()
            if not local_path.exists():
                raise FileNotFoundError(f"Local repo path not found: {local_path}")
        else:
            # Clone if not already cloned
            if not local_path.exists():
                clone_url = parsed["url"]
                kwargs = {}
                if branch:
                    kwargs["branch"] = branch
                git.Repo.clone_from(clone_url, str(local_path), depth=1, **kwargs)
            else:
                # Already cloned, just fetch
                try:
                    repo = git.Repo(str(local_path))
                    repo.remotes.origin.fetch()
                except Exception:
                    pass

        # Detect language and framework
        language = detect_language(local_path)
        framework = detect_framework(local_path)

        # Count files
        file_count = sum(
            1 for _ in local_path.rglob("*")
            if _.is_file() and ".git" not in _.parts
        )

        repo = RepoRecord(
            name=dir_name,
            url=url,
            local_path=str(local_path),
            platform=parsed["platform"],
            branch=branch or "main",
            language=language,
            framework=framework,
            file_count=file_count,
            status="registered",
        )
        self.store.register_repo(repo)
        return repo

    def remove_repo(self, name: str, delete_files: bool = True) -> bool:
        """Remove a repo from the registry and optionally delete files."""
        repo = self.store.get_repo(name)
        if repo is None:
            return False

        self.store.remove_repo(name)
        self.store.clear_repo_structure(name)

        if delete_files and repo.local_path:
            local_path = Path(repo.local_path)
            if local_path.exists() and not repo.url.startswith("/"):
                # Only delete cloned repos, not local ones
                import shutil
                shutil.rmtree(local_path, ignore_errors=True)

        return True

    def list_repos(self) -> list[RepoRecord]:
        """List all registered repos."""
        return self.store.list_repos()

    def get_repo(self, name: str) -> Optional[RepoRecord]:
        """Get a single repo by name."""
        return self.store.get_repo(name)

    def fetch_updates(self, name: str) -> Optional[list[str]]:
        """Fetch updates for a repo. Returns list of changed files or None."""
        repo = self.store.get_repo(name)
        if repo is None:
            return None

        try:
            git_repo = git.Repo(repo.local_path)
            old_head = git_repo.head.commit.hexsha
            git_repo.remotes.origin.pull()
            new_head = git_repo.head.commit.hexsha

            if old_head == new_head:
                return []

            # Get changed files
            diff = git_repo.git.diff("--name-only", old_head, new_head)
            return diff.strip().split("\n") if diff.strip() else []
        except Exception:
            return None
