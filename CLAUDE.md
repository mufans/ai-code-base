# CodeKB Project Instructions

## Project Overview

CodeKB is a universal code knowledge base that indexes code repositories and exposes them via an MCP server for AI agents. It uses a 4-layer storage architecture: ChromaDB vectors, SQLite structure, Markdown docs, and agent skills.

## Build & Run

```bash
source .venv/bin/activate

# Install
pip install -e ".[dev]"

# Run CLI
codekb --help
codekb repo list
codekb serve

# Tests
python -m pytest tests/ -v
```

## Architecture

- **Entry point**: `src/codekb/cli/main.py` (Typer CLI app)
- **MCP server**: `src/codekb/mcp/server.py` (17 tools + resources)
- **Index orchestrator**: `src/codekb/core/indexer.py` (coordinates tree-sitter + embedding)
- **Storage**: SQLite (metadata.db + structure.db) + ChromaDB (vectors) + Markdown files (docs)

## Key Patterns

- Config loading: `CodekbYamlConfig` from YAML + `Settings` from .env (pydantic-settings)
- Multi-provider LLM: `llm_providers` dict in YAML config, litellm format (openai, deepseek, etc.)
- Services instantiated via `_get_services()` in CLI and MCP server
- Tree-sitter uses `LanguageStrategy` protocol per language (Python, JavaScript implemented)
- Embedding providers implement `EmbeddingProvider` protocol (sentence-transformers, OpenAI)
- All query-time operations are zero-LLM (pure retrieval from pre-built indexes)
- LLM calls only during index building (doc generation, skill generation) and guide generation
- Cross-repo symbol search: `find_symbol_across_repos` in SqliteStore, `find_symbol`/`resolve_symbol` in StructureQuery
- `repo_name` auto-resolution: `query_usage`, `get_structure`, `get_symbol_detail` accept optional repo_name
- Guide cache: `GuideCache` wrapper (`mcp/guide_cache.py`) caches LLM-generated guide results in metadata.db
- Guide generator: `GuideGenerator` (`retrieval/guide_generator.py`) produces component/API/usage guides via LLM

## Code Style

- Python 3.11+ with `from __future__ import annotations`
- Pydantic v2 models for data structures
- Async for embedding and LLM operations, sync for tree-sitter and SQLite
- Tests use `pytest` with `pytest-asyncio` (asyncio_mode = "auto")

## File Conventions

- Storage models in `storage/sqlite_store.py` (Pydantic + SQLite operations, incl. guide_cache table)
- Indexer modules in `indexers/` (tree_sitter, embedder, doc_generator, skill_generator)
- Retrieval modules in `retrieval/` (semantic_search, structure_query, hybrid_search, reference_builder, guide_generator)
- MCP modules in `mcp/` (server.py, guide_cache.py)
- All subpackages have `__init__.py`

## Data Directory

Default `~/.codekb/` with subdirs: `repos/`, `index/` (metadata.db, structure.db), `index/vectors/` (ChromaDB), `generated/` (docs + skills).
