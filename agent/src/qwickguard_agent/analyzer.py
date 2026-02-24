"""Llama-based metric analyzer with threshold fallback.

Primary path: POST to compute worker with a structured prompt, parse JSON response.
Fallback path: Rule-based threshold analysis when the compute worker is unavailable.
"""

from __future__ import annotations

import json
import logging
import re

import httpx

from qwickguard_agent.config import ServerConfig
from qwickguard_agent.models import AnalysisResult, CollectedMetrics
from qwickguard_agent.prompts import build_analysis_prompt

logger = logging.getLogger(__name__)

# Model selection by severity tier.
_MODEL_MAP: dict[str, str] = {
    "routine": "llama3.2:3b",
    "warning": "llama-3.1-8b",
    "critical": "qwen-2.5-14b",
}

# Timeout for compute worker requests in seconds.
_COMPUTE_TIMEOUT = 60.0


async def analyze_metrics(
    metrics: CollectedMetrics,
    config: ServerConfig,
) -> AnalysisResult:
    """Analyze collected metrics using Llama, falling back to threshold rules.

    Determines severity, selects the appropriate model tier, calls the compute
    worker, and parses the JSON response.  If the compute worker is unavailable
    or returns an unparseable response, falls back to :func:`_threshold_fallback`.

    Args:
        metrics: Full snapshot of metrics from the current collection cycle.
        config: Server configuration with thresholds and monitored resources.

    Returns:
        An :class:`AnalysisResult` produced by Llama or the threshold fallback.
    """
    severity = _determine_severity(metrics, config)

    try:
        result = await _llama_analysis(metrics, config, severity)
        logger.info("Llama analysis complete: status=%s", result.status)
        return result
    except Exception as exc:  # noqa: BLE001
        logger.warning("Llama analysis failed (%s); using threshold fallback", exc)
        return _threshold_fallback(metrics, config)


def _determine_severity(metrics: CollectedMetrics, config: ServerConfig) -> str:
    """Determine the severity tier for model selection.

    Checks CPU, RAM, and disk against configured thresholds, then checks
    container health.  Returns ``"critical"``, ``"warning"``, or ``"routine"``.

    Args:
        metrics: Collected metrics snapshot.
        config: Server configuration with thresholds.

    Returns:
        One of ``"critical"``, ``"warning"``, or ``"routine"``.
    """
    t = config.thresholds
    sys = metrics.system

    # Check critical thresholds first.
    if (
        sys.cpu_percent >= t.cpu_critical
        or sys.ram_percent >= t.ram_critical
        or sys.disk_percent >= t.disk_critical
    ):
        return "critical"

    # Check for missing or unhealthy containers.
    monitored_names = {c.name for c in config.containers}
    present_names = {c.name for c in metrics.containers}
    missing = monitored_names - present_names
    if missing:
        return "critical"

    unhealthy = [
        c for c in metrics.containers if c.health not in ("healthy", "none", "")
    ]
    if unhealthy:
        return "warning"

    # Check warning thresholds.
    if (
        sys.cpu_percent >= t.cpu_warning
        or sys.ram_percent >= t.ram_warning
        or sys.disk_percent >= t.disk_warning
    ):
        return "warning"

    return "routine"


async def _llama_analysis(
    metrics: CollectedMetrics,
    config: ServerConfig,
    severity: str | None = None,
) -> AnalysisResult:
    """Call the compute worker and parse the JSON response.

    Args:
        metrics: Collected metrics snapshot.
        config: Server configuration.
        severity: Pre-computed severity tier; computed if not provided.

    Returns:
        Parsed :class:`AnalysisResult`.

    Raises:
        httpx.HTTPError: If the compute worker request fails.
        ValueError: If the response cannot be parsed into an AnalysisResult.
    """
    if severity is None:
        severity = _determine_severity(metrics, config)

    model = _MODEL_MAP.get(severity, _MODEL_MAP["routine"])
    prompt = build_analysis_prompt(metrics, config)

    payload = {
        "model": model,
        "type": "completion",
        "input": {
            "prompt": prompt,
            "maxTokens": 512,
            "temperature": 0.1,
        },
    }

    async with httpx.AsyncClient(timeout=_COMPUTE_TIMEOUT) as client:
        response = await client.post(
            f"{config.compute_worker_url}/api/infer",
            json=payload,
        )
        response.raise_for_status()

    data = response.json()
    text = data.get("text") or data.get("message", {}).get("content", "")
    if not text:
        raise ValueError("Compute worker returned empty text")

    return _parse_analysis(text)


