# find_symbol Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `find_symbol` MCP tool for cross-repo symbol lookup, and make `repo_name` optional on `query_usage`, `get_symbol_detail`, and `get_structure` with automatic symbol resolution.

**Architecture:** Three-layer change following existing patterns: `SqliteStore` adds `find_symbol_across_repos` for cross-repo SQL queries, `StructureQuery` adds `find_symbol` and `resolve_symbol` as business logic wrappers, and `server.py` registers the new MCP tool and wires auto-resolution into existing tools.

**Tech Stack:** Python 3.11+, SQLite, MCP SDK, pytest

---

## File Structure

| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `src/codekb/storage/sqlite_store.py` | Add `find_symbol_across_repos` method |
| Modify | `src/codekb/retrieval/structure_query.py` | Add `find_symbol` and `resolve_symbol` methods |
| Modify | `src/codekb/mcp/server.py` | Register `find_symbol` tool, make `repo_name` optional on 3 tools, add auto-resolution |
| Modify | `tests/test_storage.py` | Add tests for `find_symbol_across_repos` |

---

### Task 1: SqliteStore — `find_symbol_across_repos`

**Files:**
- Modify: `src/codekb/storage/sqlite_store.py` (after line 356, in the Symbol operations section)
- Test: `tests/test_storage.py`

- [ ] **Step 1: Write the failing test**

Add to `TestSqliteStore` class in `tests/test_storage.py`:

```python
def test_find_symbol_across_repos(self, sqlite_store):
    """Test cross-repo symbol search."""
    sqlite_store.insert_symbols([
        Symbol(repo_name="repo-a", file_path="svc.py", name="QuoteService",
               kind="class", signature="class QuoteService:",
               start_line=1, end_line=10, language="python",
               repo_module="quote_service"),
        Symbol(repo_name="repo-b", file_path="util.py", name="QuoteService",
               kind="class", signature="class QuoteService:",
               start_line=5, end_line=20, language="python"),
        Symbol(repo_name="repo-a", file_path="other.py", name="OtherClass",
               kind="class", start_line=1, end_line=5, language="python"),
    ])
    results = sqlite_store.find_symbol_across_repos("QuoteService")
    assert len(results) == 2
    assert results[0].repo_name == "repo-a"
    assert results[0].repo_module == "quote_service"
    assert results[1].repo_name == "repo-b"

def test_find_symbol_across_repos_no_match(self, sqlite_store):
    """Test cross-repo search with no matches."""
    sqlite_store.insert_symbols([
        Symbol(repo_name="repo-a", file_path="a.py", name="Foo",
               kind="class", start_line=1, end_line=1, language="python"),
    ])
    results = sqlite_store.find_symbol_across_repos("NonExistent")
    assert results == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/macadmin/agentProj/ai-code-base && source .venv/bin/activate && python -m pytest tests/test_storage.py::TestSqliteStore::test_find_symbol_across_repos -v`
Expected: FAIL with `AttributeError: 'SqliteStore' object has no attribute 'find_symbol_across_repos'`

- [ ] **Step 3: Write minimal implementation**

Add this method to `SqliteStore` in `src/codekb/storage/sqlite_store.py`, after the `get_symbol_by_name` method (after line 356):

```python
def find_symbol_across_repos(self, name: str) -> list[Symbol]:
    """Cross-repo exact symbol search by name."""
    conn = self._connect(self._struct_path)
    rows = conn.execute(
        "SELECT * FROM symbols WHERE name = ? ORDER BY repo_name, repo_module",
        (name,),
    ).fetchall()
    conn.close()
    return [Symbol(**dict(r)) for r in rows]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/macadmin/agentProj/ai-code-base && source .venv/bin/activate && python -m pytest tests/test_storage.py::TestSqliteStore::test_find_symbol_across_repos tests/test_storage.py::TestSqliteStore::test_find_symbol_across_repos_no_match -v`
Expected: Both PASS

- [ ] **Step 5: Commit**

```bash
git add src/codekb/storage/sqlite_store.py tests/test_storage.py
git commit -m "feat: add find_symbol_across_repos to SqliteStore"
```

---

### Task 2: StructureQuery — `find_symbol` and `resolve_symbol`

