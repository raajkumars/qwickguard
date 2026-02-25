"""Agent ingestion and query endpoints for QwickGuard Brain Service."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request

from ..escalation import escalation_engine
from ..storage import (
    get_agent_history,
    get_agents,
    get_recent_actions,
    store_report,
)

logger = logging.getLogger("qwickguard.brain.api.agents")

router = APIRouter(prefix="/api/v1")

# Compatibility router for the agent's /api/agent/report endpoint
compat_router = APIRouter(prefix="/api")


def _format_status_body(report: dict[str, Any]) -> str:
    """Format a report's analysis issues into a readable notification body."""
    analysis = report.get("analysis", {})
    issues = analysis.get("issues", [])
    hostname = report.get("hostname", report.get("agent_id", "unknown"))
    timestamp = report.get("timestamp", "")

    lines = [f"Host: {hostname}"]
    if timestamp:
        lines.append(f"Timestamp: {timestamp}")

    if issues:
        lines.append("")
        lines.append("Issues detected:")
        for issue in issues:
            if isinstance(issue, dict):
                desc = issue.get("description", str(issue))
                sev = issue.get("severity", "")
                lines.append(f"  - [{sev}] {desc}" if sev else f"  - {desc}")
            else:
                lines.append(f"  - {issue}")
    else:
        lines.append("No specific issues reported.")

    return "\n".join(lines)


async def _handle_report(agent_id: str, body: dict) -> dict:
    """Shared report handling logic for both v1 and compat endpoints."""
    body["agent_id"] = agent_id
    await store_report(body)
    logger.info("Accepted report from agent %s", agent_id)

    # Dispatch notification for non-healthy reports
    analysis = body.get("analysis", {})
    status = analysis.get("status", "healthy")
    if status in ("critical", "warning"):
        from ..notifications import dispatch_notification

        await dispatch_notification(
            agent_id=agent_id,
            severity=status,
            title=f"Agent {body.get('hostname', agent_id)} status: {status}",
            body=_format_status_body(body),
            hostname=body.get("hostname", ""),
        )

    # Check if escalation to Claude is requested
    if analysis.get("escalate_to_claude", False):
        history = await get_agent_history(agent_id, hours=24)
        result = await escalation_engine.escalate(agent_id, body, history)
        if result:
            return {"status": "accepted", "agent_id": agent_id, "escalation": result}

    return {"status": "accepted", "agent_id": agent_id}


@router.post("/agents/{agent_id}/report")
async def post_report(agent_id: str, request: Request) -> dict:
    """Accept a report payload from an agent (v1 endpoint with agent_id in path)."""
    body = await request.json()
    return await _handle_report(agent_id, body)


@compat_router.post("/agent/report")
async def post_report_compat(request: Request) -> dict:
    """Compatibility endpoint: agent_id comes from the request body."""
    body = await request.json()
    agent_id = body.get("agent_id", "unknown")
    return await _handle_report(agent_id, body)


@router.get("/agents")
async def list_agents() -> list[dict]:
    """Return all registered agents and their last-seen metadata."""
    return await get_agents()


@router.get("/agents/{agent_id}/history")
async def agent_history(agent_id: str, hours: int = 168) -> list[dict]:
    """Return report history for agent_id within the last hours hours."""
    return await get_agent_history(agent_id, hours)


@router.get("/agents/{agent_id}/actions")
async def agent_actions(agent_id: str, limit: int = 100) -> list[dict]:
    """Return the most recent autonomous actions taken by agent_id."""
    return await get_recent_actions(agent_id, limit)
