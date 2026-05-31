# Code Knowledge Base (codekb) - Design Spec

## Overview

A universal code knowledge base system that indexes 100+ code repositories (ranging from a few hundred KB to 100MB each) and provides MCP server tools for AI agents to query code, documentation, architecture, and generate code. Supports both human search and agent-driven code generation workflows.

## Key Decisions

| Decision | Choice |
|----------|--------|
| Use case | Search platform + Agent code generation |
| Repo types | Open source + internal projects, multi-language |
| Update mechanism | Webhook receiver + local indexing |
| Retrieval | Hybrid (keyword + vector semantic) |
| Architecture | 4-layer (architecture docs + agent skills + tree-sitter structure + vector embeddings) |
| Language | Python 3.11+ |
| Embedding | Pluggable (default local sentence-transformers, optional OpenAI API) |
| Vector store | ChromaDB |
| Code generation | MCP provides query/reference tools only, Agent handles generation |
| Git platforms | GitHub + GitLab + Gitee |
| Deployment | Single machine, local |
| LLM boundary | LLM participates during index building, not during MCP queries |
| README handling | Coverage assessment + on-demand supplementation |
| Multi-provider | Multiple LLM/embedding providers with purpose-based assignment |
| Secrets | Read from .env file, never stored in config yaml |

## System Architecture

```
                          GitHub / GitLab / Gitee
                                  |
                            webhook push
                                  |
                                  v
                    +---------------------------+
                    |    Webhook Receiver        |
                    |  (FastAPI, lightweight)    |
                    +------------+--------------+
                                 |
                                 v
                    +---------------------------+
                    |    Index Worker            |
                    |  1. git fetch changes      |
                    |  2. Build 3-layer index    |
                    |  3. Incremental updates    |
                    +------------+--------------+
                                 |
              +------------------+------------------+
              v                  v                  v
    +-----------------+  +----------------+  +----------------+
    | Layer 3:        |  | Layer 2:       |  | Layer 1:       |
    | Architecture    |  | Structure      |  | Semantic       |
    | Docs            |  | Index          |  | Vectors        |
    |                 |  |                |  |                |
    | Markdown files  |  | SQLite         |  | ChromaDB       |
    | (README +       |  | (tree-sitter   |  | (Embedding     |
    |  generated)     |  |  AST data)     |  |  vectors)      |
    +-----------------+  +----------------+  +----------------+
              |                  |                  |
              +------------------+------------------+
                                 |
                                 v
                    +---------------------------+
                    |    MCP Server              |
                    |  (stdio/SSE transport)     |
                    |                            |
                    |  Tools:                    |
                    |  - search_code             |
                    |  - get_structure           |
                    |  - get_architecture        |
                    |  - get_readme              |
                    |  - get_usage_examples      |
                    |  - get_integration_guide   |
                    |  - get_code_template       |
                    |  - list_repos              |
                    +---------------------------+
                                 |
                                 v
                        AI Agent / IDE
                   (Claude Code / Cursor / etc.)
```

## Data Directory Structure

```
~/.codekb/
├── repos/                                # Cloned repositories
│   ├── github.com_org_project-a/
│   │   ├── .git/
│   │   ├── README.md
│   │   └── docs/
│   └── gitlab.com_team_service-b/
│
├── index/                                # Index data
│   ├── metadata.db                       # SQLite: repo metadata, index status
│   ├── structure.db                      # SQLite: tree-sitter parsed structure
│   │                                     #   (symbol table, call graph, dependency tree)
│   └── vectors/                          # ChromaDB: semantic vector data
│
└── generated/                            # LLM-generated architecture docs
    ├── github.com_org_project-a/
    │   ├── ARCHITECTURE.md               # Architecture overview
    │   ├── CORE_CHAIN.md                 # Core chain analysis
    │   ├── API_REFERENCE.md              # API reference
    │   ├── CONFIGURATION.md              # Configuration docs
    │   ├── USAGE.md                      # Usage summary
    │   ├── COVERAGE.json                 # README coverage metadata
    │   └── skills/                       # Generated Agent Skills
    │       ├── add-cache-to-service.md
    │       ├── create-api-endpoint.md
    │       └── add-database-migration.md
    └── gitlab.com_team_service-b/
```

## 4-Layer Storage Design

### Layer 3a: Architecture Docs (Markdown files)