**Files:**
- Modify: `src/codekb/retrieval/structure_query.py` (after `list_modules` method, around line 195)
- Test: `tests/test_storage.py` (added to existing `TestSqliteStore` for simplicity, since `StructureQuery` is a thin wrapper and we test the store directly)

- [ ] **Step 1: Write the failing test**

Add to `TestSqliteStore` class in `tests/test_storage.py`:

```python
def test_find_symbol_cross_repo_via_structure_query(self, sqlite_store):
    """Test StructureQuery.find_symbol delegates correctly."""
    from codekb.retrieval.structure_query import StructureQuery
    sqlite_store.insert_symbols([
        Symbol(repo_name="repo-a", file_path="svc.py", name="QuoteService",
               kind="class", signature="class QuoteService:",
               start_line=1, end_line=10, language="python",
               repo_module="quote_service"),
    ])
    sq = StructureQuery(sqlite_store)
    results = sq.find_symbol("QuoteService")
    assert len(results) == 1
    assert results[0]["repo_name"] == "repo-a"
    assert results[0]["module"] == "quote_service"
    assert results[0]["kind"] == "class"

def test_resolve_symbol_unique_match(self, sqlite_store):
    """Test resolve_symbol with unique match returns (repo_name, module)."""
    from codekb.retrieval.structure_query import StructureQuery
    sqlite_store.insert_symbols([
        Symbol(repo_name="repo-a", file_path="svc.py", name="QuoteService",
               kind="class", start_line=1, end_line=10, language="python",
               repo_module="quote_service"),
    ])
    sq = StructureQuery(sqlite_store)
    result = sq.resolve_symbol("QuoteService")
    assert result == ("repo-a", "quote_service")

def test_resolve_symbol_no_match(self, sqlite_store):
    """Test resolve_symbol with no match returns None."""
    from codekb.retrieval.structure_query import StructureQuery
    sq = StructureQuery(sqlite_store)
    result = sq.resolve_symbol("NonExistent")
    assert result is None

def test_resolve_symbol_multiple_repos(self, sqlite_store):
    """Test resolve_symbol with matches in multiple repos returns None."""
    from codekb.retrieval.structure_query import StructureQuery
    sqlite_store.insert_symbols([
        Symbol(repo_name="repo-a", file_path="a.py", name="Foo",
               kind="class", start_line=1, end_line=5, language="python"),
        Symbol(repo_name="repo-b", file_path="b.py", name="Foo",
               kind="class", start_line=1, end_line=5, language="python"),
    ])
    sq = StructureQuery(sqlite_store)
    result = sq.resolve_symbol("Foo")
    assert result is None

def test_resolve_symbol_same_repo_different_modules(self, sqlite_store):
    """Test resolve_symbol with same repo but different modules returns first match."""
    from codekb.retrieval.structure_query import StructureQuery
    sqlite_store.insert_symbols([
        Symbol(repo_name="repo-a", file_path="a.py", name="Foo",
               kind="class", start_line=1, end_line=5, language="python",
               repo_module="mod_a"),
        Symbol(repo_name="repo-a", file_path="b.py", name="Foo",
               kind="class", start_line=1, end_line=5, language="python",
               repo_module="mod_b"),
    ])
    sq = StructureQuery(sqlite_store)
    result = sq.resolve_symbol("Foo")
    # Same repo, different modules — still resolves to that repo
    assert result == ("repo-a", "mod_a")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/macadmin/agentProj/ai-code-base && source .venv/bin/activate && python -m pytest tests/test_storage.py::TestSqliteStore::test_find_symbol_cross_repo_via_structure_query -v`
Expected: FAIL with `AttributeError: 'StructureQuery' object has no attribute 'find_symbol'`

- [ ] **Step 3: Write minimal implementation**

Add these two methods to `StructureQuery` in `src/codekb/retrieval/structure_query.py`, after the `list_modules` method (after line 195):

