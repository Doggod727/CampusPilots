import asyncio

from app.core.config import get_settings
from app.infrastructure.database import Database
from app.modules.agent_platform.evaluation_worker import EvaluationWorker, build_production_evaluator_registry


async def main() -> None:
    settings = get_settings()
    database = Database.from_settings(settings)
    worker = EvaluationWorker(database.session, build_production_evaluator_registry(settings))
    try:
        while True:
            processed = await worker.run_once()
            if processed is None:
                await asyncio.sleep(settings.agent_runtime_poll_seconds)
    finally:
        await database.dispose()


if __name__ == "__main__":
    asyncio.run(main())
