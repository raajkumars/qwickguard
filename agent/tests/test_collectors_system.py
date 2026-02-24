"""Tests for the system metric collector."""

from __future__ import annotations

import pytest

from qwickguard_agent.collectors.system import collect_system_metrics


@pytest.mark.asyncio
async def test_collect_system_metrics_returns_valid():
    """All numeric fields must be within expected physical bounds."""
    metrics = await collect_system_metrics()

    assert 0 <= metrics.cpu_percent <= 100
    assert 0 <= metrics.ram_percent <= 100
    assert 0 <= metrics.disk_percent <= 100
    assert len(metrics.load_avg) == 3
    assert all(v >= 0 for v in metrics.load_avg)
    assert metrics.uptime_seconds > 0
    assert metrics.ram_available_gb > 0
    assert metrics.disk_available_gb > 0
    assert isinstance(metrics.open_files, int)


@pytest.mark.asyncio
async def test_system_metrics_serializable():
    """SystemMetrics must serialize cleanly to a dict via model_dump."""
    metrics = await collect_system_metrics()
    data = metrics.model_dump()

    assert "cpu_percent" in data
    assert "load_avg" in data
    assert len(data["load_avg"]) == 3


@pytest.mark.asyncio
async def test_temperature_is_none_or_float():
    """temperature must be None or a non-negative float (platform-dependent)."""
    metrics = await collect_system_metrics()

    assert metrics.temperature is None or (
        isinstance(metrics.temperature, float) and metrics.temperature >= 0
    )


@pytest.mark.asyncio
async def test_open_files_non_negative():
    """open_files must be a non-negative integer even when access is denied."""
    metrics = await collect_system_metrics()

    assert metrics.open_files >= 0


@pytest.mark.asyncio
async def test_load_avg_tuple_values():
    """load_avg should contain three non-negative floats representing 1/5/15 min averages."""
    metrics = await collect_system_metrics()

    load_1, load_5, load_15 = metrics.load_avg
    for value in (load_1, load_5, load_15):
        assert isinstance(value, float)
        assert value >= 0
