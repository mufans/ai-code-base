"""Markdown file storage for generated docs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


class DocStore:
    """Read and write generated markdown documentation files."""

    def __init__(self, generated_dir: Path):
        self.generated_dir = generated_dir
        self.generated_dir.mkdir(parents=True, exist_ok=True)

    def _repo_dir(self, repo_name: str, repo_module: str = "") -> Path:
        if repo_module:
            d = self.generated_dir / repo_name / repo_module
        else:
            d = self.generated_dir / repo_name
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _skills_dir(self, repo_name: str, repo_module: str = "") -> Path:
        d = self._repo_dir(repo_name, repo_module) / "skills"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def write_doc(self, repo_name: str, filename: str, content: str, repo_module: str = ""):
        """Write a generated doc file."""
        path = self._repo_dir(repo_name, repo_module) / filename
        path.write_text(content, encoding="utf-8")

    def read_doc(self, repo_name: str, filename: str, repo_module: str = "") -> Optional[str]:
        """Read a generated doc file. Returns None if not found."""
        path = self._repo_dir(repo_name, repo_module) / filename
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    def delete_doc(self, repo_name: str, filename: str, repo_module: str = "") -> bool:
        """Delete a generated doc file."""
        path = self._repo_dir(repo_name, repo_module) / filename
        if path.exists():
            path.unlink()
            return True
        return False

    def list_docs(self, repo_name: str, repo_module: str = "") -> list[str]:
        """List all generated doc files for a repo."""
        repo_dir = self._repo_dir(repo_name, repo_module)
        return [f.name for f in repo_dir.iterdir() if f.is_file() and f.suffix in (".md", ".json")]

    def write_coverage(self, repo_name: str, coverage: dict, repo_module: str = ""):
        """Write COVERAGE.json for a repo."""
        path = self._repo_dir(repo_name, repo_module) / "COVERAGE.json"
        path.write_text(json.dumps(coverage, indent=2, ensure_ascii=False), encoding="utf-8")

    def read_coverage(self, repo_name: str, repo_module: str = "") -> Optional[dict]:
        """Read COVERAGE.json for a repo."""
        path = self._repo_dir(repo_name, repo_module) / "COVERAGE.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    # --- Skill operations ---

    def write_skill(self, repo_name: str, skill_name: str, content: str, repo_module: str = ""):
        """Write a skill file."""
        path = self._skills_dir(repo_name, repo_module) / f"{skill_name}.md"
        path.write_text(content, encoding="utf-8")

    def read_skill(self, repo_name: str, skill_name: str, repo_module: str = "") -> Optional[str]:
        """Read a skill file."""
        path = self._skills_dir(repo_name, repo_module) / f"{skill_name}.md"
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    def list_skills(self, repo_name: str, repo_module: str = "") -> list[str]:
        """List all skill files for a repo (without .md extension)."""
        skills_dir = self._skills_dir(repo_name, repo_module)
        return [f.stem for f in skills_dir.iterdir() if f.is_file() and f.suffix == ".md"]

    def delete_skill(self, repo_name: str, skill_name: str, repo_module: str = "") -> bool:
        """Delete a skill file."""
        path = self._skills_dir(repo_name, repo_module) / f"{skill_name}.md"
        if path.exists():
            path.unlink()
            return True
        return False

    def delete_repo(self, repo_name: str) -> bool:
        """Delete all generated docs for a repo."""
        repo_dir = self._repo_dir(repo_name)
        if repo_dir.exists():
            for f in repo_dir.rglob("*"):
                if f.is_file():
                    f.unlink()
            # Remove empty dirs
            for d in sorted(repo_dir.rglob("*"), reverse=True):
                if d.is_dir():
                    d.rmdir()
            repo_dir.rmdir()
            return True
        return False
