import asyncio
import os
from datetime import timedelta

from app.core.config import get_settings
from app.infrastructure.database import Database
from app.modules.agent_platform.composition import RuntimeCompositionFactory
from app.modules.agent_platform.runtime_worker import RuntimeWorker, TraceRuntimeFailureHandler, RedisRuntimeWakeup
from redis.asyncio import Redis


async def main() -> None:
    settings = get_settings()
    database = Database.from_settings(settings)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    factory = RuntimeCompositionFactory(settings)
    worker = RuntimeWorker(
        sessions=database.session,
        processor_factory=factory.command_processor,
        worker_id=os.getenv("HOSTNAME", "campuspilot-runtime-worker"),
        claim_timeout=timedelta(seconds=settings.agent_runtime_claim_timeout_seconds),
        poll_interval=settings.agent_runtime_poll_seconds,
        failures=TraceRuntimeFailureHandler(),
        wakeup=RedisRuntimeWakeup(redis),
    )
    stop = asyncio.Event()
    try:
        await worker.serve(stop)
    finally:
        await redis.aclose()
        await database.dispose()


if __name__ == "__main__":
    asyncio.run(main())
