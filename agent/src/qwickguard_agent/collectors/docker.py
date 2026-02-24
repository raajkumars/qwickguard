"""Docker container metric collector.

Collects status, health, restart count, CPU usage, and memory usage for all
containers listed in ServerConfig.  Containers present in config but absent
from Docker are reported with status="missing".
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import docker
import docker.errors
from docker import DockerClient

from ..config import ServerConfig
from ..models import ContainerStatus

logger = logging.getLogger(__name__)


def _parse_uptime(started_at: Optional[str]) -> float:
    """Return seconds since container started, or 0.0 if not available.

    Args:
        started_at: ISO-8601 string from container State.StartedAt, e.g.
            "2025-01-15T12:34:56.789012345Z".  Nanoseconds are trimmed to
            microseconds because Python's datetime does not support them.

    Returns:
        Elapsed seconds as a float, or 0.0 on any parse error.
    """
    if not started_at or started_at.startswith("0001-"):
        return 0.0
    try:
        # Trim sub-microsecond precision (nanoseconds) before parsing.
        trimmed = started_at[:26].rstrip("Z") + "+00:00"
        started = datetime.fromisoformat(trimmed)
        now = datetime.now(tz=timezone.utc)
        delta = (now - started).total_seconds()
        return max(delta, 0.0)
    except (ValueError, OverflowError):
        return 0.0


def _collect_cpu_memory(container: docker.models.containers.Container) -> tuple[float, float]:
    """Fetch CPU percent and memory usage from container stats.

    Skips stat collection for non-running containers to avoid blocking calls
    that return immediately with zeroed data anyway.

    Args:
        container: A docker-sdk Container object whose status is "running".

    Returns:
        Tuple of (cpu_percent, memory_mb).  Both are 0.0 on any error.
    """
    try:
        stats = container.stats(stream=False)

        # CPU percent: delta-based calculation per Docker docs.
        cpu_delta = (
            stats["cpu_stats"]["cpu_usage"]["total_usage"]
            - stats["precpu_stats"]["cpu_usage"]["total_usage"]
        )
        system_delta = (
            stats["cpu_stats"]["system_cpu_usage"]
            - stats["precpu_stats"]["system_cpu_usage"]
        )
        num_cpus = stats["cpu_stats"].get(
            "online_cpus",
            len(stats["cpu_stats"]["cpu_usage"].get("percpu_usage", [1])),
        )
        if system_delta > 0 and cpu_delta >= 0:
            cpu_percent = (cpu_delta / system_delta) * num_cpus * 100.0
        else:
            cpu_percent = 0.0

        # Memory in MiB.
        mem_usage = stats.get("memory_stats", {}).get("usage", 0)
        memory_mb = mem_usage / (1024 ** 2)

    except (KeyError, TypeError, docker.errors.APIError) as exc:
        logger.debug("Could not read stats for container: %s", exc)
        cpu_percent = 0.0
        memory_mb = 0.0

    return round(cpu_percent, 2), round(memory_mb, 2)


def _collect_single(
    container: docker.models.containers.Container,
) -> ContainerStatus:
    """Build a ContainerStatus from a live docker Container object.

    Args:
        container: docker-sdk Container with .attrs already populated (i.e.
            after container.reload() or from client.containers.list()).

    Returns:
        ContainerStatus populated from container metadata and stats.
    """
    attrs = container.attrs
    state = attrs.get("State", {})

    # Strip leading "/" from Docker container names.
    raw_name = attrs.get("Name", "")
    name = raw_name.lstrip("/") or container.short_id

    status = state.get("Status", "unknown")

    # Health: present only when a HEALTHCHECK is defined in the image.
    health_info = state.get("Health")
    health = health_info.get("Status", "none") if health_info else "none"

    restart_count: int = attrs.get("RestartCount", 0)

    uptime_seconds = _parse_uptime(state.get("StartedAt"))

    if status == "running":
        cpu_percent, memory_mb = _collect_cpu_memory(container)
    else:
        cpu_percent = 0.0
        memory_mb = 0.0

    return ContainerStatus(
        name=name,
        status=status,
        health=health,
        restart_count=restart_count,
        cpu_percent=cpu_percent,
        memory_mb=memory_mb,
        uptime_seconds=round(uptime_seconds, 1),
    )


def collect_docker_metrics(config: ServerConfig) -> list[ContainerStatus]:
    """Collect runtime status for every container defined in ServerConfig.

    Running and stopped containers are included.  Containers present in config
    but absent from Docker are reported with status="missing".

    Args:
        config: Validated ServerConfig containing the list of containers to
            monitor.

    Returns:
        List of ContainerStatus, one entry per configured container.  An empty
        list is returned (with an error log) if Docker is unavailable.
    """
    if not config.containers:
        return []

    try:
        client: DockerClient = docker.from_env()
    except docker.errors.DockerException as exc:
        logger.error("Cannot connect to Docker daemon: %s", exc)
        return [
            ContainerStatus(
                name=c.name,
                status="error",
                health="none",
                restart_count=0,
                cpu_percent=0.0,
                memory_mb=0.0,
                uptime_seconds=0.0,
            )
            for c in config.containers
        ]

    try:
        # Fetch all containers once (running + stopped) to avoid N round-trips.
        all_containers = client.containers.list(all=True)
        docker_map: dict[str, docker.models.containers.Container] = {
            c.attrs.get("Name", "").lstrip("/"): c for c in all_containers
        }
    except docker.errors.APIError as exc:
        logger.error("Docker API error while listing containers: %s", exc)
        client.close()
        return []
    finally:
        pass  # client closed in the outer finally below

    results: list[ContainerStatus] = []

    for cfg_container in config.containers:
        container = docker_map.get(cfg_container.name)
        if container is None:
            logger.warning("Configured container not found in Docker: %s", cfg_container.name)
            results.append(
                ContainerStatus(
                    name=cfg_container.name,
                    status="missing",
                    health="none",
                    restart_count=0,
                    cpu_percent=0.0,
                    memory_mb=0.0,
                    uptime_seconds=0.0,
                )
            )
            continue

        try:
            results.append(_collect_single(container))
        except docker.errors.DockerException as exc:
            # Container may have stopped between list and inspect.
            logger.warning(
                "Failed to collect metrics for container %s: %s",
                cfg_container.name,
                exc,
            )
            results.append(
                ContainerStatus(
                    name=cfg_container.name,
                    status="error",
                    health="none",
                    restart_count=0,
                    cpu_percent=0.0,
                    memory_mb=0.0,
                    uptime_seconds=0.0,
                )
            )

    client.close()
    return results
