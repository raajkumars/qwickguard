"""Autonomous healer with action catalog, cooldown enforcement, and audit logging."""

from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from qwickguard_agent.config import ServerConfig
from qwickguard_agent.models import ActionResult


# ---------------------------------------------------------------------------
# Action catalog (allowlist only)
# ---------------------------------------------------------------------------

ACTION_CATALOG: dict[str, str] = {
    "restart_container": "docker restart {target}",
    "docker_compose_up": "docker compose -f {target} up -d",
    "kill_zombies": "pkill -9 -f '{target}'",
    "prune_images": "docker system prune -af",
    "run_backup": "{home}/.qwickguard/scripts/backup.sh",
    "restart_colima": "colima restart",
    "rotate_logs": "find {target} -name '*.log' -mtime +7 -delete",
}

# ---------------------------------------------------------------------------
# Blocked patterns (NEVER execute if command contains these)
# ---------------------------------------------------------------------------

BLOCKED_PATTERNS: list[str] = [
    "docker rm",
    "docker volume rm",
    "docker system prune --volumes",
    "DROP ",
    "DELETE FROM",
    "TRUNCATE",
    "git push",
    "git checkout",
]

# ---------------------------------------------------------------------------
# Cooldown configuration: (max_count, window_seconds)
# ---------------------------------------------------------------------------

COOLDOWN_CONFIG: dict[str, tuple[int, int]] = {
    "restart_container": (3, 30 * 60),   # 3 per 30 min per target
    "prune_images":      (1, 24 * 3600), # 1 per day
    "restart_colima":    (1, 3600),      # 1 per hour
    "kill_zombies":      (5, 3600),      # 5 per hour
    "run_backup":        (1, 3600),      # 1 per hour
}

# ---------------------------------------------------------------------------
# Cooldown tracker
# ---------------------------------------------------------------------------


class CooldownTracker:
    """Tracks action invocation history to enforce per-action rate limits."""

    def __init__(self) -> None:
        # key: "action:target" -> list of timestamps (float epoch seconds)
        self._history: dict[str, list[float]] = defaultdict(list)

    def _key(self, action: str, target: str) -> str:
        return f"{action}:{target}"

    def check(self, action: str, target: str) -> bool:
        """Return True if the action is allowed (within cooldown limits).

        Returns False if the cooldown threshold has been reached.
        Actions not in COOLDOWN_CONFIG are always allowed.
        """
        if action not in COOLDOWN_CONFIG:
            return True

        max_count, window_seconds = COOLDOWN_CONFIG[action]
        key = self._key(action, target)
        now = time.time()
        cutoff = now - window_seconds

        # Prune expired entries
        self._history[key] = [ts for ts in self._history[key] if ts >= cutoff]

        return len(self._history[key]) < max_count

    def record(self, action: str, target: str) -> None:
        """Record a timestamp for the given action:target pair."""
        key = self._key(action, target)
        self._history[key].append(time.time())


# ---------------------------------------------------------------------------
# Healer
# ---------------------------------------------------------------------------


