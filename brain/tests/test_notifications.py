"""Tests for notification dispatch (GitHub Issues, Slack, Discord)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from qwickguard_brain.storage import init_db


@pytest_asyncio.fixture
async def db(tmp_path):
    """Initialise a temporary database for each test."""
    await init_db(str(tmp_path / "test.db"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_http_response(status_code: int = 200, json_data: dict | None = None) -> MagicMock:
    """Return a mock httpx response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------------------
# dispatch_notification
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dispatch_internal_only(db):
    """Info severity: stores internally, no external HTTP calls."""
    with patch("qwickguard_brain.notifications.store_notification", new_callable=AsyncMock) as mock_store, \
         patch("qwickguard_brain.notifications.settings") as mock_settings:

        mock_settings.github_token = None
        mock_settings.slack_webhook_url = None
        mock_settings.discord_webhook_url = None

        from qwickguard_brain.notifications import dispatch_notification
        await dispatch_notification(
            agent_id="agent-1",
            severity="info",
            title="Test info alert",
            body="Just an info message.",
            hostname="host-1",
        )

    # Should only call store_notification once (internal channel)
    mock_store.assert_called_once_with(
        "agent-1", "info", "Test info alert", "Just an info message.", "internal"
    )


@pytest.mark.asyncio
async def test_dispatch_info_skips_external_even_if_configured(db):
    """Info severity skips GitHub, Slack, and Discord even when they are configured."""
    with patch("qwickguard_brain.notifications.store_notification", new_callable=AsyncMock) as mock_store, \
         patch("qwickguard_brain.notifications.settings") as mock_settings, \
         patch("qwickguard_brain.notifications.httpx.AsyncClient") as mock_client_cls:

        mock_settings.github_token = "ghp_test"
        mock_settings.github_repo = "owner/repo"
        mock_settings.slack_webhook_url = "https://hooks.slack.com/test"
        mock_settings.discord_webhook_url = "https://discord.com/api/webhooks/test"

        from qwickguard_brain.notifications import dispatch_notification
        await dispatch_notification(
            agent_id="agent-1",
            severity="info",
            title="Info alert",
            body="Just info.",
            hostname="host-1",
        )

    # Only internal store call; no HTTP client instantiated for external channels
    mock_store.assert_called_once()
    mock_client_cls.assert_not_called()


# ---------------------------------------------------------------------------
# create_github_issue
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_github_no_token():
    """Returns None immediately when no GitHub token is configured."""
    with patch("qwickguard_brain.notifications.settings") as mock_settings:
        mock_settings.github_token = None

        from qwickguard_brain.notifications import create_github_issue
        result = await create_github_issue("critical", "Test title", "Test body")

    assert result is None


@pytest.mark.asyncio
async def test_dispatch_github_issue(db):
    """Critical severity creates a GitHub issue when token is configured."""
    search_response = _mock_http_response(
        200, {"total_count": 0, "items": []}
    )
    create_response = _mock_http_response(200, {"number": 42})

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=search_response)
    mock_client.post = AsyncMock(return_value=create_response)

    with patch("qwickguard_brain.notifications.store_notification", new_callable=AsyncMock) as mock_store, \
         patch("qwickguard_brain.notifications.settings") as mock_settings, \
         patch("qwickguard_brain.notifications.httpx.AsyncClient", return_value=mock_client):

        mock_settings.github_token = "ghp_test"
        mock_settings.github_repo = "owner/repo"
        mock_settings.slack_webhook_url = None
        mock_settings.discord_webhook_url = None

        from qwickguard_brain.notifications import dispatch_notification
        await dispatch_notification(
            agent_id="agent-1",
            severity="critical",
            title="High CPU usage",
            body="CPU at 95%.",
            hostname="host-1",
        )

    # Verify internal + github store calls
    channels = [call.args[4] for call in mock_store.call_args_list]
    assert "internal" in channels
    assert "github" in channels

    # Verify issue creation was attempted (POST to issues endpoint)
    post_calls = mock_client.post.call_args_list
    assert any("issues" in str(call) for call in post_calls)


@pytest.mark.asyncio
async def test_github_dedup_adds_comment(db):
    """When an open issue with the same title exists, adds a comment instead of creating new."""
    search_response = _mock_http_response(
        200,
        {
            "total_count": 1,
            "items": [{"number": 99, "title": "Agent host-1 heartbeat missing"}],
        },
    )
    comment_response = _mock_http_response(200, {"id": 1})

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=search_response)
    mock_client.post = AsyncMock(return_value=comment_response)

    with patch("qwickguard_brain.notifications.settings") as mock_settings, \
         patch("qwickguard_brain.notifications.httpx.AsyncClient", return_value=mock_client):

        mock_settings.github_token = "ghp_test"
        mock_settings.github_repo = "owner/repo"

        from qwickguard_brain.notifications import create_github_issue
        result = await create_github_issue(
            "critical",
            "Agent host-1 heartbeat missing",
            "Still missing after 30 minutes.",
        )

    # Should return existing issue number
    assert result == 99

    # Should POST a comment (not create new issue)
    post_calls = mock_client.post.call_args_list
    assert len(post_calls) == 1
    assert "comments" in str(post_calls[0])


