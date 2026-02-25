"""Agent ingestion and query endpoints for QwickGuard Brain Service."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from ..storage import (
    get_agents,
    get_agent_history,
    get_recent_actions,
    store_report,
)

logger = logging.getLogger("qwickguard.brain.api.agents")

router = APIRouter(prefix="/api/v1")


@router.post("/agents/{agent_id}/report")
async def post_report(agent_id: str, request: Request) -> dict:
    """Accept a report payload from an agent and persist it."""
    body = await request.json()
    # Ensure agent_id from path is authoritative
    body["agent_id"] = agent_id
    await store_report(body)
    logger.info("Accepted report from agent %s", agent_id)
    return {"status": "accepted", "agent_id": agent_id}


@router.get("/agents")
async def list_agents() -> list[dict]:
    """Return all registered agents and their last-seen metadata."""
    return await get_agents()


@router.get("/agents/{agent_id}/history")
async def agent_history(agent_id: str, hours: int = 168) -> list[dict]:
    """Return report history for *agent_id* within the last *hours* hours."""
    return await get_agent_history(agent_id, hours)


@router.get("/agents/{agent_id}/actions")
async def agent_actions(agent_id: str, limit: int = 100) -> list[dict]:
    """Return the most recent autonomous actions taken by *agent_id*."""
    return await get_recent_actions(agent_id, limit)
