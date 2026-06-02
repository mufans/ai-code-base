# CodeKB

English | [中文](README_CN.md)

Universal code knowledge base that indexes 100+ code repositories and provides MCP server tools for AI agents (Claude Code, Cursor, etc.) to query code, documentation, architecture, and generate code.

## Architecture

```
GitHub / GitLab / Gitee
        |  webhook push
        v
  +------------------+
  | Webhook Receiver  |  (FastAPI)
  +--------+---------+
           |
           v
  +------------------+     +------------------+     +------------------+
  | Layer 3: Docs    |     | Layer 2: Structure|    | Layer 1: Vectors |
  | (Markdown)       |     | (SQLite)         |    | (ChromaDB)       |
  +------------------+     +------------------+     +------------------+
           |                        |                        |
           +------------------------+------------------------+
                                    |
                                    v
                          +------------------+
                          |   MCP Server     |  (stdio / SSE)
                          +------------------+
                                    |
                                    v
                          AI Agent / IDE
```

**4-Layer Storage:**

| Layer | Backend | Content |
|-------|---------|---------|
| Layer 1 | ChromaDB | Semantic vector embeddings (code + docs) |
| Layer 2 | SQLite | Tree-sitter parsed structure (symbols, calls, imports) |
| Layer 3a | Markdown files | LLM-generated architecture docs |
| Layer 3b | Markdown files | Agent skills (actionable task-level prompts) |

**Key design decision:** Zero LLM calls at query time (MCP runtime). All LLM processing happens at index time. Query-time retrieval is pure vector + SQLite lookups.

## Quick Start

### Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### Configure

```bash
cp .env.example .env
cp codekb.example.yaml codekb.yaml
# Edit .env with your API keys
# Edit codekb.yaml with your settings
```

### Add a Repository

```bash
# Add a remote repo
codekb repo add https://github.com/org/repo

# Add a local repo
codekb repo add --local /path/to/local/repo

# Add with alias
codekb repo add https://github.com/org/repo --name my-project
```

### Index and Sync

```bash
# Sync all repos (incremental)
codekb sync

# Full re-index a specific repo
codekb reindex my-project

# Force full sync of all repos
codekb sync --full
```

### MCP Server

```bash
# Start stdio MCP server (for CLI tools like Claude Code)
codekb serve

# Start SSE MCP server (for web-based tools)
codekb serve --transport sse --port 8000
```

### Docs & Skills

```bash
# Generate architecture docs
codekb docs generate my-project

# Verify generated docs
codekb docs verify my-project

# Generate agent skills
codekb skills generate my-project

# List skills
codekb skills list my-project
```

### Webhook Receiver

```bash
# Start webhook server for auto-indexing on push
codekb webhook --port 8080
```

## CLI Reference

```
codekb repo add <url> [--branch main] [--name alias] [--local]
codekb repo remove <name>
codekb repo list
codekb repo info <name>

codekb sync [--repo name] [--full]
codekb reindex <name>
codekb serve [--transport stdio|sse] [--port 8000]
codekb webhook [--port 8080]

codekb docs generate <name>
codekb docs verify <name>

codekb skills list <name>
codekb skills generate <name>
codekb skills verify <name>
codekb skills review <name> <skill> [--approve/--reject]
```

## MCP Tools

**17 tools** organized into 4 categories:

### Query Tools (Core Entry Points)

| Tool | Description |
|------|-------------|
| `query_usage` | Primary "how to use" tool — auto-follows doc-first chain: README → API guide → component guide → usage examples → recommendation |
| `find_symbol` | Find which repo and module a symbol belongs to (cross-repo search) |
| `resolve_keyword` | Resolve ambiguous keyword to repo/module/symbol type |
| `search_code` | Semantic code search across repositories |

### Management & Metadata

| Tool | Description |
|------|-------------|
| `list_repos` | List all indexed repositories |
| `get_repo_info` | Detailed repository information |
| `list_modules` | List modules in a repo (monorepo support) |
| `get_module_dependencies` | Cross-module dependency relationships |
| `get_structure` | Code structure (classes, functions, signatures); `repo_name` optional |
| `get_symbol_detail` | Full symbol definition + call graph; `repo_name` optional |
| `get_file_content` | Read file content |

### Documentation & Skills

| Tool | Description |
|------|-------------|
| `get_architecture` | Generated architecture document |
| `get_code_template` | Project code templates |
| `list_skills` | List available agent skills |
| `get_skill` | Get skill content |
| `list_doc_index` | List document index (metadata only) |
| `read_doc` | Read full markdown document by path |

## Project Structure

```
src/codekb/
  cli/main.py           # Typer CLI
  core/
    config.py           # YAML + .env config loading (multi-provider)
    repo_manager.py     # Repo clone, register, metadata
    indexer.py          # Index orchestrator (full/incremental)
  indexers/
    tree_sitter.py      # AST parsing (Python, JavaScript)
    embedder.py         # Vector embedding pipeline
    doc_generator.py    # LLM doc generation + verification
    skill_generator.py  # Skill generation + auto-verify
  storage/
    sqlite_store.py     # SQLite metadata + structure (incl. guide_cache)
    vector_store.py     # ChromaDB vector operations
    doc_store.py        # Markdown file storage
  retrieval/
    semantic_search.py  # Vector semantic search
    structure_query.py  # Structured SQLite queries (incl. find_symbol, resolve)
    hybrid_search.py    # RRF hybrid search (vector + keyword)
    reference_builder.py # Usage examples, templates, guides
    guide_generator.py  # LLM-powered guide generation (component/API/examples)
  mcp/
    server.py           # MCP server (17 tools + resources)
    guide_cache.py      # Guide result caching layer
  webhook/
    receiver.py         # FastAPI webhook receiver
    adapters/           # GitHub, GitLab, Gitee adapters
```

## Tech Stack

| Component | Choice | Rationale |
|-----------|--------|-----------|
| Language | Python 3.11+ | MCP SDK support, mature ecosystem |
| CLI | Typer | Type-safe, auto-completion |
| Web | FastAPI | Webhook receiver, async |
| MCP SDK | mcp[cli] | Anthropic official Python SDK |
| Code parsing | tree-sitter | Multi-language AST parsing |
| Vector DB | ChromaDB | Lightweight embedded, no extra service |
| Structured storage | SQLite | Lightweight, single-file |
| LLM interface | litellm | Unified interface for all providers |
| Embedding | Pluggable | Default sentence-transformers, optional OpenAI |

**Multi-Provider Config** — `codekb.yaml` supports multiple LLM/embedding providers via litellm format:

```yaml
codekb:
  llm_providers:
    openai:
      provider: openai
      base_url: https://api.openai.com/v1
      model: gpt-4o-mini
    deepseek:
      provider: openai
      base_url: https://api.deepseek.com/v1
      model: deepseek-chat
  assignments:
    doc_generation: openai
```
| Config | pydantic-settings | Type-safe config, .env support |

## Testing

```bash
source .venv/bin/activate
python -m pytest tests/ -v
```

86 tests covering config, storage, tree-sitter parsing, guide cache/generator, webhooks, and full integration pipeline.

## License

MIT
