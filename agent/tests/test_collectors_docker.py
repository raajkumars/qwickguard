"""Tests for the Docker container metric collector."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import docker.errors
import pytest

from qwickguard_agent.collectors.docker import collect_docker_metrics
from qwickguard_agent.config import ContainerConfig, ServerConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_config(*container_names: str) -> ServerConfig:
    """Build a minimal ServerConfig with the given container names."""
    return ServerConfig(
        hostname="test-host",
        agent_id="test-agent",
        brain_url="http://brain:8000",
        compute_worker_url="http://worker:8001",
        containers=[ContainerConfig(name=n) for n in container_names],
    )


def _make_container(
    name: str,
    status: str = "running",
    health_status: str | None = "healthy",
    restart_count: int = 0,
    started_at: str = "2025-01-15T12:00:00.000000000Z",
    cpu_stats: dict | None = None,
    memory_usage: int = 256 * 1024 * 1024,
) -> MagicMock:
    """Build a mock docker Container object with realistic attrs."""
    container = MagicMock()
    container.short_id = name[:12]

    # Build State dict
    state: dict = {
        "Status": status,
        "StartedAt": started_at,
        "RestartCount": restart_count,
    }
    if health_status is not None:
        state["Health"] = {"Status": health_status}

    container.attrs = {
        "Name": f"/{name}",
        "State": state,
        "RestartCount": restart_count,
    }

    # Default CPU stats
    if cpu_stats is None:
        cpu_stats = {
            "cpu_stats": {
                "cpu_usage": {"total_usage": 2_000_000_000, "percpu_usage": [1, 1]},
                "system_cpu_usage": 20_000_000_000,
                "online_cpus": 2,
            },
            "precpu_stats": {
                "cpu_usage": {"total_usage": 1_000_000_000},
                "system_cpu_usage": 10_000_000_000,
            },
            "memory_stats": {"usage": memory_usage},
        }

    container.stats.return_value = cpu_stats
    return container


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCollectRunningContainer:
    """A healthy running container should have all fields populated."""

    def test_collect_running_container(self, mocker: "pytest_mock.MockerFixture") -> None:
        config = _minimal_config("web")
        container = _make_container(
            name="web",
            status="running",
            health_status="healthy",
            restart_count=3,
            memory_usage=512 * 1024 * 1024,
        )

        mock_client = MagicMock()
        mock_client.containers.list.return_value = [container]
        mocker.patch("docker.from_env", return_value=mock_client)

        results = collect_docker_metrics(config)

        assert len(results) == 1
        cs = results[0]
        assert cs.name == "web"
        assert cs.status == "running"
        assert cs.health == "healthy"
        assert cs.restart_count == 3
        assert cs.cpu_percent > 0
        assert cs.memory_mb == pytest.approx(512.0, rel=0.01)
        assert cs.uptime_seconds > 0

        mock_client.close.assert_called_once()

    def test_cpu_calculation(self, mocker: "pytest_mock.MockerFixture") -> None:
        """CPU percent = (delta_container / delta_system) * num_cpus * 100."""
        config = _minimal_config("app")
        # delta_cpu = 1_000_000_000, delta_sys = 10_000_000_000, cpus = 2
        # expected = (1e9 / 1e10) * 2 * 100 = 20.0
        container = _make_container("app")

        mock_client = MagicMock()
        mock_client.containers.list.return_value = [container]
        mocker.patch("docker.from_env", return_value=mock_client)

        results = collect_docker_metrics(config)

        assert results[0].cpu_percent == pytest.approx(20.0, rel=0.01)


class TestCollectMissingContainer:
    """Containers in config but absent from Docker get status='missing'."""

    def test_collect_missing_container(self, mocker: "pytest_mock.MockerFixture") -> None:
        config = _minimal_config("ghost")

        mock_client = MagicMock()
        mock_client.containers.list.return_value = []  # nothing running
        mocker.patch("docker.from_env", return_value=mock_client)

        results = collect_docker_metrics(config)

        assert len(results) == 1
        cs = results[0]
        assert cs.name == "ghost"
        assert cs.status == "missing"
        assert cs.health == "none"
        assert cs.restart_count == 0
        assert cs.cpu_percent == 0.0
        assert cs.memory_mb == 0.0
        assert cs.uptime_seconds == 0.0


class TestCollectExitedContainer:
    """Exited containers should have cpu=0 and memory=0 (no stats call)."""

    def test_collect_exited_container(self, mocker: "pytest_mock.MockerFixture") -> None:
        config = _minimal_config("worker")
        container = _make_container(
            name="worker",
            status="exited",
            health_status=None,
            started_at="0001-01-01T00:00:00Z",
        )

        mock_client = MagicMock()
        mock_client.containers.list.return_value = [container]
        mocker.patch("docker.from_env", return_value=mock_client)

        results = collect_docker_metrics(config)

        assert len(results) == 1
        cs = results[0]
        assert cs.status == "exited"
        assert cs.cpu_percent == 0.0
        assert cs.memory_mb == 0.0
        # stats() should NOT have been called for a non-running container
        container.stats.assert_not_called()

    def test_uptime_zero_for_never_started(self, mocker: "pytest_mock.MockerFixture") -> None:
        config = _minimal_config("worker")
        container = _make_container(
            name="worker",
            status="exited",
            started_at="0001-01-01T00:00:00Z",
        )

        mock_client = MagicMock()
        mock_client.containers.list.return_value = [container]
        mocker.patch("docker.from_env", return_value=mock_client)

        results = collect_docker_metrics(config)

        assert results[0].uptime_seconds == 0.0


class TestCollectUnhealthyContainer:
    """A running container with health='unhealthy' must surface that status."""

    def test_collect_unhealthy_container(self, mocker: "pytest_mock.MockerFixture") -> None:
        config = _minimal_config("api")
        container = _make_container(
            name="api",
            status="running",
            health_status="unhealthy",
        )

        mock_client = MagicMock()
        mock_client.containers.list.return_value = [container]
        mocker.patch("docker.from_env", return_value=mock_client)

        results = collect_docker_metrics(config)

        assert results[0].health == "unhealthy"

    def test_no_healthcheck_returns_none(self, mocker: "pytest_mock.MockerFixture") -> None:
        """Container without HEALTHCHECK should report health='none'."""
        config = _minimal_config("legacy")
        container = _make_container(
            name="legacy",
            status="running",
            health_status=None,  # no Health key in State
        )

        mock_client = MagicMock()
        mock_client.containers.list.return_value = [container]
        mocker.patch("docker.from_env", return_value=mock_client)

        results = collect_docker_metrics(config)

        assert results[0].health == "none"


class TestDockerUnavailable:
    """Graceful degradation when Docker daemon is unreachable."""

    def test_docker_unavailable(self, mocker: "pytest_mock.MockerFixture") -> None:
        config = _minimal_config("web", "db")
        mocker.patch(
            "docker.from_env",
            side_effect=docker.errors.DockerException("Cannot connect"),
        )

        results = collect_docker_metrics(config)

        # Returns one error entry per configured container, does not raise.
        assert len(results) == 2
        for cs in results:
            assert cs.status == "error"
            assert cs.cpu_percent == 0.0
            assert cs.memory_mb == 0.0

    def test_empty_config_returns_empty_list(self, mocker: "pytest_mock.MockerFixture") -> None:
        """No containers configured → empty list without touching Docker."""
        config = _minimal_config()  # no containers
        mock_from_env = mocker.patch("docker.from_env")

        results = collect_docker_metrics(config)

        assert results == []
        mock_from_env.assert_not_called()

    def test_api_error_on_list(self, mocker: "pytest_mock.MockerFixture") -> None:
        """APIError during containers.list() returns empty list gracefully."""
        config = _minimal_config("web")
        mock_client = MagicMock()
        mock_client.containers.list.side_effect = docker.errors.APIError("API error")
        mocker.patch("docker.from_env", return_value=mock_client)

        results = collect_docker_metrics(config)

        assert results == []
