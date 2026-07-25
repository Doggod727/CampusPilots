from __future__ import annotations

import asyncio

from app.core.config import get_settings
from app.infrastructure.database import Database
from app.modules.ai_knowledge.artifact_store import KnowledgeArtifactStore
from app.modules.ai_knowledge.ingestion import IngestionRepository, IngestionWorker
from app.modules.ai_knowledge.vectors import BgeSmallZhEmbeddingProvider, create_vector_store


async def main() -> None:
    settings = get_settings(); database = Database.from_settings(settings); processed = 0
    try:
        async with database.session() as session:
            worker = IngestionWorker(IngestionRepository(session), KnowledgeArtifactStore(settings.knowledge_upload_root, settings.knowledge_max_file_bytes), BgeSmallZhEmbeddingProvider(str(settings.knowledge_embedding_model_path)), create_vector_store(settings))
            while True:
                async with session.begin(): changed = await worker.run_once()
                if not changed: break
                processed += 1
    finally: await database.dispose()
    print(f"M1 ingestion worker drained {processed} task(s)")


if __name__ == "__main__": asyncio.run(main())
