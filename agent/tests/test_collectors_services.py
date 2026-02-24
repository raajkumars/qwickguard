"""Tests for the HTTP service health collector."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from qwickguard_agent.collectors.services import collect_service_health
from qwickguard_agent.config import ServerConfig, ServiceConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _config_with_services(*services: tuple[str, str]) -> ServerConfig:
    """Build a minimal ServerConfig with the given (name, url) service pairs."""
    return ServerConfig(
        hostname="test-host",
        agent_id="test-agent",
        brain_url="http://brain:8000",
        compute_worker_url="http://worker:8001",
        services=[ServiceConfig(name=name, url=url) for name, url in services],
    )


def _mock_response(status_code: int) -> MagicMock:
    """Create a mock httpx.Response with the given status code."""
    response = MagicMock(spec=httpx.Response)
    response.status_code = status_code
    return response


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestHealthyService:
    """A service returning HTTP 200 is healthy with a positive response time."""

    @pytest.mark.asyncio
    async def test_healthy_service(self, mocker: "pytest_mock.MockerFixture") -> None:
        config = _config_with_services(("api", "http://localhost:8000/health"))

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=_mock_response(200))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        mocker.patch("httpx.AsyncClient", return_value=mock_client)

        results = await collect_service_health(config)

        assert len(results) == 1
        svc = results[0]
        assert svc.name == "api"
        assert svc.url == "http://localhost:8000/health"
        assert svc.healthy is True
        assert svc.response_time_ms >= 0
        assert svc.error is None


class TestUnhealthyService:
    """A service returning a non-200 status code is not healthy."""

    @pytest.mark.asyncio
    async def test_unhealthy_service_500(self, mocker: "pytest_mock.MockerFixture") -> None:
        config = _config_with_services(("broken", "http://localhost:9000/health"))

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=_mock_response(500))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        mocker.patch("httpx.AsyncClient", return_value=mock_client)

        results = await collect_service_health(config)

        assert len(results) == 1
        svc = results[0]
        assert svc.healthy is False
        assert svc.error is not None
        assert "500" in svc.error

    @pytest.mark.asyncio
    async def test_unhealthy_service_503(self, mocker: "pytest_mock.MockerFixture") -> None:
        config = _config_with_services(("gateway", "http://localhost:3300/health"))

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=_mock_response(503))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        mocker.patch("httpx.AsyncClient", return_value=mock_client)

        results = await collect_service_health(config)

        svc = results[0]
        assert svc.healthy is False
        assert "503" in svc.error


class TestUnreachableService:
    """Connection errors produce healthy=False with the exception message."""

    @pytest.mark.asyncio
    async def test_connect_error(self, mocker: "pytest_mock.MockerFixture") -> None:
        config = _config_with_services(("offline", "http://localhost:19999/health"))

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=httpx.ConnectError("Connection refused")
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        mocker.patch("httpx.AsyncClient", return_value=mock_client)

        results = await collect_service_health(config)

        assert len(results) == 1
        svc = results[0]
        assert svc.healthy is False
        assert svc.error is not None
        assert "Connection refused" in svc.error
        assert svc.response_time_ms >= 0


class TestTimeoutService:
    """A service that times out is reported as unhealthy."""

    @pytest.mark.asyncio
    async def test_timeout_exception(self, mocker: "pytest_mock.MockerFixture") -> None:
        config = _config_with_services(("slow", "http://localhost:8080/health"))

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=httpx.TimeoutException("Request timed out")
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        mocker.patch("httpx.AsyncClient", return_value=mock_client)

        results = await collect_service_health(config)

        assert len(results) == 1
        svc = results[0]
        assert svc.healthy is False
        assert svc.error is not None
        assert "timed out" in svc.error.lower() or "timeout" in svc.error.lower()


class TestEmptyServicesConfig:
    """No services configured returns an empty list without making requests."""

    @pytest.mark.asyncio
    async def test_empty_services(self, mocker: "pytest_mock.MockerFixture") -> None:
        config = ServerConfig(
            hostname="test-host",
            agent_id="test-agent",
            brain_url="http://brain:8000",
            compute_worker_url="http://worker:8001",
        )

        mock_async_client = mocker.patch("httpx.AsyncClient")

        results = await collect_service_health(config)

        assert results == []
        mock_async_client.assert_not_called()


class TestMultipleServices:
    """Multiple services produce one result each with independent error handling."""

    @pytest.mark.asyncio
    async def test_mixed_health(self, mocker: "pytest_mock.MockerFixture") -> None:
        config = _config_with_services(
            ("healthy-svc", "http://localhost:8000/health"),
            ("broken-svc", "http://localhost:9000/health"),
        )

        responses = [_mock_response(200), _mock_response(500)]

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=responses)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        mocker.patch("httpx.AsyncClient", return_value=mock_client)

        results = await collect_service_health(config)

        assert len(results) == 2
        assert results[0].healthy is True
        assert results[1].healthy is False
