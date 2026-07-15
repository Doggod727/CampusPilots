"""Create the M1 AI knowledge schema.

Revision ID: 0007_ai_knowledge_schema
Revises: 0006_agent_runtime_delivery
"""
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision = "0007_ai_knowledge_schema"
down_revision = "0006_agent_runtime_delivery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    source = Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    source = source.removeprefix("BEGIN;").removesuffix("COMMIT;\n")
    op.execute(sa.text(source))


def downgrade() -> None:
    op.execute(sa.text("""
        DROP TRIGGER IF EXISTS trg_message_feedback_updated_at ON ai_knowledge.message_feedback;
        DROP TRIGGER IF EXISTS trg_messages_updated_at ON ai_knowledge.messages;
        DROP TRIGGER IF EXISTS trg_conversations_updated_at ON ai_knowledge.conversations;
        DROP TRIGGER IF EXISTS trg_ingestion_jobs_updated_at ON ai_knowledge.ingestion_jobs;
        DROP TRIGGER IF EXISTS trg_documents_updated_at ON ai_knowledge.documents;
        DROP TRIGGER IF EXISTS trg_knowledge_bases_updated_at ON ai_knowledge.knowledge_bases;
        DROP TABLE IF EXISTS ai_knowledge.llm_calls;
        DROP TABLE IF EXISTS ai_knowledge.message_feedback;
        DROP TABLE IF EXISTS ai_knowledge.message_citations;
        DROP TABLE IF EXISTS ai_knowledge.retrieval_runs;
        DROP TABLE IF EXISTS ai_knowledge.document_chunks;
        DROP TABLE IF EXISTS ai_knowledge.ingestion_jobs;
        DROP TABLE IF EXISTS ai_knowledge.messages;
        DROP TABLE IF EXISTS ai_knowledge.conversations;
        DROP TABLE IF EXISTS ai_knowledge.documents;
        DROP TABLE IF EXISTS ai_knowledge.knowledge_base_members;
        DROP TABLE IF EXISTS ai_knowledge.knowledge_bases;
        DROP FUNCTION IF EXISTS ai_knowledge.set_updated_at();
        DROP SCHEMA IF EXISTS ai_knowledge;
    """))
