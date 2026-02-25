"""Claude API escalation engine for QwickGuard Brain.

Escalates complex infrastructure issues to Claude for deeper diagnosis.
Rate limited (configurable per day per agent) with 1-hour issue dedup cache.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import settings
from .storage import store_escalation

logger = logging.getLogger("qwickguard.brain.escalation")

_PROMPT_PATH = Path(__file__).resolve().parent.parent.parent / "prompts" / "diagnosis.md"


def _load_diagnosis_prompt(hostname: str) -> str:
    """Load and format the diagnosis system prompt."""
    template = _PROMPT_PATH.read_text()
    return template.replace("{hostname}", hostname)


def _hash_issues(issues: list[dict[str, Any]]) -> str:
    """Create a stable hash for a list of issues for dedup."""
    canonical = json.dumps(issues, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def _format_escalation_context(report: dict[str, Any], history: list[dict[str, Any]]) -> str:
    """Format report + recent history into a user message for Claude."""
    parts = ["## Current Report\n"]
    parts.append(f"Agent: {report.get('agent_id', 'unknown')}")
    parts.append(f"Hostname: {report.get('hostname', 'unknown')}")
    parts.append(f"Timestamp: {report.get('timestamp', 'unknown')}")
    parts.append(f"\n### Metrics\n```json\n{json.dumps(report.get('metrics', {}), indent=2)}\n```")
    parts.append(f"\n### Analysis\n```json\n{json.dumps(report.get('analysis', {}), indent=2)}\n```")
    parts.append(f"\n### Actions Taken\n```json\n{json.dumps(report.get('actions_taken', []), indent=2)}\n```")

    if history:
        parts.append(f"\n## Recent History ({len(history)} reports)")
        # Summarize recent statuses
        statuses = [h.get("status", "unknown") for h in history[:10]]
        parts.append(f"Recent statuses: {', '.join(statuses)}")

    return "\n".join(parts)


def _parse_diagnosis(text: str) -> dict[str, Any]:
    """Parse Claude's JSON response, handling markdown code fences."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        # Remove first and last lines (code fence markers)
        lines = [l for l in lines[1:] if not l.strip().startswith("```")]
        cleaned = "\n".join(lines)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        logger.warning("Failed to parse Claude response as JSON: %s", text[:200])
        return {
            "severity": "warning",
            "diagnosis": text[:500],
            "recommended_actions": [],
            "escalation_summary": "Claude response could not be parsed as structured JSON.",
        }


class EscalationEngine:
    """Manages Claude API escalation with rate limiting and caching."""

    def __init__(self) -> None:
        self._client = None
        self._daily_count: dict[str, int] = {}  # "agent_id:date" -> count
        self._cache: dict[str, tuple[datetime, dict]] = {}  # issue_hash -> (ts, response)

    def _get_client(self):
        """Lazy-init the Anthropic async client."""
        if self._client is None and settings.anthropic_api_key:
            from anthropic import AsyncAnthropic
            self._client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        return self._client

    async def escalate(
        self,
        agent_id: str,
        report: dict[str, Any],
        history: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        """Escalate an issue to Claude for diagnosis.

        Returns structured diagnosis dict or None if escalation is skipped
        (no API key, rate limited, or cached).
        """
        client = self._get_client()
        if client is None:
            logger.info("Escalation skipped: no ANTHROPIC_API_KEY configured")
            return None

        # Rate limit: max N per day per agent
        today = date.today().isoformat()
        rate_key = f"{agent_id}:{today}"
        if self._daily_count.get(rate_key, 0) >= settings.max_claude_calls_per_day:
            logger.info("Escalation skipped: rate limit reached for %s today", agent_id)
            return None

        # Cache: same issues within 1 hour = return cached
        issues = report.get("analysis", {}).get("issues", [])
        issue_hash = _hash_issues(issues)
        if issue_hash in self._cache:
            cached_ts, cached_resp = self._cache[issue_hash]
            if datetime.now(timezone.utc) - cached_ts < timedelta(hours=1):
                logger.info("Escalation skipped: cached response for issue hash %s", issue_hash)
                return cached_resp

        # Call Claude
        hostname = report.get("hostname", "unknown")
        system_prompt = _load_diagnosis_prompt(hostname)
        user_message = _format_escalation_context(report, history or [])

        try:
            response = await client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1024,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
            )
            result = _parse_diagnosis(response.content[0].text)
        except Exception:
            logger.exception("Claude API call failed for agent %s", agent_id)
            return None

        # Update rate limit and cache
        self._daily_count[rate_key] = self._daily_count.get(rate_key, 0) + 1
        self._cache[issue_hash] = (datetime.now(timezone.utc), result)

        # Persist escalation
        trigger_reason = "; ".join(
            i.get("description", str(i)) for i in issues
        ) or "Escalation requested"
        await store_escalation(
            agent_id=agent_id,
            timestamp=report.get("timestamp", datetime.now(timezone.utc).isoformat()),
            trigger_reason=trigger_reason,
            claude_response=json.dumps(result),
            actions_recommended=json.dumps(result.get("recommended_actions", [])),
        )

        logger.info(
            "Escalation complete for %s: severity=%s, actions=%d",
            agent_id,
            result.get("severity"),
            len(result.get("recommended_actions", [])),
        )
        return result


# Module-level singleton
escalation_engine = EscalationEngine()
