from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from app.modules.ai_knowledge.models import MessageCitation


def append_citations(session, message_id, citations) -> tuple[MessageCitation, ...]:
    entities = tuple(MessageCitation(id=uuid4(), message_id=message_id, citation_no=index + 1, chunk_id=item.chunk_id, document_id=item.document_id, document_title=item.document_title, source_location=item.source_location, page_number=item.page_number, quote_excerpt=item.content[:500], relevance_score=Decimal(str(item.score))) for index, item in enumerate(citations))
    session.add_all(entities)
    return entities