```python
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
    # Group by repo_name
    repos = {s.repo_name for s in symbols}
    if len(repos) > 1:
        return None
    # Single repo — return first match (module may vary)
    first = symbols[0]
    return (first.repo_name, first.repo_module or None)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/macadmin/agentProj/ai-code-base && source .venv/bin/activate && python -m pytest tests/test_storage.py::TestSqliteStore::test_find_symbol_cross_repo_via_structure_query tests/test_storage.py::TestSqliteStore::test_resolve_symbol_unique_match tests/test_storage.py::TestSqliteStore::test_resolve_symbol_no_match tests/test_storage.py::TestSqliteStore::test_resolve_symbol_multiple_repos tests/test_storage.py::TestSqliteStore::test_resolve_symbol_same_repo_different_modules -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/codekb/retrieval/structure_query.py tests/test_storage.py
git commit -m "feat: add find_symbol and resolve_symbol to StructureQuery"
```

---

### Task 3: MCP Server — Register `find_symbol` tool

**Files:**
- Modify: `src/codekb/mcp/server.py`

- [ ] **Step 1: Add tool definition to `list_tools`**

In `src/codekb/mcp/server.py`, in the `list_tools` function, add this tool definition **before** the `search_code` tool (around line 117), so the two query tools stay grouped:

```python
            types.Tool(
                name="find_symbol",
                description=(
                    "Find which repository and module a symbol (class, function, etc.) belongs to. "
                    "Use when you know a symbol name but not its repo_name, e.g. find_symbol(symbol_name='QuoteService'). "
                    "Returns repo_name, module, file_path, kind, signature for each match across all repos."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "symbol_name": {"type": "string", "description": "Symbol name to search for (exact match)"},
                    },
                    "required": ["symbol_name"],
                },
            ),
```

- [ ] **Step 2: Add handler in `_handle_tool`**

In `src/codekb/mcp/server.py`, in the `_handle_tool` function, add this case **before** the `elif name == "search_code":` block (around line 551):

```python
    elif name == "find_symbol":
        results = structure_query.find_symbol(arguments["symbol_name"])
        return {
            "results": results,
            "total": len(results),
        }
```

- [ ] **Step 3: Update `_CODEKB_INSTRUCTIONS`**

In `src/codekb/mcp/server.py`, update `_CODEKB_INSTRUCTIONS` (line 28) to:

```python
_CODEKB_INSTRUCTIONS = """\
Use `query_usage` for all "how to use" and "what is X" questions. It automatically follows doc-first priority: README -> API guide -> component guide -> usage examples -> recommendation.

Use `search_code` only when you need to find specific code snippets, definitions, or search by content.

Use `find_symbol` when you know a symbol name but not which repo/module it belongs to. Returns the symbol's repo_name and module so you can call other tools.

Use `list_doc_index` + `read_doc` for reading specific documentation files by path.

Key principle: Documentation first, code second."""
```

- [ ] **Step 4: Run full test suite to verify nothing is broken**

Run: `cd /Users/macadmin/agentProj/ai-code-base && source .venv/bin/activate && python -m pytest tests/ -v`
Expected: All existing tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/codekb/mcp/server.py
git commit -m "feat: register find_symbol MCP tool"
```

---

### Task 4: MCP Server — Make `repo_name` optional on 3 tools with auto-resolution

**Files:**
- Modify: `src/codekb/mcp/server.py`

- [ ] **Step 1: Update `query_usage` schema — remove `repo_name` from required**

In `src/codekb/mcp/server.py`, find the `query_usage` tool definition (around line 108-115). Change `inputSchema` to:

```python
                inputSchema={
                    "type": "object",
                    "properties": {
                        "repo_name": {"type": "string", "description": "Repository name (optional — auto-resolved if omitted)"},
                        "query": {"type": "string", "description": "Query content (e.g. 'QuoteService usage', 'how to use Router')"},
                        "module": {"type": "string", "description": "Optional module name"},
                        "symbol": {"type": "string", "description": "Optional symbol name (class, function, etc.)"},
                    },
                    "required": ["query"],
                },
```

- [ ] **Step 2: Update `get_structure` schema — remove `repo_name` from required**

Find the `get_structure` tool definition (around line 165-175). Change `inputSchema` to:

```python
                inputSchema={
                    "type": "object",
                    "properties": {
                        "repo_name": {"type": "string", "description": "Repository name (optional — auto-resolved if omitted)"},
                        "path": {"type": "string", "description": "Optional file path filter"},
                        "module": {"type": "string", "description": "Optional module filter"},
                    },
                    "required": [],
                },