- Original README and docs from repos are read-only, never modified
- LLM-generated docs are stored separately in `generated/` directory
- Generated docs supplement what README doesn't cover, not replace it

**Documentation Coverage Assessment:**

Before generating docs, the system evaluates what the existing README covers across 8 dimensions:

1. Project overview
2. Quick start (install, configure, run)
3. Architecture overview
4. Core chains (main business/data flows)
5. API reference
6. Configuration reference
7. Dependencies explanation
8. Usage examples

For each dimension, the system marks it as: covered by README, partially covered, or missing. Only missing/partial dimensions are generated.

**Coverage metadata is stored in COVERAGE.json:**
```json
{
  "total_dimensions": 8,
  "covered_by_readme": ["overview", "quickstart", "dependencies"],
  "generated": ["architecture", "core_chain", "configuration"],
  "enriched": ["api_reference"],
  "missing": []
}
```

**Document quality assurance (3-stage):**

1. **Fact-based generation**: LLM receives tree-sitter parsed data (real symbol names, signatures, call graphs, file trees) as ground truth context, not asked to free-form generate
2. **Automated verification**: Programmatic checks (no LLM) against structure.db - verify class/function names exist, call relationships match the call graph, core modules are covered
3. **Quality tagging**: Each generated doc has a quality tag in its frontmatter:
   - `verified` - all assertions passed verification
   - `partial` - some assertions unverified, listed explicitly
   - `draft` - unverified initial draft
   - `manual` - human-reviewed and approved

### Layer 3b: Agent Skills (Structured prompts)

In addition to descriptive architecture docs, the system generates actionable **Skills** — structured prompts that tell agents HOW to work with a specific project.

**Skill vs Architecture Doc:**
- Architecture Doc: "The project has a RedisPool class that manages connections" (descriptive)
- Skill: "To add caching to a Service, import CacheService, inject in `__init__`, use @cached decorator" (prescriptive)

**Skill generation:**
- LLM generates Skills based on tree-sitter structure data + architecture docs + real usage patterns from the codebase
- Skills are task-level and actionable: "add-cache", "create-api-endpoint", "add-database-migration"
- Each Skill includes: trigger conditions, step-by-step instructions with code templates, gotchas and conventions

**Skill trust levels (tiered trust system):**

| Status | Meaning | Agent Behavior |
|--------|---------|----------------|
| `draft` | Auto-generated + automated checks passed | Agent can use but warns user |
| `verified` | Human reviewed and approved | Agent uses directly, no warning |
| `deprecated` | Code changes invalidated the skill | Agent does not use |

**Skill file format:**
```yaml
---
name: add-cache-to-service
confidence: 0.85
status: draft              # draft / verified / deprecated
verified_steps:            # Automated check results
  - "Import path src.cache exists"
  - "CacheService class found in structure index"
  - "@cached decorator signature matches"
unverified_steps:          # Could not auto-verify
  - "TTL default 300 seconds"  # reason: cannot infer from code
source_commit: abc123
last_verified_at: 2026-05-31T10:00:00Z
---
# Skill content (Markdown with code templates)
```

**Skill lifecycle:**
1. **Generation**: LLM generates during index build, after architecture docs are ready
2. **Auto-verification** (no LLM): Check import paths exist, class names in structure.db, code snippets match repo files
3. **Usage**: `draft` skills usable with warning, `verified` skills used directly
4. **Human review**: `codekb skills review <repo>` — CLI lists draft skills for human to approve
5. **Code change handling**: Webhook triggers re-verification of affected skills; passing keeps `verified`, failing downgrades to `deprecated`
6. **Regeneration**: Optionally auto-regenerate deprecated skills on code change

### Layer 2: Structure Index (SQLite)

Tree-sitter parsed structured data:
- Symbol table: classes, functions, methods with signatures, parameters, return types
- Call graph: function call relationships
- Import/dependency graph
- File tree and directory structure
- Entry point identification (main, app, index files)

### Layer 1: Semantic Vectors (ChromaDB)

- Code chunks at function/class level granularity (using tree-sitter boundaries, not arbitrary splitting)
- Document paragraphs from README and generated docs
- Each vector associated with metadata: `{repo, file, line_range, symbol, type}`

## Index Building

### Full Index (first-time `repo add`)

