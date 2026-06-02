"""CodeKB CLI - Command line interface."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from codekb import __version__
from codekb.core.config import CodekbYamlConfig, Settings, ensure_data_dir, get_data_dir, load_config, load_settings
from codekb.core.repo_manager import RepoManager
from codekb.core.indexer import IndexOrchestrator
from codekb.storage.doc_store import DocStore
from codekb.storage.sqlite_store import SqliteStore
from codekb.storage.vector_store import VectorStore

console = Console()
error_console = Console(stderr=True)

app = typer.Typer(
    name="codekb",
    help="Universal code knowledge base with MCP server for AI agents.",
    no_args_is_help=True,
)

repo_app = typer.Typer(help="Repository management.")
app.add_typer(repo_app, name="repo")

docs_app = typer.Typer(help="Document management.")
app.add_typer(docs_app, name="docs")

skills_app = typer.Typer(help="Skill management.")
app.add_typer(skills_app, name="skills")


def _get_services(config: Optional[CodekbYamlConfig] = None):
    """Get common services."""
    if config is None:
        config = load_config()
    data_dir = ensure_data_dir(config)
    store = SqliteStore(data_dir / "index")
    vector_store = VectorStore(data_dir / "index" / "vectors")
    doc_store = DocStore(data_dir / "generated")
    repo_manager = RepoManager(config, store)
    return config, store, vector_store, doc_store, repo_manager


def _create_llm_client(config: CodekbYamlConfig, settings: Settings) -> Optional[dict]:
    """Create an LLM client config dict from config + settings.

    Returns a dict with 'model', 'api_base', 'api_key' or None if no LLM configured.
    """
    provider_name = config.assignments.doc_generation
    provider_config = config.llm_providers.get(provider_name)
    if provider_config is None:
        return None

    api_key = settings.OPENAI_API_KEY
    model = provider_config.model or "gpt-4o-mini"
    api_base = provider_config.base_url

    import os
    provider_type = provider_config.provider.lower()

    # Resolve API key based on provider type
    if provider_type == "openai" and api_base:
        # OpenAI-compatible provider (deepseek, etc.) with custom base_url
        if "deepseek" in (model or "").lower() or "deepseek" in (api_base or "").lower():
            api_key = os.environ.get("DEEPSEEK_API_KEY") or settings.OPENAI_API_KEY
            # litellm needs openai/ prefix for OpenAI-compatible endpoints
            model = f"openai/{model}"
    elif "deepseek" in (model or "").lower():
        api_key = os.environ.get("DEEPSEEK_API_KEY") or settings.OPENAI_API_KEY

    return {
        "model": model,
        "api_base": api_base,
        "api_key": api_key,
    }


@app.command()
def version():
    """Show version information."""
    console.print(f"codekb {__version__}")


@app.command()
def serve(
    transport: str = typer.Option("stdio", help="Transport: stdio or sse"),
    port: int = typer.Option(8000, help="Port for SSE transport"),
):
    """Start MCP server."""
    from codekb.mcp.server import run_server
    run_server(transport=transport, port=port)


@app.command()
def sync(
    repo: Optional[str] = typer.Option(None, "--repo", "-r", help="Sync specific repo"),
    full: bool = typer.Option(False, "--full", help="Force full re-index"),
    module: Optional[str] = typer.Option(None, "--module", "-m", help="Filter to specific module"),
):
    """Sync indexes (incremental by default)."""
    config, store, vector_store, doc_store, repo_manager = _get_services()
    orchestrator = IndexOrchestrator(config, store, vector_store, doc_store, repo_manager)

    if repo:
        repos = [store.get_repo(repo)]
        if repos[0] is None:
            error_console.print(f"[red]Repo not found: {repo}[/red]")
            raise typer.Exit(code=1)
    else:
        repos = store.list_repos()

    async def _sync():
        for r in repos:
            try:
                if full:
                    console.print(f"Full re-indexing [bold]{r.name}[/bold]...")
                    result = await orchestrator.full_index(r.name)
                else:
                    console.print(f"Incremental sync [bold]{r.name}[/bold]...")
                    changed = repo_manager.fetch_updates(r.name)

                    # If repo has never been indexed, fallback to full index
                    if changed is None or (not changed and not store.get_index_status(r.name)):
                        console.print(f"  Running full index...")
                        result = await orchestrator.full_index(r.name)
                    elif not changed:
                        console.print(f"  [green]Up to date[/green]")
                        continue
                    else:
                        result = await orchestrator.incremental_index(r.name, changed)
                console.print(f"  [green]Done[/green]: {result}")
            except Exception as e:
                error_console.print(f"  [red]Error: {e}[/red]")

    asyncio.run(_sync())


@app.command()
def reindex(name: str):
    """Force full re-index of a repository."""
    config, store, vector_store, doc_store, repo_manager = _get_services()
    orchestrator = IndexOrchestrator(config, store, vector_store, doc_store, repo_manager)

    async def _reindex():
        console.print(f"Full re-indexing [bold]{name}[/bold]...")
        result = await orchestrator.full_index(name)
        console.print(f"[green]Done[/green]: {result}")

    asyncio.run(_reindex())


@repo_app.command("add")
def repo_add(
    url: str = typer.Argument(help="Repository URL or local path"),
    branch: Optional[str] = typer.Option(None, "--branch", "-b"),
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Alias for the repo"),
    local: bool = typer.Option(False, "--local", help="Add local repo"),
    file: Optional[str] = typer.Option(None, "--file", "-f", help="Batch import from file"),
):
    """Add a repository."""
    config, store, vector_store, doc_store, repo_manager = _get_services()

    try:
        repo = repo_manager.add_repo(url, branch=branch, name=name, is_local=local)
        console.print(f"[green]Added[/green] [bold]{repo.name}[/bold]")
        console.print(f"  URL: {repo.url}")
        console.print(f"  Language: {repo.language}")
        console.print(f"  Framework: {repo.framework}")
        console.print(f"  Files: {repo.file_count}")
    except Exception as e:
        error_console.print(f"[red]Error adding repo: {e}[/red]")
        raise typer.Exit(code=1)


@repo_app.command("remove")
def repo_remove(name: str):
    """Remove a repository."""
    config, store, vector_store, doc_store, repo_manager = _get_services()

    if repo_manager.remove_repo(name):
        console.print(f"[green]Removed[/green] [bold]{name}[/bold]")
    else:
        error_console.print(f"[red]Repo not found: {name}[/red]")
        raise typer.Exit(code=1)


@repo_app.command("list")
def repo_list():
    """List all registered repositories."""
    config, store, vector_store, doc_store, repo_manager = _get_services()

    repos = store.list_repos()
    if not repos:
        console.print("No repos registered.")
        return

    table = Table(title="Registered Repositories")
    table.add_column("Name", style="bold")
    table.add_column("Language")
    table.add_column("Platform")
    table.add_column("Status")
    table.add_column("Files")

    for r in repos:
        table.add_row(r.name, r.language, r.platform, r.status, str(r.file_count))

    console.print(table)


@repo_app.command("info")
def repo_info(name: str):
    """Show detailed repository information."""
    config, store, vector_store, doc_store, repo_manager = _get_services()

    repo = store.get_repo(name)
    if repo is None:
        error_console.print(f"[red]Repo not found: {name}[/red]")
        raise typer.Exit(code=1)

    console.print(f"[bold]{repo.name}[/bold]")
    console.print(f"  URL: {repo.url}")
    console.print(f"  Local: {repo.local_path}")
    console.print(f"  Platform: {repo.platform}")
    console.print(f"  Branch: {repo.branch}")
    console.print(f"  Language: {repo.language}")
    console.print(f"  Framework: {repo.framework}")
    console.print(f"  Status: {repo.status}")
    console.print(f"  Files: {repo.file_count}")
    console.print(f"  Added: {repo.added_at}")
    console.print(f"  Last indexed: {repo.last_indexed_at}")

    # Show modules
    modules_data = store.get_repo_modules(name)
    if modules_data:
        console.print(f"\n  [bold]Modules ({len(modules_data)}):[/bold]")
        for mod in modules_data:
            mod_lang = f" ({mod['language']})" if mod.get("language") else ""
            console.print(f"    {mod['name']}: {mod.get('path', '')}{mod_lang}")

    # Show file tree summary
    files = store.get_file_tree(name)
    if files:
        console.print(f"\n  [bold]Indexed files ({len(files)}):[/bold]")
        for f in files[:20]:
            entry_marker = " [dim](entry point)[/dim]" if f.is_entry_point else ""
            module_marker = f" [{f.repo_module}]" if f.repo_module else ""
            console.print(f"    {f.path} ({f.language}, {f.symbol_count} symbols){module_marker}{entry_marker}")
        if len(files) > 20:
            console.print(f"    ... and {len(files) - 20} more")


@repo_app.command("modules")
def repo_modules(name: str):
    """List modules in a repository."""
    config, store, vector_store, doc_store, repo_manager = _get_services()

    repo = store.get_repo(name)
    if repo is None:
        error_console.print(f"[red]Repo not found: {name}[/red]")
        raise typer.Exit(code=1)

    modules_data = store.get_repo_modules(name)
    if not modules_data:
        console.print("No modules detected (single-module repository).")
        return

    table = Table(title=f"Modules for {name}")
    table.add_column("Name", style="bold")
    table.add_column("Path")
    table.add_column("Language")
    table.add_column("Source")

    for mod in modules_data:
        table.add_row(
            mod["name"],
            mod.get("path", ""),
            mod.get("language", ""),
            mod.get("source", "auto"),
        )

    console.print(table)


@docs_app.command("generate")
def docs_generate(
    name: str,
    module: str = typer.Option("", "--module", "-m", help="Generate docs for specific module"),
):
    """Generate architecture docs for a repository."""
    from codekb.indexers.doc_generator import DocGenerator

    config, store, vector_store, doc_store, repo_manager = _get_services()
    settings = load_settings()
    llm_client = _create_llm_client(config, settings)
    generator = DocGenerator(store, doc_store, config)

    async def _generate():
        console.print(f"Generating docs for [bold]{name}[/bold]...")
        if llm_client:
            console.print(f"  Using LLM: [bold]{llm_client['model']}[/bold]")
        result = await generator.generate_docs(name, llm_client=llm_client, repo_module=module)
        console.print(f"[green]Done[/green]: {result}")

    asyncio.run(_generate())


@docs_app.command("verify")
def docs_verify(name: str):
    """Verify generated docs for a repository."""
    from codekb.indexers.doc_generator import DocGenerator

    config, store, vector_store, doc_store, repo_manager = _get_services()
    generator = DocGenerator(store, doc_store, config)

    results = generator.verify_docs(name)
    if not results:
        console.print("No generated docs to verify.")
        return

    for r in results:
        status_color = "green" if r["quality"] == "verified" else "yellow" if r["quality"] == "partial" else "red"
        console.print(f"  [{status_color}]{r['quality']}[/{status_color}] {r['doc']} "
                      f"({r['verified']} verified, {r['unverified']} unverified)")


@skills_app.command("list")
def skills_list(name: str):
    """List all skills for a repository."""
    from codekb.indexers.skill_generator import SkillGenerator

    config, store, vector_store, doc_store, repo_manager = _get_services()
    sg = SkillGenerator(store, doc_store, config)

    skills = sg.list_skills(name)
    if not skills:
        console.print(f"No skills found for {name}.")
        return

    table = Table(title=f"Skills for {name}")
    table.add_column("Name", style="bold")
    table.add_column("Status")
    table.add_column("Confidence")

    for s in skills:
        status_color = "green" if s["status"] == "verified" else "yellow" if s["status"] == "draft" else "red"
        table.add_row(s["name"], f"[{status_color}]{s['status']}[/{status_color}]", str(s["confidence"]))

    console.print(table)


@skills_app.command("review")
def skills_review(
    name: str,
    skill: str = typer.Argument(help="Skill name to review"),
    approve: bool = typer.Option(True, "--approve/--reject", help="Approve or reject"),
):
    """Review and approve draft skills."""
    from codekb.indexers.skill_generator import SkillGenerator

    config, store, vector_store, doc_store, repo_manager = _get_services()
    sg = SkillGenerator(store, doc_store, config)

    result = sg.review_skill(name, skill, approve=approve)
    console.print(f"Skill [bold]{skill}[/bold] → {result['status']}")


@skills_app.command("generate")
def skills_generate(
    name: str,
    module: str = typer.Option("", "--module", "-m", help="Generate skills for specific module"),
):
    """Generate skills for a repository."""
    from codekb.indexers.skill_generator import SkillGenerator

    config, store, vector_store, doc_store, repo_manager = _get_services()
    settings = load_settings()
    llm_client = _create_llm_client(config, settings)
    sg = SkillGenerator(store, doc_store, config)

    async def _generate():
        console.print(f"Generating skills for [bold]{name}[/bold]...")
        if llm_client:
            console.print(f"  Using LLM: [bold]{llm_client['model']}[/bold]")
        result = await sg.generate_skills(name, llm_client=llm_client, repo_module=module)
        if not result:
            console.print("No skills generated.")
            return
        for s in result:
            console.print(f"  [bold]{s['name']}[/bold] (status={s['status']}, confidence={s['confidence']})")

    asyncio.run(_generate())


@skills_app.command("verify")
def skills_verify(name: str):
    """Re-verify all skills against current code."""
    from codekb.indexers.skill_generator import SkillGenerator

    config, store, vector_store, doc_store, repo_manager = _get_services()
    sg = SkillGenerator(store, doc_store, config)

    results = sg.verify_skills(name)
    for r in results:
        status_color = "green" if r["status"] == "verified" else "yellow" if r["status"] == "draft" else "red"
        console.print(f"  [{status_color}]{r['status']}[/{status_color}] {r['name']} "
                      f"({r['verified']} verified, {r['unverified']} unverified)")


@app.command()
def webhook(
    port: int = typer.Option(8080, "--port", "-p", help="Port to listen on"),
):
    """Start webhook receiver."""
    import uvicorn
    from codekb.webhook.receiver import create_webhook_app
    from codekb.core.indexer import IndexOrchestrator

    config, store, vector_store, doc_store, repo_manager = _get_services()
    orchestrator = IndexOrchestrator(config, store, vector_store, doc_store, repo_manager)

    app = create_webhook_app(orchestrator, webhook_secret=None)
    console.print(f"Starting webhook receiver on port {port}...")
    uvicorn.run(app, host="0.0.0.0", port=port)


@app.command()
def watch(
    repo: Optional[str] = typer.Option(None, "--repo", "-r", help="Watch specific repo only"),
):
    """Watch for file changes and trigger incremental re-indexing.

    Uses watchdog library with 2000ms debounce and content_hash change detection.
    Watches all indexed repos by default, or a specific repo with --repo.
    """
    from codekb.watcher.file_watcher import FileWatcher

    config = load_config()

    # If watching a specific repo, verify it exists
    if repo:
        data_dir = ensure_data_dir(config)
        store = SqliteStore(data_dir / "index")
        repo_record = store.get_repo(repo)
        if repo_record is None:
            error_console.print(f"[red]Repo not found: {repo}[/red]")
            raise typer.Exit(code=1)
        if repo_record.status != "indexed":
            error_console.print(f"[red]Repo '{repo}' is not indexed (status: {repo_record.status})[/red]")
            raise typer.Exit(code=1)

    watcher = FileWatcher(config)
    repos = watcher.store.list_repos()
    indexed = [r for r in repos if r.status == "indexed"]
    if repo:
        indexed = [r for r in indexed if r.name == repo]

    if not indexed:
        error_console.print("[red]No indexed repos to watch. Run 'codekb sync' first.[/red]")
        raise typer.Exit(code=1)

    console.print(f"Watching [bold]{len(indexed)}[/bold] repo(s) for changes...")
    for r in indexed:
        console.print(f"  {r.name} ({r.local_path})")
    console.print("Press Ctrl+C to stop.")

    watcher.watch_forever()