def _threshold_fallback(
    metrics: CollectedMetrics,
    config: ServerConfig,
) -> AnalysisResult:
    """Rule-based analysis used when the compute worker is unavailable.

    Checks CPU, RAM, and disk against configured thresholds; inspects container
    health; identifies zombie processes; and logs unhealthy services without
    automatically remediating them.

    Args:
        metrics: Collected metrics snapshot.
        config: Server configuration with thresholds and monitored resources.

    Returns:
        A rule-based :class:`AnalysisResult`.
    """
    t = config.thresholds
    sys = metrics.system
    issues: list[str] = []
    actions: list[dict] = []
    critical_count = 0

    # CPU checks
    if sys.cpu_percent >= t.cpu_critical:
        issues.append(f"CPU critical: {sys.cpu_percent:.1f}% >= {t.cpu_critical}%")
        critical_count += 1
    elif sys.cpu_percent >= t.cpu_warning:
        issues.append(f"CPU warning: {sys.cpu_percent:.1f}% >= {t.cpu_warning}%")

    # RAM checks
    if sys.ram_percent >= t.ram_critical:
        issues.append(f"RAM critical: {sys.ram_percent:.1f}% >= {t.ram_critical}%")
        critical_count += 1
    elif sys.ram_percent >= t.ram_warning:
        issues.append(f"RAM warning: {sys.ram_percent:.1f}% >= {t.ram_warning}%")

    # Disk checks
    if sys.disk_percent >= t.disk_critical:
        issues.append(f"Disk critical: {sys.disk_percent:.1f}% >= {t.disk_critical}%")
        critical_count += 1
    elif sys.disk_percent >= t.disk_warning:
        issues.append(f"Disk warning: {sys.disk_percent:.1f}% >= {t.disk_warning}%")

    # Container checks
    monitored_names = {c.name for c in config.containers}
    container_config_map = {c.name: c for c in config.containers}
    present_map = {c.name: c for c in metrics.containers}
    missing_names = monitored_names - set(present_map)

    for name in sorted(missing_names):
        issues.append(f"Container missing: {name}")
        critical_count += 1
        cfg = container_config_map.get(name)
        if cfg and cfg.compose_file:
            actions.append(
                {
                    "action": "docker_compose_up",
                    "target": name,
                    "reason": f"Container {name} is missing; starting via compose file",
                }
            )
        else:
            actions.append(
                {
                    "action": "restart_container",
                    "target": name,
                    "reason": f"Container {name} is missing",
                }
            )

    for name, container in present_map.items():
        if container.health not in ("healthy", "none", ""):
            issues.append(f"Container unhealthy: {name} (health={container.health})")
            actions.append(
                {
                    "action": "restart_container",
                    "target": name,
                    "reason": f"Container {name} health check status: {container.health}",
                }
            )

    # Zombie / dead process checks
    for proc in metrics.processes:
        if not proc.alive:
            issues.append(f"Process dead/zombie: {proc.name} (pattern={proc.pattern})")
            actions.append(
                {
                    "action": "kill_zombies",
                    "target": proc.pattern,
                    "reason": f"Process {proc.name} is not alive",
                }
            )

    # Service checks — log only, no auto-remediation
    for svc in metrics.services:
        if not svc.healthy:
            logger.warning("Service unhealthy: %s (%s) - %s", svc.name, svc.url, svc.error)
            issues.append(f"Service unhealthy: {svc.name} ({svc.url})")

    # Determine overall status
    if critical_count > 0:
        status: str = "critical"
    elif issues:
        status = "warning"
    else:
        status = "healthy"

    escalate = critical_count > 2

    return AnalysisResult(
        status=status,
        issues=issues,
        actions=actions,
        escalate_to_claude=escalate,
    )


def _parse_analysis(text: str) -> AnalysisResult:
    """Extract and parse a JSON AnalysisResult from Llama response text.

    Handles responses wrapped in markdown code fences (```json ... ```) as well
    as plain JSON with surrounding prose.

    Args:
        text: Raw text returned by the compute worker.

    Returns:
        A validated :class:`AnalysisResult`.

    Raises:
        ValueError: If no valid JSON object matching the schema can be extracted.
    """
    # Strip markdown code fences if present.
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence_match:
        json_str = fence_match.group(1)
    else:
        # Find the outermost JSON object in the text.
        obj_match = re.search(r"\{.*\}", text, re.DOTALL)
        if not obj_match:
            raise ValueError(f"No JSON object found in Llama response: {text!r}")
        json_str = obj_match.group(0)

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Failed to parse JSON from Llama response: {exc}") from exc

    try:
        return AnalysisResult.model_validate(data)
    except Exception as exc:
        raise ValueError(f"AnalysisResult validation failed: {exc}") from exc
