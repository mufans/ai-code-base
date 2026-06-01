"""Guide cache manager for LLM-generated tool results."""

from __future__ import annotations

import json
from typing import Optional

from codekb.storage.sqlite_store import SqliteStore


class GuideCache:
    """Manages cached guide results in metadata.db guide_cache table."""

    def __init__(self, store: SqliteStore):
        self.store = store

    def get(self, repo_name: str, tool_type: str, query_key: str,
            module: str = "") -> Optional[dict]:
        """Get cached result as dict. Returns None on miss."""
        raw = self.store.get_guide_cache(repo_name, tool_type, query_key, module=module)
        if raw is None:
            return None
        return json.loads(raw)

    def get_json(self, repo_name: str, tool_type: str, query_key: str,
                 module: str = "") -> Optional[str]:
        """Get cached result as raw JSON string. Returns None on miss."""
        return self.store.get_guide_cache(repo_name, tool_type, query_key, module=module)

    def set(self, repo_name: str, tool_type: str, query_key: str,
            result: dict, module: str = ""):
        """Cache a guide result."""
        self.store.set_guide_cache(
            repo_name, tool_type, query_key,
            json.dumps(result, ensure_ascii=False),
            module=module,
        )

    def clear(self, repo_name: str):
        """Clear all cached results for a repo."""
        self.store.clear_guide_cache(repo_name)
