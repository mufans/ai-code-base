# CodeKB Guide Query Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 4 new MCP tools (get_component_guide, get_usage_examples, recommend_component, get_api_guide) that return aggregated results in a single call, with LLM-assisted generation and SQLite caching.

**Architecture:** Each tool orchestrates multiple SQLite queries (symbols/calls/imports tables) to gather context, then uses litellm (reusing existing LLM config pattern) to generate descriptions/examples when the cache misses. Results are cached in a new `guide_cache` table in metadata.db.

**Tech Stack:** Python 3.11+, SQLite, litellm (existing dependency), pydantic v2

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `src/codekb/mcp/guide_cache.py` | Create | Cache CRUD for guide_cache table in metadata.db |
| `src/codekb/retrieval/guide_generator.py` | Create | Core logic for 4 tools: orchestrate queries → build prompt → call LLM → parse result |
| `src/codekb/storage/sqlite_store.py` | Modify | Add guide_cache table creation + clear method |
| `src/codekb/mcp/server.py` | Modify | Register 4 new tools + route to GuideGenerator |
| `tests/test_guide_cache.py` | Create | Tests for cache CRUD |
| `tests/test_guide_generator.py` | Create | Tests for guide generation logic |

---

### Task 1: Add guide_cache table to SqliteStore

**Files:**
- Modify: `src/codekb/storage/sqlite_store.py`
- Test: `tests/test_storage.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_storage.py`:

```python
class TestGuideCache:
    def test_cache_miss_returns_none(self, sqlite_store):
        result = sqlite_store.get_guide_cache("test-repo", "component_guide", "Foo")
        assert result is None

    def test_cache_write_and_read(self, sqlite_store):
        sqlite_store.set_guide_cache("test-repo", "component_guide", "Foo", '{"name":"Foo"}')
        result = sqlite_store.get_guide_cache("test-repo", "component_guide", "Foo")
        assert result == '{"name":"Foo"}'

    def test_cache_upsert(self, sqlite_store):
        sqlite_store.set_guide_cache("test-repo", "component_guide", "Foo", '{"v":1}')
        sqlite_store.set_guide_cache("test-repo", "component_guide", "Foo", '{"v":2}')
        result = sqlite_store.get_guide_cache("test-repo", "component_guide", "Foo")
        assert result == '{"v":2}'

    def test_cache_clear_by_repo(self, sqlite_store):
        sqlite_store.set_guide_cache("repo-a", "component_guide", "Foo", '{"a":1}')
        sqlite_store.set_guide_cache("repo-b", "component_guide", "Bar", '{"b":2}')
        sqlite_store.clear_guide_cache("repo-a")
        assert sqlite_store.get_guide_cache("repo-a", "component_guide", "Foo") is None
        assert sqlite_store.get_guide_cache("repo-b", "component_guide", "Bar") == '{"b":2}'

    def test_cache_with_module(self, sqlite_store):
        sqlite_store.set_guide_cache("test-repo", "component_guide", "Foo", '{"x":1}', module="biz_ui")
        # Same key without module should miss
        assert sqlite_store.get_guide_cache("test-repo", "component_guide", "Foo") is None
        # With module should hit
        assert sqlite_store.get_guide_cache("test-repo", "component_guide", "Foo", module="biz_ui") == '{"x":1}'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest tests/test_storage.py::TestGuideCache -v`
Expected: FAIL with `AttributeError: 'SqliteStore' object has no attribute 'get_guide_cache'`

- [ ] **Step 3: Add guide_cache table creation to `_init_databases`**

In `src/codekb/storage/sqlite_store.py`, inside `_init_databases()`, after the `conn.commit()` for metadata.db tables (around line 146), add:

```python
        # Ensure guide_cache table exists (idempotent migration)
        self._ensure_guide_cache_table()
```

Then add the method after `_ensure_doc_index_table`:

```python
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
```

Now add the three CRUD methods at the end of the `SqliteStore` class (before the Module operations section):

```python
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
        """Clear all guide cache entries for a repo (called on re-index)."""
        conn = self._connect(self._meta_path)
        conn.execute("DELETE FROM guide_cache WHERE repo_name = ?", (repo_name,))
        conn.commit()
        conn.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && python -m pytest tests/test_storage.py::TestGuideCache -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/codekb/storage/sqlite_store.py tests/test_storage.py
git commit -m "feat: add guide_cache table and CRUD methods to SqliteStore"
```

