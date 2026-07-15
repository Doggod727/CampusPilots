from app.main import create_app
def test_knowledge_base_operations_registered():
 ops={getattr(r,"operation_id",None) for r in create_app().routes};assert {"listKnowledgeBases","createKnowledgeBase","getKnowledgeBase","updateKnowledgeBase","deleteKnowledgeBase"}<=ops
