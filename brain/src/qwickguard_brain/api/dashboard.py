"""Dashboard UI endpoints for QwickGuard Brain Service.

Serves the main dashboard HTML page and HTMX partial endpoints
used for auto-refresh of the overview content.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..storage import get_agents, get_latest_report, get_recent_actions

logger = logging.getLogger("qwickguard.brain.api.dashboard")

router = APIRouter()
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates")
)


def _parse_report(report: dict[str, Any] | None) -> dict[str, Any]:
    """Parse JSON string fields from a raw report dict into Python objects.

    The storage layer stores metrics_json, analysis_json, and actions_json as
    raw JSON strings. This function deserialises them into their native Python
    equivalents (dicts / lists) and attaches them under the unprefixed keys
    ``metrics``, ``analysis``, and ``actions`` for template convenience.
    """
    if not report:
        return {}
    result = dict(report)
    field_map = {
        "metrics_json": "metrics",
        "analysis_json": "analysis",
        "actions_json": "actions",
    }
    for src_key, dest_key in field_map.items():
        if src_key in result and isinstance(result[src_key], str):
            try:
                result[dest_key] = json.loads(result[src_key])
            except json.JSONDecodeError:
                logger.warning("Failed to parse %s for report id=%s", src_key, result.get("id"))
                result[dest_key] = {}
    return result


def _relative_time(timestamp_str: str | None) -> str:
    """Convert an ISO-8601 timestamp string to a human-readable relative time.

    Examples: "5s ago", "12m ago", "3h ago", "2d ago", "never".
    """
    if not timestamp_str:
        return "never"
    try:
        dt = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        diff = datetime.now(timezone.utc) - dt
        seconds = int(diff.total_seconds())
        if seconds < 0:
            return "just now"
        if seconds < 60:
            return f"{seconds}s ago"
        if seconds < 3600:
            return f"{seconds // 60}m ago"
        if seconds < 86400:
            return f"{seconds // 3600}h ago"
        return f"{seconds // 86400}d ago"
    except (ValueError, TypeError):
        return "unknown"


async def _build_template_context(request: Request) -> dict[str, Any]:
    """Fetch agent data and assemble the Jinja2 template context dict."""
    agents = await get_agents()
    agent: dict[str, Any] | None = agents[0] if agents else None

    report: dict[str, Any] = {}
    actions: list[dict[str, Any]] = []

    if agent:
        raw_report = await get_latest_report(agent["agent_id"])
        report = _parse_report(raw_report)
        actions = await get_recent_actions(agent["agent_id"], limit=10)

    return {
        "request": request,
        "agent": agent,
        "report": report,
        "actions": actions,
        "relative_time": _relative_time,
    }


@router.get("/", response_class=HTMLResponse)
@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    """Render the full dashboard page with the base layout."""
    context = await _build_template_context(request)
    return templates.TemplateResponse("dashboard.html", context)


@router.get("/dashboard/partials/overview", response_class=HTMLResponse)
async def overview_partial(request: Request) -> HTMLResponse:
    """Return the inner dashboard content fragment for HTMX auto-refresh.

    This endpoint is called every 300 seconds by the dashboard page via
    ``hx-get`` on the main content container. It returns only the partial
    template (no base layout wrapper) so HTMX can swap it in place.
    """
    context = await _build_template_context(request)
    return templates.TemplateResponse("partials/system_status.html", context)
