"""Report delivery to QwickBrain with local queue fallback.

Primary path: POST AgentReport to the brain API as JSON.
Fallback path: Queue report as a JSON file in ~/.qwickguard/report_queue/
  when the brain is unreachable, then replay the queue when the brain
  comes back online.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import httpx

from qwickguard_agent.models import AgentReport

logger = logging.getLogger(__name__)

# Timeout for brain API requests in seconds.
_BRAIN_TIMEOUT = 10.0

# Directory for queued reports.
_QUEUE_DIR = Path.home() / ".qwickguard" / "report_queue"


def _queue_dir() -> Path:
    """Return the report queue directory, creating it if absent."""
    _QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    return _QUEUE_DIR


def _queue_report(report: AgentReport) -> Path:
    """Persist a report as a JSON file in the local queue.

    The filename is based on the current UTC timestamp to guarantee ordering
    and uniqueness.

    Args:
        report: The AgentReport to persist.

    Returns:
        The Path of the written queue file.
    """
    queue_dir = _queue_dir()
    filename = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S") + ".json"
    queue_file = queue_dir / filename

    # Avoid clobbering if two reports arrive in the same second.
    counter = 0
    while queue_file.exists():
        counter += 1
        filename = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S") + f"_{counter}.json"
        queue_file = queue_dir / filename

    queue_file.write_text(report.model_dump_json(), encoding="utf-8")
    logger.debug("Queued report locally: %s", queue_file)
    return queue_file


async def report_to_brain(report: AgentReport, brain_url: str) -> None:
    """POST an AgentReport to the brain API.

    If the brain is unreachable or returns a non-2xx status, the report is
    queued locally as a JSON file so it can be replayed later.  After a
    successful delivery, any previously queued reports are replayed.

    Args:
        report: The AgentReport produced at the end of a collection cycle.
        brain_url: Base URL of the QwickBrain server (e.g. "http://brain:8080").
    """
    endpoint = f"{brain_url}/api/agent/report"
    payload = json.loads(report.model_dump_json())

    try:
        async with httpx.AsyncClient(timeout=_BRAIN_TIMEOUT) as client:
            response = await client.post(endpoint, json=payload)
            response.raise_for_status()
        logger.info(
            "Report delivered to brain: agent_id=%s status=%s",
            report.agent_id,
            report.analysis.status,
        )
        # Brain is reachable — try to drain the queue.
        await _replay_queue(brain_url)

    except (httpx.HTTPError, httpx.TimeoutException) as exc:
        logger.warning(
            "Brain unreachable (%s); queuing report locally for agent_id=%s",
            exc,
            report.agent_id,
        )
        _queue_report(report)


async def _replay_queue(brain_url: str) -> None:
    """Attempt to send any locally queued reports to the brain.

    Iterates queue files in filename order (chronological).  Each file is
    sent to the brain and deleted on success.  On the first delivery failure
    the replay stops to avoid out-of-order delivery and to allow the next
    cycle to retry.

    Args:
        brain_url: Base URL of the QwickBrain server.
    """
    queue_dir = _queue_dir()
    queue_files = sorted(queue_dir.glob("*.json"))

    if not queue_files:
        return

    logger.info("Replaying %d queued report(s) to brain", len(queue_files))
    endpoint = f"{brain_url}/api/agent/report"

    async with httpx.AsyncClient(timeout=_BRAIN_TIMEOUT) as client:
        for queue_file in queue_files:
            try:
                payload = json.loads(queue_file.read_text(encoding="utf-8"))
                response = await client.post(endpoint, json=payload)
                response.raise_for_status()
                queue_file.unlink()
                logger.info("Replayed queued report: %s", queue_file.name)
            except (httpx.HTTPError, httpx.TimeoutException) as exc:
                logger.warning(
                    "Failed to replay queued report %s (%s); stopping replay",
                    queue_file.name,
                    exc,
                )
                break
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "Unexpected error replaying %s (%s); skipping file",
                    queue_file.name,
                    exc,
                )
                # Move past corrupt files rather than blocking the queue forever.
                queue_file.unlink(missing_ok=True)
