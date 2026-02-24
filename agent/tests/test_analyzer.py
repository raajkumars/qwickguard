"""Tests for the Llama analyzer and threshold fallback."""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from qwickguard_agent.analyzer import (
    _determine_severity,
    _parse_analysis,
    _threshold_fallback,
    analyze_metrics,
)
from qwickguard_agent.config import (
    ContainerConfig,
    ServerConfig,
    ThresholdConfig,
)
from qwickguard_agent.models import (
    AnalysisResult,
    CollectedMetrics,
    ContainerStatus,
    ProcessInfo,
    ServiceHealth,
    SystemMetrics,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_system(
    cpu: float = 10.0,
    ram: float = 20.0,
    disk: float = 30.0,
) -> SystemMetrics:
    return SystemMetrics(
        cpu_percent=cpu,
        ram_percent=ram,
        ram_available_gb=10.0,
        disk_percent=disk,
        disk_available_gb=100.0,
        load_avg=(0.5, 0.4, 0.3),
        open_files=200,
        uptime_seconds=86400.0,
    )


def _make_container(
    name: str = "web",
    status: str = "running",
    health: str = "healthy",
    restarts: int = 0,
) -> ContainerStatus:
    return ContainerStatus(
        name=name,
        status=status,
        health=health,
        restart_count=restarts,
        cpu_percent=1.0,
        memory_mb=100.0,
        uptime_seconds=3600.0,
    )


def _make_config(
    containers: list[ContainerConfig] | None = None,
    thresholds: ThresholdConfig | None = None,
    zombie_patterns: list[str] | None = None,
) -> ServerConfig:
    return ServerConfig(
        hostname="test-host",
        agent_id="test-agent",
        brain_url="http://brain:8000",
        compute_worker_url="http://compute:8001",
        thresholds=thresholds or ThresholdConfig(),
        containers=containers or [],
        zombie_patterns=zombie_patterns or [],
    )


def _make_metrics(
    system: SystemMetrics | None = None,
    containers: list[ContainerStatus] | None = None,
    services: list[ServiceHealth] | None = None,
    processes: list[ProcessInfo] | None = None,
) -> CollectedMetrics:
    return CollectedMetrics(
        system=system or _make_system(),
        containers=containers or [],
        services=services or [],
        processes=processes or [],
        timestamp=datetime.utcnow(),
    )


# ---------------------------------------------------------------------------
# _threshold_fallback tests
# ---------------------------------------------------------------------------


def test_threshold_fallback_healthy():
    """All metrics normal: status healthy, no issues, no actions."""
    metrics = _make_metrics()
    config = _make_config()
    result = _threshold_fallback(metrics, config)

    assert result.status == "healthy"
    assert result.issues == []
    assert result.actions == []
    assert result.escalate_to_claude is False


def test_threshold_fallback_cpu_warning():
    """CPU at warning threshold produces warning status."""
    metrics = _make_metrics(system=_make_system(cpu=85.0))
    config = _make_config()
    result = _threshold_fallback(metrics, config)

    assert result.status == "warning"
    assert any("CPU warning" in i for i in result.issues)
    assert result.escalate_to_claude is False


def test_threshold_fallback_cpu_critical():
    """CPU at critical threshold produces critical status."""
    metrics = _make_metrics(system=_make_system(cpu=96.0))
    config = _make_config()
    result = _threshold_fallback(metrics, config)

    assert result.status == "critical"
    assert any("CPU critical" in i for i in result.issues)


def test_threshold_fallback_missing_container():
    """Missing monitored container produces an action to restart or compose up."""
    config = _make_config(containers=[ContainerConfig(name="db", critical=True)])
    # No containers running
    metrics = _make_metrics(containers=[])
    result = _threshold_fallback(metrics, config)

    assert result.status == "critical"
    assert any("Container missing" in i for i in result.issues)
    # Action must be restart_container or docker_compose_up
    action_names = {a["action"] for a in result.actions}
    assert action_names & {"restart_container", "docker_compose_up"}


def test_threshold_fallback_unhealthy_container():
    """Unhealthy container (non-healthy health status) triggers restart action."""
    config = _make_config(containers=[ContainerConfig(name="app")])
    container = _make_container(name="app", health="unhealthy")
    metrics = _make_metrics(containers=[container])
    result = _threshold_fallback(metrics, config)

    assert any(a["action"] == "restart_container" for a in result.actions)
    assert any("Container unhealthy" in i for i in result.issues)


def test_threshold_fallback_zombies():
    """Dead process triggers kill_zombies action."""
    process = ProcessInfo(name="ghost-worker", pid=None, pattern="ghost-worker", alive=False)
    metrics = _make_metrics(processes=[process])
    config = _make_config()
    result = _threshold_fallback(metrics, config)

    assert any(a["action"] == "kill_zombies" for a in result.actions)
    assert any("Process dead/zombie" in i for i in result.issues)


def test_threshold_fallback_escalation():
    """More than 2 critical issues sets escalate_to_claude=True."""
    # Three critical issues: CPU critical + RAM critical + disk critical
    system = _make_system(cpu=96.0, ram=96.0, disk=91.0)
    metrics = _make_metrics(system=system)
    config = _make_config()
    result = _threshold_fallback(metrics, config)

    assert result.escalate_to_claude is True
    assert result.status == "critical"


# ---------------------------------------------------------------------------
# _llama_analysis / analyze_metrics tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_llama_analysis_success():
    """Valid JSON response from compute worker is parsed correctly."""
    expected = {
        "status": "warning",
        "issues": ["High CPU"],
        "actions": [{"action": "restart_container", "target": "web", "reason": "high load"}],
        "escalate_to_claude": False,
    }
    mock_response = MagicMock()
    mock_response.json.return_value = {"text": json.dumps(expected)}
    mock_response.raise_for_status = MagicMock()

    metrics = _make_metrics(system=_make_system(cpu=85.0))
    config = _make_config()

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await analyze_metrics(metrics, config)

    assert result.status == "warning"
    assert result.issues == ["High CPU"]
    assert result.escalate_to_claude is False


@pytest.mark.asyncio
async def test_llama_analysis_fallback():
    """httpx error causes graceful fallback to threshold analysis."""
    import httpx

    metrics = _make_metrics(system=_make_system(cpu=96.0))
    config = _make_config()

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await analyze_metrics(metrics, config)

    # Should have used threshold fallback; CPU at 96% is critical
    assert result.status == "critical"


# ---------------------------------------------------------------------------
# _parse_analysis tests
# ---------------------------------------------------------------------------


def test_parse_analysis_with_code_fences():
    """JSON wrapped in markdown code fences is extracted and parsed."""
    data = {
        "status": "healthy",
        "issues": [],
        "actions": [],
        "escalate_to_claude": False,
    }
    text = f"```json\n{json.dumps(data)}\n```"
    result = _parse_analysis(text)

    assert result.status == "healthy"
    assert result.issues == []
    assert result.escalate_to_claude is False


def test_parse_analysis_plain_json():
    """Plain JSON without fences is extracted correctly."""
    data = {
        "status": "warning",
        "issues": ["high disk"],
        "actions": [],
        "escalate_to_claude": False,
    }
    result = _parse_analysis(json.dumps(data))

    assert result.status == "warning"
    assert "high disk" in result.issues


def test_parse_analysis_json_with_surrounding_text():
    """JSON embedded in surrounding prose is extracted correctly."""
    data = {
        "status": "critical",
        "issues": ["cpu over threshold"],
        "actions": [{"action": "restart_container", "target": "app", "reason": "cpu"}],
        "escalate_to_claude": True,
    }
    text = f"Based on my analysis here is the result:\n{json.dumps(data)}\nPlease act accordingly."
    result = _parse_analysis(text)

    assert result.status == "critical"
    assert result.escalate_to_claude is True


def test_parse_analysis_invalid_json_raises():
    """Non-parseable text raises ValueError."""
    with pytest.raises(ValueError):
        _parse_analysis("This is not JSON at all.")


# ---------------------------------------------------------------------------
# _determine_severity tests
# ---------------------------------------------------------------------------


def test_determine_severity_routine():
    """All metrics below warning thresholds returns routine."""
    metrics = _make_metrics(system=_make_system(cpu=10.0, ram=20.0, disk=30.0))
    config = _make_config()
    assert _determine_severity(metrics, config) == "routine"


def test_determine_severity_warning_cpu():
    """CPU at warning threshold returns warning."""
    metrics = _make_metrics(system=_make_system(cpu=82.0))
    config = _make_config()
    assert _determine_severity(metrics, config) == "warning"


def test_determine_severity_critical_cpu():
    """CPU at critical threshold returns critical."""
    metrics = _make_metrics(system=_make_system(cpu=96.0))
    config = _make_config()
    assert _determine_severity(metrics, config) == "critical"


def test_determine_severity_missing_container_is_critical():
    """Missing monitored container is treated as critical severity."""
    config = _make_config(containers=[ContainerConfig(name="db")])
    metrics = _make_metrics(containers=[])  # db is missing
    assert _determine_severity(metrics, config) == "critical"


def test_determine_severity_unhealthy_container_is_warning():
    """Unhealthy container (present but not healthy) is warning severity."""
    config = _make_config(containers=[ContainerConfig(name="app")])
    container = _make_container(name="app", health="unhealthy")
    metrics = _make_metrics(
        system=_make_system(cpu=10.0, ram=20.0, disk=30.0),
        containers=[container],
    )
    assert _determine_severity(metrics, config) == "warning"
