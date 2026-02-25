from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import aiosqlite

logger = logging.getLogger("qwickguard.brain.storage")

_db_path: str = "/data/qwickguard.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL,
    hostname TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    status TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    analysis_json TEXT NOT NULL,
    actions_json TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_reports_agent_ts ON agent_reports(agent_id, timestamp);

CREATE TABLE IF NOT EXISTS agents (
    agent_id TEXT PRIMARY KEY,
    hostname TEXT NOT NULL,
    last_report_at TEXT,
    last_status TEXT,
    registered_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS escalations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    trigger_reason TEXT NOT NULL,
    claude_response TEXT,
    actions_recommended TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL,
    severity TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT,
    channel TEXT NOT NULL,
    external_id TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
"""


async def init_db(db_path: str = "/data/qwickguard.db") -> None:
    """Create tables and initialise the module-level database path."""
    global _db_path
    _db_path = db_path
    async with aiosqlite.connect(_db_path) as db:
        await db.executescript(_SCHEMA)
        await db.commit()
    logger.info("Database initialised at %s", _db_path)


async def store_report(report: dict[str, Any]) -> None:
    """Insert an agent report row and upsert the agents registry row."""
    agent_id = report["agent_id"]
    hostname = report.get("hostname", "")
    timestamp = report.get("timestamp", datetime.now(timezone.utc).isoformat())
    analysis = report.get("analysis", {})
    status = analysis.get("status", report.get("status", "unknown"))
    metrics_json = json.dumps(report.get("metrics", {}))
    analysis_json = json.dumps(analysis)
    actions_json = json.dumps(report.get("actions_taken", report.get("actions", [])))

    async with aiosqlite.connect(_db_path) as db:
        await db.execute(
            """
            INSERT INTO agent_reports
                (agent_id, hostname, timestamp, status, metrics_json, analysis_json, actions_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (agent_id, hostname, timestamp, status, metrics_json, analysis_json, actions_json),
        )
        await db.execute(
            """
            INSERT INTO agents (agent_id, hostname, last_report_at, last_status)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET
                hostname = excluded.hostname,
                last_report_at = excluded.last_report_at,
                last_status = excluded.last_status
            """,
            (agent_id, hostname, timestamp, status),
        )
        await db.commit()


async def get_agent_history(agent_id: str, hours: int = 168) -> list[dict[str, Any]]:
    """Return agent reports within the given time window (default 7 days)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    async with aiosqlite.connect(_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT * FROM agent_reports
            WHERE agent_id = ? AND timestamp >= ?
            ORDER BY timestamp DESC
            """,
            (agent_id, cutoff),
        ) as cursor:
            rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def get_agents() -> list[dict[str, Any]]:
    """Return all registered agents."""
    async with aiosqlite.connect(_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM agents ORDER BY last_report_at DESC") as cursor:
            rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def get_latest_report(agent_id: str) -> dict[str, Any] | None:
    """Return the most recent report for the given agent."""
    async with aiosqlite.connect(_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT * FROM agent_reports
            WHERE agent_id = ?
            ORDER BY timestamp DESC
            LIMIT 1
            """,
            (agent_id,),
        ) as cursor:
            row = await cursor.fetchone()
    return dict(row) if row else None


async def get_recent_reports(agent_id: str, limit: int = 50) -> list[dict[str, Any]]:
    """Return the most recent reports for the given agent."""
    async with aiosqlite.connect(_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT * FROM agent_reports
            WHERE agent_id = ?
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (agent_id, limit),
        ) as cursor:
            rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def get_recent_actions(agent_id: str, limit: int = 100) -> list[dict[str, Any]]:
    """Extract and flatten actions_json from recent reports for the given agent."""
    async with aiosqlite.connect(_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT actions_json, timestamp FROM agent_reports
            WHERE agent_id = ?
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (agent_id, limit),
        ) as cursor:
            rows = await cursor.fetchall()

    actions: list[dict[str, Any]] = []
    for row in rows:
        try:
            parsed = json.loads(row["actions_json"])
            if isinstance(parsed, list):
                for action in parsed:
                    actions.append({"timestamp": row["timestamp"], **action} if isinstance(action, dict) else {"timestamp": row["timestamp"], "action": action})
        except (json.JSONDecodeError, TypeError):
            logger.warning("Failed to parse actions_json for agent %s at %s", agent_id, row["timestamp"])
    return actions


async def get_recent_notifications(
    limit: int = 50, severity: str | None = None
) -> list[dict[str, Any]]:
    """Return recent notifications, optionally filtered by severity."""
    async with aiosqlite.connect(_db_path) as db:
        db.row_factory = aiosqlite.Row
        if severity:
            async with db.execute(
                """
                SELECT * FROM notifications
                WHERE severity = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (severity, limit),
            ) as cursor:
                rows = await cursor.fetchall()
        else:
            async with db.execute(
                """
                SELECT * FROM notifications
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ) as cursor:
                rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def get_recent_escalations(
    agent_id: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    """Return recent escalations, optionally filtered by agent."""
    async with aiosqlite.connect(_db_path) as db:
        db.row_factory = aiosqlite.Row
        if agent_id:
            async with db.execute(
                """
                SELECT * FROM escalations
                WHERE agent_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (agent_id, limit),
            ) as cursor:
                rows = await cursor.fetchall()
        else:
            async with db.execute(
                """
                SELECT * FROM escalations
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ) as cursor:
                rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def cleanup_old_data(retention_days: int = 7) -> None:
    """Delete records older than retention_days from all tables."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
    async with aiosqlite.connect(_db_path) as db:
        await db.execute("DELETE FROM agent_reports WHERE created_at < ?", (cutoff,))
        await db.execute("DELETE FROM escalations WHERE created_at < ?", (cutoff,))
        await db.execute("DELETE FROM notifications WHERE created_at < ?", (cutoff,))
        await db.commit()
    logger.info("Cleaned up records older than %d days (cutoff: %s)", retention_days, cutoff)


async def store_escalation(
    agent_id: str,
    timestamp: str,
    trigger_reason: str,
    claude_response: str | None,
    actions_recommended: str | None,
) -> None:
    """Persist an escalation record."""
    async with aiosqlite.connect(_db_path) as db:
        await db.execute(
            """
            INSERT INTO escalations
                (agent_id, timestamp, trigger_reason, claude_response, actions_recommended)
            VALUES (?, ?, ?, ?, ?)
            """,
            (agent_id, timestamp, trigger_reason, claude_response, actions_recommended),
        )
        await db.commit()


async def store_notification(
    agent_id: str,
    severity: str,
    title: str,
    body: str | None,
    channel: str,
    external_id: str | None = None,
) -> None:
    """Persist a notification record."""
    async with aiosqlite.connect(_db_path) as db:
        await db.execute(
            """
            INSERT INTO notifications
                (agent_id, severity, title, body, channel, external_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (agent_id, severity, title, body, channel, external_id),
        )
        await db.commit()