```

- [ ] **Step 3: Update `get_symbol_detail` schema — remove `repo_name` from required**

Find the `get_symbol_detail` tool definition (around line 177-189). Change `inputSchema` to:

```python
                inputSchema={
                    "type": "object",
                    "properties": {
                        "repo_name": {"type": "string", "description": "Repository name (optional — auto-resolved if omitted)"},
                        "symbol_name": {"type": "string"},
                        "module": {"type": "string", "description": "Optional module filter"},
                    },
                    "required": ["symbol_name"],
                },
```

- [ ] **Step 4: Add auto-resolution helper function**

Add this helper function in `src/codekb/mcp/server.py`, **before** `_handle_query_usage` (around line 370):

```python
def _resolve_repo(structure_query: StructureQuery, symbol_name: str) -> dict:
    """Try to auto-resolve a symbol to a repo_name.

    Returns {"repo_name": str} on success,
    or {"error": str} / {"candidates": list} on failure.
    """
    resolved = structure_query.resolve_symbol(symbol_name)
    if resolved is None:
        # Check if zero or multiple repos
        results = structure_query.find_symbol(symbol_name)
        if not results:
            return {"error": f"Symbol '{symbol_name}' not found in any repository. Use list_repos to see available repos."}
        # Multiple repos
        candidates = [
            {"repo_name": r["repo_name"], "module": r["module"]}
            for r in results
        ]
        return {
            "error": f"Symbol '{symbol_name}' found in multiple repos. Please specify repo_name.",
            "candidates": candidates,
        }
    return {"repo_name": resolved[0], "module_hint": resolved[1]}
```

- [ ] **Step 5: Wire auto-resolution into `_handle_query_usage`**

At the top of `_handle_query_usage` (line 381), replace the existing `repo_name = arguments["repo_name"]` line with:

```python
    repo_name = arguments.get("repo_name")
    query = arguments["query"]
    module = arguments.get("module")
    symbol = arguments.get("symbol")

    # Auto-resolve repo_name if not provided
    if not repo_name:
        lookup_name = symbol or query
        resolved = _resolve_repo(structure_query, lookup_name)
        if "error" in resolved:
            return resolved
        repo_name = resolved["repo_name"]
        # If module not provided but resolution found one, use it as hint
        if not module and resolved.get("module_hint"):
            module = resolved["module_hint"]
```

- [ ] **Step 6: Wire auto-resolution into `get_structure` handler**

In `_handle_tool`, find the `elif name == "get_structure":` block (around line 573). Replace it with:

```python
    elif name == "get_structure":
        repo_name = arguments.get("repo_name")
        if not repo_name:
            resolved = _resolve_repo(structure_query, arguments.get("path", ""))
            if "error" in resolved:
                return resolved
            repo_name = resolved["repo_name"]
        result = structure_query.get_structure(
            repo_name,
            path=arguments.get("path"),
            repo_module=arguments.get("module"),
        )
        # Truncate large results to avoid exceeding token limits
        if isinstance(result, list) and len(result) > 20:
            total = len(result)
            result = result[:20]
            result.append({
                "file": f"... and {total - 20} more files",
                "symbols": [],
                "truncated": True,
                "hint": "Use 'path' or 'module' parameter to narrow results",
            })
        return result
```

- [ ] **Step 7: Wire auto-resolution into `get_symbol_detail` handler**

In `_handle_tool`, find the `elif name == "get_symbol_detail":` block (around line 591). Replace it with:

```python
    elif name == "get_symbol_detail":
        repo_name = arguments.get("repo_name")
        if not repo_name:
            resolved = _resolve_repo(structure_query, arguments["symbol_name"])
            if "error" in resolved:
                return resolved
            repo_name = resolved["repo_name"]
        return structure_query.get_symbol_detail(
            repo_name,
            arguments["symbol_name"],
            repo_module=arguments.get("module"),
        ) or {"error": "Symbol not found"}
```

- [ ] **Step 8: Run full test suite**

Run: `cd /Users/macadmin/agentProj/ai-code-base && source .venv/bin/activate && python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 9: Commit**

```bash
git add src/codekb/mcp/server.py
git commit -m "feat: make repo_name optional with auto-resolution on query_usage, get_symbol_detail, get_structure"
```
