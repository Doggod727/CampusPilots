from __future__ import annotations

import asyncio
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.core.config import get_settings
from app.infrastructure.database import Database
from app.modules.ai_knowledge.models import KnowledgeBase
from app.modules.platform.models import AppConfig, User

CONFIGS = (
    ("rag.top_k", "rag", 6, "integer", "向量检索候选数量", True),
    ("rag.score_threshold", "rag", 0.62, "number", "低于该分数时进入兜底", True),
    ("chat.max_history_turns", "chat", 6, "integer", "Prompt最大历史轮数", True),
    ("chat.max_question_chars", "chat", 2000, "integer", "问题最大字符数", True),
    ("ingestion.max_file_mb", "ingestion", 20, "integer", "单文件上限", True),
    ("ingestion.max_files_per_request", "ingestion", 10, "integer", "单次文件上限", True),
    ("ingestion.chunk_size", "ingestion", 500, "integer", "默认切分长度", True),
    ("ingestion.chunk_overlap", "ingestion", 80, "integer", "默认切分重叠", True),
    ("llm.deepseek_thinking", "llm", False, "boolean", "RAG关闭Thinking", False),
)
DEMO_KB_ID = UUID("61000000-0000-4000-8000-000000000001")


async def seed(session) -> None:
    for key, namespace, value, value_type, description, editable in CONFIGS:
        statement = insert(AppConfig).values(key=key, namespace=namespace, value=value, value_type=value_type, description=description, editable=editable)
        await session.execute(statement.on_conflict_do_update(index_elements=[AppConfig.key], set_={"namespace": namespace, "value": value, "value_type": value_type, "description": description, "editable": editable}))
    owner = (await session.execute(select(User).where(User.username == "knowledge01", User.deleted_at.is_(None)))).scalar_one_or_none()
    if owner is not None:
        statement = insert(KnowledgeBase).values(id=DEMO_KB_ID, name="四川大学校园知识库", description="四川大学公开资料演示知识库（学校简介、公告与要闻）", visibility="public", owner_user_id=owner.id, embedding_model="bge-small-zh-v1.5", chunk_size=500, chunk_overlap=80, collection_name="kb_61000000000040008000000000000001", created_by=owner.id)
        await session.execute(statement.on_conflict_do_update(index_elements=[KnowledgeBase.id], set_={"description": "四川大学公开资料演示知识库（学校简介、公告与要闻）", "visibility": "public", "owner_user_id": owner.id, "created_by": owner.id}))


async def main() -> None:
    database = Database.from_settings(get_settings())
    try:
        async with database.session() as session:
            async with session.begin(): await seed(session)
    finally: await database.dispose()
    print("M1 configuration and demo knowledge seed completed")


if __name__ == "__main__": asyncio.run(main())