```
Step 1: Clone
  git clone --depth 1 → ~/.codekb/repos/<platform>_<org>_<repo>/
  Detect primary language, framework, project structure

Step 2: Tree-sitter Parsing (no LLM)
  Iterate all source files
  → Parse AST, extract symbols, record signatures
  → Build import/dependency graph
  → Build function call graph
  → Identify entry points
  Result → structure.db

Step 3: Vectorization (Embedding model)
  Chunk based on tree-sitter boundaries (function/class level)
  → Code chunks → Embedding → ChromaDB
  → README/docs paragraphs → Embedding → ChromaDB
  Each vector linked to {repo, file, line_range, symbol, type}

Step 4: README Analysis + Doc Generation (LLM)
  Input to LLM:
  - structure.db summary (module list, entry points, core classes)
  - Original README full text
  - Key entry file code (main, config, router)
  - Dependency list

  LLM executes:
  → Assess README coverage → COVERAGE.json
  → Generate missing architecture docs → ARCHITECTURE.md
  → Generate core chain docs → CORE_CHAIN.md
  → Generate other docs as needed → API_REFERENCE.md / CONFIGURATION.md

Step 5: Document Quality Verification (no LLM for core checks)
  Programmatic checks against structure.db:
  → Class/function names mentioned exist in symbol table
  → Call relationships match call graph
  → Core modules are all covered
  Result: quality tag + unverified_claims list in doc frontmatter

Step 6: Vectorize Generated Docs (Embedding model)
  Generated architecture docs → chunk by section → Embedding → ChromaDB
  Enables semantic search to also match architecture doc content

Step 7: Skill Generation (LLM)
  Based on architecture docs + structure data + real usage patterns
  → LLM generates task-level Skills (add-cache, create-api-endpoint, etc.)
  → Auto-verify: check import paths, class names, code snippets against structure.db
  → Tag with status: draft / verified / deprecated
  → Store in generated/<repo>/skills/
```

### Incremental Index (webhook triggered)

```
Webhook push event
  → Compare changed file list (git diff --name-only)
  → For unchanged files: skip
  → For changed source files (.py, .java, .go, etc.):
      - Re-parse with tree-sitter
      - Update structure.db (delete old symbols + insert new)
      - Re-vectorize changed file's code chunks
      - Update ChromaDB
  → For changed docs (README.md, docs/):
      - Update original docs
      - Re-assess coverage
      - Re-generate affected docs if needed
  → For config/dependency changes:
      - Update dependency info
      - Trigger doc re-generation if dependencies changed significantly
```

### Initial Indexing (CLI)

```bash
# Add single repo
codekb repo add <url-or-local-path> [--branch main] [--name alias]

# Batch import from file
codekb repo add --file repos.txt

# Add local repo
codekb repo add --local /path/to/repo

# Sync all repos (build/update indexes)
codekb sync [--repo name] [--full]

# Force full re-index
codekb reindex <name>
```

### Concurrency

- 100+ repos can be indexed in parallel (each repo is independent)
- Within a single repo, tree-sitter parsing can be parallelized per file
- LLM doc generation is the bottleneck (~2-4 LLM calls per repo)
- Embedding can use batch API calls to reduce overhead
- Incremental updates are fast (typically only a few changed files)

## MCP Server

### Transport

- stdio (for CLI tools like Claude Code)
- SSE (for web-based tools)

### Tools

**Repository Management:**

- `list_repos()` - List all indexed repos with basic info
  Returns: `[{name, url, language, last_indexed, file_count}]`

- `get_repo_info(repo_name)` - Detailed repo information
  Returns: `{tech_stack, structure_summary, readme_summary, doc_coverage}`

**Document Queries:**

- `get_readme(repo_name)` - Original README full text

- `get_architecture(repo_name)` - Generated architecture document
  Returns: `{architecture_doc, confidence, generated_sections}`

- `get_core_chain(repo_name)` - Core chain analysis document

- `get_config_docs(repo_name)` - Configuration documentation

- `get_api_reference(repo_name)` - API reference document

**Code Queries:**

- `search_code(query, repo_name?, file_type?, top_k=10)` - Semantic code search
  Returns: `[{file, line_range, code, score, context}]`

- `get_structure(repo_name, path?)` - Code structure (classes, functions, signatures)
  Returns: `[{name, type, signature, file, line, children}]`

