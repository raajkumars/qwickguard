"""Tests for main.py: run_cycle integration and error resilience."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from qwickguard_agent.config import ContainerConfig, ServerConfig, ThresholdConfig
from qwickguard_agent.healer import Healer
from qwickguard_agent.main import run_cycle
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config() -> ServerConfig:
    return ServerConfig(
        hostname="test-host",
        agent_id="test-agent",
        brain_url="http://brain:8080",
        compute_worker_url="http://compute:8081",
        thresholds=ThresholdConfig(),
    )


def _make_system_metrics() -> SystemMetrics:
    return SystemMetrics(
        cpu_percent=10.0,
        ram_percent=40.0,
        ram_available_gb=8.0,
        disk_percent=30.0,
        disk_available_gb=100.0,
        load_avg=(0.1, 0.2, 0.3),
        open_files=50,
        uptime_seconds=3600.0,
    )


def _make_healthy_analysis() -> AnalysisResult:
    return AnalysisResult(
        status="healthy",
        issues=[],
        actions=[],
        escalate_to_claude=False,
    )


def _make_analysis_with_actions() -> AnalysisResult:
    return AnalysisResult(
        status="warning",
        issues=["Container missing: myapp"],
        actions=[
            {
                "action": "restart_container",
                "target": "myapp",
                "reason": "Container myapp is missing",
            }
        ],
        escalate_to_claude=False,
    )


def _make_healer(tmp_path: Path) -> Healer:
    config = _make_config()
    return Healer(config=config, audit_log_path=tmp_path / "audit.jsonl")


# ---------------------------------------------------------------------------
# test_run_cycle_completes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_cycle_completes(tmp_path: Path) -> None:
    """run_cycle calls all four collectors, analyzer, and reporter without errors."""
    config = _make_config()
    healer = _make_healer(tmp_path)

    system_metrics = _make_system_metrics()
    analysis = _make_healthy_analysis()

    with (
        patch(
            "qwickguard_agent.main.collect_system_metrics",
            new=AsyncMock(return_value=system_metrics),
        ),
        patch(
            "qwickguard_agent.main.collect_docker_metrics",
            return_value=[],
        ),
        patch(
            "qwickguard_agent.main.collect_service_health",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "qwickguard_agent.main.collect_process_info",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "qwickguard_agent.main.analyze_metrics",
            new=AsyncMock(return_value=analysis),
        ),
        patch(
            "qwickguard_agent.main.report_to_brain",
            new=AsyncMock(),
        ) as mock_report,
    ):
        await run_cycle(config, healer)

    # Verify reporter was called with an AgentReport.
    mock_report.assert_called_once()
    report_arg: AgentReport = mock_report.call_args[0][0]
    assert isinstance(report_arg, AgentReport)
    assert report_arg.agent_id == "test-agent"
    assert report_arg.analysis.status == "healthy"
    assert report_arg.actions_taken == []


# ---------------------------------------------------------------------------
# test_run_cycle_handles_collector_error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_cycle_handles_collector_error(tmp_path: Path) -> None:
    """When one collector raises, the cycle continues using empty/fallback data."""
    config = _make_config()
    healer = _make_healer(tmp_path)

    analysis = _make_healthy_analysis()

    with (
        patch(
            "qwickguard_agent.main.collect_system_metrics",
            new=AsyncMock(side_effect=RuntimeError("psutil error")),
        ),
        patch(
            "qwickguard_agent.main.collect_docker_metrics",
            side_effect=RuntimeError("docker daemon unreachable"),
        ),
        patch(
            "qwickguard_agent.main.collect_service_health",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "qwickguard_agent.main.collect_process_info",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "qwickguard_agent.main.analyze_metrics",
            new=AsyncMock(return_value=analysis),
        ),
        patch(
            "qwickguard_agent.main.report_to_brain",
            new=AsyncMock(),
        ) as mock_report,
    ):
        # Should not raise even though two collectors failed.
        await run_cycle(config, healer)

    # Reporter was still called.
    mock_report.assert_called_once()
    report_arg: AgentReport = mock_report.call_args[0][0]
    # System metrics fallback: all zeros.
    assert report_arg.metrics.system.cpu_percent == 0.0
    # Docker fallback: empty list.
    assert report_arg.metrics.containers == []


# ---------------------------------------------------------------------------
# test_run_cycle_no_actions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_cycle_no_actions(tmp_path: Path) -> None:
    """When analysis returns no actions, healer.execute_actions is not called."""
    config = _make_config()
    healer = _make_healer(tmp_path)

    system_metrics = _make_system_metrics()
    analysis = _make_healthy_analysis()  # no actions

    with (
        patch(
            "qwickguard_agent.main.collect_system_metrics",
            new=AsyncMock(return_value=system_metrics),
        ),
        patch(
            "qwickguard_agent.main.collect_docker_metrics",
            return_value=[],
        ),
        patch(
            "qwickguard_agent.main.collect_service_health",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "qwickguard_agent.main.collect_process_info",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "qwickguard_agent.main.analyze_metrics",
            new=AsyncMock(return_value=analysis),
        ),
        patch.object(healer, "execute_actions", new=AsyncMock()) as mock_execute,
        patch(
            "qwickguard_agent.main.report_to_brain",
            new=AsyncMock(),
        ),
    ):
        await run_cycle(config, healer)

    # execute_actions must not have been called when there are no actions.
    mock_execute.assert_not_called()


# ---------------------------------------------------------------------------
# test_run_cycle_with_actions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_cycle_with_actions(tmp_path: Path) -> None:
    """When analysis produces actions, healer.execute_actions is called once."""
    config = ServerConfig(
        hostname="test-host",
        agent_id="test-agent",
        brain_url="http://brain:8080",
        compute_worker_url="http://compute:8081",
        containers=[ContainerConfig(name="myapp")],
    )
    healer = Healer(config=config, audit_log_path=tmp_path / "audit.jsonl")

    system_metrics = _make_system_metrics()
    analysis = _make_analysis_with_actions()

    action_result = ActionResult(
        action="restart_container",
        target="myapp",
        reason="Container myapp is missing",
        decided_by="agent:threshold_rules",
        result="success",
    )

    with (
        patch(
            "qwickguard_agent.main.collect_system_metrics",
            new=AsyncMock(return_value=system_metrics),
        ),
        patch(
            "qwickguard_agent.main.collect_docker_metrics",
            return_value=[],
        ),
        patch(
            "qwickguard_agent.main.collect_service_health",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "qwickguard_agent.main.collect_process_info",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "qwickguard_agent.main.analyze_metrics",
            new=AsyncMock(return_value=analysis),
        ),
        patch.object(
            healer, "execute_actions", new=AsyncMock(return_value=[action_result])
        ) as mock_execute,
        patch(
            "qwickguard_agent.main.report_to_brain",
            new=AsyncMock(),
        ) as mock_report,
    ):
        await run_cycle(config, healer)

    # execute_actions should have been called once with the analysis actions.
    mock_execute.assert_called_once_with(
        analysis.actions, decided_by="agent:threshold_rules"
    )

    # The report should include the action result.
    report_arg: AgentReport = mock_report.call_args[0][0]
    assert len(report_arg.actions_taken) == 1
    assert report_arg.actions_taken[0].action == "restart_container"
    assert report_arg.actions_taken[0].result == "success"


# ---------------------------------------------------------------------------
# test_run_cycle_service_collector_error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_cycle_service_collector_error(tmp_path: Path) -> None:
    """Service collector failure produces empty services list; cycle continues."""
    config = _make_config()
    healer = _make_healer(tmp_path)

    system_metrics = _make_system_metrics()
    analysis = _make_healthy_analysis()

    with (
        patch(
            "qwickguard_agent.main.collect_system_metrics",
            new=AsyncMock(return_value=system_metrics),
        ),
        patch(
            "qwickguard_agent.main.collect_docker_metrics",
            return_value=[],
        ),
        patch(
            "qwickguard_agent.main.collect_service_health",
            new=AsyncMock(side_effect=Exception("httpx pool closed")),
        ),
        patch(
            "qwickguard_agent.main.collect_process_info",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "qwickguard_agent.main.analyze_metrics",
            new=AsyncMock(return_value=analysis),
        ),
        patch(
            "qwickguard_agent.main.report_to_brain",
            new=AsyncMock(),
        ) as mock_report,
    ):
        await run_cycle(config, healer)

    mock_report.assert_called_once()
    report_arg: AgentReport = mock_report.call_args[0][0]
    assert report_arg.metrics.services == []
