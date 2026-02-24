"""Tests for reporter.py: brain delivery, local queue fallback, and queue replay."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from qwickguard_agent.models import (
    ActionResult,
    AgentReport,
    AnalysisResult,
    CollectedMetrics,
    ProcessInfo,
    ServiceHealth,
    SystemMetrics,
    ContainerStatus,
)
from qwickguard_agent.reporter import _queue_report, report_to_brain, _replay_queue


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_report(agent_id: str = "test-agent") -> AgentReport:
    """Build a minimal but valid AgentReport."""
    system = SystemMetrics(
        cpu_percent=10.0,
        ram_percent=40.0,
        ram_available_gb=8.0,
        disk_percent=30.0,
        disk_available_gb=100.0,
        load_avg=(0.1, 0.2, 0.3),
        open_files=50,
        uptime_seconds=3600.0,
    )
    metrics = CollectedMetrics(
        system=system,
        containers=[],
        services=[],
        processes=[],
        timestamp=datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
    )
    analysis = AnalysisResult(
        status="healthy",
        issues=[],
        actions=[],
        escalate_to_claude=False,
    )
    return AgentReport(
        agent_id=agent_id,
        hostname="test-host",
        timestamp=datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        metrics=metrics,
        analysis=analysis,
        actions_taken=[],
    )


# ---------------------------------------------------------------------------
# test_report_to_brain_success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_report_to_brain_success(tmp_path: Path) -> None:
    """When brain returns 200, report is delivered and no queue file is created."""
    report = _make_report()

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()  # no-op on success

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    queue_dir = tmp_path / "report_queue"
    queue_dir.mkdir()

    with (
        patch("qwickguard_agent.reporter.httpx.AsyncClient", return_value=mock_client),
        patch("qwickguard_agent.reporter._QUEUE_DIR", queue_dir),
    ):
        await report_to_brain(report, "http://brain:8080")

    # Brain should have been called once for the report.
    mock_client.post.assert_called_once()
    call_args = mock_client.post.call_args
    assert call_args[0][0] == "http://brain:8080/api/agent/report"
    posted_payload = call_args[1]["json"]
    assert posted_payload["agent_id"] == "test-agent"

    # No queue files should have been written.
    queue_files = list(queue_dir.glob("*.json"))
    assert len(queue_files) == 0


# ---------------------------------------------------------------------------
# test_report_to_brain_queues_on_failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_report_to_brain_queues_on_failure(tmp_path: Path) -> None:
    """When brain is unreachable, report is saved to the local queue directory."""
    report = _make_report()

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(
        side_effect=httpx.ConnectError("Connection refused")
    )
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    queue_dir = tmp_path / "report_queue"
    queue_dir.mkdir()

    with (
        patch("qwickguard_agent.reporter.httpx.AsyncClient", return_value=mock_client),
        patch("qwickguard_agent.reporter._QUEUE_DIR", queue_dir),
    ):
        await report_to_brain(report, "http://brain:8080")

    # A queue file should have been created.
    queue_files = list(queue_dir.glob("*.json"))
    assert len(queue_files) == 1

    # The file should contain a valid JSON report.
    content = json.loads(queue_files[0].read_text(encoding="utf-8"))
    assert content["agent_id"] == "test-agent"
    assert content["hostname"] == "test-host"


@pytest.mark.asyncio
async def test_report_to_brain_queues_on_http_status_error(tmp_path: Path) -> None:
    """When brain returns 500, report is saved to the local queue directory."""
    report = _make_report()

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError(
            "500 Internal Server Error",
            request=MagicMock(),
            response=MagicMock(),
        )
    )

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    queue_dir = tmp_path / "report_queue"
    queue_dir.mkdir()

    with (
        patch("qwickguard_agent.reporter.httpx.AsyncClient", return_value=mock_client),
        patch("qwickguard_agent.reporter._QUEUE_DIR", queue_dir),
    ):
        await report_to_brain(report, "http://brain:8080")

    queue_files = list(queue_dir.glob("*.json"))
    assert len(queue_files) == 1


# ---------------------------------------------------------------------------
# test_replay_queue
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replay_queue(tmp_path: Path) -> None:
    """Queued reports are sent to brain and deleted on successful delivery."""
    queue_dir = tmp_path / "report_queue"
    queue_dir.mkdir()

    # Create two queue files with distinct names (sorted order matters).
    report1 = _make_report(agent_id="agent-1")
    report2 = _make_report(agent_id="agent-2")

    file1 = queue_dir / "20240101_120000.json"
    file2 = queue_dir / "20240101_120001.json"
    file1.write_text(report1.model_dump_json(), encoding="utf-8")
    file2.write_text(report2.model_dump_json(), encoding="utf-8")

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with (
        patch("qwickguard_agent.reporter.httpx.AsyncClient", return_value=mock_client),
        patch("qwickguard_agent.reporter._QUEUE_DIR", queue_dir),
    ):
        await _replay_queue("http://brain:8080")

    # Both files should have been sent and deleted.
    assert mock_client.post.call_count == 2
    assert not file1.exists()
    assert not file2.exists()


@pytest.mark.asyncio
async def test_replay_queue_stops_on_first_failure(tmp_path: Path) -> None:
    """Queue replay stops after the first delivery failure, leaving remaining files."""
    queue_dir = tmp_path / "report_queue"
    queue_dir.mkdir()

    report = _make_report()
    file1 = queue_dir / "20240101_120000.json"
    file2 = queue_dir / "20240101_120001.json"
    file1.write_text(report.model_dump_json(), encoding="utf-8")
    file2.write_text(report.model_dump_json(), encoding="utf-8")

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(
        side_effect=httpx.ConnectError("Connection refused")
    )
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with (
        patch("qwickguard_agent.reporter.httpx.AsyncClient", return_value=mock_client),
        patch("qwickguard_agent.reporter._QUEUE_DIR", queue_dir),
    ):
        await _replay_queue("http://brain:8080")

    # Only one attempt should have been made (stopped after first failure).
    assert mock_client.post.call_count == 1
    # Both files should still exist.
    assert file1.exists()
    assert file2.exists()


@pytest.mark.asyncio
async def test_replay_queue_empty(tmp_path: Path) -> None:
    """Replay does nothing and makes no HTTP calls when queue is empty."""
    queue_dir = tmp_path / "report_queue"
    queue_dir.mkdir()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with (
        patch("qwickguard_agent.reporter.httpx.AsyncClient", return_value=mock_client),
        patch("qwickguard_agent.reporter._QUEUE_DIR", queue_dir),
    ):
        await _replay_queue("http://brain:8080")

    mock_client.post.assert_not_called()


# ---------------------------------------------------------------------------
# test _queue_report directly
# ---------------------------------------------------------------------------


def test_queue_report_creates_file(tmp_path: Path) -> None:
    """_queue_report persists the report as JSON in the queue directory."""
    queue_dir = tmp_path / "report_queue"
    queue_dir.mkdir()

    report = _make_report()

    with patch("qwickguard_agent.reporter._QUEUE_DIR", queue_dir):
        queue_file = _queue_report(report)

    assert queue_file.exists()
    content = json.loads(queue_file.read_text(encoding="utf-8"))
    assert content["agent_id"] == "test-agent"


def test_queue_report_no_clobber(tmp_path: Path) -> None:
    """_queue_report does not overwrite an existing file with the same timestamp."""
    queue_dir = tmp_path / "report_queue"
    queue_dir.mkdir()

    report = _make_report()
    fixed_ts = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    with (
        patch("qwickguard_agent.reporter._QUEUE_DIR", queue_dir),
        patch("qwickguard_agent.reporter.datetime") as mock_dt,
    ):
        mock_dt.now.return_value = fixed_ts
        file1 = _queue_report(report)
        file2 = _queue_report(report)

    assert file1 != file2
    assert file1.exists()
    assert file2.exists()
