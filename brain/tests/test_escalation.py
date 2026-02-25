"""Tests for the Claude API escalation engine."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from qwickguard_brain.escalation import (
    EscalationEngine,
    _format_escalation_context,
    _hash_issues,
    _parse_diagnosis,
)
from qwickguard_brain.storage import init_db


@pytest_asyncio.fixture
async def db(tmp_path):
    await init_db(str(tmp_path / "test.db"))


def _sample_report():
    return {
        "agent_id": "test-agent",
        "hostname": "test-host",
        "timestamp": "2026-02-24T12:00:00Z",
        "metrics": {"cpu_percent": 95.0, "memory_percent": 88.0},
        "analysis": {
            "status": "critical",
            "issues": [
                {"description": "CPU above 90%", "severity": "critical"},
                {"description": "Memory above 85%", "severity": "warning"},
            ],
            "actions": [{"action": "restart_container", "target": "app"}],
            "escalate_to_claude": True,
        },
        "actions_taken": [
            {"action": "restart_container", "target": "app", "result": "success"}
        ],
    }


class TestParsingHelpers:
    def test_hash_issues_deterministic(self):
        issues = [{"description": "CPU high", "severity": "critical"}]
        h1 = _hash_issues(issues)
        h2 = _hash_issues(issues)
        assert h1 == h2

    def test_hash_issues_different_for_different_issues(self):
        h1 = _hash_issues([{"description": "CPU high"}])
        h2 = _hash_issues([{"description": "Memory high"}])
        assert h1 != h2

    def test_parse_diagnosis_valid_json(self):
        raw = json.dumps({
            "severity": "critical",
            "diagnosis": "CPU overloaded",
            "recommended_actions": [],
            "escalation_summary": "CPU is at 95%",
        })
        result = _parse_diagnosis(raw)
        assert result["severity"] == "critical"
        assert result["diagnosis"] == "CPU overloaded"

    def test_parse_diagnosis_with_code_fence(self):
        raw = '```json\n{"severity": "warning", "diagnosis": "test", "recommended_actions": [], "escalation_summary": "test"}\n```'
        result = _parse_diagnosis(raw)
        assert result["severity"] == "warning"

    def test_parse_diagnosis_invalid_json(self):
        result = _parse_diagnosis("This is not JSON at all")
        assert result["severity"] == "warning"
        assert "could not be parsed" in result["escalation_summary"]

    def test_format_escalation_context(self):
        report = _sample_report()
        ctx = _format_escalation_context(report, [])
        assert "test-agent" in ctx
        assert "test-host" in ctx
        assert "95.0" in ctx


class TestEscalationEngine:
    @pytest.mark.asyncio
    async def test_escalate_no_api_key(self, db):
        engine = EscalationEngine()
        # No API key configured
        with patch.object(engine, "_get_client", return_value=None):
            result = await engine.escalate("agent-1", _sample_report())
        assert result is None

    @pytest.mark.asyncio
    async def test_escalate_rate_limited(self, db):
        engine = EscalationEngine()
        # Fill rate limit
        from datetime import date
        today = date.today().isoformat()
        engine._daily_count[f"agent-1:{today}"] = 999

        mock_client = MagicMock()
        with patch.object(engine, "_get_client", return_value=mock_client):
            with patch("qwickguard_brain.escalation.settings") as mock_settings:
                mock_settings.max_claude_calls_per_day = 20
                mock_settings.anthropic_api_key = "test-key"
                result = await engine.escalate("agent-1", _sample_report())
        assert result is None

    @pytest.mark.asyncio
    async def test_escalate_cached(self, db):
        engine = EscalationEngine()
        report = _sample_report()
        issues = report["analysis"]["issues"]
        issue_hash = _hash_issues(issues)

        cached_result = {"severity": "critical", "diagnosis": "cached", "recommended_actions": [], "escalation_summary": "cached"}
        engine._cache[issue_hash] = (datetime.now(timezone.utc), cached_result)

        mock_client = MagicMock()
        with patch.object(engine, "_get_client", return_value=mock_client):
            with patch("qwickguard_brain.escalation.settings") as mock_settings:
                mock_settings.max_claude_calls_per_day = 20
                mock_settings.anthropic_api_key = "test-key"
                result = await engine.escalate("agent-1", report)
        assert result == cached_result

    @pytest.mark.asyncio
    async def test_escalate_cache_expired(self, db):
        engine = EscalationEngine()
        report = _sample_report()
        issues = report["analysis"]["issues"]
        issue_hash = _hash_issues(issues)

        # Cache from 2 hours ago - should be expired
        old_result = {"severity": "warning", "diagnosis": "old", "recommended_actions": [], "escalation_summary": "old"}
        engine._cache[issue_hash] = (datetime.now(timezone.utc) - timedelta(hours=2), old_result)

        # Mock Claude response
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=json.dumps({
            "severity": "critical",
            "diagnosis": "fresh",
            "recommended_actions": [],
            "escalation_summary": "fresh",
        }))]

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with patch.object(engine, "_get_client", return_value=mock_client):
            with patch("qwickguard_brain.escalation.settings") as mock_settings:
                mock_settings.max_claude_calls_per_day = 20
                mock_settings.anthropic_api_key = "test-key"
                result = await engine.escalate("agent-1", report)

        assert result is not None
        assert result["diagnosis"] == "fresh"

    @pytest.mark.asyncio
    async def test_escalate_api_error(self, db):
        engine = EscalationEngine()
        report = _sample_report()

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(side_effect=Exception("API down"))

        with patch.object(engine, "_get_client", return_value=mock_client):
            with patch("qwickguard_brain.escalation.settings") as mock_settings:
                mock_settings.max_claude_calls_per_day = 20
                mock_settings.anthropic_api_key = "test-key"
                result = await engine.escalate("agent-1", report)
        assert result is None
