from types import SimpleNamespace
from uuid import uuid4

from app.modules.ai_knowledge.citations import append_citations


class Session:
    def add_all(self,items): self.items=items


def test_citations_are_numbered_and_excerpt_is_bounded():
    session=Session();message=uuid4()
    item=SimpleNamespace(chunk_id=uuid4(),document_id=uuid4(),document_title="guide",source_location="p1",page_number=1,content="x"*600,score=0.8)
    values=append_citations(session,message,[item])
    assert values[0].citation_no==1 and len(values[0].quote_excerpt)==500
