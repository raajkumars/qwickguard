"""Background heartbeat checker for QwickGuard Brain Service.

Monitors registered agents and logs a warning when an agent has not
reported within the configured timeout.  Task 4 will replace
``_alert_missing_heartbeat`` with real notification dispatch.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from .storage import get_agents

logger = logging.getLogger("qwickguard.brain.registry")


# Will be replaced with real notification dispatch in Task 4
async def _alert_missing_heartbeat(agent_id: str, hostname: str, minutes_since: int) -> None:
    logger.warning(
        "Agent %s (%s) heartbeat missing for %d minutes",
        agent_id,
        hostname,
        minutes_since,
    )


async def heartbeat_checker(timeout_minutes: int = 15, interval_seconds: int = 60) -> None:
    """Check every *interval_seconds* seconds for agents missing heartbeat.

    An agent is considered missing when its last report is older than
    *timeout_minutes* minutes.  The checker runs indefinitely and is
    expected to be started as an ``asyncio.Task``.
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