@pytest.mark.asyncio
async def test_github_search_failure_falls_through_to_create(db):
    """When search raises an exception, falls through to attempt issue creation."""
    create_response = _mock_http_response(200, {"number": 7})

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(side_effect=Exception("Network error"))
    mock_client.post = AsyncMock(return_value=create_response)

    with patch("qwickguard_brain.notifications.settings") as mock_settings, \
         patch("qwickguard_brain.notifications.httpx.AsyncClient", return_value=mock_client):

        mock_settings.github_token = "ghp_test"
        mock_settings.github_repo = "owner/repo"

        from qwickguard_brain.notifications import create_github_issue
        result = await create_github_issue("warning", "Disk usage high", "Disk at 88%.")

    # Falls through to create new issue
    assert result == 7


# ---------------------------------------------------------------------------
# send_slack_alert
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_slack_no_webhook():
    """Returns False immediately when no Slack webhook URL is configured."""
    with patch("qwickguard_brain.notifications.settings") as mock_settings:
        mock_settings.slack_webhook_url = None

        from qwickguard_brain.notifications import send_slack_alert
        result = await send_slack_alert("critical", "Test", "Body", "host-1")

    assert result is False


@pytest.mark.asyncio
async def test_dispatch_slack(db):
    """Warning severity sends to Slack when webhook is configured."""
    slack_response = _mock_http_response(200)

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=slack_response)

    with patch("qwickguard_brain.notifications.store_notification", new_callable=AsyncMock) as mock_store, \
         patch("qwickguard_brain.notifications.settings") as mock_settings, \
         patch("qwickguard_brain.notifications.httpx.AsyncClient", return_value=mock_client):

        mock_settings.github_token = None
        mock_settings.slack_webhook_url = "https://hooks.slack.com/test"
        mock_settings.discord_webhook_url = None

        from qwickguard_brain.notifications import dispatch_notification
        await dispatch_notification(
            agent_id="agent-2",
            severity="warning",
            title="Memory pressure",
            body="Memory at 85%.",
            hostname="host-2",
        )

    # Verify internal + slack store calls
    channels = [call.args[4] for call in mock_store.call_args_list]
    assert "internal" in channels
    assert "slack" in channels

    # Verify Slack POST was made
    mock_client.post.assert_called_once()
    call_kwargs = mock_client.post.call_args
    payload = call_kwargs.kwargs.get("json") or call_kwargs.args[1] if len(call_kwargs.args) > 1 else call_kwargs.kwargs.get("json")
    assert payload is not None
    attachments = payload.get("attachments", [])
    assert len(attachments) == 1
    assert "host-2" in attachments[0]["title"]


@pytest.mark.asyncio
async def test_slack_alert_color_critical():
    """Critical severity uses red (#FF0000) color in Slack attachment."""
    captured_payload: dict = {}

    slack_response = _mock_http_response(200)
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    async def capture_post(url, **kwargs):
        captured_payload.update(kwargs.get("json", {}))
        return slack_response

    mock_client.post = capture_post

    with patch("qwickguard_brain.notifications.settings") as mock_settings, \
         patch("qwickguard_brain.notifications.httpx.AsyncClient", return_value=mock_client):

        mock_settings.slack_webhook_url = "https://hooks.slack.com/test"

        from qwickguard_brain.notifications import send_slack_alert
        result = await send_slack_alert("critical", "High CPU", "CPU at 99%", "host-1")

    assert result is True
    assert captured_payload["attachments"][0]["color"] == "#FF0000"


@pytest.mark.asyncio
async def test_slack_alert_color_warning():
    """Warning severity uses orange (#FFA500) color in Slack attachment."""
    captured_payload: dict = {}

    slack_response = _mock_http_response(200)
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    async def capture_post(url, **kwargs):
        captured_payload.update(kwargs.get("json", {}))
        return slack_response

    mock_client.post = capture_post

    with patch("qwickguard_brain.notifications.settings") as mock_settings, \
         patch("qwickguard_brain.notifications.httpx.AsyncClient", return_value=mock_client):

        mock_settings.slack_webhook_url = "https://hooks.slack.com/test"

        from qwickguard_brain.notifications import send_slack_alert
        result = await send_slack_alert("warning", "Disk usage", "Disk at 88%", "host-1")

    assert result is True
    assert captured_payload["attachments"][0]["color"] == "#FFA500"


# ---------------------------------------------------------------------------
# send_discord_alert
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_discord_no_webhook():
    """Returns False immediately when no Discord webhook URL is configured."""
    with patch("qwickguard_brain.notifications.settings") as mock_settings:
        mock_settings.discord_webhook_url = None

        from qwickguard_brain.notifications import send_discord_alert
        result = await send_discord_alert("critical", "Test", "Body", "host-1")

    assert result is False