---

### Task 2: Create GuideCache wrapper

**Files:**
- Create: `src/codekb/mcp/guide_cache.py`
- Create: `tests/test_guide_cache.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_guide_cache.py`:

```python
"""Tests for guide cache wrapper."""

import json

import pytest

from codekb.mcp.guide_cache import GuideCache
from codekb.storage.sqlite_store import SqliteStore


@pytest.fixture
def cache(tmp_path):
    store = SqliteStore(tmp_path / "index")
    return GuideCache(store)


class TestGuideCache:
    def test_get_returns_none_on_miss(self, cache):
        result = cache.get("repo", "component_guide", "Foo")
        assert result is None

    def test_set_and_get(self, cache):
        data = {"symbol_name": "Foo", "description": "A foo component"}
        cache.set("repo", "component_guide", "Foo", data)
        result = cache.get("repo", "component_guide", "Foo")
        assert result == data

    def test_get_json_returns_raw_string(self, cache):
        cache.set("repo", "component_guide", "Foo", {"v": 1})
        raw = cache.get_json("repo", "component_guide", "Foo")
        assert raw is not None
        assert json.loads(raw) == {"v": 1}

    def test_set_overwrites(self, cache):
        cache.set("repo", "component_guide", "Foo", {"v": 1})
        cache.set("repo", "component_guide", "Foo", {"v": 2})
        result = cache.get("repo", "component_guide", "Foo")
        assert result == {"v": 2}

    def test_clear_by_repo(self, cache):
        cache.set("repo-a", "component_guide", "Foo", {"a": 1})
        cache.set("repo-b", "component_guide", "Bar", {"b": 2})
        cache.clear("repo-a")
        assert cache.get("repo-a", "component_guide", "Foo") is None
        assert cache.get("repo-b", "component_guide", "Bar") == {"b": 2}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest tests/test_guide_cache.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'codekb.mcp.guide_cache'`

- [ ] **Step 3: Create GuideCache class**

Create `src/codekb/mcp/guide_cache.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && python -m pytest tests/test_guide_cache.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/codekb/mcp/guide_cache.py tests/test_guide_cache.py
git commit -m "feat: add GuideCache wrapper for cached guide results"
```

---

### Task 3: Create GuideGenerator with LLM helper

**Files:**
- Create: `src/codekb/retrieval/guide_generator.py`
- Create: `tests/test_guide_generator.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_guide_generator.py`:

