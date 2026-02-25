"""Dashboard UI endpoints for QwickGuard Brain Service.

Serves the main dashboard HTML page and HTMX partial endpoints
used for auto-refresh of the overview content.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..storage import (
    get_agents,
    get_agent_history,
    get_latest_report,
    get_recent_actions,
    get_recent_escalations,
    get_recent_notifications,
)

logger = logging.getLogger("qwickguard.brain.api.dashboard")

router = APIRouter()
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates")
)

_RUNBOOKS_DIR = Path("/app/runbooks")


def _parse_report(report: dict[str, Any] | None) -> dict[str, Any]:
    """Parse JSON string fields from a raw report dict into Python objects."""
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
    """Convert an ISO-8601 timestamp string to a human-readable relative time."""
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


def _parse_range(range_str: str) -> int:
    """Convert a range string like '1h', '6h', '24h', '7d' to hours."""
    mapping = {"1h": 1, "6h": 6, "24h": 24, "7d": 168}
    return mapping.get(range_str, 24)


def _prepare_chart_data(reports: list[dict[str, Any]]) -> dict[str, Any]:
    """Transform raw report rows into chart-ready data series."""
    reports = list(reversed(reports))  # oldest first for charts

    timestamps: list[str] = []
    cpu: list[float] = []
    memory: list[float] = []
    disk: list[float] = []
    load_1m: list[float] = []
    load_5m: list[float] = []
    load_15m: list[float] = []

    # Track container restarts and service response times
    container_restarts: dict[str, int] = {}
    service_data: dict[str, list[float | None]] = {}

    for row in reports:
        # Parse metrics JSON
        metrics_raw = row.get("metrics_json", "{}")
        if isinstance(metrics_raw, str):
            try:
                metrics = json.loads(metrics_raw)
            except json.JSONDecodeError:
                metrics = {}
        else:
            metrics = metrics_raw

        sys = metrics.get("system", {})
        ts = row.get("timestamp", "")

        # Shorten timestamp for display
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            timestamps.append(dt.strftime("%H:%M"))
        except (ValueError, TypeError):
            timestamps.append(ts[:16] if ts else "")

        cpu.append(sys.get("cpu_percent", 0))
        memory.append(sys.get("ram_percent", 0))
        disk.append(sys.get("disk_percent", 0))

        load_avg = sys.get("load_avg", [0, 0, 0])
        if isinstance(load_avg, list) and len(load_avg) >= 3:
            load_1m.append(load_avg[0])
            load_5m.append(load_avg[1])
            load_15m.append(load_avg[2])
        else:
            load_1m.append(0)
            load_5m.append(0)
            load_15m.append(0)

        # Container restarts
        for c in metrics.get("containers", []):
            name = c.get("name", "unknown")
            restarts = c.get("restart_count", 0)
            container_restarts[name] = container_restarts.get(name, 0) + restarts

        # Service response times
        for s in metrics.get("services", []):
            name = s.get("name", "unknown")
            if name not in service_data:
                service_data[name] = [None] * (len(timestamps) - 1)
            service_data[name].append(s.get("response_time_ms"))

        # Pad missing service entries
        for name in service_data:
            if len(service_data[name]) < len(timestamps):
                service_data[name].append(None)

    return {
        "timestamps": timestamps,
        "cpu": cpu,
        "memory": memory,
        "disk": disk,
        "load_1m": load_1m,
        "load_5m": load_5m,
        "load_15m": load_15m,
        "container_restarts": {
            "names": list(container_restarts.keys()),
            "counts": list(container_restarts.values()),
        },
        "service_response": [
            {"name": name, "times": times}
            for name, times in service_data.items()
        ],
    }


def _scan_runbooks(runbooks_dir: Path) -> list[dict[str, str]]:
    """Scan a directory for markdown runbook files and extract metadata."""
    runbooks = []
    if not runbooks_dir.exists():
        return runbooks

    for f in sorted(runbooks_dir.glob("*.md")):
        content = f.read_text(errors="replace")
        # Extract title from first H1
        title_match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
        title = title_match.group(1) if title_match else f.stem.replace("-", " ").title()

        # Extract severity from content
        severity = None
        if "severity: critical" in content.lower() or "critical" in f.stem.lower():
            severity = "critical"
        elif "severity: warning" in content.lower() or "warning" in f.stem.lower():
            severity = "warning"

        # Extract first paragraph as description
        description = ""
        lines = content.split("\n")
        for line in lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and not stripped.startswith("---"):
                description = stripped[:150]
                break

        runbooks.append({
            "filename": f.name,
            "title": title,
            "severity": severity,
            "description": description,
        })

    return runbooks


def _render_markdown(text: str) -> str:
    """Convert markdown to HTML. Uses the markdown library if available, else basic conversion."""
    try:
        import markdown
        return markdown.markdown(text, extensions=["fenced_code", "tables", "toc"])
    except ImportError:
        # Basic fallback: wrap in <pre> tags
        import html
        return f"<pre>{html.escape(text)}</pre>"


# ---------------------------------------------------------------------------
# Common context builder
# ---------------------------------------------------------------------------

async def _get_agent() -> dict[str, Any] | None:
    """Get the first (primary) agent."""
    agents = await get_agents()
    return agents[0] if agents else None


async def _build_template_context(request: Request) -> dict[str, Any]:
    """Fetch agent data and assemble the Jinja2 template context dict."""
    agent = await _get_agent()

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


# ---------------------------------------------------------------------------
# Dashboard overview
# ---------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    """Render the full dashboard page with the base layout."""
    context = await _build_template_context(request)
    return templates.TemplateResponse("dashboard.html", context)


@router.get("/dashboard/partials/overview", response_class=HTMLResponse)
async def overview_partial(request: Request) -> HTMLResponse:
    """Return the inner dashboard content fragment for HTMX auto-refresh."""
    context = await _build_template_context(request)
    return templates.TemplateResponse("partials/system_status.html", context)


# ---------------------------------------------------------------------------
# Metrics charts page
# ---------------------------------------------------------------------------

@router.get("/metrics", response_class=HTMLResponse)
async def metrics_page(request: Request, range: str = "24h") -> HTMLResponse:
    """Render the metrics charts page with historical data."""
    agent = await _get_agent()
    if not agent:
        return templates.TemplateResponse("metrics.html", {
            "request": request,
            "agent": None,
            "chart_data": {"timestamps": []},
            "range": range,
        })

    hours = _parse_range(range)
    history = await get_agent_history(agent["agent_id"], hours=hours)
    chart_data = _prepare_chart_data(history)

    return templates.TemplateResponse("metrics.html", {
        "request": request,
        "agent": agent,
        "chart_data": chart_data,
        "range": range,
    })


@router.get("/metrics/partials/charts", response_class=HTMLResponse)
async def metrics_charts_partial(request: Request, range: str = "24h") -> HTMLResponse:
    """Return the inner metrics charts content fragment for HTMX auto-refresh."""
    agent = await _get_agent()
    if not agent:
        return templates.TemplateResponse("partials/metrics_charts.html", {
            "request": request,
            "agent": None,
            "chart_data": {"timestamps": []},
            "range": range,
        })

    hours = _parse_range(range)
    history = await get_agent_history(agent["agent_id"], hours=hours)
    chart_data = _prepare_chart_data(history)

    return templates.TemplateResponse("partials/metrics_charts.html", {
        "request": request,
        "agent": agent,
        "chart_data": chart_data,
        "range": range,
    })


# ---------------------------------------------------------------------------
# Audit log viewer
# ---------------------------------------------------------------------------

@router.get("/audit", response_class=HTMLResponse)
async def audit_page(
    request: Request,
    action: str | None = None,
    result: str | None = None,
    page: int = 1,
) -> HTMLResponse:
    """Render the audit log page with filterable action history."""
    agent = await _get_agent()
    all_actions: list[dict[str, Any]] = []

    if agent:
        all_actions = await get_recent_actions(agent["agent_id"], limit=500)

    # Collect unique action types for filter dropdown
    available_actions = sorted({a.get("action", "") for a in all_actions if a.get("action")})

    # Apply filters
    filtered = all_actions
    if action:
        filtered = [a for a in filtered if a.get("action") == action]
    if result:
        filtered = [a for a in filtered if a.get("result", a.get("status", "")).lower() == result.lower()]

    # Paginate
    per_page = 50
    start = (page - 1) * per_page
    end = start + per_page
    page_actions = filtered[start:end]
    has_more = len(filtered) > end

    return templates.TemplateResponse("audit.html", {
        "request": request,
        "agent": agent,
        "actions": page_actions,
        "available_actions": available_actions,
        "filters": {"action": action or "", "result": result or ""},
        "page": page,
        "has_more": has_more,
        "relative_time": _relative_time,
    })


# ---------------------------------------------------------------------------
# Runbook browser
# ---------------------------------------------------------------------------

@router.get("/runbooks", response_class=HTMLResponse)
async def runbooks_list(request: Request) -> HTMLResponse:
    """List all available runbooks."""
    agent = await _get_agent()
    runbooks = _scan_runbooks(_RUNBOOKS_DIR)
    return templates.TemplateResponse("runbooks.html", {
        "request": request,
        "agent": agent,
        "runbooks": runbooks,
    })


@router.get("/runbooks/{filename}", response_class=HTMLResponse)
async def runbook_detail(request: Request, filename: str) -> HTMLResponse:
    """Render a single runbook as HTML."""
    agent = await _get_agent()

    # Sanitize filename to prevent directory traversal
    safe_name = Path(filename).name
    filepath = _RUNBOOKS_DIR / safe_name

    if not filepath.exists() or not filepath.suffix == ".md":
        return HTMLResponse(status_code=404, content="Runbook not found")

    raw = filepath.read_text(errors="replace")
    html_content = _render_markdown(raw)

    # Extract title
    title_match = re.search(r"^#\s+(.+)$", raw, re.MULTILINE)
    title = title_match.group(1) if title_match else safe_name

    return templates.TemplateResponse("runbook_detail.html", {
        "request": request,
        "agent": agent,
        "content": html_content,
        "title": title,
        "filename": safe_name,
    })


# ---------------------------------------------------------------------------
# Notifications and escalations page
# ---------------------------------------------------------------------------

@router.get("/notifications", response_class=HTMLResponse)
async def notifications_page(
    request: Request,
    severity: str | None = None,
) -> HTMLResponse:
    """Render notification history and escalation log."""
    agent = await _get_agent()
    notifications = await get_recent_notifications(limit=100, severity=severity)
    escalations = await get_recent_escalations(limit=50)

    # Parse Claude response JSON in escalations
    for esc in escalations:
        if esc.get("claude_response") and isinstance(esc["claude_response"], str):
            try:
                esc["claude_parsed"] = json.loads(esc["claude_response"])
            except json.JSONDecodeError:
                esc["claude_parsed"] = None
        if esc.get("actions_recommended") and isinstance(esc["actions_recommended"], str):
            try:
                esc["actions_parsed"] = json.loads(esc["actions_recommended"])
            except json.JSONDecodeError:
                esc["actions_parsed"] = None

    return templates.TemplateResponse("notifications.html", {
        "request": request,
        "agent": agent,
        "notifications": notifications,
        "escalations": escalations,
        "severity_filter": severity or "",
        "relative_time": _relative_time,
    })
