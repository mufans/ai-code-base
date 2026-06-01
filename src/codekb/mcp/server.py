"""MCP Server exposing all codekb retrieval and management tools."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from codekb.core.config import CodekbYamlConfig, ensure_data_dir, load_config, load_settings
from codekb.core.indexer import IndexOrchestrator
from codekb.core.repo_manager import RepoManager
from codekb.indexers.embedder import create_embedding_provider
from codekb.mcp.guide_cache import GuideCache
from codekb.retrieval.guide_generator import GuideGenerator
from codekb.retrieval.hybrid_search import HybridSearch
from codekb.retrieval.reference_builder import ReferenceBuilder
from codekb.retrieval.semantic_search import SemanticSearch
from codekb.retrieval.structure_query import StructureQuery
from codekb.storage.doc_store import DocStore
from codekb.storage.sqlite_store import SqliteStore
from codekb.storage.vector_store import VectorStore


def _get_services(config: Optional[CodekbYamlConfig] = None):
    """Instantiate all services."""
    if config is None:
        config = load_config()
    data_dir = ensure_data_dir(config)
    settings = load_settings()
    store = SqliteStore(data_dir / "index")
    vector_store = VectorStore(data_dir / "index" / "vectors")
    doc_store = DocStore(data_dir / "generated")
    repo_manager = RepoManager(config, store)
    return config, settings, store, vector_store, doc_store, repo_manager


def _create_llm_client_dict(config: CodekbYamlConfig, settings) -> Optional[dict]:
    """Create an LLM client config dict for guide tools."""
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


def _create_server(config: Optional[CodekbYamlConfig] = None) -> Server:
    """Create and configure the MCP server with all tools and resources."""
    config, settings, store, vector_store, doc_store, repo_manager = _get_services(config)

    server = Server("codekb")

    # Set up retrieval services
    embedding_provider = create_embedding_provider(config, settings, "code_embedding")
    semantic_search = SemanticSearch(vector_store, embedding_provider)
    structure_query = StructureQuery(store)
    hybrid_search = HybridSearch(semantic_search, store)
    reference_builder = ReferenceBuilder(store, structure_query, hybrid_search)
    guide_cache = GuideCache(store)
    guide_generator = GuideGenerator(store, guide_cache)

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name="list_repos",
                description="List all indexed repositories",
                inputSchema={"type": "object", "properties": {}, "required": []},
            ),
            types.Tool(
                name="get_repo_info",
                description="Get detailed repository information",
                inputSchema={
                    "type": "object",
                    "properties": {"repo_name": {"type": "string", "description": "Repository name"}},
                    "required": ["repo_name"],
                },
            ),
            types.Tool(
                name="list_modules",
                description="List all modules in a repository (for monorepo/multi-package support)",
                inputSchema={
                    "type": "object",
                    "properties": {"repo_name": {"type": "string"}},
                    "required": ["repo_name"],
                },
            ),
            types.Tool(
                name="get_module_dependencies",
                description="Get cross-module dependency relationships in a repository",
                inputSchema={
                    "type": "object",
                    "properties": {"repo_name": {"type": "string"}},
                    "required": ["repo_name"],
                },
            ),
            types.Tool(
                name="search_code",
                description="Semantic code search across repositories",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"},
                        "repo_name": {"type": "string", "description": "Optional repo filter"},
                        "module": {"type": "string", "description": "Optional module filter"},
                        "top_k": {"type": "integer", "description": "Max results", "default": 10},
                    },
                    "required": ["query"],
                },
            ),
            types.Tool(
                name="get_structure",
                description="Get code structure (classes, functions, signatures)",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "repo_name": {"type": "string"},
                        "path": {"type": "string", "description": "Optional file path filter"},
                        "module": {"type": "string", "description": "Optional module filter"},
                    },
                    "required": ["repo_name"],
                },
            ),
            types.Tool(
                name="get_symbol_detail",
                description="Get full symbol definition, references, and call graph",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "repo_name": {"type": "string"},
                        "symbol_name": {"type": "string"},
                        "module": {"type": "string", "description": "Optional module filter"},
                    },
                    "required": ["repo_name", "symbol_name"],
                },
            ),
            types.Tool(
                name="get_file_content",
                description="Read file content from a repository",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "repo_name": {"type": "string"},
                        "file_path": {"type": "string"},
                        "start_line": {"type": "integer"},
                        "end_line": {"type": "integer"},
                    },
                    "required": ["repo_name", "file_path"],
                },
            ),
            types.Tool(
                name="get_readme",
                description="Get README content from a repository",
                inputSchema={
                    "type": "object",
                    "properties": {"repo_name": {"type": "string"}},
                    "required": ["repo_name"],
                },
            ),
            types.Tool(
                name="get_architecture",
                description="Get generated architecture document for a repository",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "repo_name": {"type": "string"},
                        "module": {"type": "string", "description": "Optional module filter"},
                    },
                    "required": ["repo_name"],
                },
            ),
            types.Tool(
                name="get_usage_examples",
                description="Find real usage examples of a library or pattern",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "repo_name": {"type": "string"},
                        "library_or_pattern": {"type": "string"},
                        "top_k": {"type": "integer", "default": 5},
                        "module": {"type": "string", "description": "Optional module filter"},
                    },
                    "required": ["repo_name", "library_or_pattern"],
                },
            ),
            types.Tool(
                name="get_integration_guide",
                description="Get integration guide for a library in a project",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "repo_name": {"type": "string"},
                        "library": {"type": "string"},
                        "module": {"type": "string", "description": "Optional module filter"},
                    },
                    "required": ["repo_name", "library"],
                },
            ),
            types.Tool(
                name="get_code_template",
                description="Get typical code patterns for a project",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "repo_name": {"type": "string"},
                        "pattern_type": {
                            "type": "string",
                            "description": "controller, service, repository, config, test, model, etc.",
                        },
                        "module": {"type": "string", "description": "Optional module filter"},
                    },
                    "required": ["repo_name", "pattern_type"],
                },
            ),
            types.Tool(
                name="list_skills",
                description="List available skills for a repository",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "repo_name": {"type": "string"},
                        "module": {"type": "string", "description": "Optional module filter"},
                    },
                    "required": ["repo_name"],
                },
            ),
            types.Tool(
                name="get_skill",
                description="Get a specific skill's full content",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "repo_name": {"type": "string"},
                        "skill_name": {"type": "string"},
                        "module": {"type": "string", "description": "Optional module filter"},
                    },
                    "required": ["repo_name", "skill_name"],
                },
            ),
            types.Tool(
                name="list_doc_index",
                description="List document index for a repository (file name, type, size, title). Lightweight metadata only, no content.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "repo_name": {"type": "string", "description": "Repository name"},
                        "doc_type": {"type": "string", "description": "Optional type filter: readme, claude_md, skill, architecture, docs, other"},
                    },
                    "required": ["repo_name"],
                },
            ),
            types.Tool(
                name="read_doc",
                description="Read the full content of a markdown document from a repository by file path",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "repo_name": {"type": "string", "description": "Repository name"},
                        "file_path": {"type": "string", "description": "Relative file path, e.g. CLAUDE.md or docs/architecture.md"},
                    },
                    "required": ["repo_name", "file_path"],
                },
            ),
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
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
        try:
            result = await _handle_tool(name, arguments, store, vector_store, doc_store,
                                         repo_manager, structure_query, hybrid_search,
                                         reference_builder, config, guide_generator)
            return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]
        except Exception as e:
            return [types.TextContent(type="text", text=json.dumps({"error": str(e)}))]

    @server.list_resources()
    async def list_resources() -> list[types.Resource]:
        repos = store.list_repos()
        resources = []
        for repo in repos:
            resources.append(types.Resource(
                uri=f"codekb://repos/{repo.name}/readme",
                name=f"{repo.name} README",
                mimeType="text/markdown",
            ))
            resources.append(types.Resource(
                uri=f"codekb://repos/{repo.name}/architecture",
                name=f"{repo.name} Architecture",
                mimeType="text/markdown",
            ))
            resources.append(types.Resource(
                uri=f"codekb://repos/{repo.name}/core-chain",
                name=f"{repo.name} Core Chain",
                mimeType="text/markdown",
            ))
            # Add module-level resources
            modules_data = store.get_repo_modules(repo.name)
            for mod in modules_data:
                mod_name = mod["name"]
                resources.append(types.Resource(
                    uri=f"codekb://repos/{repo.name}/modules/{mod_name}/architecture",
                    name=f"{repo.name}/{mod_name} Architecture",
                    mimeType="text/markdown",
                ))
        return resources

    @server.list_resource_templates()
    async def list_resource_templates() -> list[types.ResourceTemplate]:
        return [
            types.ResourceTemplate(
                uriTemplate="codekb://repos/{repo}/file/{path}",
                name="Repository file",
                mimeType="text/plain",
            ),
        ]

    @server.read_resource()
    async def read_resource(uri: types.AnyUrl) -> str:
        uri_str = str(uri)

        if uri_str.startswith("codekb://repos/"):
            parts = uri_str[len("codekb://repos/"):].split("/", 1)
            if len(parts) >= 1:
                repo_name = parts[0]
                rest = parts[1] if len(parts) > 1 else ""

                if rest == "readme":
                    content = structure_query.get_readme(repo_name)
                    return content or f"No README found for {repo_name}"
                elif rest == "architecture":
                    content = doc_store.read_doc(repo_name, "ARCHITECTURE.md")
                    return content or f"No architecture doc generated for {repo_name}"
                elif rest == "core-chain":
                    content = doc_store.read_doc(repo_name, "CORE_CHAIN.md")
                    return content or f"No core chain doc generated for {repo_name}"
                elif rest.startswith("modules/") and "/architecture" in rest:
                    # Module-level architecture resource
                    # Format: modules/{module_name}/architecture
                    mod_rest = rest[len("modules/"):]
                    mod_name = mod_rest.split("/")[0]
                    content = doc_store.read_doc(repo_name, "ARCHITECTURE.md",
                                                  repo_module=mod_name)
                    return content or f"No architecture doc for module {mod_name}"
                elif rest.startswith("file/"):
                    file_path = rest[5:]
                    result = structure_query.get_file_content(repo_name, file_path)
                    return result["content"] if result else f"File not found: {file_path}"

        return f"Unknown resource: {uri_str}"

    return server


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
    if name == "list_repos":
        repos = store.list_repos()
        return [
            {
                "name": r.name,
                "url": r.url,
                "language": r.language,
                "last_indexed": r.last_indexed_at,
                "file_count": r.file_count,
                "status": r.status,
            }
            for r in repos
        ]

    elif name == "get_repo_info":
        repo = store.get_repo(arguments["repo_name"])
        if repo is None:
            return {"error": "Repo not found"}
        stats = structure_query.get_stats(repo.name)
        return {
            "name": repo.name,
            "url": repo.url,
            "language": repo.language,
            "framework": repo.framework,
            "status": repo.status,
            "tech_stack": stats.get("languages", {}),
            "structure_summary": {
                "total_symbols": stats.get("total_symbols", 0),
                "total_files": stats.get("total_files", 0),
                "entry_points": stats.get("entry_points", []),
            },
            "readme_summary": "See get_readme tool",
            "doc_coverage": doc_store.read_coverage(repo.name),
        }

    elif name == "list_modules":
        modules = structure_query.list_modules(arguments["repo_name"])
        modules_data = store.get_repo_modules(arguments["repo_name"])
        return {
            "modules": modules_data if modules_data else [{"name": m} for m in modules],
            "is_monorepo": len(modules) > 0,
        }

    elif name == "get_module_dependencies":
        return structure_query.get_module_dependencies(arguments["repo_name"])

    elif name == "search_code":
        query = arguments["query"]
        repo_name = arguments.get("repo_name")
        repo_module = arguments.get("module")
        top_k = arguments.get("top_k", 10)
        results = await hybrid_search.search(query, repo_name=repo_name,
                                              repo_module=repo_module, top_k=top_k)
        return [
            {
                "file": r.file_path,
                "line_range": f"{r.metadata.get('start_line', '')}-{r.metadata.get('end_line', '')}",
                "code": r.content[:500],
                "score": round(r.score, 4),
                "module": r.repo_module,
                "context": {
                    "name": r.metadata.get("name", ""),
                    "kind": r.metadata.get("kind", ""),
                },
            }
            for r in results
        ]

    elif name == "get_structure":
        result = structure_query.get_structure(
            arguments["repo_name"],
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

    elif name == "get_symbol_detail":
        return structure_query.get_symbol_detail(
            arguments["repo_name"],
            arguments["symbol_name"],
            repo_module=arguments.get("module"),
        ) or {"error": "Symbol not found"}

    elif name == "get_file_content":
        result = structure_query.get_file_content(
            arguments["repo_name"],
            arguments["file_path"],
            start_line=arguments.get("start_line"),
            end_line=arguments.get("end_line"),
        )
        return result or {"error": "File not found"}

    elif name == "get_readme":
        content = structure_query.get_readme(arguments["repo_name"])
        return {"content": content} if content else {"error": "README not found"}

    elif name == "get_architecture":
        repo_module = arguments.get("module", "")
        content = doc_store.read_doc(arguments["repo_name"], "ARCHITECTURE.md",
                                      repo_module=repo_module)
        coverage = doc_store.read_coverage(arguments["repo_name"], repo_module=repo_module)
        return {
            "architecture_doc": content or "Not yet generated",
            "confidence": coverage,
            "generated_sections": [
                f for f in doc_store.list_docs(arguments["repo_name"],
                                                repo_module=arguments.get("module", ""))
                if f.endswith(".md")
            ],
        }

    elif name == "get_usage_examples":
        return await reference_builder.get_usage_examples(
            arguments["repo_name"],
            arguments["library_or_pattern"],
            top_k=arguments.get("top_k", 5),
            repo_module=arguments.get("module"),
        )

    elif name == "get_integration_guide":
        return await reference_builder.get_integration_guide(
            arguments["repo_name"],
            arguments["library"],
            repo_module=arguments.get("module"),
        )

    elif name == "get_code_template":
        return await reference_builder.get_code_template(
            arguments["repo_name"],
            arguments["pattern_type"],
            repo_module=arguments.get("module"),
        )

    elif name == "list_skills":
        repo_module = arguments.get("module", "")
        skills = doc_store.list_skills(arguments["repo_name"], repo_module=repo_module)
        return [{"name": s, "status": "available"} for s in skills]

    elif name == "get_skill":
        repo_module = arguments.get("module", "")
        content = doc_store.read_skill(arguments["repo_name"], arguments["skill_name"],
                                        repo_module=repo_module)
        if content is None:
            return {"error": "Skill not found"}
        return {"name": arguments["skill_name"], "content": content}

    elif name == "list_doc_index":
        doc_type = arguments.get("doc_type")
        entries = store.get_doc_index(arguments["repo_name"], doc_type=doc_type)
        return [
            {
                "file_path": e["file_path"],
                "doc_type": e["doc_type"],
                "title": e["title"],
                "size_bytes": e["size_bytes"],
            }
            for e in entries
        ]

    elif name == "read_doc":
        entry = store.get_doc_index_by_path(arguments["repo_name"], arguments["file_path"])
        if entry is None:
            return {"error": f"Document not found in index: {arguments['file_path']}"}
        full_path = Path(entry["full_path"])
        if not full_path.exists():
            return {"error": f"File not found on disk: {full_path}"}
        content = full_path.read_text(encoding="utf-8", errors="replace")
        return {
            "file_path": entry["file_path"],
            "doc_type": entry["doc_type"],
            "title": entry["title"],
            "content": content,
        }

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

    else:
        return {"error": f"Unknown tool: {name}"}


def run_server(transport: str = "stdio", port: int = 8000, config: Optional[CodekbYamlConfig] = None):
    """Run the MCP server."""
    server = _create_server(config)

    if transport == "stdio":
        import asyncio
        async def _run():
            async with stdio_server() as (read_stream, write_stream):
                await server.run(read_stream, write_stream, server.create_initialization_options())
        asyncio.run(_run())
    elif transport == "sse":
        # SSE transport using mcp CLI helper
        import subprocess
        subprocess.run([
            "python", "-m", "mcp", "server", "sse",
            "--port", str(port),
            "--module", "codekb.mcp.server",
        ])
    else:
        raise ValueError(f"Unknown transport: {transport}")
