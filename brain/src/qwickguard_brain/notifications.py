"""Notification dispatch for QwickGuard Brain.

Routes alerts to GitHub Issues and Slack/Discord based on severity.
GitHub Issues: dedup by title search, comment on existing if found.
Slack: color-coded attachments.
Discord: color-coded embeds.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from .config import settings
from .storage import store_notification

logger = logging.getLogger("qwickguard.brain.notifications")


async def create_github_issue(
    severity: str,
    title: str,
    body: str,
    labels: list[str] | None = None,
) -> int | None:
    """Create or update a GitHub issue. Returns issue number or None on failure.

    Deduplicates by searching for an existing open issue with the same title.
    If one is found, a comment is added to it instead of creating a new issue.
    """
    if not settings.github_token:
        return None

    headers = {
        "Authorization": f"token {settings.github_token}",
        "Accept": "application/vnd.github.v3+json",
    }
    labels = labels or [severity, "auto-generated"]

    async with httpx.AsyncClient(timeout=30) as client:
        # Dedup: search for existing open issue with same title
        try:
            search_resp = await client.get(
                "https://api.github.com/search/issues",
                params={"q": f'repo:{settings.github_repo} is:open in:title "{title}"'},
                headers=headers,
            )
            search_resp.raise_for_status()
            search_data = search_resp.json()

            if search_data.get("total_count", 0) > 0:
                existing = search_data["items"][0]
                issue_num = existing["number"]
                # Add comment to existing issue
                await client.post(
                    f"https://api.github.com/repos/{settings.github_repo}/issues/{issue_num}/comments",
                    json={"body": body},
                    headers=headers,
                )
                logger.info("Added comment to existing issue #%d", issue_num)
                return issue_num
        except Exception:
            logger.exception("GitHub issue search failed")

        # Create new issue
        try:
            resp = await client.post(
                f"https://api.github.com/repos/{settings.github_repo}/issues",
                json={"title": title, "body": body, "labels": labels},
                headers=headers,
            )
            resp.raise_for_status()
            issue_num = resp.json()["number"]
            logger.info("Created GitHub issue #%d: %s", issue_num, title)
            return issue_num
        except Exception:
            logger.exception("GitHub issue creation failed")
            return None


async def close_github_issue(title: str, comment: str) -> bool:
    """Find and close an open GitHub issue by title match.

    Adds a resolution comment before closing. Returns True if an issue was
    found and closed, False otherwise.
    """
    if not settings.github_token:
        return False

    headers = {
        "Authorization": f"token {settings.github_token}",
        "Accept": "application/vnd.github.v3+json",
    }

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            search_resp = await client.get(
                "https://api.github.com/search/issues",
                params={"q": f'repo:{settings.github_repo} is:open in:title "{title}"'},
                headers=headers,
            )
            search_resp.raise_for_status()
            items = search_resp.json().get("items", [])
            if not items:
                return False

            issue_num = items[0]["number"]
            # Add resolution comment
            await client.post(
                f"https://api.github.com/repos/{settings.github_repo}/issues/{issue_num}/comments",
                json={"body": comment},
                headers=headers,
            )
            # Close issue
            await client.patch(
                f"https://api.github.com/repos/{settings.github_repo}/issues/{issue_num}",
                json={"state": "closed"},
                headers=headers,
            )
            logger.info("Closed GitHub issue #%d: %s", issue_num, title)
            return True
        except Exception:
            logger.exception("GitHub issue close failed")
            return False


async def send_slack_alert(
    severity: str,
    title: str,
    body: str,
    hostname: str,
) -> bool:
    """Send alert to Slack webhook with color-coded attachment. Returns True on success."""
    webhook_url = settings.slack_webhook_url
    if not webhook_url:
        return False

    color_map = {"critical": "#FF0000", "warning": "#FFA500", "info": "#36A64F"}
    payload: dict[str, Any] = {
        "attachments": [
            {
                "color": color_map.get(severity, "#808080"),
                "title": f"[{hostname}] {title}",
                "text": body[:2000],
                "footer": "QwickGuard",
                "ts": int(time.time()),
            }
        ]
    }

    async with httpx.AsyncClient(timeout=15) as client:
        try:
            resp = await client.post(webhook_url, json=payload)
            resp.raise_for_status()
            logger.info("Slack alert sent: %s", title)
            return True
        except Exception:
            logger.exception("Slack alert failed")
            return False


async def send_discord_alert(
    severity: str,
    title: str,
    body: str,
    hostname: str,
) -> bool:
    """Send alert to Discord webhook with color-coded embed. Returns True on success."""
    webhook_url = settings.discord_webhook_url
    if not webhook_url:
        return False

    color_map = {"critical": 0xFF0000, "warning": 0xFFA500, "info": 0x36A64F}
    payload: dict[str, Any] = {
        "embeds": [
            {
                "title": f"[{hostname}] {title}",
                "description": body[:2000],
                "color": color_map.get(severity, 0x808080),
                "footer": {"text": "QwickGuard"},
            }
        ]
    }

    async with httpx.AsyncClient(timeout=15) as client:
        try:
            resp = await client.post(webhook_url, json=payload)
            resp.raise_for_status()
            logger.info("Discord alert sent: %s", title)
            return True
        except Exception:
            logger.exception("Discord alert failed")
            return False


async def dispatch_notification(
    agent_id: str,
    severity: str,
    title: str,
    body: str,
    hostname: str = "",
) -> None:
    """Route notification to appropriate channels based on severity.

    Always stores internally. For critical and warning severity, also
    dispatches to GitHub Issues, Slack, and Discord if configured.
    """
    # Always store internally
    await store_notification(agent_id, severity, title, body, "internal")

    # GitHub Issues: critical and warning only
    if severity in ("critical", "warning") and settings.github_token:
        labels = [severity, "auto-generated"]
        issue_num = await create_github_issue(severity, title, body, labels)
        if issue_num:
            await store_notification(
                agent_id, severity, title, body, "github", str(issue_num)
            )

    # Slack: critical and warning only
    if severity in ("critical", "warning") and settings.slack_webhook_url:
        sent = await send_slack_alert(severity, title, body, hostname)
        if sent:
            await store_notification(agent_id, severity, title, body, "slack")

    # Discord: critical and warning only
    if severity in ("critical", "warning") and settings.discord_webhook_url:
        sent = await send_discord_alert(severity, title, body, hostname)
        if sent:
            await store_notification(agent_id, severity, title, body, "discord")