```python
"""Tests for guide generator."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from codekb.retrieval.guide_generator import GuideGenerator
from codekb.mcp.guide_cache import GuideCache
from codekb.storage.sqlite_store import SqliteStore, Symbol, CallRelation, ImportRecord


@pytest.fixture
def store(tmp_path):
    return SqliteStore(tmp_path / "index")


@pytest.fixture
def cache(store):
    return GuideCache(store)


@pytest.fixture
def generator(store, cache):
    return GuideGenerator(store, cache)


def _seed_symbols(store: SqliteStore):
    """Seed test data: a component + its caller."""
    # The component definition
    store.insert_symbols([
        Symbol(
            repo_name="test-repo", file_path="lib/components/Button.ets",
            name="Button", kind="struct",
            signature="struct Button",
            docstring="A reusable button component",
            start_line=1, end_line=30, parent="", language="typescript",
            source='@Component\nstruct Button {\n  @Prop text: string = ""\n  build() { ... }\n}',
        ),
        Symbol(
            repo_name="test-repo", file_path="lib/components/Button.ets",
            name="onClick", kind="method",
            signature="onClick(handler: () => void): void",
            start_line=10, end_line=15, parent="Button", language="typescript",
            source="onClick(handler: () => void): void { this._handler = handler }",
        ),
    ])
    # A caller file
    store.insert_symbols([
        Symbol(
            repo_name="test-repo", file_path="pages/HomePage.ets",
            name="HomePage", kind="struct",
            signature="struct HomePage",
            start_line=1, end_line=50, parent="", language="typescript",
            source='Button({ text: "Submit" })',
        ),
    ])
    store.insert_calls([
        CallRelation(
            repo_name="test-repo", caller_file="pages/HomePage.ets",
            caller_name="HomePage", callee_name="Button", callee_file="",
            line_number=20,
        ),
    ])
    store.insert_imports([
        ImportRecord(
            repo_name="test-repo", file_path="pages/HomePage.ets",
            module="@jfzt/components", imported_names='["Button"]',
            line_number=1, is_relative=False,
        ),
    ])


class TestGetComponentGuide:
    @pytest.mark.asyncio
    async def test_returns_cached_result(self, generator, cache):
        cached = {"symbol_name": "Button", "description": "cached"}
        cache.set("test-repo", "component_guide", "Button", cached)

        result = await generator.get_component_guide("test-repo", "Button")
        assert result["symbol_name"] == "Button"
        assert result["description"] == "cached"

    @pytest.mark.asyncio
    async def test_symbol_not_found(self, generator):
        result = await generator.get_component_guide("test-repo", "NonExistent")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_generates_guide_without_llm(self, generator, store):
        _seed_symbols(store)
        # No LLM client → should return structure data without description/example
        result = await generator.get_component_guide("test-repo", "Button")
        assert result["symbol_name"] == "Button"
        assert result["type"] == "struct"
        assert result["file"] == "lib/components/Button.ets"
        assert len(result["properties"]) >= 0
        assert result.get("usage_example") is None  # No LLM → no example

    @pytest.mark.asyncio
    async def test_generates_guide_with_llm(self, generator, store):
        _seed_symbols(store)
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps({
            "description": "A reusable button component",
            "usage_example": 'Button({ text: "Submit" })',
        })

        mock_llm = {"model": "test-model"}
        import litellm
        litellm.acompletion = AsyncMock(return_value=mock_response)

        result = await generator.get_component_guide(
            "test-repo", "Button", llm_client=mock_llm
        )
        assert result["description"] == "A reusable button component"
        assert "Submit" in result["usage_example"]

        # Verify cached
        cached = generator.cache.get("test-repo", "component_guide", "Button")
        assert cached is not None
        assert cached["symbol_name"] == "Button"


class TestGetUsageExamples:
    @pytest.mark.asyncio
    async def test_finds_callers(self, generator, store):
        _seed_symbols(store)
        result = await generator.get_usage_examples("test-repo", "Button")
        assert len(result["examples"]) >= 1
        assert result["examples"][0]["file"] == "pages/HomePage.ets"

    @pytest.mark.asyncio
    async def test_excludes_definition_file(self, generator, store):
        _seed_symbols(store)
        result = await generator.get_usage_examples("test-repo", "Button")
        for ex in result["examples"]:
            assert ex["file"] != "lib/components/Button.ets"


class TestRecommendComponent:
    @pytest.mark.asyncio
    async def test_returns_cached(self, generator, cache):
        cached = {"requirement": "a button", "recommendations": []}
        cache.set("test-repo", "recommend", "a button", cached)

        result = await generator.recommend_component("test-repo", "a button")
        assert result["requirement"] == "a button"


class TestGetApiGuide:
    @pytest.mark.asyncio
    async def test_returns_cached(self, generator, cache):
        cached = {"library_name": "biz_ui", "guide": {"description": "cached"}}
        cache.set("test-repo", "api_guide", "biz_ui", cached)

        result = await generator.get_api_guide("test-repo", "biz_ui")
        assert result["library_name"] == "biz_ui"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest tests/test_guide_generator.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'codekb.retrieval.guide_generator'`

- [ ] **Step 3: Create GuideGenerator**

Create `src/codekb/retrieval/guide_generator.py`:

