"""Prompt construction for Llama-based metric analysis."""

from __future__ import annotations

from qwickguard_agent.config import ServerConfig
from qwickguard_agent.models import CollectedMetrics

# Known action catalog that Llama may select from.
_ACTION_CATALOG = [
    "restart_container",
    "docker_compose_up",
    "kill_zombies",
    "prune_images",
    "run_backup",
    "restart_colima",
    "rotate_logs",
]


def build_analysis_prompt(metrics: CollectedMetrics, config: ServerConfig) -> str:
    """Build a structured prompt for Llama analysis of collected server metrics.

    Args:
        metrics: Full snapshot of metrics gathered in the current cycle.
        config: Server configuration including thresholds and monitored resources.

    Returns:
        A prompt string ready for submission to the compute worker.
    """
    t = config.thresholds
    sys = metrics.system

    # Container summary lines
    container_lines: list[str] = []
    monitored_names = {c.name for c in config.containers}
    present_names = {c.name for c in metrics.containers}
    missing_names = monitored_names - present_names

    for c in metrics.containers:
        container_lines.append(
            f"  - {c.name}: status={c.status}, health={c.health}, "
            f"restarts={c.restart_count}, cpu={c.cpu_percent:.1f}%, "
            f"mem={c.memory_mb:.0f}MB"
        )
    for name in sorted(missing_names):
        container_lines.append(f"  - {name}: MISSING (not running)")

    containers_block = "\n".join(container_lines) if container_lines else "  (none monitored)"

    # Service health summary
    service_lines: list[str] = []
    for svc in metrics.services:
        status = "healthy" if svc.healthy else f"UNHEALTHY ({svc.error or 'no error detail'})"
        service_lines.append(
            f"  - {svc.name} ({svc.url}): {status}, "
            f"response_time={svc.response_time_ms:.0f}ms"
        )
    services_block = "\n".join(service_lines) if service_lines else "  (none monitored)"

    # Zombie process summary
    zombie_lines: list[str] = []
    for proc in metrics.processes:
        state = "alive" if proc.alive else "DEAD/ZOMBIE"
        pid_info = f"pid={proc.pid}" if proc.pid else "no pid"
        zombie_lines.append(f"  - {proc.name} ({pid_info}): {state}")
    zombies_block = "\n".join(zombie_lines) if zombie_lines else "  (none tracked)"

    # Action catalog formatted for the prompt
    catalog_formatted = "\n".join(f"  - {a}" for a in _ACTION_CATALOG)

    prompt = f"""You are a server operations assistant analyzing system metrics for {config.hostname}.

## Current System Metrics

CPU usage: {sys.cpu_percent:.1f}%
RAM usage: {sys.ram_percent:.1f}% ({sys.ram_available_gb:.2f} GB available)
Disk usage: {sys.disk_percent:.1f}% ({sys.disk_available_gb:.2f} GB available)
Load average (1/5/15 min): {sys.load_avg[0]:.2f} / {sys.load_avg[1]:.2f} / {sys.load_avg[2]:.2f}
Open file descriptors: {sys.open_files}
System uptime: {sys.uptime_seconds:.0f} seconds

## Configured Thresholds

CPU warning: {t.cpu_warning}% | CPU critical: {t.cpu_critical}%
RAM warning: {t.ram_warning}% | RAM critical: {t.ram_critical}%
Disk warning: {t.disk_warning}% | Disk critical: {t.disk_critical}%

## Container Status

{containers_block}

## Service Health

{services_block}

## Process / Zombie Status

{zombies_block}

## Task

Analyze the metrics above and return a JSON object with this exact schema:

{{
  "status": "<healthy|warning|critical>",
  "issues": ["<concise issue description>", ...],
  "actions": [
    {{"action": "<action_name>", "target": "<target>", "reason": "<why>"}},
    ...
  ],
  "escalate_to_claude": <true|false>
}}

Rules:
- "status" must be "healthy", "warning", or "critical".
- "issues" lists specific problems found. Empty list if none.
- "actions" must only use actions from this catalog:
{catalog_formatted}
- Set "escalate_to_claude" to true only if there are more than 2 critical issues or the
  situation is too complex for automated remediation.
- Return ONLY the JSON object, no other text.
"""
    return prompt
