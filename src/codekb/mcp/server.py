"""MCP Server exposing all codekb retrieval and management tools."""

from __future__ import annotations

import json
from typing import Optional

import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from codekb.core.config import CodekbYamlConfig, ensure_data_dir, load_config, load_settings
from codekb.core.indexer import IndexOrchestrator
from codekb.core.repo_manager import RepoManager
from codekb.indexers.embedder import create_embedding_provider
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
                name="search_code",
                description="Semantic code search across repositories",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"},
                        "repo_name": {"type": "string", "description": "Optional repo filter"},
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
                    "properties": {"repo_name": {"type": "string"}},
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
                    },
                    "required": ["repo_name", "pattern_type"],
                },
            ),
            types.Tool(
                name="list_skills",
                description="List available skills for a repository",
                inputSchema={
                    "type": "object",
                    "properties": {"repo_name": {"type": "string"}},
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
                    },
                    "required": ["repo_name", "skill_name"],
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
        try:
            result = await _handle_tool(name, arguments, store, vector_store, doc_store,
                                         repo_manager, structure_query, hybrid_search,
                                         reference_builder, config)
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

    elif name == "search_code":
        query = arguments["query"]
        repo_name = arguments.get("repo_name")
        top_k = arguments.get("top_k", 10)
        results = await hybrid_search.search(query, repo_name=repo_name, top_k=top_k)
        return [
            {
                "file": r.file_path,
                "line_range": f"{r.metadata.get('start_line', '')}-{r.metadata.get('end_line', '')}",
                "code": r.content[:500],
                "score": round(r.score, 4),
                "context": {
                    "name": r.metadata.get("name", ""),
                    "kind": r.metadata.get("kind", ""),
                },
            }
            for r in results
        ]

    elif name == "get_structure":
        return structure_query.get_structure(
            arguments["repo_name"],
            path=arguments.get("path"),
        )

    elif name == "get_symbol_detail":
        return structure_query.get_symbol_detail(
            arguments["repo_name"],
            arguments["symbol_name"],
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
        content = doc_store.read_doc(arguments["repo_name"], "ARCHITECTURE.md")
        coverage = doc_store.read_coverage(arguments["repo_name"])
        return {
            "architecture_doc": content or "Not yet generated",
            "confidence": coverage,
            "generated_sections": [
                f for f in doc_store.list_docs(arguments["repo_name"])
                if f.endswith(".md")
            ],
        }

    elif name == "get_usage_examples":
        return await reference_builder.get_usage_examples(
            arguments["repo_name"],
            arguments["library_or_pattern"],
            top_k=arguments.get("top_k", 5),
        )

    elif name == "get_integration_guide":
        return await reference_builder.get_integration_guide(
            arguments["repo_name"],
            arguments["library"],
        )

    elif name == "get_code_template":
        return await reference_builder.get_code_template(
            arguments["repo_name"],
            arguments["pattern_type"],
        )

    elif name == "list_skills":
        skills = doc_store.list_skills(arguments["repo_name"])
        return [{"name": s, "status": "available"} for s in skills]

    elif name == "get_skill":
        content = doc_store.read_skill(arguments["repo_name"], arguments["skill_name"])
        if content is None:
            return {"error": "Skill not found"}
        return {"name": arguments["skill_name"], "content": content}

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
