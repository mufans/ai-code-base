"""CodeKB CLI - Command line interface."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from codekb import __version__
from codekb.core.config import CodekbYamlConfig, ensure_data_dir, get_data_dir, load_config
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
                    changed = repo_manager.fetch_updates(r.name) or []
                    if not changed:
                        console.print(f"  [green]Up to date[/green]")
                        continue
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

    # Show file tree summary
    files = store.get_file_tree(name)
    if files:
        console.print(f"\n  [bold]Indexed files ({len(files)}):[/bold]")
        for f in files[:20]:
            entry_marker = " [dim](entry point)[/dim]" if f.is_entry_point else ""
            console.print(f"    {f.path} ({f.language}, {f.symbol_count} symbols){entry_marker}")
        if len(files) > 20:
            console.print(f"    ... and {len(files) - 20} more")


@docs_app.command("generate")
def docs_generate(name: str):
    """Generate architecture docs for a repository."""
    from codekb.indexers.doc_generator import DocGenerator

    config, store, vector_store, doc_store, repo_manager = _get_services()
    generator = DocGenerator(store, doc_store, config)

    async def _generate():
        console.print(f"Generating docs for [bold]{name}[/bold]...")
        result = await generator.generate_docs(name)
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
def skills_generate(name: str):
    """Generate skills for a repository."""
    from codekb.indexers.skill_generator import SkillGenerator

    config, store, vector_store, doc_store, repo_manager = _get_services()
    sg = SkillGenerator(store, doc_store, config)

    async def _generate():
        console.print(f"Generating skills for [bold]{name}[/bold]...")
        result = await sg.generate_skills(name)
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
