"""GitLab webhook adapter."""

from __future__ import annotations

from codekb.webhook.receiver import WebhookEvent


class GitLabAdapter:
    """GitLab webhook adapter."""

    def parse_webhook(self, payload: dict, headers: dict) -> WebhookEvent:
        """Parse GitLab webhook payload."""
        from codekb.webhook.receiver import _parse_gitlab
        event = _parse_gitlab(payload)
        if event is None:
            raise ValueError("Not a push event")
        return event
