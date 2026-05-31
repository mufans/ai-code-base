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

| Tool | Description |
|------|-------------|
| `list_repos` | List all indexed repos |
| `get_repo_info` | Detailed repo information |
| `search_code` | Semantic code search |
| `get_structure` | Code structure (classes, functions, signatures) |
| `get_symbol_detail` | Full symbol definition + call graph |
| `get_file_content` | Read file content |
| `get_readme` | README content |
| `get_architecture` | Generated architecture doc |
| `get_usage_examples` | Real code pattern examples |
| `get_integration_guide` | Library integration guide |
| `get_code_template` | Project code templates |
| `list_skills` | List available agent skills |
| `get_skill` | Get skill content |

## Project Structure

```
src/codekb/
  cli/main.py           # Typer CLI
  core/
    config.py           # YAML + .env config loading
    repo_manager.py     # Repo clone, register, metadata
    indexer.py          # Index orchestrator (full/incremental)
  indexers/
    tree_sitter.py      # AST parsing (Python, JavaScript)
    embedder.py         # Vector embedding pipeline
    doc_generator.py    # LLM doc generation + verification
    skill_generator.py  # Skill generation + auto-verify
  storage/
    sqlite_store.py     # SQLite metadata + structure
    vector_store.py     # ChromaDB vector operations
    doc_store.py        # Markdown file storage
  retrieval/
    semantic_search.py  # Vector semantic search
    structure_query.py  # Structured SQLite queries
    hybrid_search.py    # RRF hybrid search (vector + keyword)
    reference_builder.py # Usage examples, templates, guides
  mcp/server.py         # MCP server (14 tools + resources)
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
| Config | pydantic-settings | Type-safe config, .env support |

## Testing

```bash
source .venv/bin/activate
python -m pytest tests/ -v
```

61 tests covering config, storage, tree-sitter parsing, webhooks, and full integration pipeline.

## License

MIT