@pytest.mark.asyncio
async def test_dispatch_discord(db):
    """Critical severity sends to Discord when webhook is configured."""
    discord_response = _mock_http_response(204)

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=discord_response)

    with patch("qwickguard_brain.notifications.store_notification", new_callable=AsyncMock) as mock_store, \
         patch("qwickguard_brain.notifications.settings") as mock_settings, \
         patch("qwickguard_brain.notifications.httpx.AsyncClient", return_value=mock_client):

        mock_settings.github_token = None
        mock_settings.slack_webhook_url = None
        mock_settings.discord_webhook_url = "https://discord.com/api/webhooks/test"

        from qwickguard_brain.notifications import dispatch_notification
        await dispatch_notification(
            agent_id="agent-3",
            severity="critical",
            title="Service down",
            body="nginx is not responding.",
            hostname="host-3",
        )

    channels = [call.args[4] for call in mock_store.call_args_list]
    assert "internal" in channels
    assert "discord" in channels

    mock_client.post.assert_called_once()
    call_kwargs = mock_client.post.call_args
    payload = call_kwargs.kwargs.get("json", {})
    embeds = payload.get("embeds", [])
    assert len(embeds) == 1
    assert "host-3" in embeds[0]["title"]
    assert embeds[0]["color"] == 0xFF0000


# ---------------------------------------------------------------------------
# close_github_issue
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_close_github_issue_no_token():
    """Returns False immediately when no GitHub token is configured."""
    with patch("qwickguard_brain.notifications.settings") as mock_settings:
        mock_settings.github_token = None

        from qwickguard_brain.notifications import close_github_issue
        result = await close_github_issue("Agent host-1 heartbeat missing", "Resolved.")

    assert result is False


@pytest.mark.asyncio
async def test_close_github_issue_not_found():
    """Returns False when no open issue matches the title."""
    search_response = _mock_http_response(200, {"total_count": 0, "items": []})

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=search_response)

    with patch("qwickguard_brain.notifications.settings") as mock_settings, \
         patch("qwickguard_brain.notifications.httpx.AsyncClient", return_value=mock_client):

        mock_settings.github_token = "ghp_test"
        mock_settings.github_repo = "owner/repo"

        from qwickguard_brain.notifications import close_github_issue
        result = await close_github_issue("Nonexistent issue", "Resolved.")

    assert result is False


@pytest.mark.asyncio
async def test_close_github_issue_success():
    """Closes an open issue by adding a comment and patching state to closed."""
    search_response = _mock_http_response(
        200,
        {"total_count": 1, "items": [{"number": 55, "title": "Agent host-1 heartbeat missing"}]},
    )
    comment_response = _mock_http_response(200, {"id": 1})
    patch_response = _mock_http_response(200, {"number": 55, "state": "closed"})

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=search_response)
    mock_client.post = AsyncMock(return_value=comment_response)
    mock_client.patch = AsyncMock(return_value=patch_response)

    with patch("qwickguard_brain.notifications.settings") as mock_settings, \
         patch("qwickguard_brain.notifications.httpx.AsyncClient", return_value=mock_client):

        mock_settings.github_token = "ghp_test"
        mock_settings.github_repo = "owner/repo"

        from qwickguard_brain.notifications import close_github_issue
        result = await close_github_issue(
            "Agent host-1 heartbeat missing", "Agent is reporting again."
        )

    assert result is True
    # Verify comment was added
    mock_client.post.assert_called_once()
    assert "comments" in str(mock_client.post.call_args)
    # Verify issue was patched to closed
    mock_client.patch.assert_called_once()
    patch_body = mock_client.patch.call_args.kwargs.get("json", {})
    assert patch_body.get("state") == "closed"


# ---------------------------------------------------------------------------
# _format_status_body (agents.py helper)
# ---------------------------------------------------------------------------

def test_format_status_body_with_issues():
    """Formats issues list into readable lines."""
    from qwickguard_brain.api.agents import _format_status_body

    report = {
        "hostname": "prod-server-1",
        "timestamp": "2026-02-24T12:00:00Z",
        "analysis": {
            "issues": [
                {"description": "CPU above 90%", "severity": "critical"},
                {"description": "Memory above 85%", "severity": "warning"},
            ]
        },
    }
    body = _format_status_body(report)
    assert "prod-server-1" in body
    assert "CPU above 90%" in body
    assert "Memory above 85%" in body
    assert "critical" in body
    assert "warning" in body


def test_format_status_body_no_issues():
    """Handles empty issues list gracefully."""
    from qwickguard_brain.api.agents import _format_status_body

    report = {
        "hostname": "host-1",
        "analysis": {"issues": []},
    }
    body = _format_status_body(report)
    assert "host-1" in body
    assert "No specific issues reported." in body


def test_format_status_body_missing_analysis():
    """Handles missing analysis key gracefully."""
    from qwickguard_brain.api.agents import _format_status_body

    report = {"hostname": "host-1"}
    body = _format_status_body(report)
    assert "host-1" in body
    assert "No specific issues reported." in body
