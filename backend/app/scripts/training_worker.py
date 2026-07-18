import asyncio
from pathlib import Path

from app.core.config import get_settings
from app.infrastructure.database import Database
from app.modules.agent_platform.artifact_store import DatasetArtifactStore
from app.modules.agent_platform.training_worker import LoraTrainingBackend, TrainingWorker

# 数据集产物是版本化持久引用，训练读取不适用上传隔离 TTL（上传 TTL 仅约束隔离上传窗口）。
PERSISTENT_DATASET_TTL_SECONDS = 10 * 365 * 86400


async def main() -> None:
    settings = get_settings()
    if settings.modelops_execution_mode != "local":
        print("training worker disabled: MODELOPS_EXECUTION_MODE is not local", flush=True)
        return
    database = Database.from_settings(settings)
    model_root = Path(settings.model_artifact_root)
    worker = TrainingWorker(
        database.session,
        LoraTrainingBackend(model_root),
        DatasetArtifactStore(
            Path(settings.dataset_artifact_root), ttl_seconds=PERSISTENT_DATASET_TTL_SECONDS
        ),
        model_root,
        poll_interval=settings.agent_runtime_poll_seconds,
    )
    try:
        while True:
            processed = await worker.run_once()
            if processed is None:
                await asyncio.sleep(settings.agent_runtime_poll_seconds)
    finally:
        await database.dispose()


if __name__ == "__main__":
    asyncio.run(main())
