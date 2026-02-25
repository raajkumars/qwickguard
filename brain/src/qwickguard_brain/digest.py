"""Daily digest generation for QwickGuard Brain.

Aggregates 24h metrics, actions, and incidents into a summary report.
Uses Claude when available, falls back to template-based digest.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import settings
from .notifications import dispatch_notification
from .storage import get_agents, get_agent_history, get_recent_actions, get_recent_escalations

logger = logging.getLogger("qwickguard.brain.digest")

_DIGEST_PROMPT_PATH = Path(__file__).resolve().parent.parent.parent / "prompts" / "daily_digest.md"


def _format_digest_context(
    agent: dict[str, Any],
    reports: list[dict[str, Any]],
    actions: list[dict[str, Any]],
    escalations: list[dict[str, Any]],
) -> str:
    """Format 24h data into context string for Claude or template."""
    parts = [f"# 24h Report for {agent.get('hostname', 'unknown')}"]
    parts.append(f"Agent ID: {agent.get('agent_id', 'unknown')}")
    parts.append(f"Reports in period: {len(reports)}")

    # Compute metric averages and peaks from reports
    cpu_vals, mem_vals, disk_vals = [], [], []
    status_counts: dict[str, int] = {}
    for r in reports:
        try:
            metrics = json.loads(r["metrics_json"]) if isinstance(r.get("metrics_json"), str) else r.get("metrics", {})
            cpu_vals.append(metrics.get("cpu_percent", 0))
            mem_vals.append(metrics.get("memory_percent", 0))
            disk_vals.append(metrics.get("disk_percent", 0))
        except (json.JSONDecodeError, TypeError):
            pass
        status = r.get("status", "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1

    if cpu_vals:
        parts.append(f"\n## Metrics")
        parts.append(f"CPU: avg={sum(cpu_vals)/len(cpu_vals):.1f}%, peak={max(cpu_vals):.1f}%")
        parts.append(f"RAM: avg={sum(mem_vals)/len(mem_vals):.1f}%, peak={max(mem_vals):.1f}%")
        parts.append(f"Disk: avg={sum(disk_vals)/len(disk_vals):.1f}%, peak={max(disk_vals):.1f}%")

    parts.append(f"\n## Status Distribution")
    for status, count in sorted(status_counts.items()):
        parts.append(f"- {status}: {count} reports")

    parts.append(f"\n## Actions Taken: {len(actions)}")
    action_counts: dict[str, int] = {}
    for a in actions:
        action_name = a.get("action", "unknown")
        action_counts[action_name] = action_counts.get(action_name, 0) + 1
    for action_name, count in sorted(action_counts.items()):
        parts.append(f"- {action_name}: {count}")

    if escalations:
        parts.append(f"\n## Escalations: {len(escalations)}")
        for e in escalations:
            parts.append(f"- {e.get('trigger_reason', 'unknown')}")

    return "\n".join(parts)


def template_digest(
    agent: dict[str, Any],
    reports: list[dict[str, Any]],
    actions: list[dict[str, Any]],
    escalations: list[dict[str, Any]],
) -> str:
    """Generate a template-based digest (no Claude needed)."""
    hostname = agent.get("hostname", "unknown")
    total = len(reports)
    if total == 0:
        return f"No reports received from {hostname} in the last 24 hours."

    # Compute metrics
    cpu_vals, mem_vals, disk_vals = [], [], []
    healthy_count = 0
    for r in reports:
        try:
            metrics = json.loads(r["metrics_json"]) if isinstance(r.get("metrics_json"), str) else r.get("metrics", {})
            cpu_vals.append(metrics.get("cpu_percent", 0))
            mem_vals.append(metrics.get("memory_percent", 0))
            disk_vals.append(metrics.get("disk_percent", 0))
        except (json.JSONDecodeError, TypeError):
            pass
        if r.get("status") == "healthy":
            healthy_count += 1

    health_pct = (healthy_count / total * 100) if total > 0 else 0

    lines = [f"## Daily Digest: {hostname} - {date.today()}"]
    lines.append("")

    # Health assessment
    if health_pct >= 95:
        lines.append(f"Overall: Healthy ({health_pct:.0f}% of {total} reports were healthy)")
    elif health_pct >= 75:
        lines.append(f"Overall: Mostly healthy ({health_pct:.0f}% of {total} reports were healthy)")
    else:
        lines.append(f"Overall: Degraded ({health_pct:.0f}% of {total} reports were healthy)")

    # Metrics
    if cpu_vals:
        lines.append("")
        lines.append("### Key Metrics (24h)")
        lines.append(f"- CPU: avg {sum(cpu_vals)/len(cpu_vals):.1f}%, peak {max(cpu_vals):.1f}%")
        lines.append(f"- RAM: avg {sum(mem_vals)/len(mem_vals):.1f}%, peak {max(mem_vals):.1f}%")
        lines.append(f"- Disk: avg {sum(disk_vals)/len(disk_vals):.1f}%, peak {max(disk_vals):.1f}%")

    # Actions
    if actions:
        lines.append("")
        lines.append(f"### Actions Taken: {len(actions)}")
        action_counts: dict[str, int] = {}
        for a in actions:
            name = a.get("action", "unknown")
            action_counts[name] = action_counts.get(name, 0) + 1
        for name, count in sorted(action_counts.items()):
            lines.append(f"- {name}: {count}")

    # Escalations
    if escalations:
        lines.append("")
        lines.append(f"### Escalations: {len(escalations)}")

    return "\n".join(lines)


async def claude_digest(hostname: str, context: str) -> str | None:
    """Generate digest using Claude API. Returns None on failure."""
    try:
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        system_prompt = _DIGEST_PROMPT_PATH.read_text().replace("{hostname}", hostname)

        response = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=system_prompt,
            messages=[{"role": "user", "content": context}],
        )
        return response.content[0].text
    except Exception:
        logger.exception("Claude digest generation failed, falling back to template")
        return None


async def generate_daily_digest() -> None:
    """Generate and dispatch daily digest for all agents."""
    agents = await get_agents()
    if not agents:
        logger.info("No agents registered, skipping digest")
        return

    for agent in agents:
        agent_id = agent["agent_id"]
        hostname = agent.get("hostname", "unknown")

        # Aggregate 24h data
        reports = await get_agent_history(agent_id, hours=24)
        actions = await get_recent_actions(agent_id, limit=500)
        escalations = await get_recent_escalations(agent_id, limit=50)

        # Filter actions to 24h window
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        actions = [a for a in actions if a.get("timestamp", "") >= cutoff]

        # Generate summary
        summary: str | None = None
        if settings.anthropic_api_key:
            context = _format_digest_context(agent, reports, actions, escalations)
            summary = await claude_digest(hostname, context)

        if summary is None:
            summary = template_digest(agent, reports, actions, escalations)

        # Dispatch
        title = f"Daily Digest: {hostname} - {date.today()}"
        await dispatch_notification(
            agent_id=agent_id,
            severity="info",
            title=title,
            body=summary,
            hostname=hostname,
        )
        logger.info("Daily digest dispatched for %s", hostname)


async def schedule_daily_digest(hour: int = 8, minute: int = 0) -> None:
    """Background task: run digest at configured time daily."""
    while True:
        now = datetime.now()
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        wait_seconds = (target - now).total_seconds()
        logger.info("Next daily digest scheduled in %.0f seconds", wait_seconds)
        await asyncio.sleep(wait_seconds)
        try:
            await generate_daily_digest()
        except Exception:
            logger.exception("Daily digest generation failed")
