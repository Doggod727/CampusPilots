from app.modules.ai_knowledge.knowledge import KnowledgeBaseNotFound,KnowledgeBaseConflict
def test_m1_knowledge_errors_are_stable():
 assert KnowledgeBaseNotFound().code=="KNOWLEDGE_BASE_NOT_FOUND" and KnowledgeBaseConflict().status_code==409
