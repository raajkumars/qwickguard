"""Service health collector using HTTP health checks.

Performs HTTP GET requests against each configured service endpoint and
records the response time, status code, and any connection errors.
"""

from __future__ import annotations

import time

import httpx

from ..config import ServerConfig
from ..models import ServiceHealth


async def collect_service_health(config: ServerConfig) -> list[ServiceHealth]:
    """Perform HTTP health checks for all configured service endpoints.

    For each service in ``config.services``, sends a GET request and records
    whether the response is healthy (HTTP 200), the round-trip response time,
    and any error message when the request fails.

    Args:
        config: Server configuration containing the list of service endpoints.

    Returns:
        A list of :class:`ServiceHealth` instances, one per configured service.
        Returns an empty list if no services are configured.

    Notes:
        - Healthy is defined as ``status_code == 200``.
        - Any exception (connection refused, timeout, DNS failure, etc.) is
          caught and reported as ``healthy=False`` with the exception message
          stored in ``error``.
        - Response time is measured in milliseconds using ``time.perf_counter``.
    """
    if not config.services:
        return []

    results: list[ServiceHealth] = []

    async with httpx.AsyncClient(timeout=5.0) as client:
        for service in config.services:
            start = time.perf_counter()
            try:
                response = await client.get(service.url)
                elapsed_ms = (time.perf_counter() - start) * 1000.0
                healthy = response.status_code == 200
                error: str | None = (
                    None
                    if healthy
                    else f"HTTP {response.status_code}"
                )
                results.append(
                    ServiceHealth(
                        name=service.name,
                        url=service.url,
                        healthy=healthy,
                        response_time_ms=round(elapsed_ms, 2),
                        error=error,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                elapsed_ms = (time.perf_counter() - start) * 1000.0
                results.append(
                    ServiceHealth(
                        name=service.name,
                        url=service.url,
                        healthy=False,
                        response_time_ms=round(elapsed_ms, 2),
                        error=str(exc),
                    )
                )

    return results
