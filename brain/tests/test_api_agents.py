"""Tests for agent ingestion API endpoints."""
from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from qwickguard_brain.main import app
from qwickguard_brain.storage import init_db


@pytest_asyncio.fixture
async def client(tmp_path):
    """Create test client with temporary database."""
    db_path = str(tmp_path / "test.db")
    await init_db(db_path)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


def _sample_report(agent_id="test-agent-1", hostname="test-host"):
    return {
        "agent_id": agent_id,
        "hostname": hostname,
        "timestamp": "2026-02-24T12:00:00Z",
        "metrics": {
            "cpu_percent": 45.0,
            "memory_percent": 60.0,
            "disk_percent": 70.0,
        },
        "analysis": {
            "status": "healthy",
            "issues": [],
            "actions": [],
            "escalate_to_claude": False,
        },
        "actions_taken": [],
    }


@pytest.mark.asyncio
async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["service"] == "qwickguard-brain"


@pytest.mark.asyncio
async def test_post_report(client):
    report = _sample_report()
    resp = await client.post("/api/v1/agents/test-agent-1/report", json=report)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "accepted"
    assert data["agent_id"] == "test-agent-1"


@pytest.mark.asyncio
async def test_get_agents_after_report(client):
    report = _sample_report()
    await client.post("/api/v1/agents/test-agent-1/report", json=report)
    resp = await client.get("/api/v1/agents")
    assert resp.status_code == 200
    agents = resp.json()
    assert len(agents) >= 1
    assert any(a["agent_id"] == "test-agent-1" for a in agents)


@pytest.mark.asyncio
async def test_get_history(client):
    for i in range(3):
        report = _sample_report()
        report["timestamp"] = f"2026-02-24T{12 + i:02d}:00:00Z"
        await client.post("/api/v1/agents/test-agent-1/report", json=report)

    resp = await client.get("/api/v1/agents/test-agent-1/history")
    assert resp.status_code == 200
    history = resp.json()
    assert len(history) == 3


@pytest.mark.asyncio
async def test_get_actions(client):
    report = _sample_report()
    report["actions_taken"] = [
        {"action": "restart_container", "target": "test-container", "result": "success"}
    ]
    await client.post("/api/v1/agents/test-agent-1/report", json=report)

    resp = await client.get("/api/v1/agents/test-agent-1/actions")
    assert resp.status_code == 200
    actions = resp.json()
    assert len(actions) >= 1


@pytest.mark.asyncio
async def test_report_stores_status_from_analysis(client):
    report = _sample_report()
    report["analysis"]["status"] = "warning"
    await client.post("/api/v1/agents/test-agent-1/report", json=report)

    resp = await client.get("/api/v1/agents")
    agents = resp.json()
    agent = next(a for a in agents if a["agent_id"] == "test-agent-1")
    assert agent["last_status"] == "warning"
