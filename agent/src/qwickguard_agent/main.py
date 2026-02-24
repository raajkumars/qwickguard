"""Entry point for the QwickGuard agent.

Execution model (initial scaffold):
- Loads configuration from YAML.
- Sets up logging to stdout and ~/.qwickguard/logs/agent.log.
- Runs a single collection cycle, then exits.
  (Looping is implemented but the default invocation runs once so the
  package can be tested without blocking.)
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from pathlib import Path

from qwickguard_agent.config import ServerConfig, load_config

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


async def run_cycle(config: ServerConfig) -> None:
    """Execute a single agent monitoring and remediation cycle.

    This is currently a placeholder.  Subsequent milestones will wire in
    the collectors, analyser, and action executor.

    Args:
        config: Validated server configuration.
    """
    logger.info(
        "Starting cycle for agent_id=%s hostname=%s",
        config.agent_id,
        config.hostname,
    )

    # TODO M3: invoke collectors (system, containers, services, processes)
    # TODO M3: run rule-based analysis
    # TODO M3: execute approved actions
    # TODO M4: escalate to Claude Brain when needed

    logger.info(
        "Completed cycle for agent_id=%s hostname=%s",
        config.agent_id,
        config.hostname,
    )


async def agent_loop(config: ServerConfig, *, run_once: bool = False) -> None:
    """Run the agent in a continuous loop.

    Args:
        config: Validated server configuration.
        run_once: If ``True``, execute exactly one cycle and return.
            Used for testing and the default ``main()`` behaviour during
            the scaffold phase.
    """
    logger.info(
        "Agent loop starting. interval=%ds run_once=%s",
        config.check_interval_seconds,
        run_once,
    )

    while True:
        start = time.monotonic()
        try:
            await run_cycle(config)
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

    During the scaffold phase this runs a single cycle and exits so the
    package can be installed and invoked without hanging.
    """
    config = load_config()
    setup_logging()

    logger.info(
        "QwickGuard agent starting. agent_id=%s hostname=%s brain_url=%s",
        config.agent_id,
        config.hostname,
        config.brain_url,
    )

    asyncio.run(agent_loop(config, run_once=True))

    logger.info("QwickGuard agent finished.")


if __name__ == "__main__":
    main()
