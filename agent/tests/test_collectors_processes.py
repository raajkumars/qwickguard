"""Tests for the process liveness collector."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import psutil
import pytest

from qwickguard_agent.collectors.processes import collect_process_info
from qwickguard_agent.config import ServerConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _config(
    runners: list[str] | None = None,
    zombies: list[str] | None = None,
) -> ServerConfig:
    """Build a minimal ServerConfig with given runner paths and zombie patterns."""
    return ServerConfig(
        hostname="test-host",
        agent_id="test-agent",
        brain_url="http://brain:8000",
        compute_worker_url="http://worker:8001",
        github_runners=runners or [],
        zombie_patterns=zombies or [],
    )


def _mock_process(pid: int, cmdline: list[str], status: str = "running") -> MagicMock:
    """Create a mock psutil.Process with the given attributes."""
    proc = MagicMock(spec=psutil.Process)
    proc.info = {"pid": pid, "cmdline": cmdline, "status": status}
    return proc


# ---------------------------------------------------------------------------
# GitHub Runner Tests
# ---------------------------------------------------------------------------


class TestRunnerAlive:
    """A runner process found in the process list reports alive=True."""

    @pytest.mark.asyncio
    async def test_runner_alive(self, mocker: "pytest_mock.MockerFixture") -> None:
        runner_path = "/home/runner/actions-runner/run.sh"
        config = _config(runners=[runner_path])

        processes = [
            _mock_process(1001, ["/bin/bash", runner_path, "--once"]),
        ]
        mocker.patch("psutil.process_iter", return_value=iter(processes))

        results = await collect_process_info(config)

        assert len(results) == 1
        proc_info = results[0]
        assert proc_info.name == f"runner:{runner_path}"
        assert proc_info.pattern == runner_path
        assert proc_info.alive is True
        assert proc_info.pid is None  # aggregate check, no specific PID

    @pytest.mark.asyncio
    async def test_runner_alive_path_is_substring(
        self, mocker: "pytest_mock.MockerFixture"
    ) -> None:
        """Runner is detected when the path is a substring of a cmdline argument."""
        runner_path = "/home/runner/actions-runner"
        config = _config(runners=[runner_path])

        processes = [
            _mock_process(2002, [f"{runner_path}/run.sh", "--jitconfig", "abc"]),
        ]
        mocker.patch("psutil.process_iter", return_value=iter(processes))

        results = await collect_process_info(config)

        assert results[0].alive is True


class TestRunnerDead:
    """No process matching the runner path reports alive=False."""

    @pytest.mark.asyncio
    async def test_runner_dead_empty_process_list(
        self, mocker: "pytest_mock.MockerFixture"
    ) -> None:
        runner_path = "/home/runner/actions-runner/run.sh"
        config = _config(runners=[runner_path])

        mocker.patch("psutil.process_iter", return_value=iter([]))

        results = await collect_process_info(config)

        assert len(results) == 1
        assert results[0].alive is False

    @pytest.mark.asyncio
    async def test_runner_dead_no_matching_cmdline(
        self, mocker: "pytest_mock.MockerFixture"
    ) -> None:
        runner_path = "/home/runner/actions-runner/run.sh"
        config = _config(runners=[runner_path])

        processes = [
            _mock_process(100, ["/usr/bin/python3", "app.py"]),
            _mock_process(101, ["/bin/bash", "/opt/scripts/backup.sh"]),
        ]
        mocker.patch("psutil.process_iter", return_value=iter(processes))

        results = await collect_process_info(config)

        assert results[0].alive is False


# ---------------------------------------------------------------------------
# Zombie Process Tests
# ---------------------------------------------------------------------------


class TestDetectZombieProcess:
    """Processes matching a zombie pattern are reported with alive=True."""

    @pytest.mark.asyncio
    async def test_detect_zombie_process(
        self, mocker: "pytest_mock.MockerFixture"
    ) -> None:
        pattern = "stale-worker"
        config = _config(zombies=[pattern])

        processes = [
            _mock_process(3001, ["/usr/bin/node", "stale-worker.js", "--daemon"]),
        ]
        mocker.patch("psutil.process_iter", return_value=iter(processes))

        results = await collect_process_info(config)

        assert len(results) == 1
        proc_info = results[0]
        assert proc_info.name == f"zombie:{pattern}"
        assert proc_info.pattern == pattern
        assert proc_info.pid == 3001
        assert proc_info.alive is True

    @pytest.mark.asyncio
    async def test_detect_multiple_zombies_same_pattern(
        self, mocker: "pytest_mock.MockerFixture"
    ) -> None:
        """Multiple processes matching the same pattern each get an entry."""
        pattern = "old-task"
        config = _config(zombies=[pattern])

        processes = [
            _mock_process(4001, ["/bin/sh", "-c", "old-task --run"]),
            _mock_process(4002, ["/bin/sh", "-c", "old-task --run"]),
        ]
        mocker.patch("psutil.process_iter", return_value=iter(processes))

        results = await collect_process_info(config)

        assert len(results) == 2
        pids = {r.pid for r in results}
        assert pids == {4001, 4002}


class TestNoZombies:
    """When no processes match the zombie pattern, no entries are produced."""

    @pytest.mark.asyncio
    async def test_no_zombies(self, mocker: "pytest_mock.MockerFixture") -> None:
        pattern = "stale-worker"
        config = _config(zombies=[pattern])

        processes = [
            _mock_process(5001, ["/usr/bin/python3", "healthy_app.py"]),
            _mock_process(5002, ["/bin/bash", "deploy.sh"]),
        ]
        mocker.patch("psutil.process_iter", return_value=iter(processes))

        results = await collect_process_info(config)

        assert results == []

    @pytest.mark.asyncio
    async def test_no_zombies_empty_process_list(
        self, mocker: "pytest_mock.MockerFixture"
    ) -> None:
        config = _config(zombies=["stale-worker"])

        mocker.patch("psutil.process_iter", return_value=iter([]))

        results = await collect_process_info(config)

        assert results == []


# ---------------------------------------------------------------------------
# Error Handling Tests
# ---------------------------------------------------------------------------


class TestProcessIterErrors:
    """NoSuchProcess and AccessDenied during iteration are silently skipped."""

    @pytest.mark.asyncio
    async def test_no_such_process_skipped(
        self, mocker: "pytest_mock.MockerFixture"
    ) -> None:
        runner_path = "/home/runner/actions-runner/run.sh"
        config = _config(runners=[runner_path])

        vanishing_proc = MagicMock(spec=psutil.Process)
        # Accessing .info raises NoSuchProcess
        type(vanishing_proc).info = property(
            lambda self: (_ for _ in ()).throw(
                psutil.NoSuchProcess(pid=9999)
            )
        )

        valid_proc = _mock_process(6001, [runner_path, "--once"])

        mocker.patch(
            "psutil.process_iter", return_value=iter([vanishing_proc, valid_proc])
        )

        results = await collect_process_info(config)

        # The valid process is still found despite the earlier error.
        assert results[0].alive is True

    @pytest.mark.asyncio
    async def test_access_denied_skipped(
        self, mocker: "pytest_mock.MockerFixture"
    ) -> None:
        runner_path = "/home/runner/actions-runner/run.sh"
        config = _config(runners=[runner_path])

        denied_proc = MagicMock(spec=psutil.Process)
        type(denied_proc).info = property(
            lambda self: (_ for _ in ()).throw(
                psutil.AccessDenied(pid=7777)
            )
        )

        mocker.patch("psutil.process_iter", return_value=iter([denied_proc]))

        results = await collect_process_info(config)

        # No processes found; runner is dead.
        assert results[0].alive is False


# ---------------------------------------------------------------------------
# Empty Config Tests
# ---------------------------------------------------------------------------


class TestEmptyConfig:
    """No runners or zombie patterns configured returns an empty list."""

    @pytest.mark.asyncio
    async def test_empty_config(self, mocker: "pytest_mock.MockerFixture") -> None:
        config = ServerConfig(
            hostname="test-host",
            agent_id="test-agent",
            brain_url="http://brain:8000",
            compute_worker_url="http://worker:8001",
        )

        mock_iter = mocker.patch("psutil.process_iter")

        results = await collect_process_info(config)

        assert results == []
        mock_iter.assert_not_called()
