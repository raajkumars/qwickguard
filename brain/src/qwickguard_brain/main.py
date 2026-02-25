"""QwickGuard Brain Service entry point.

Wires together:
- FastAPI application with health, agent, and dashboard routers
- SQLite database initialisation via storage.init_db
- Background heartbeat checker via registry.heartbeat_checker
- Daily data cleanup via storage.cleanup_old_data
- Daily digest generation via digest.schedule_daily_digest
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .config import settings
from .storage import cleanup_old_data, init_db
from .api.health import router as health_router
from .api.agents import router as agents_router, compat_router as agents_compat_router
from .api.dashboard import router as dashboard_router
from .registry import heartbeat_checker
from .digest import schedule_daily_digest

logger = logging.getLogger("qwickguard.brain")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    await init_db(settings.database_path)

    async def daily_cleanup() -> None:
        while True:
            await asyncio.sleep(86400)
            await cleanup_old_data(settings.data_retention_days)

    cleanup_task = asyncio.create_task(daily_cleanup())
    heartbeat_task = asyncio.create_task(
        heartbeat_checker(timeout_minutes=settings.heartbeat_timeout_minutes)
    )
    digest_task = asyncio.create_task(schedule_daily_digest())
    yield
    cleanup_task.cancel()
    heartbeat_task.cancel()
    digest_task.cancel()


app = FastAPI(
    title="QwickGuard Brain",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(health_router)
app.include_router(agents_router)
app.include_router(agents_compat_router)
app.include_router(dashboard_router)
