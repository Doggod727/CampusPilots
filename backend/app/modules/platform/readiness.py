from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from time import perf_counter
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict
from redis.asyncio import Redis
from sqlalchemy import text

from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.infrastructure.database import Database
from app.shared.responses import SuccessResponse

router = APIRouter(prefix="/health", tags=["Health"])


class DependencyStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["up", "down"]
    latency_ms: int
    message: str | None = None


class ReadinessDependencies(BaseModel):
    model_config = ConfigDict(extra="forbid")

    postgres: DependencyStatus
    redis: DependencyStatus
    chroma: DependencyStatus


class ReadinessData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ready", "not_ready"]
    dependencies: ReadinessDependencies


Probe = Callable[[], Awaitable[DependencyStatus]]


def _status(start: float, *, up: bool, message: str | None = None) -> DependencyStatus:
    return DependencyStatus(
        status="up" if up else "down",
        latency_ms=max(0, int((perf_counter() - start) * 1000)),
        message=message,
    )


async def probe_postgres(settings: Settings) -> DependencyStatus:
    started = perf_counter()
    database: Database | None = None
    try:
        database = Database.from_settings(settings)
        async with database.session() as session:
            await session.execute(text("SELECT 1"))
        return _status(started, up=True)
    except Exception:
        return _status(started, up=False, message="postgres unavailable")
    finally:
        if database is not None:
            await database.dispose()


async def probe_redis(settings: Settings) -> DependencyStatus:
    started = perf_counter()
    client: Redis | None = None
    try:
        client = Redis.from_url(settings.redis_url)
        await client.ping()
        return _status(started, up=True)
    except Exception:
        return _status(started, up=False, message="redis unavailable")
    finally:
        if client is not None:
            await client.aclose()


async def probe_chroma() -> DependencyStatus:
    """Report Chroma as not configured until the M1 adapter is available.

    This keeps readiness deterministic for the current backend-only delivery while
    preserving the OpenAPI dependency shape. A future adapter can replace this
    probe without changing the endpoint contract.
    """
    started = perf_counter()
    if os.getenv("CHROMA_URL"):
        return _status(started, up=False, message="chroma probe unavailable")
    return _status(started, up=True, message="not configured")


async def check_readiness(
    settings: Settings,
    *,
    postgres: Callable[[Settings], Awaitable[DependencyStatus]] = probe_postgres,
    redis: Callable[[Settings], Awaitable[DependencyStatus]] = probe_redis,
    chroma: Probe = probe_chroma,
) -> ReadinessData:
    postgres_status, redis_status, chroma_status = await asyncio.gather(
        postgres(settings), redis(settings), chroma()
    )
    dependencies = ReadinessDependencies(
        postgres=postgres_status,
        redis=redis_status,
        chroma=chroma_status,
    )
    return ReadinessData(
        status=(
            "ready"
            if all(
                item.status == "up"
                for item in (
                    dependencies.postgres,
                    dependencies.redis,
                    dependencies.chroma,
                )
            )
            else "not_ready"
        ),
        dependencies=dependencies,
    )


@router.get(
    "/ready",
    operation_id="getReadiness",
    response_model=SuccessResponse[ReadinessData],
)
async def get_readiness(request: Request) -> SuccessResponse[ReadinessData]:
    try:
        settings = get_settings()
    except Exception as exc:
        raise AppError(
            status_code=503,
            code="SERVICE_NOT_READY",
            message="关键服务尚未就绪",
        ) from exc
    data = await check_readiness(settings)
    if data.status != "ready":
        raise AppError(
            status_code=503,
            code="SERVICE_NOT_READY",
            message="关键服务尚未就绪",
        )
    return SuccessResponse(
        data=data,
        request_id=request.state.request_id,
        timestamp=datetime.now(UTC),
    )
