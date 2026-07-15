from app.main import create_app
def test_knowledge_base_operations_registered():
 ops={getattr(r,"operation_id",None) for r in create_app().routes};assert {"listKnowledgeBases","createKnowledgeBase","getKnowledgeBase","updateKnowledgeBase","deleteKnowledgeBase"}<=ops
 assert {"listDocuments","uploadDocuments","getDocument","deleteDocument","listDocumentChunks","publishDocument","deactivateDocument","getIngestionJob","retryIngestionJob"}<=ops
 assert {"listConversations","createConversation","getConversation","deleteConversation","listConversationMessages","getMessage"}<=ops
 assert {"createChatCompletion","streamChatCompletion"}<=ops
