"""GitHub webhook adapter."""

from __future__ import annotations

from codekb.webhook.receiver import WebhookEvent


class GitHubAdapter:
    """GitHub webhook adapter."""

    def parse_webhook(self, payload: dict, headers: dict) -> WebhookEvent:
        """Parse GitHub webhook payload."""
        from codekb.webhook.receiver import _parse_github
        event = _parse_github(payload)
        if event is None:
            raise ValueError("Not a push event")
        return event