```python
"""Guide generator: orchestrates SQLite queries + LLM generation for 4 guide tools."""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

from codekb.mcp.guide_cache import GuideCache
from codekb.storage.sqlite_store import SqliteStore

logger = logging.getLogger(__name__)

MAX_OUTPUT_CHARS = {
    "component_guide": 12000,
    "usage_examples": 8000,
    "recommend": 6000,
    "api_guide": 15000,
}


def _truncate_code(code: str, max_len: int = 1500) -> str:
    """Truncate code string to max_len with ellipsis."""
    if len(code) <= max_len:
        return code
    return code[:max_len] + "\n... (truncated)"


def _truncate_result(result: dict, tool_type: str) -> dict:
    """Truncate result to fit within output limit."""
    max_chars = MAX_OUTPUT_CHARS.get(tool_type, 10000)
    serialized = json.dumps(result, ensure_ascii=False)
    if len(serialized) <= max_chars:
        return result
    # Truncate code fields
    for key in ("usage_example", "example", "source", "code"):
        if key in result and isinstance(result[key], str) and len(result[key]) > 500:
            result[key] = _truncate_code(result[key], 500)
    for ex in result.get("examples", []):
        if "code" in ex and isinstance(ex["code"], str):
            ex["code"] = _truncate_code(ex["code"], 1000)
    return result


async def _call_llm(prompt: str, llm_client: dict) -> Optional[str]:
    """Call LLM via litellm. Returns response text or None on failure."""
    try:
        import litellm
        kwargs = {
            "model": llm_client.get("model", "gpt-4o-mini"),
            "messages": [{"role": "user", "content": prompt}],
        }
        if llm_client.get("api_base"):
            kwargs["api_base"] = llm_client["api_base"]
        if llm_client.get("api_key"):
            kwargs["api_key"] = llm_client["api_key"]
        response = await litellm.acompletion(**kwargs)
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f"LLM call failed: {e}")
        return None


class GuideGenerator:
    """Core logic for the 4 guide query tools."""

    def __init__(self, store: SqliteStore, cache: GuideCache):
        self.store = store
        self.cache = cache

    async def get_component_guide(
        self,
        repo_name: str,
        symbol_name: str,
        module: Optional[str] = None,
        llm_client: Optional[dict] = None,
    ) -> dict:
        """Generate a component usage guide."""
        tool_type = "component_guide"

        # Check cache
        cached = self.cache.get(repo_name, tool_type, symbol_name, module=module or "")
        if cached is not None:
            return cached

        # Step 1: Get symbol definition
        symbols = self.store.get_symbol_by_name(repo_name, symbol_name, repo_module=module)
        if not symbols:
            return {"error": f"Symbol not found: {symbol_name}"}

        main_symbol = symbols[0]
        # Collect child symbols (methods, nested types)
        children = [
            s for s in symbols[1:]
            if s.parent == symbol_name
        ] if len(symbols) > 1 else []

        # Step 2: Find callers
        calls = self.store.get_calls_to(repo_name, symbol_name, repo_module=module)
        caller_files = list(set(c.caller_file for c in calls))

        # Step 3: Find importers
        imports = self.store.get_imports(repo_name, repo_module=module)
        importer_files = [
            imp.file_path for imp in imports
            if symbol_name in (imp.imported_names or "")
        ]

        # Step 4: Extract caller code snippets
        all_caller_files = list(set(caller_files + importer_files))
        caller_snippets = []
        for f in all_caller_files[:5]:
            caller_symbols = self.store.get_symbols(repo_name, file_path=f)
            for cs in caller_symbols:
                if symbol_name in (cs.source or ""):
                    caller_snippets.append({
                        "file": cs.file_path,
                        "name": cs.name,
                        "line_range": f"{cs.start_line}-{cs.end_line}",
                        "code": _truncate_code(cs.source or "", 800),
                    })
                    if len(caller_snippets) >= 3:
                        break
            if len(caller_snippets) >= 3:
                break

        # Step 5: Find related symbols (same file, different name)
        related = list(set(
            s.name for s in self.store.get_symbols(repo_name, file_path=main_symbol.file_path)
            if s.name != symbol_name and not s.parent
        ))[:5]

        # Build properties from child symbols that look like properties/fields
        properties = []
        methods = []
        for child in children:
            entry = {"name": child.name, "signature": child.signature}
            if child.kind in ("method", "function"):
                methods.append(entry)
            else:
                properties.append(entry)

        # Step 6: LLM generation (optional)
        description = None
        usage_example = None
        if llm_client is not None:
            prompt = self._build_component_guide_prompt(
                symbol_name, main_symbol, properties, methods, caller_snippets
            )
            response = await _call_llm(prompt, llm_client)
            if response:
                try:
                    # Strip markdown code block if present
                    cleaned = re.sub(r'^```\w*\n?', '', response)
                    cleaned = re.sub(r'\n?```$', '', cleaned).strip()
                    llm_result = json.loads(cleaned)
                    description = llm_result.get("description")
                    usage_example = llm_result.get("usage_example")
                except json.JSONDecodeError:
                    description = response[:500]

        result = {
            "symbol_name": symbol_name,
            "type": main_symbol.kind,
            "language": main_symbol.language,
            "file": main_symbol.file_path,
            "module": main_symbol.repo_module,
            "description": description,
            "properties": properties,
            "methods": methods,
            "usage_example": usage_example,
            "related_symbols": related,
        }

        result = _truncate_result(result, tool_type)

        # Cache it
        self.cache.set(repo_name, tool_type, symbol_name, result, module=module or "")
        return result

    def _build_component_guide_prompt(self, name, symbol, properties, methods, snippets):
        props_str = "\n".join(f"  - {p['name']}: {p['signature']}" for p in properties) or "  (none)"
        methods_str = "\n".join(f"  - {m['name']}: {m['signature']}" for m in methods) or "  (none)"
        snippets_str = "\n".join(
            f"  File: {s['file']}\n  {s['code']}" for s in snippets
        ) or "  (no usage found)"

        return f"""Analyze this code component and generate a usage guide.

Component: {name} ({symbol.kind})
File: {symbol.file_path}
Source:
{symbol.source or ""}

Properties:
{props_str}

Methods:
{methods_str}

Usage in other files:
{snippets_str}

Return ONLY a JSON object with these fields:
- "description": A concise description of what this component does and when to use it (1-2 sentences)
- "usage_example": A minimal code example showing how to use this component (as a string)
"""

    async def get_usage_examples(
        self,
        repo_name: str,
        symbol_name: str,
        module: Optional[str] = None,
        top_k: int = 5,
        llm_client: Optional[dict] = None,
    ) -> dict:
        """Find actual usage examples of a symbol."""
        tool_type = "usage_examples"

        # Check cache
        cached = self.cache.get(repo_name, tool_type, symbol_name, module=module or "")
        if cached is not None:
            return cached

        # Get definition file to exclude
        def_symbols = self.store.get_symbol_by_name(repo_name, symbol_name, repo_module=module)
        def_files = set(s.file_path for s in def_symbols)

        # Find callers via calls table
        calls = self.store.get_calls_to(repo_name, symbol_name, repo_module=module)
        caller_files = set(c.caller_file for c in calls)

        # Find importers
        imports = self.store.get_imports(repo_name, repo_module=module)
        importer_files = set(
            imp.file_path for imp in imports
            if symbol_name in (imp.imported_names or "")
        )

        # Combine and exclude definition files
        candidate_files = (caller_files | importer_files) - def_files

        # Extract code snippets from callers
        examples = []
        for f in list(candidate_files)[:10]:
            file_symbols = self.store.get_symbols(repo_name, file_path=f)
            for s in file_symbols:
                if symbol_name in (s.source or ""):
                    context = None
                    # Optionally generate context with LLM
                    if llm_client is not None and len(examples) < top_k:
                        context = await self._generate_context(
                            symbol_name, s, llm_client
                        )
                    examples.append({
                        "file": s.file_path,
                        "line_range": f"{s.start_line}-{s.end_line}",
                        "code": _truncate_code(s.source or "", 800),
                        "context": context,
                    })
                    if len(examples) >= top_k:
                        break
            if len(examples) >= top_k:
                break

        result = {
            "symbol_name": symbol_name,
            "examples": examples,
        }
        result = _truncate_result(result, tool_type)
        self.cache.set(repo_name, tool_type, symbol_name, result, module=module or "")
        return result

    async def _generate_context(self, symbol_name, symbol, llm_client):
        """Generate one-line context for a usage example."""
        prompt = f"""In one short sentence, describe how {symbol_name} is used in this code:

File: {symbol.file_path}
Code: {symbol.source or ""[:1000]}

Context (one sentence):"""
        response = await _call_llm(prompt, llm_client)
        return response.strip() if response else None

    async def recommend_component(
        self,
        repo_name: str,
        requirement: str,
        module: Optional[str] = None,
        top_k: int = 3,
        llm_client: Optional[dict] = None,
    ) -> dict:
        """Recommend components based on a natural language requirement."""
        tool_type = "recommend"

        # Check cache
        cached = self.cache.get(repo_name, tool_type, requirement, module=module or "")
        if cached is not None:
            return cached

        # Get all top-level symbols
        symbols = self.store.get_symbols(repo_name, repo_module=module)
        # Build a compact symbol list for LLM
        symbol_list = []
        seen = set()
        for s in symbols:
            if not s.parent and s.name not in seen:
                seen.add(s.name)
                symbol_list.append(f"{s.name} ({s.kind}) - {s.repo_module}")
        symbol_list_str = "\n".join(f"{i+1}. {line}" for i, line in enumerate(symbol_list[:200]))

        if llm_client is None:
            return {
                "requirement": requirement,
                "recommendations": [],
                "error": "LLM client required for recommend_component",
            }

        prompt = f"""User requirement: {requirement}

Available components in {repo_name}:
{symbol_list_str}

Recommend the top {top_k} components that best match the requirement.
Return ONLY a JSON array, each element with:
- "symbol_name": exact name from the list above
- "relevance_score": 0-1
- "reason": one sentence explaining why it matches
- "brief_usage": one line showing how to use it
"""

        response = await _call_llm(prompt, llm_client)
        recommendations = []
        if response:
            try:
                cleaned = re.sub(r'^```\w*\n?', '', response)
                cleaned = re.sub(r'\n?```$', '', cleaned).strip()
                recommendations = json.loads(cleaned)
                if not isinstance(recommendations, list):
                    recommendations = [recommendations]
            except json.JSONDecodeError:
                logger.error(f"Failed to parse LLM recommendation response")

        result = {
            "requirement": requirement,
            "recommendations": recommendations[:top_k],
        }
        result = _truncate_result(result, tool_type)
        self.cache.set(repo_name, tool_type, requirement, result, module=module or "")
        return result

    async def get_api_guide(
        self,
        repo_name: str,
        library_name: str,
        module: Optional[str] = None,
        llm_client: Optional[dict] = None,
    ) -> dict:
        """Generate an API/library integration guide."""
        tool_type = "api_guide"

        # Check cache
        cached = self.cache.get(repo_name, tool_type, library_name, module=module or "")
        if cached is not None:
            return cached

        # Find export files (Index.ets, index.ts, etc.)
        file_tree = self.store.get_file_tree(repo_name, repo_module=module)
        export_files = [
            f for f in file_tree
            if f.path and (
                f.path.endswith("Index.ets") or
                f.path.endswith("index.ts") or
                f.path.endswith("index.js") or
                f.path.endswith("__init__.py")
            )
        ]

        # Find symbols in the target module
        target_module = module or library_name
        symbols = self.store.get_symbols(repo_name, repo_module=target_module)

        # Build component list
        components = []
        seen = set()
        for s in symbols:
            if not s.parent and s.name not in seen and s.kind in (
                "struct", "class", "interface", "enum", "function", "constant"
            ):
                seen.add(s.name)
                components.append({"name": s.name, "kind": s.kind})

        # Find imports of this library
        imports = self.store.get_imports(repo_name, repo_module=module)
        import_statements = []
        for imp in imports:
            if library_name in (imp.module or ""):
                import_statements.append(
                    f"import {{ {imp.imported_names} }} from '{imp.module}'"
                )

        # LLM generation
        guide = None
        if llm_client is not None:
            prompt = self._build_api_guide_prompt(
                library_name, components, import_statements, export_files
            )
            response = await _call_llm(prompt, llm_client)
            if response:
                try:
                    cleaned = re.sub(r'^```\w*\n?', '', response)
                    cleaned = re.sub(r'\n?```$', '', cleaned).strip()
                    guide = json.loads(cleaned)
                except json.JSONDecodeError:
                    guide = {"description": response[:500]}

        if guide is None:
            guide = {
                "description": None,
                "import_statement": import_statements[0] if import_statements else None,
                "components": [{"name": c["name"], "brief": None} for c in components[:20]],
                "setup": None,
                "example": None,
            }

        result = {
            "library_name": library_name,
            "guide": guide,
        }
        result = _truncate_result(result, tool_type)
        self.cache.set(repo_name, tool_type, library_name, result, module=module or "")
        return result

    def _build_api_guide_prompt(self, library_name, components, imports, export_files):
        comp_str = "\n".join(f"  - {c['name']} ({c['kind']})" for c in components[:30])
        imports_str = "\n".join(f"  {imp}" for imp in imports[:10]) or "  (none found)"
        files_str = "\n".join(f"  {f.path}" for f in export_files[:5]) or "  (none found)"

        return f"""Generate an API integration guide for the library/module: {library_name}

Exported components:
{comp_str}

Known import statements:
{imports_str}

Export files:
{files_str}

Return ONLY a JSON object with:
- "description": 1-2 sentence description of the library
- "import_statement": the typical import statement
- "components": array of {{""name"", ""brief""}} for each component (brief = 3-5 words)
- "setup": setup steps as a string (numbered list)
- "example": a complete code example showing how to integrate and use this library
"""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && python -m pytest tests/test_guide_generator.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/codekb/retrieval/guide_generator.py tests/test_guide_generator.py
git commit -m "feat: add GuideGenerator with 4 guide tools logic"
```

---

### Task 4: Register 4 new tools in MCP server

**Files:**
- Modify: `src/codekb/mcp/server.py`

- [ ] **Step 1: Add imports and service instantiation**

In `src/codekb/mcp/server.py`, add import at the top (after existing imports):

```python
from codekb.mcp.guide_cache import GuideCache
from codekb.retrieval.guide_generator import GuideGenerator
```

In `_create_server()`, after the line that creates `reference_builder` (line 50), add:

```python
    guide_cache = GuideCache(store)
    guide_generator = GuideGenerator(store, guide_cache)
