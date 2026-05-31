"""Gitee webhook adapter."""

from __future__ import annotations

from codekb.webhook.receiver import WebhookEvent


class GiteeAdapter:
    """Gitee webhook adapter."""

    def parse_webhook(self, payload: dict, headers: dict) -> WebhookEvent:
        """Parse Gitee webhook payload."""
        from codekb.webhook.receiver import _parse_gitee
        event = _parse_gitee(payload)
        if event is None:
            raise ValueError("Not a push event")
        return event
