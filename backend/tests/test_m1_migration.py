from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_m1_revision_chain_and_schema_objects_are_frozen():
    revision = (ROOT / "migrations" / "versions" / "0008_ai_knowledge_schema.py").read_text(encoding="utf-8")
    sql = (ROOT / "migrations" / "versions" / "0008_ai_knowledge_schema.sql").read_text(encoding="utf-8")
    assert 'revision = "0008_ai_knowledge_schema"' in revision
    assert 'down_revision = "0007_community_schema"' in revision
    assert sql.count("CREATE TABLE IF NOT EXISTS ai_knowledge.") == 11
    for name in ("knowledge_bases", "documents", "ingestion_jobs", "document_chunks", "conversations", "messages", "retrieval_runs", "message_citations", "message_feedback", "llm_calls"):
        assert f"ai_knowledge.{name}" in sql
    assert sql.count("CREATE TRIGGER") == 6
    assert "for statement in _split_sql(source)" in revision
    assert "op.execute(sa.text(source))" not in revision
    assert "op.execute(sa.text(\"\"\"" not in revision


def test_m1_downgrade_is_reverse_order_and_preserves_shared_extension():
    revision = (ROOT / "migrations" / "versions" / "0008_ai_knowledge_schema.py").read_text(encoding="utf-8")
    start = revision.index("def downgrade")
    assert revision.index("DROP TABLE IF EXISTS ai_knowledge.llm_calls", start) < revision.index("DROP TABLE IF EXISTS ai_knowledge.knowledge_bases", start)
    assert "DROP EXTENSION" not in revision