```

- [ ] **Step 2: Add 4 new tool definitions to `list_tools()`**

In the `list_tools()` function, before the closing `]` of the returned list (before line 254), add these 4 tool definitions:

```python
            types.Tool(
                name="get_component_guide",
                description="Get a component usage guide: properties, methods, usage example, and related symbols. Returns complete guide in one call.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "repo_name": {"type": "string", "description": "Repository name"},
                        "symbol_name": {"type": "string", "description": "Component/class name"},
                        "module": {"type": "string", "description": "Optional module filter"},
                    },
                    "required": ["repo_name", "symbol_name"],
                },
            ),
            types.Tool(
                name="get_usage_examples_v2",
                description="Find real usage examples of a symbol in calling code, excluding its own definition file.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "repo_name": {"type": "string", "description": "Repository name"},
                        "symbol_name": {"type": "string", "description": "Symbol name to find usages for"},
                        "module": {"type": "string", "description": "Optional module filter"},
                        "top_k": {"type": "integer", "description": "Max examples (default 5)", "default": 5},
                    },
                    "required": ["repo_name", "symbol_name"],
                },
            ),
            types.Tool(
                name="recommend_component",
                description="Recommend components matching a natural language requirement description. Supports Chinese and English.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "repo_name": {"type": "string", "description": "Repository name"},
                        "requirement": {"type": "string", "description": "Requirement description (Chinese or English)"},
                        "module": {"type": "string", "description": "Optional module filter"},
                        "top_k": {"type": "integer", "description": "Max recommendations (default 3)", "default": 3},
                    },
                    "required": ["repo_name", "requirement"],
                },
            ),
            types.Tool(
                name="get_api_guide",
                description="Get an API/library integration guide with import statements, component list, setup steps, and code example.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "repo_name": {"type": "string", "description": "Repository name"},
                        "library_name": {"type": "string", "description": "Library or module name"},
                        "module": {"type": "string", "description": "Optional module filter"},
                    },
                    "required": ["repo_name", "library_name"],
                },
            ),
