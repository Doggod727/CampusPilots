from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable
from app.modules.ai_knowledge.models import SCHEMA
from app.infrastructure.database import Base

def test_m1_metadata_contains_all_eleven_tables_and_compiles():
    names={table.name for table in Base.metadata.tables.values() if table.schema==SCHEMA}
    assert names=={"knowledge_bases","knowledge_base_members","documents","ingestion_jobs","document_chunks","conversations","messages","retrieval_runs","message_citations","message_feedback","llm_calls"}
    sql="\n".join(str(CreateTable(table).compile(dialect=postgresql.dialect())) for table in Base.metadata.tables.values() if table.schema==SCHEMA)
    assert "JSONB" in sql and "UUID" in sql and "TIMESTAMP WITH TIME ZONE" in sql

def test_sensitive_orm_fields_are_hidden_from_repr():
    from app.modules.ai_knowledge.models import DocumentChunk, RetrievalRun
    assert "secret-content" not in repr(DocumentChunk(content="secret-content"))
    assert "a"*64 not in repr(RetrievalRun(query_sha256="a"*64))