- `get_symbol_detail(repo_name, symbol_name)` - Full symbol definition and references
  Returns: `{definition, references, call_graph}`

- `get_file_content(repo_name, file_path, start_line?, end_line?)` - File raw content
  Returns: `{content, language}`

**Reference Tools (for Agent code generation assistance):**

- `get_usage_examples(repo_name, library_or_pattern)` - Real usage examples from the codebase
  Searches ChromaDB + structure.db for real code patterns, returns actual code snippets
  Returns: `[{description, code, file}]`

- `get_integration_guide(repo_name, library)` - Integration guide
  Aggregates from knowledge base: config items, init code, usage examples, dependencies
  Returns: `{config_items, init_code_examples, usage_examples, dependencies}`

- `get_code_template(repo_name, pattern_type)` - Typical code patterns in project
  pattern_type: controller / service / repository / config / test ...
  Returns: `{template, conventions, examples}`

**Skill Tools:**

- `list_skills(repo_name)` - List available skills for a repo
  Returns: `[{name, status, confidence, description}]`

- `get_skill(repo_name, skill_name)` - Get a specific skill's full content
  Returns: `{name, status, confidence, content, verified_steps, unverified_steps}`
  Agent behavior: if status is `draft`, include a warning; if `deprecated`, refuse to return

**MCP Resources:**

- `codekb://repos/{repo}/readme`
- `codekb://repos/{repo}/architecture`
- `codekb://repos/{repo}/core-chain`
- `codekb://repos/{repo}/file/{path}`

### LLM Boundary

- **Index time (offline):** LLM participates in doc generation and coverage assessment; verification is programmatic (no LLM)
- **Query time (online, MCP runtime):** Zero LLM calls - pure retrieval from pre-built indexes
- All code examples and references come from real indexed code, not LLM-generated

## Multi-Provider Configuration

### Config file (codekb.yaml)

```yaml
codekb:
  data_dir: ~/.codekb

  llm_providers:
    openai:
      provider: openai
      # api_key read from .env OPENAI_API_KEY
    anthropic:
      provider: anthropic
      # api_key read from .env ANTHROPIC_API_KEY
    local:
      provider: ollama
      base_url: http://localhost:11434
    azure-openai:
      provider: azure
      api_version: "2024-02-01"
      base_url: https://xxx.openai.azure.com
      # api_key read from .env AZURE_API_KEY

  embedding_providers:
    local:
      provider: sentence-transformers
      model: all-MiniLM-L6-v2
      dimension: 384
    openai:
      provider: openai
      model: text-embedding-3-small
      dimension: 1536
    codestral:
      provider: openai
      model: codestral-embed
      base_url: https://embed.mistral.ai/v1
      dimension: 1024

  assignments:
    doc_generation: openai          # LLM for generating architecture docs
    code_embedding: local           # Embedding for code chunks
    doc_embedding: local            # Embedding for doc chunks

  webhook:
    # secret read from .env WEBHOOK_SECRET
    port: 8080

  index:
    chunk_size: 512
    chunk_overlap: 64
    max_file_size: 1MB
    exclude_patterns:
      - "*.min.js"
      - "*.lock"
      - "node_modules/**"
      - "__pycache__/**"
      - ".git/**"
      - "dist/**"
      - "build/**"
```

### .env file

```env
OPENAI_API_KEY=sk-xxx
ANTHROPIC_API_KEY=sk-ant-xxx
MISTRAL_API_KEY=xxx
AZURE_API_KEY=xxx
WEBHOOK_SECRET=xxx
```

**API key resolution rule:** Each provider automatically reads the corresponding key from `.env` based on provider type (e.g., `openai` -> `OPENAI_API_KEY`, `anthropic` -> `ANTHROPIC_API_KEY`). No explicit api_key needed in yaml.

### Provider Interfaces

```python
class EmbeddingProvider(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]
    async def embed_query(self, text: str) -> list[float]

# Implementations:
# - SentenceTransformerProvider (local)
# - OpenAIEmbeddingProvider (OpenAI API)
# - OllamaEmbeddingProvider (local Ollama)

# LLM via litellm unified interface
# Supports: openai, anthropic, azure, ollama, vllm, etc.
```

## Webhook Receiver

### Supported Platforms