```

- [ ] **Step 3: Add 4 handler branches in `_handle_tool()`**

In `_handle_tool()`, update the function signature to include `guide_generator`:

```python
async def _handle_tool(
    name: str,
    arguments: dict,
    store: SqliteStore,
    vector_store: VectorStore,
    doc_store: DocStore,
    repo_manager: RepoManager,
    structure_query: StructureQuery,
    hybrid_search: HybridSearch,
    reference_builder: ReferenceBuilder,
    config: CodekbYamlConfig,
    guide_generator: GuideGenerator,
) -> dict | list:
```

Update the `call_tool()` call to pass `guide_generator`:

```python
            result = await _handle_tool(name, arguments, store, vector_store, doc_store,
                                         repo_manager, structure_query, hybrid_search,
                                         reference_builder, config, guide_generator)
```

Add the 4 new `elif` branches before the `else:` at the end of `_handle_tool()`:

```python
    elif name == "get_component_guide":
        return await guide_generator.get_component_guide(
            arguments["repo_name"],
            arguments["symbol_name"],
            module=arguments.get("module"),
            llm_client=_create_llm_client_dict(config, load_settings()),
        )

    elif name == "get_usage_examples_v2":
        return await guide_generator.get_usage_examples(
            arguments["repo_name"],
            arguments["symbol_name"],
            module=arguments.get("module"),
            top_k=arguments.get("top_k", 5),
            llm_client=_create_llm_client_dict(config, load_settings()),
        )

    elif name == "recommend_component":
        return await guide_generator.recommend_component(
            arguments["repo_name"],
            arguments["requirement"],
            module=arguments.get("module"),
            top_k=arguments.get("top_k", 3),
            llm_client=_create_llm_client_dict(config, load_settings()),
        )

    elif name == "get_api_guide":
        return await guide_generator.get_api_guide(
            arguments["repo_name"],
            arguments["library_name"],
            module=arguments.get("module"),
            llm_client=_create_llm_client_dict(config, load_settings()),
        )
