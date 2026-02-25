"""Background heartbeat checker for QwickGuard Brain Service.

Monitors registered agents and dispatches critical notifications when an
agent has not reported within the configured timeout.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from .storage import get_agents

logger = logging.getLogger("qwickguard.brain.registry")


async def _alert_missing_heartbeat(
    agent_id: str, hostname: str, minutes_since: int
) -> None:
    """Dispatch a critical notification for a missing heartbeat.

    Uses the notifications module to route the alert to all configured
    channels (internal storage, GitHub Issues, Slack, Discord).
    """
    from .notifications import dispatch_notification

    await dispatch_notification(
        agent_id=agent_id,
        severity="critical",
        title=f"Agent {hostname} heartbeat missing",
        body=(
            f"No report received from agent {agent_id} ({hostname}) "
            f"in {minutes_since} minutes."
        ),
        hostname=hostname,
    )


async def heartbeat_checker(
    timeout_minutes: int = 15, interval_seconds: int = 60
) -> None:
    """Check every interval for agents missing heartbeat beyond timeout.

    An agent is considered missing when its last report is older than
    timeout_minutes. The checker runs indefinitely and is expected to be
    started as an asyncio.Task.
    """
    while True:
        try:
            agents = await get_agents()
            now = datetime.now(timezone.utc)
            for agent in agents:
                last_report = agent.get("last_report_at")
                if not last_report:
                    continue
                # Parse ISO timestamp; handle both +00:00 and Z suffixes
                if isinstance(last_report, str):
                    last_dt = datetime.fromisoformat(last_report.replace("Z", "+00:00"))
                    if last_dt.tzinfo is None:
                        last_dt = last_dt.replace(tzinfo=timezone.utc)
                else:
                    continue
                diff = now - last_dt
                minutes_since = int(diff.total_seconds() / 60)
                if minutes_since > timeout_minutes:
                    await _alert_missing_heartbeat(
                        agent["agent_id"], agent["hostname"], minutes_since
                    )
        except Exception:
            logger.exception("Error in heartbeat checker")
        await asyncio.sleep(interval_seconds)
