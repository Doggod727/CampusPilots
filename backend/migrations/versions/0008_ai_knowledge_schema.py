"""Create the M1 AI knowledge schema.

Revision ID: 0008_ai_knowledge_schema
Revises: 0007_community_schema
"""
from collections.abc import Iterator
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision = "0008_ai_knowledge_schema"
down_revision = "0007_community_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    source = Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    for statement in _split_sql(source):
        if statement.upper() not in {"BEGIN", "COMMIT"}:
            op.execute(sa.text(statement))


def _split_sql(script: str) -> Iterator[str]:
    """Split PostgreSQL SQL without cutting quoted or dollar-quoted bodies."""
    statement_start = 0
    index = 0
    in_single_quote = False
    in_dollar_quote = False
    while index < len(script):
        if in_dollar_quote:
            if script.startswith("$$", index):
                in_dollar_quote = False
                index += 2
            else:
                index += 1
            continue
        character = script[index]
        if in_single_quote:
            if character == "'":
                if index + 1 < len(script) and script[index + 1] == "'":
                    index += 2
                    continue
                in_single_quote = False
            index += 1
            continue
        if script.startswith("$$", index):
            in_dollar_quote = True
            index += 2
        elif character == "'":
            in_single_quote = True
            index += 1
        elif character == ";":
            statement = script[statement_start:index].strip()
            if statement:
                yield statement
            statement_start = index + 1
            index += 1
        else:
            index += 1
    trailing = script[statement_start:].strip()
    if trailing:
        yield trailing


def downgrade() -> None:
    source = """
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
    """
    for statement in _split_sql(source):
        op.execute(sa.text(statement))
