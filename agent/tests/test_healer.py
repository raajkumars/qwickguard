"""Tests for the autonomous healer: catalog validation, cooldowns, audit logging."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from qwickguard_agent.config import ContainerConfig, ServerConfig, ThresholdConfig
from qwickguard_agent.healer import ACTION_CATALOG, CooldownTracker, Healer
from qwickguard_agent.models import ActionResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(container_names: list[str] | None = None) -> ServerConfig:
    """Build a minimal ServerConfig with optional container entries."""
    containers = [ContainerConfig(name=n) for n in (container_names or [])]
    return ServerConfig(
        hostname="test-host",
        agent_id="test-agent",
        brain_url="http://localhost:8080",
        compute_worker_url="http://localhost:8081",
        containers=containers,
    )


def _make_healer(tmp_path: Path, container_names: list[str] | None = None) -> Healer:
    config = _make_config(container_names)
    audit_log = tmp_path / "audit.jsonl"
    return Healer(config=config, audit_log_path=audit_log)


def _action_dict(action: str, target: str = "", reason: str = "test") -> dict:
    return {"action": action, "target": target, "reason": reason}


# ---------------------------------------------------------------------------
# Subprocess mock helpers
# ---------------------------------------------------------------------------


def _mock_subprocess_success():
    """Return an async context that simulates a successful subprocess."""
    proc = MagicMock()
    proc.returncode = 0
    proc.communicate = AsyncMock(return_value=(b"ok", b""))
    return proc


def _mock_subprocess_failure(stderr: bytes = b"something went wrong", returncode: int = 1):
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(b"", stderr))
    return proc


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_action_in_catalog_executes(tmp_path: Path) -> None:
    """A known action with a valid target executes and returns 'success'."""
    healer = _make_healer(tmp_path, container_names=["my-app"])

    proc = _mock_subprocess_success()
    with patch("asyncio.create_subprocess_shell", new=AsyncMock(return_value=proc)):
        results = await healer.execute_actions(
            [_action_dict("restart_container", "my-app")],
            decided_by="rules",
        )

    assert len(results) == 1
    assert results[0].result == "success"
    assert results[0].action == "restart_container"
    assert results[0].target == "my-app"


@pytest.mark.asyncio
async def test_action_not_in_catalog_rejected(tmp_path: Path) -> None:
    """An action not in the catalog is rejected immediately."""
    healer = _make_healer(tmp_path)

    results = await healer.execute_actions(
        [_action_dict("rm_everything", "/")],
        decided_by="rules",
    )

    assert results[0].result == "rejected"
    assert "not in the allowed catalog" in results[0].error


@pytest.mark.asyncio
async def test_blocked_pattern_rejected(tmp_path: Path) -> None:
    """Commands containing blocked patterns are rejected even if action is catalog-valid.

    We test this by monkey-patching _build_command to inject a blocked pattern.
    """
    healer = _make_healer(tmp_path, container_names=["my-app"])

    # Override _build_command to return a command that contains a blocked pattern
    original_build = healer._build_command

    def _patched_build(action: str, target: str) -> str:
        if action == "prune_images":
            return "docker system prune --volumes"
        return original_build(action, target)

    healer._build_command = _patched_build

    results = await healer.execute_actions(
        [_action_dict("prune_images")],
        decided_by="rules",
    )

    assert results[0].result == "rejected"
    assert "blocked pattern" in results[0].error


@pytest.mark.asyncio
async def test_cooldown_prevents_execution(tmp_path: Path) -> None:
    """Exhausting the cooldown budget causes subsequent calls to return 'cooldown'."""
    healer = _make_healer(tmp_path, container_names=["my-app"])

    proc = _mock_subprocess_success()

    with patch("asyncio.create_subprocess_shell", new=AsyncMock(return_value=proc)):
        # restart_container allows 3 per 30 min; exhaust the budget
        for _ in range(3):
            results = await healer.execute_actions(
                [_action_dict("restart_container", "my-app")],
                decided_by="rules",
            )
            assert results[0].result == "success"

        # 4th call should be rate-limited
        results = await healer.execute_actions(
            [_action_dict("restart_container", "my-app")],
            decided_by="rules",
        )

    assert results[0].result == "cooldown"


@pytest.mark.asyncio
async def test_cooldown_allows_after_window(tmp_path: Path) -> None:
    """After the cooldown window passes, the action is allowed again."""
    tracker = CooldownTracker()

    action = "restart_container"
    target = "my-app"
    _, window_seconds = (3, 30 * 60)

    # Record 3 timestamps in the past (just outside the 30-min window)
    past_time = time.time() - (30 * 60 + 10)  # 10 seconds past the window
    tracker._history[f"{action}:{target}"] = [past_time, past_time, past_time]

    # Should now be allowed because all entries are outside the window
    assert tracker.check(action, target) is True


@pytest.mark.asyncio
async def test_unknown_container_rejected(tmp_path: Path) -> None:
    """restart_container with an unknown container name is rejected."""
    healer = _make_healer(tmp_path, container_names=["known-app"])

    results = await healer.execute_actions(
        [_action_dict("restart_container", "unknown-container")],
        decided_by="rules",
    )

    assert results[0].result == "rejected"
    assert "not in the monitored containers list" in results[0].error


@pytest.mark.asyncio
async def test_audit_log_written(tmp_path: Path) -> None:
    """Every action result is appended as a JSON line to the audit log."""
    healer = _make_healer(tmp_path, container_names=["my-app"])
    audit_log = tmp_path / "audit.jsonl"

    proc = _mock_subprocess_success()
    with patch("asyncio.create_subprocess_shell", new=AsyncMock(return_value=proc)):
        await healer.execute_actions(
            [
                _action_dict("restart_container", "my-app", reason="unhealthy"),
                _action_dict("not_real_action", "x", reason="test rejection"),
            ],
            decided_by="claude",
        )

    assert audit_log.exists()
    lines = audit_log.read_text().strip().splitlines()
    assert len(lines) == 2

    first = json.loads(lines[0])
    assert first["action"] == "restart_container"
    assert first["result"] == "success"
    assert first["decided_by"] == "claude"

    second = json.loads(lines[1])
    assert second["action"] == "not_real_action"
    assert second["result"] == "rejected"


@pytest.mark.asyncio
async def test_command_timeout(tmp_path: Path) -> None:
    """A subprocess that exceeds 60 seconds results in 'failed' with timeout error."""
    healer = _make_healer(tmp_path, container_names=["my-app"])

    async def _slow_create(*args, **kwargs):
        await asyncio.sleep(100)  # simulate never finishing

    with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError):
        results = await healer.execute_actions(
            [_action_dict("restart_container", "my-app")],
            decided_by="rules",
        )

    assert results[0].result == "failed"
    assert "timed out" in results[0].error


@pytest.mark.asyncio
async def test_prune_images_no_target(tmp_path: Path) -> None:
    """prune_images does not require a target and executes successfully."""
    healer = _make_healer(tmp_path)

    proc = _mock_subprocess_success()
    with patch("asyncio.create_subprocess_shell", new=AsyncMock(return_value=proc)):
        results = await healer.execute_actions(
            [_action_dict("prune_images", target="")],
            decided_by="rules",
        )

    assert results[0].result == "success"


@pytest.mark.asyncio
async def test_multiple_actions_sequential(tmp_path: Path) -> None:
    """Two independent actions both execute and both appear in results."""
    healer = _make_healer(tmp_path, container_names=["app-a", "app-b"])

    proc = _mock_subprocess_success()
    with patch("asyncio.create_subprocess_shell", new=AsyncMock(return_value=proc)):
        results = await healer.execute_actions(
            [
                _action_dict("restart_container", "app-a", reason="unhealthy"),
                _action_dict("restart_container", "app-b", reason="unhealthy"),
            ],
            decided_by="rules",
        )

    assert len(results) == 2
    assert results[0].result == "success"
    assert results[0].target == "app-a"
    assert results[1].result == "success"
    assert results[1].target == "app-b"

    # Both should appear in the audit log
    audit_log = tmp_path / "audit.jsonl"
    lines = audit_log.read_text().strip().splitlines()
    assert len(lines) == 2