```

- [ ] **Step 4: Add `_create_llm_client_dict` helper**

Add this helper function in `server.py` (before `_create_server`):

```python
def _create_llm_client_dict(config: CodekbYamlConfig, settings) -> Optional[dict]:
    """Create an LLM client config dict for guide tools. Reuses the same logic as CLI."""
    import os
    provider_name = config.assignments.doc_generation
    provider_config = config.llm_providers.get(provider_name)
    if provider_config is None:
        return None

    api_key = settings.OPENAI_API_KEY
    model = provider_config.model or "gpt-4o-mini"
    api_base = provider_config.base_url

    provider_type = provider_config.provider.lower()
    if provider_type == "openai" and api_base:
        if "deepseek" in (model or "").lower() or "deepseek" in (api_base or "").lower():
            api_key = os.environ.get("DEEPSEEK_API_KEY") or settings.OPENAI_API_KEY
            model = f"openai/{model}"
    elif "deepseek" in (model or "").lower():
        api_key = os.environ.get("DEEPSEEK_API_KEY") or settings.OPENAI_API_KEY

    return {
        "model": model,
        "api_base": api_base,
        "api_key": api_key,
    }
```

- [ ] **Step 5: Run existing tests to verify no regressions**

Run: `source .venv/bin/activate && python -m pytest tests/ -v`
Expected: All existing tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/codekb/mcp/server.py
git commit -m "feat: register 4 new guide query tools in MCP server"
```

