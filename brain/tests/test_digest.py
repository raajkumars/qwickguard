"""Tests for the daily digest generation module."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from qwickguard_brain.digest import (
    _format_digest_context,
    generate_daily_digest,
    template_digest,
)
from qwickguard_brain.storage import init_db


@pytest_asyncio.fixture
async def db(tmp_path):
    await init_db(str(tmp_path / "test.db"))


def _make_report(status: str = "healthy", cpu: float = 30.0, mem: float = 40.0, disk: float = 50.0) -> dict:
    return {
        "agent_id": "test-agent",
        "hostname": "test-host",
        "timestamp": "2026-02-24T12:00:00+00:00",
        "status": status,
        "metrics_json": json.dumps({
            "cpu_percent": cpu,
            "memory_percent": mem,
            "disk_percent": disk,
        }),
    }


def _make_agent(agent_id: str = "test-agent", hostname: str = "test-host") -> dict:
    return {
        "agent_id": agent_id,
        "hostname": hostname,
        "last_report_at": "2026-02-24T12:00:00+00:00",
        "last_status": "healthy",
    }


class TestTemplateDigest:
    def test_template_digest_healthy(self):
        agent = _make_agent()
        # 20 healthy reports out of 20 = 100% healthy
        reports = [_make_report(status="healthy") for _ in range(20)]
        result = template_digest(agent, reports, [], [])
        assert "Healthy" in result
        assert "test-host" in result

    def test_template_digest_degraded(self):
        agent = _make_agent()
        # 3 healthy out of 20 = 15% healthy -> Degraded
        reports = (
            [_make_report(status="healthy") for _ in range(3)]
            + [_make_report(status="critical") for _ in range(17)]
        )
        result = template_digest(agent, reports, [], [])
        assert "Degraded" in result

    def test_template_digest_mostly_healthy(self):
        agent = _make_agent()
        # 17 healthy out of 20 = 85% -> Mostly healthy
        reports = (
            [_make_report(status="healthy") for _ in range(17)]
            + [_make_report(status="warning") for _ in range(3)]
        )
        result = template_digest(agent, reports, [], [])
        assert "Mostly healthy" in result

    def test_template_digest_no_reports(self):
        agent = _make_agent()
        result = template_digest(agent, [], [], [])
        assert "No reports" in result
        assert "test-host" in result

    def test_template_digest_includes_metrics(self):
        agent = _make_agent()
        reports = [_make_report(cpu=80.0, mem=60.0, disk=45.0)]
        result = template_digest(agent, reports, [], [])
        assert "CPU" in result
        assert "80.0" in result

    def test_template_digest_includes_actions(self):
        agent = _make_agent()
        reports = [_make_report()]
        actions = [
            {"action": "restart_container", "timestamp": "2026-02-24T10:00:00+00:00"},
            {"action": "restart_container", "timestamp": "2026-02-24T11:00:00+00:00"},
            {"action": "disk_cleanup", "timestamp": "2026-02-24T12:00:00+00:00"},
        ]
        result = template_digest(agent, reports, actions, [])
        assert "Actions Taken: 3" in result
        assert "restart_container" in result
        assert "disk_cleanup" in result

    def test_template_digest_includes_escalations(self):
        agent = _make_agent()
        reports = [_make_report()]
        escalations = [
            {"trigger_reason": "CPU above 90%", "timestamp": "2026-02-24T10:00:00+00:00"},
        ]
        result = template_digest(agent, reports, [], escalations)
        assert "Escalations" in result

    def test_template_digest_no_actions_section_when_empty(self):
        agent = _make_agent()
        reports = [_make_report()]
        result = template_digest(agent, reports, [], [])
        assert "Actions Taken" not in result


class TestFormatDigestContext:
    def test_format_digest_context_includes_hostname(self):
        agent = _make_agent(hostname="prod-server-01")
        reports = [_make_report()]
        ctx = _format_digest_context(agent, reports, [], [])
        assert "prod-server-01" in ctx

    def test_format_digest_context_includes_agent_id(self):
        agent = _make_agent(agent_id="agent-abc-123")
        reports = [_make_report()]
        ctx = _format_digest_context(agent, reports, [], [])
        assert "agent-abc-123" in ctx

    def test_format_digest_context_includes_metrics(self):
        agent = _make_agent()
        reports = [_make_report(cpu=75.5, mem=60.0, disk=40.0)]
        ctx = _format_digest_context(agent, reports, [], [])
        assert "CPU" in ctx
        assert "75.5" in ctx

    def test_format_digest_context_report_count(self):
        agent = _make_agent()
        reports = [_make_report() for _ in range(5)]
        ctx = _format_digest_context(agent, reports, [], [])
        assert "Reports in period: 5" in ctx

    def test_format_digest_context_no_reports(self):
        agent = _make_agent()
        ctx = _format_digest_context(agent, [], [], [])
        assert "Reports in period: 0" in ctx
        # No metrics section without data
        assert "CPU" not in ctx

    def test_format_digest_context_status_distribution(self):
        agent = _make_agent()
        reports = [
            _make_report(status="healthy"),
            _make_report(status="healthy"),
            _make_report(status="critical"),
        ]
        ctx = _format_digest_context(agent, reports, [], [])
        assert "healthy: 2" in ctx
        assert "critical: 1" in ctx

    def test_format_digest_context_escalations(self):
        agent = _make_agent()
        escalations = [{"trigger_reason": "Memory above 90%"}]
        ctx = _format_digest_context(agent, [], [], escalations)
        assert "Escalations" in ctx
        assert "Memory above 90%" in ctx

    def test_format_digest_context_action_counts(self):
        agent = _make_agent()
        actions = [
            {"action": "restart_container"},
            {"action": "restart_container"},
            {"action": "disk_cleanup"},
        ]
        ctx = _format_digest_context(agent, [], actions, [])
        assert "restart_container: 2" in ctx
        assert "disk_cleanup: 1" in ctx

    def test_format_digest_context_handles_missing_metrics(self):
        """Reports with malformed metrics_json should not crash context formatting."""
        agent = _make_agent()
        reports = [
            {"agent_id": "test-agent", "status": "healthy", "metrics_json": "not-valid-json"},
        ]
        # Should not raise
        ctx = _format_digest_context(agent, reports, [], [])
        assert "test-agent" in ctx


class TestGenerateDailyDigest:
    @pytest.mark.asyncio
    async def test_generate_daily_digest_no_agents(self, db):
        """Returns gracefully when no agents are registered."""
        with patch("qwickguard_brain.digest.get_agents", new_callable=AsyncMock) as mock_agents:
            mock_agents.return_value = []
            # Should not raise
            await generate_daily_digest()
            mock_agents.assert_called_once()

    @pytest.mark.asyncio
    async def test_generate_daily_digest_with_agent_dispatches(self, db):
        """Verify dispatch_notification is called for a registered agent."""
        agent = _make_agent()
        reports = [_make_report()]
        actions = [{"action": "restart_container", "timestamp": "2026-02-24T12:00:00+00:00"}]
        escalations: list = []

        with (
            patch("qwickguard_brain.digest.get_agents", new_callable=AsyncMock) as mock_agents,
            patch("qwickguard_brain.digest.get_agent_history", new_callable=AsyncMock) as mock_history,
            patch("qwickguard_brain.digest.get_recent_actions", new_callable=AsyncMock) as mock_actions,
            patch("qwickguard_brain.digest.get_recent_escalations", new_callable=AsyncMock) as mock_escalations,
            patch("qwickguard_brain.digest.settings") as mock_settings,
            patch("qwickguard_brain.digest.dispatch_notification", new_callable=AsyncMock) as mock_dispatch,
        ):
            mock_agents.return_value = [agent]
            mock_history.return_value = reports
            mock_actions.return_value = actions
            mock_escalations.return_value = escalations
            mock_settings.anthropic_api_key = None  # Force template path

            await generate_daily_digest()

        mock_dispatch.assert_called_once()
        call_kwargs = mock_dispatch.call_args.kwargs
        assert call_kwargs["agent_id"] == "test-agent"
        assert call_kwargs["severity"] == "info"
        assert "test-host" in call_kwargs["title"]
        assert call_kwargs["body"]  # Non-empty summary

    @pytest.mark.asyncio
    async def test_generate_daily_digest_uses_template_when_no_api_key(self, db):
        """Template digest used when no Anthropic API key is configured."""
        agent = _make_agent()
        reports = [_make_report(status="healthy") for _ in range(10)]

        with (
            patch("qwickguard_brain.digest.get_agents", new_callable=AsyncMock) as mock_agents,
            patch("qwickguard_brain.digest.get_agent_history", new_callable=AsyncMock) as mock_history,
            patch("qwickguard_brain.digest.get_recent_actions", new_callable=AsyncMock) as mock_actions,
            patch("qwickguard_brain.digest.get_recent_escalations", new_callable=AsyncMock) as mock_escalations,
            patch("qwickguard_brain.digest.settings") as mock_settings,
            patch("qwickguard_brain.digest.dispatch_notification", new_callable=AsyncMock) as mock_dispatch,
        ):
            mock_agents.return_value = [agent]
            mock_history.return_value = reports
            mock_actions.return_value = []
            mock_escalations.return_value = []
            mock_settings.anthropic_api_key = None

            await generate_daily_digest()

        body = mock_dispatch.call_args.kwargs["body"]
        assert "Healthy" in body

    @pytest.mark.asyncio
    async def test_generate_daily_digest_claude_fallback_on_error(self, db):
        """Falls back to template digest when Claude API raises an exception."""
        agent = _make_agent()
        reports = [_make_report(status="healthy") for _ in range(5)]

        with (
            patch("qwickguard_brain.digest.get_agents", new_callable=AsyncMock) as mock_agents,
            patch("qwickguard_brain.digest.get_agent_history", new_callable=AsyncMock) as mock_history,
            patch("qwickguard_brain.digest.get_recent_actions", new_callable=AsyncMock) as mock_actions,
            patch("qwickguard_brain.digest.get_recent_escalations", new_callable=AsyncMock) as mock_escalations,
            patch("qwickguard_brain.digest.settings") as mock_settings,
            patch("qwickguard_brain.digest.claude_digest", new_callable=AsyncMock) as mock_claude,
            patch("qwickguard_brain.digest.dispatch_notification", new_callable=AsyncMock) as mock_dispatch,
        ):
            mock_agents.return_value = [agent]
            mock_history.return_value = reports
            mock_actions.return_value = []
            mock_escalations.return_value = []
            mock_settings.anthropic_api_key = "fake-key"
            mock_claude.return_value = None  # Simulate Claude failure

            await generate_daily_digest()

        mock_dispatch.assert_called_once()
        body = mock_dispatch.call_args.kwargs["body"]
        # Template fallback should produce valid output
        assert body

    @pytest.mark.asyncio
    async def test_generate_daily_digest_multiple_agents(self, db):
        """Digest dispatched once per agent."""
        agents = [
            _make_agent(agent_id="agent-1", hostname="host-1"),
            _make_agent(agent_id="agent-2", hostname="host-2"),
        ]

        with (
            patch("qwickguard_brain.digest.get_agents", new_callable=AsyncMock) as mock_agents,
            patch("qwickguard_brain.digest.get_agent_history", new_callable=AsyncMock) as mock_history,
            patch("qwickguard_brain.digest.get_recent_actions", new_callable=AsyncMock) as mock_actions,
            patch("qwickguard_brain.digest.get_recent_escalations", new_callable=AsyncMock) as mock_escalations,
            patch("qwickguard_brain.digest.settings") as mock_settings,
            patch("qwickguard_brain.digest.dispatch_notification", new_callable=AsyncMock) as mock_dispatch,
        ):
            mock_agents.return_value = agents
            mock_history.return_value = []
            mock_actions.return_value = []
            mock_escalations.return_value = []
            mock_settings.anthropic_api_key = None

            await generate_daily_digest()

        assert mock_dispatch.call_count == 2
        dispatched_agent_ids = {
            call.kwargs["agent_id"] for call in mock_dispatch.call_args_list
        }
        assert dispatched_agent_ids == {"agent-1", "agent-2"}