class Healer:
    """Executes remediation actions from a validated allowlist.

    All actions are validated against the catalog, checked for blocked
    patterns, subject to cooldown limits, and fully audited to a JSON-lines
    log file.
    """

    def __init__(
        self,
        config: ServerConfig,
        audit_log_path: str | Path,
    ) -> None:
        self._config = config
        self._audit_log_path = Path(audit_log_path)
        self._cooldown = CooldownTracker()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def execute_actions(
        self,
        actions: list[dict],
        decided_by: str,
    ) -> list[ActionResult]:
        """Execute a list of action dicts sequentially.

        Each dict is expected to have at minimum:
          - "action": str  (key into ACTION_CATALOG)
          - "target": str  (parameter for the command template; may be "")
          - "reason": str  (human-readable reason for the action)

        Returns a list of ActionResult instances in the same order.
        """
        results: list[ActionResult] = []
        for action_dict in actions:
            result = await self._execute_one(action_dict, decided_by)
            results.append(result)
        return results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _execute_one(
        self,
        action_dict: dict,
        decided_by: str,
    ) -> ActionResult:
        action: str = action_dict.get("action", "")
        target: str = action_dict.get("target", "")
        reason: str = action_dict.get("reason", "")

        # Step 1: Validate action is in catalog
        if action not in ACTION_CATALOG:
            result = ActionResult(
                action=action,
                target=target,
                reason=reason,
                decided_by=decided_by,
                result="rejected",
                error=f"Action '{action}' is not in the allowed catalog",
            )
            self._audit(result)
            return result

        # Step 2: Build command
        try:
            command = self._build_command(action, target)
        except KeyError as exc:
            result = ActionResult(
                action=action,
                target=target,
                reason=reason,
                decided_by=decided_by,
                result="rejected",
                error=f"Failed to build command: {exc}",
            )
            self._audit(result)
            return result

        # Step 3: Check for blocked patterns
        blocked = self._check_blocked_patterns(command)
        if blocked is not None:
            result = ActionResult(
                action=action,
                target=target,
                reason=reason,
                decided_by=decided_by,
                result="rejected",
                error=f"Command contains blocked pattern: '{blocked}'",
            )
            self._audit(result)
            return result

        # Step 4: Validate target for container actions
        if action == "restart_container":
            validation_error = self._validate_container_target(target)
            if validation_error is not None:
                result = ActionResult(
                    action=action,
                    target=target,
                    reason=reason,
                    decided_by=decided_by,
                    result="rejected",
                    error=validation_error,
                )
                self._audit(result)
                return result

        # Step 5: Check cooldown
        if not self._cooldown.check(action, target):
            result = ActionResult(
                action=action,
                target=target,
                reason=reason,
                decided_by=decided_by,
                result="cooldown",
                error=f"Cooldown active for action '{action}' on target '{target}'",
            )
            self._audit(result)
            return result

        # Step 6: Execute
        result = await self._run_command(
            action=action,
            target=target,
            reason=reason,
            decided_by=decided_by,
            command=command,
        )

        # Step 7: Record cooldown on any execution attempt (success or failure)
        self._cooldown.record(action, target)

        self._audit(result)
        return result

    def _build_command(self, action: str, target: str) -> str:
        """Format the catalog command template with the given target.

        For actions that use {home}, substitutes the $HOME path.
        For actions that use {target}, substitutes the target string.
        """
        import os

        template = ACTION_CATALOG[action]
        return template.format(target=target, home=os.path.expanduser("~"))

    def _check_blocked_patterns(self, command: str) -> Optional[str]:
        """Return the first blocked pattern found in the command, or None."""
        for pattern in BLOCKED_PATTERNS:
            if pattern in command:
                return pattern
        return None

    def _validate_container_target(self, target: str) -> Optional[str]:
        """Validate that target is a known container name in config.

        Returns an error string if invalid, None if valid.
        """
        if not target:
            return "Container target must not be empty"

        known_names = {c.name for c in self._config.containers}
        if target not in known_names:
            return (
                f"Container '{target}' is not in the monitored containers list. "
                f"Known containers: {sorted(known_names)}"
            )
        return None

    async def _run_command(
        self,
        action: str,
        target: str,
        reason: str,
        decided_by: str,
        command: str,
    ) -> ActionResult:
        """Run the shell command with a 60-second timeout."""
        try:
            proc = await asyncio.wait_for(
                asyncio.create_subprocess_shell(
                    command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                ),
                timeout=60.0,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60.0)
            if proc.returncode == 0:
                return ActionResult(
                    action=action,
                    target=target,
                    reason=reason,
                    decided_by=decided_by,
                    result="success",
                )
            else:
                return ActionResult(
                    action=action,
                    target=target,
                    reason=reason,
                    decided_by=decided_by,
                    result="failed",
                    error=stderr.decode(errors="replace").strip() or f"Exit code {proc.returncode}",
                )
        except asyncio.TimeoutError:
            return ActionResult(
                action=action,
                target=target,
                reason=reason,
                decided_by=decided_by,
                result="failed",
                error="Command timed out after 60 seconds",
            )
        except Exception as exc:  # noqa: BLE001
            return ActionResult(
                action=action,
                target=target,
                reason=reason,
                decided_by=decided_by,
                result="failed",
                error=str(exc),
            )

    def _audit(self, result: ActionResult) -> None:
        """Append a JSON line to the audit log for the given action result."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": result.action,
            "target": result.target,
            "reason": result.reason,
            "decided_by": result.decided_by,
            "result": result.result,
            "error": result.error,
        }
        self._audit_log_path.parent.mkdir(parents=True, exist_ok=True)
        with self._audit_log_path.open("a") as fh:
            fh.write(json.dumps(entry) + "\n")
