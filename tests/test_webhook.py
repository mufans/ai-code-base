"""Tests for webhook receiver and adapters."""

import pytest
from unittest.mock import MagicMock, AsyncMock

from codekb.webhook.receiver import (
    WebhookEvent,
    _parse_github,
    _parse_gitlab,
    _parse_gitee,
    _validate_signature,
    create_webhook_app,
)
from codekb.webhook.adapters.github import GitHubAdapter
from codekb.webhook.adapters.gitlab import GitLabAdapter
from codekb.webhook.adapters.gitee import GiteeAdapter


class TestGitHubParsing:
    def test_parse_push_event(self):
        payload = {
            "ref": "refs/heads/main",
            "repository": {
                "full_name": "org/test-repo",
                "clone_url": "https://github.com/org/test-repo.git",
            },
            "commits": [
                {
                    "id": "abc123",
                    "added": ["new_file.py"],
                    "modified": ["main.py"],
                    "removed": [],
                },
                {
                    "id": "def456",
                    "added": [],
                    "modified": ["utils.py", "main.py"],
                    "removed": ["old_file.py"],
                },
            ],
        }
        event = _parse_github(payload)

        assert event is not None
        assert event.platform == "github"
        assert event.repo_name == "org/test-repo"
        assert event.branch == "main"
        assert "main.py" in event.changed_files
        assert "new_file.py" in event.changed_files
        assert "utils.py" in event.changed_files
        assert "old_file.py" in event.changed_files
        assert len(event.commits) == 2

    def test_parse_non_push_event(self):
        payload = {"ref": "refs/tags/v1.0"}
        event = _parse_github(payload)
        assert event is None

    def test_parse_empty_commits(self):
        payload = {
            "ref": "refs/heads/main",
            "repository": {"full_name": "test", "clone_url": "http://example.com"},
            "commits": [],
        }
        event = _parse_github(payload)
        assert event is not None
        assert event.changed_files == []


class TestGitLabParsing:
    def test_parse_push_event(self):
        payload = {
            "ref": "refs/heads/main",
            "project": {
                "path_with_namespace": "org/test-repo",
                "web_url": "https://gitlab.com/org/test-repo",
            },
            "commits": [
                {
                    "id": "abc123",
                    "added": ["new.py"],
                    "modified": ["main.py"],
                    "removed": [],
                },
            ],
        }
        event = _parse_gitlab(payload)

        assert event is not None
        assert event.platform == "gitlab"
        assert event.repo_name == "org/test-repo"
        assert "new.py" in event.changed_files


class TestGiteeParsing:
    def test_parse_push_event(self):
        payload = {
            "ref": "refs/heads/main",
            "repository": {
                "full_name": "org/test-repo",
                "html_url": "https://gitee.com/org/test-repo",
            },
            "commits": [
                {
                    "sha": "abc123",
                    "added": ["new.py"],
                    "modified": ["main.py"],
                    "removed": [],
                },
            ],
        }
        event = _parse_gitee(payload)

        assert event is not None
        assert event.platform == "gitee"
        assert event.repo_name == "org/test-repo"


class TestSignatureValidation:
    def test_github_valid_signature(self):
        import hashlib
        import hmac

        secret = "test-secret"
        body = b'{"test": true}'
        expected_sig = "sha256=" + hmac.new(
            secret.encode(), body, hashlib.sha256
        ).hexdigest()

        assert _validate_signature(body, secret, expected_sig, "github") is True

    def test_github_invalid_signature(self):
        assert _validate_signature(b"body", "secret", "sha256=invalid", "github") is False

    def test_gitlab_token_validation(self):
        assert _validate_signature(b"body", "my-token", "my-token", "gitlab") is True
        assert _validate_signature(b"body", "my-token", "wrong", "gitlab") is False


class TestAdapters:
    def test_github_adapter(self):
        adapter = GitHubAdapter()
        payload = {
            "ref": "refs/heads/main",
            "repository": {"full_name": "test", "clone_url": "http://example.com"},
            "commits": [],
        }
        event = adapter.parse_webhook(payload, {})
        assert event.platform == "github"

    def test_gitlab_adapter(self):
        adapter = GitLabAdapter()
        payload = {
            "ref": "refs/heads/main",
            "project": {"path_with_namespace": "test", "web_url": "http://example.com"},
            "commits": [],
        }
        event = adapter.parse_webhook(payload, {})
        assert event.platform == "gitlab"

    def test_gitee_adapter(self):
        adapter = GiteeAdapter()
        payload = {
            "ref": "refs/heads/main",
            "repository": {"full_name": "test", "html_url": "http://example.com"},
            "commits": [],
        }
        event = adapter.parse_webhook(payload, {})
        assert event.platform == "gitee"


class TestWebhookApp:
    def test_create_app(self):
        mock_orchestrator = MagicMock()
        app = create_webhook_app(mock_orchestrator)
        assert app is not None
        assert app.title == "CodeKB Webhook Receiver"
