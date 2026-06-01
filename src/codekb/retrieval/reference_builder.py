"""Reference builder: usage examples, templates, integration guides."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from codekb.retrieval.hybrid_search import HybridSearch
from codekb.retrieval.structure_query import StructureQuery
from codekb.storage.sqlite_store import SqliteStore


class ReferenceBuilder:
    """Builds reference materials from indexed code and docs."""

    def __init__(self, store: SqliteStore, structure_query: StructureQuery, hybrid_search: HybridSearch):
        self.store = store
        self.structure = structure_query
        self.search = hybrid_search

    async def get_usage_examples(
        self,
        repo_name: str,
        library_or_pattern: str,
        top_k: int = 5,
        repo_module: Optional[str] = None,
    ) -> list[dict]:
        """Find real usage examples of a library or pattern in the codebase.

        Searches for code that imports or uses the given library/pattern.
        """
        results = await self.search.search(
            query=f"usage example {library_or_pattern}",
            repo_name=repo_name,
            repo_module=repo_module,
            top_k=top_k,
        )

        examples = []
        seen_files = set()
        for r in results:
            if r.file_path in seen_files:
                continue
            seen_files.add(r.file_path)

            name = r.metadata.get("name", "")
            kind = r.metadata.get("kind", "")

            examples.append({
                "description": f"{kind} {name} in {r.file_path}",
                "code": r.content,
                "file": r.file_path,
                "score": r.score,
            })

        return examples

    async def get_integration_guide(
        self,
        repo_name: str,
        library: str,
        repo_module: Optional[str] = None,
    ) -> dict:
        """Build an integration guide for using a library in this project.

        Aggregates: config items, init code, usage examples, dependencies.
        """
        # Find all imports of this library
        symbols = self.store.get_symbols(repo_name, repo_module=repo_module)
        imports = self.store.get_imports(repo_name, repo_module=repo_module)

        related_imports = [
            i for i in imports
            if library.lower() in i.module.lower()
        ]

        # Find files that import this library
        related_files = set()
        for imp in related_imports:
            related_files.add(imp.file_path)

        # Search for usage examples
        examples = await self.get_usage_examples(repo_name, library, top_k=5,
                                                  repo_module=repo_module)

        # Get config items from code
        config_items = []
        for sym in symbols:
            if "config" in sym.name.lower() or "setting" in sym.name.lower():
                if any(library.lower() in (sym.source or "").lower() for imp in related_imports if sym.file_path == imp.file_path):
                    config_items.append({
                        "name": sym.name,
                        "file": sym.file_path,
                        "signature": sym.signature,
                    })

        return {
            "library": library,
            "import_statements": [
                {"module": i.module, "names": i.imported_names, "file": i.file_path, "line": i.line_number}
                for i in related_imports
            ],
            "related_files": sorted(related_files),
            "usage_examples": examples,
            "config_items": config_items,
        }

    async def get_code_template(
        self,
        repo_name: str,
        pattern_type: str,
        repo_module: Optional[str] = None,
    ) -> dict:
        """Get typical code patterns for a project.

        pattern_type: controller, service, repository, config, test, model, etc.
        """
        # Search for code matching the pattern type
        results = await self.search.search(
            query=f"{pattern_type} pattern implementation",
            repo_name=repo_name,
            repo_module=repo_module,
            top_k=5,
        )

        templates = []
        for r in results:
            name = r.metadata.get("name", "")
            kind = r.metadata.get("kind", "")

            templates.append({
                "name": name,
                "kind": kind,
                "code": r.content,
                "file": r.file_path,
            })

        # Get conventions from existing code structure
        stats = self.structure.get_stats(repo_name, repo_module=repo_module)

        return {
            "pattern_type": pattern_type,
            "templates": templates,
            "conventions": {
                "languages": stats.get("languages", {}),
                "symbol_kinds": stats.get("symbol_kinds", {}),
            },
            "examples": [
                {"name": t["name"], "file": t["file"]}
                for t in templates[:3]
            ],
        }
