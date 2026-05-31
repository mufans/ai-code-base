"""Webhook receiver for GitHub/GitLab/Gitee push events."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel

from codekb.core.indexer import IndexOrchestrator


class WebhookEvent(BaseModel):
    """Normalized webhook event."""
    platform: str
    repo_name: str
    repo_url: str
    branch: str
    changed_files: list[str]
    commits: list[str] = []


def create_webhook_app(
    orchestrator: IndexOrchestrator,
    webhook_secret: Optional[str] = None,
) -> FastAPI:
    """Create a FastAPI app for receiving webhooks."""
    app = FastAPI(title="CodeKB Webhook Receiver", version="0.1.0")

    @app.post("/webhook/{platform}")
    async def handle_webhook(platform: str, request: Request):
        body = await request.body()

        # Validate signature
        if webhook_secret:
            signature = request.headers.get("X-Hub-Signature-256") or request.headers.get("X-Gitlab-Token", "")
            if not _validate_signature(body, webhook_secret, signature, platform):
                raise HTTPException(status_code=401, detail="Invalid signature")

        payload = await request.json()

        # Parse based on platform
        try:
            event = _parse_webhook(platform, payload)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        if event is None:
            return {"status": "ignored", "reason": "Not a push event"}

        # Find repo by URL
        repo = orchestrator.store.get_repo(event.repo_name)
        if repo is None:
            # Try to find by URL
            repos = orchestrator.store.list_repos()
            for r in repos:
                if event.repo_url in r.url or r.url in event.repo_url:
                    repo = r
                    break

        if repo is None:
            return {"status": "ignored", "reason": f"Repo not registered: {event.repo_name}"}

        # Trigger incremental indexing
        try:
            result = await orchestrator.incremental_index(repo.name, event.changed_files)
            return {"status": "ok", "repo": repo.name, "result": result}
        except Exception as e:
            return {"status": "error", "repo": repo.name, "error": str(e)}

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


def _validate_signature(body: bytes, secret: str, signature: str, platform: str) -> bool:
    """Validate webhook signature."""
    if platform == "github":
        if not signature.startswith("sha256="):
            return False
        expected = "sha256=" + hmac.new(
            secret.encode(), body, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature)
    elif platform == "gitlab":
        return signature == secret
    elif platform == "gitee":
        if not signature.startswith("sha256="):
            return False
        expected = "sha256=" + hmac.new(
            secret.encode(), body, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature)
    return True


def _parse_webhook(platform: str, payload: dict) -> Optional[WebhookEvent]:
    """Parse webhook payload into normalized event."""
    if platform == "github":
        return _parse_github(payload)
    elif platform == "gitlab":
        return _parse_gitlab(payload)
    elif platform == "gitee":
        return _parse_gitee(payload)
    else:
        raise ValueError(f"Unsupported platform: {platform}")


def _parse_github(payload: dict) -> Optional[WebhookEvent]:
    """Parse GitHub push event."""
    ref = payload.get("ref", "")
    if not ref.startswith("refs/heads/"):
        return None

    branch = ref.replace("refs/heads/", "")
    repo = payload.get("repository", {})
    repo_url = repo.get("clone_url", "")
    repo_name = repo.get("full_name", "")

    changed_files = set()
    commits = []
    for commit in payload.get("commits", []):
        commits.append(commit.get("id", ""))
        changed_files.update(commit.get("added", []))
        changed_files.update(commit.get("modified", []))
        changed_files.update(commit.get("removed", []))

    return WebhookEvent(
        platform="github",
        repo_name=repo_name,
        repo_url=repo_url,
        branch=branch,
        changed_files=list(changed_files),
        commits=commits,
    )


def _parse_gitlab(payload: dict) -> Optional[WebhookEvent]:
    """Parse GitLab push event."""
    ref = payload.get("ref", "")
    if not ref.startswith("refs/heads/"):
        return None

    branch = ref.replace("refs/heads/", "")
    repo = payload.get("project", {})
    repo_url = repo.get("http_url", "") or repo.get("web_url", "")
    repo_name = repo.get("path_with_namespace", "")

    changed_files = set()
    commits = []
    for commit in payload.get("commits", []):
        commits.append(commit.get("id", ""))
        changed_files.update(commit.get("added", []))
        changed_files.update(commit.get("modified", []))
        changed_files.update(commit.get("removed", []))

    return WebhookEvent(
        platform="gitlab",
        repo_name=repo_name,
        repo_url=repo_url,
        branch=branch,
        changed_files=list(changed_files),
        commits=commits,
    )


def _parse_gitee(payload: dict) -> Optional[WebhookEvent]:
    """Parse Gitee push event."""
    ref = payload.get("ref", "")
    if not ref.startswith("refs/heads/"):
        return None

    branch = ref.replace("refs/heads/", "")
    repo = payload.get("repository", {})
    repo_url = repo.get("html_url", "")
    repo_name = repo.get("full_name", "")

    changed_files = set()
    commits = []
    for commit in payload.get("commits", []):
        commits.append(commit.get("sha", ""))
        changed_files.update(commit.get("added", []))
        changed_files.update(commit.get("modified", []))
        changed_files.update(commit.get("removed", []))

    return WebhookEvent(
        platform="gitee",
        repo_name=repo_name,
        repo_url=repo_url,
        branch=branch,
        changed_files=list(changed_files),
        commits=commits,
    )