---

### Task 5: Clear guide cache on re-index

**Files:**
- Modify: `src/codekb/core/repo_manager.py`

- [ ] **Step 1: Find where re-indexing happens**

Check `src/codekb/core/repo_manager.py` for the index method that clears structure data. Look for `clear_repo_structure`.

- [ ] **Step 2: Add cache clear call**

In the re-index method, after the call to `store.clear_repo_structure(repo_name)`, add:

```python
        # Clear guide cache on re-index
        from codekb.mcp.guide_cache import GuideCache
        guide_cache = GuideCache(store)
        guide_cache.clear(repo_name)
```

- [ ] **Step 3: Run tests to verify no regressions**

Run: `source .venv/bin/activate && python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add src/codekb/core/repo_manager.py
git commit -m "feat: clear guide cache on repo re-index"
```

---

### Task 6: End-to-end smoke test

**Files:**
- No new files

- [ ] **Step 1: Verify server starts with new tools**

Run: `source .venv/bin/activate && python -c "from codekb.mcp.server import _create_server; s = _create_server(); print('Server created OK')"`

Expected: `Server created OK`

- [ ] **Step 2: Run full test suite**

Run: `source .venv/bin/activate && python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 3: Final commit if any fixes needed**

```bash
git add -A
git commit -m "fix: address smoke test issues"
```
