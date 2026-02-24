"""Entry point for the QwickGuard agent.

Execution model:
- Loads configuration from YAML.
- Sets up logging to stdout and ~/.qwickguard/logs/agent.log.
- Runs the collect -> analyze -> heal -> report cycle on a configurable
  interval.  Pass ``--once`` on the CLI or set ``run_once=True`` in
  :func:`agent_loop` to execute a single cycle and exit.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from qwickguard_agent.analyzer import analyze_metrics
from qwickguard_agent.collectors.docker import collect_docker_metrics
from qwickguard_agent.collectors.processes import collect_process_info
from qwickguard_agent.collectors.services import collect_service_health
from qwickguard_agent.collectors.system import collect_system_metrics
from qwickguard_agent.config import ServerConfig, load_config
from qwickguard_agent.healer import Healer
from qwickguard_agent.models import AgentReport, CollectedMetrics, SystemMetrics
from qwickguard_agent.reporter import report_to_brain

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------


def setup_logging(log_dir: Path | None = None) -> None:
    """Configure root logger to write to stdout and a rotating file.

    Args:
        log_dir: Directory for the log file.  Defaults to
            ``~/.qwickguard/logs/``.
    """
    if log_dir is None:
        log_dir = Path.home() / ".qwickguard" / "logs"

    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "agent.log"

    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    datefmt = "%Y-%m-%dT%H:%M:%S"

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # Stdout handler
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(logging.INFO)
    stdout_handler.setFormatter(logging.Formatter(fmt, datefmt=datefmt))

    # File handler
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(fmt, datefmt=datefmt))

    root.addHandler(stdout_handler)
    root.addHandler(file_handler)

    logger.info("Logging initialised. File: %s", log_file)


# ---------------------------------------------------------------------------
# Cycle logic
# ---------------------------------------------------------------------------


async def run_cycle(config: ServerConfig, healer: Healer) -> None:
    """Execute a single agent monitoring and remediation cycle.

    The cycle performs four steps in sequence:

    1. COLLECT: gather metrics from system, Docker, services, and processes.
       Each collector is run independently; a failure in one collector is
       logged and that domain reports empty/fallback data so the cycle
       continues.
    2. ANALYZE: run the Llama analyzer (with threshold fallback) on the
       collected metrics.
    3. ACT: if the analysis produced actions, execute them via the healer.
    4. REPORT: build an AgentReport and POST it to the brain API, queuing
       locally if the brain is unreachable.

    Args:
        config: Validated server configuration.
        healer: Healer instance to use for executing remediation actions.
    """
    cycle_start = datetime.now(tz=timezone.utc)
    logger.info(
        "Starting cycle for agent_id=%s hostname=%s",
        config.agent_id,
        config.hostname,
    )

    # ------------------------------------------------------------------
    # STEP 1: COLLECT
    # ------------------------------------------------------------------

    # System metrics — critical; use a fallback on failure.
    try:
        system_metrics = await collect_system_metrics()
    except Exception as exc:  # noqa: BLE001
        logger.error("System collector failed: %s", exc)
        system_metrics = SystemMetrics(
            cpu_percent=0.0,
            ram_percent=0.0,
            ram_available_gb=0.0,
            disk_percent=0.0,
            disk_available_gb=0.0,
            load_avg=(0.0, 0.0, 0.0),
            open_files=0,
            uptime_seconds=0.0,
        )

    # Docker metrics — non-critical; empty list on failure.
    try:
        containers = collect_docker_metrics(config)
    except Exception as exc:  # noqa: BLE001
        logger.error("Docker collector failed: %s", exc)
        containers = []

    # Service health — non-critical; empty list on failure.
    try:
        services = await collect_service_health(config)
    except Exception as exc:  # noqa: BLE001
        logger.error("Service collector failed: %s", exc)
        services = []

    # Process info — non-critical; empty list on failure.
    try:
        processes = await collect_process_info(config)
    except Exception as exc:  # noqa: BLE001
        logger.error("Process collector failed: %s", exc)
        processes = []

    metrics = CollectedMetrics(
        system=system_metrics,
        containers=containers,
        services=services,
        processes=processes,
        timestamp=cycle_start,
    )

    logger.debug(
        "Collection complete: containers=%d services=%d processes=%d",
        len(containers),
        len(services),
        len(processes),
    )

    # ------------------------------------------------------------------
    # STEP 2: ANALYZE
    # ------------------------------------------------------------------

    analysis = await analyze_metrics(metrics, config)
    logger.info(
        "Analysis complete: status=%s issues=%d actions=%d",
        analysis.status,
        len(analysis.issues),
        len(analysis.actions),
    )

    # ------------------------------------------------------------------
    # STEP 3: ACT
    # ------------------------------------------------------------------

    actions_taken = []
    if analysis.actions:
        logger.info("Executing %d action(s)", len(analysis.actions))
        actions_taken = await healer.execute_actions(
            analysis.actions,
            decided_by="agent:threshold_rules",
        )
        logger.info(
            "Actions complete: %s",
            [(a.action, a.result) for a in actions_taken],
        )
    else:
        logger.debug("No actions to execute this cycle")

    # ------------------------------------------------------------------
    # STEP 4: REPORT
    # ------------------------------------------------------------------

    report = AgentReport(
        agent_id=config.agent_id,
        hostname=config.hostname,
        timestamp=cycle_start,
        metrics=metrics,
        analysis=analysis,
        actions_taken=actions_taken,
    )

    await report_to_brain(report, config.brain_url)

    logger.info(
        "Completed cycle for agent_id=%s hostname=%s status=%s",
        config.agent_id,
        config.hostname,
        analysis.status,
    )


async def agent_loop(
    config: ServerConfig,
    *,
    run_once: bool = False,
) -> None:
    """Run the agent in a continuous loop.

    Creates a :class:`Healer` instance for the lifetime of the loop and
    passes it to each :func:`run_cycle` invocation.  Per-cycle exceptions
    are caught and logged so the loop continues running.

    Args:
        config: Validated server configuration.
        run_once: If ``True``, execute exactly one cycle and return.
            Used for testing and single-shot invocation via ``--once``.
    """
    logger.info(
        "Agent loop starting. interval=%ds run_once=%s",
        config.check_interval_seconds,
        run_once,
    )

    audit_log_path = Path.home() / ".qwickguard" / "logs" / "healer-audit.jsonl"
    healer = Healer(config=config, audit_log_path=audit_log_path)

    while True:
        start = time.monotonic()
        try:
            await run_cycle(config, healer)
        except Exception:
            logger.exception("Unhandled error in agent cycle")

        if run_once:
            break

        elapsed = time.monotonic() - start
        sleep_for = max(0.0, config.check_interval_seconds - elapsed)
        logger.debug("Sleeping for %.1f seconds until next cycle", sleep_for)
        await asyncio.sleep(sleep_for)

    logger.info("Agent loop finished")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entry point registered in pyproject.toml.

    Accepts an optional ``--once`` flag to execute a single cycle and exit.
    Without the flag the agent runs continuously on the configured interval.
    """
    parser = argparse.ArgumentParser(description="QwickGuard infrastructure agent")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Execute a single monitoring cycle and exit",
    )
    args = parser.parse_args()

    config = load_config()
    setup_logging()

    logger.info(
        "QwickGuard agent starting. agent_id=%s hostname=%s brain_url=%s",
        config.agent_id,
        config.hostname,
        config.brain_url,
    )

    asyncio.run(agent_loop(config, run_once=args.once))

    logger.info("QwickGuard agent finished.")


if __name__ == "__main__":
    main()