- GitHub (push events)
- GitLab (push events)
- Gitee (push events)

### Adapter Interface

```python
class GitPlatformAdapter(Protocol):
    def parse_webhook(self, payload, headers) -> WebhookEvent
    def register_webhook(self, repo_url, callback_url) -> bool

# Implementations: GitHubAdapter, GitLabAdapter, GiteeAdapter
```

### Webhook Flow

1. Platform sends push event to `POST /webhook/{platform}`
2. Receiver validates signature using secret from .env
3. Adapter parses payload into normalized `WebhookEvent` (repo, branch, changed files)
4. Triggers Index Worker for incremental update

## Project Structure

```
code-knowledge-base/
├── pyproject.toml
├── src/codekb/
│   ├── __init__.py
│   ├── cli/
│   │   ├── __init__.py
│   │   └── main.py                 # typer CLI: repo add/remove/list, sync, serve, webhook
│   ├── core/
│   │   ├── __init__.py
│   │   ├── repo_manager.py         # Repo clone, register, metadata
│   │   ├── indexer.py              # Index orchestrator (full/incremental)
│   │   └── config.py               # Config loading (.env + yaml)
│   ├── indexers/
│   │   ├── __init__.py
│   │   ├── tree_sitter.py          # Layer 2: tree-sitter → structure.db
│   │   ├── embedder.py             # Layer 1: vectorization → ChromaDB
│   │   ├── doc_generator.py        # Layer 3a: LLM doc generation + coverage
│   │   └── skill_generator.py      # Layer 3b: LLM skill generation + auto-verify
│   ├── storage/
│   │   ├── __init__.py
│   │   ├── sqlite_store.py         # SQLite operations
│   │   ├── vector_store.py         # ChromaDB operations
│   │   └── doc_store.py            # Markdown file read/write
│   ├── retrieval/
│   │   ├── __init__.py
│   │   ├── semantic_search.py      # Vector semantic search
│   │   ├── structure_query.py      # Structured queries
│   │   ├── hybrid_search.py        # Hybrid search (vector + keyword ranking)
│   │   └── reference_builder.py    # Reference tools (examples/templates/guides)
│   ├── mcp/
│   │   ├── __init__.py
│   │   └── server.py               # MCP server + tool registration
│   └── webhook/
│       ├── __init__.py
│       ├── receiver.py             # FastAPI webhook receiver
│       └── adapters/
│           ├── github.py
│           ├── gitlab.py
│           └── gitee.py
├── tests/
│   ├── test_indexer.py
│   ├── test_storage.py
│   ├── test_retrieval.py
│   ├── test_mcp.py
│   └── test_webhook.py
├── codekb.example.yaml
└── .env.example
```

## Technology Stack

| Component | Choice | Rationale |
|-----------|--------|-----------|
| Language | Python 3.11+ | Mature ecosystem, official MCP SDK support |
| CLI | typer | Type-safe, auto-completion |
| Web framework | FastAPI | Webhook receiver, async support |
| MCP SDK | mcp[cli] | Anthropic official Python MCP SDK |
| Code parsing | py-tree-sitter | Multi-language AST parsing, industry standard |
| Vector DB | ChromaDB | Lightweight embedded, no extra service needed |
| Structured storage | SQLite (aiosqlite) | Lightweight, single-file, Python built-in |
| LLM interface | litellm | Unified interface for all providers |
| Embedding | Pluggable | Default sentence-transformers, optional OpenAI |
| Config | pydantic-settings | Type-safe config, .env support |

## CLI Commands

```bash
# Repository management
codekb repo add <url-or-local-path> [--branch main] [--name alias]
codekb repo remove <name>
codekb repo list
codekb repo info <name>

# Index management
codekb sync [--repo name] [--full]     # Sync indexes, incremental by default
codekb reindex <name>                  # Force full re-index

# Services
codekb serve                           # Start MCP server (stdio)
codekb webhook --port 8080             # Start webhook receiver

# Document management
codekb docs generate <name>            # Manually trigger doc generation
codekb docs verify <name>              # Manually trigger doc verification

# Skill management
codekb skills list <name>              # List all skills for a repo
codekb skills review <name>            # Review draft skills, approve to verified
codekb skills generate <name>          # Manually trigger skill generation
codekb skills verify <name>            # Re-verify all skills against current code
```
