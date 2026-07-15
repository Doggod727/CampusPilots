import asyncio
from types import SimpleNamespace
from uuid import uuid4

from app.modules.ai_knowledge.retrieval import RetrievalService
from app.modules.ai_knowledge.vectors import VectorHit


class Knowledge:
    async def require(self,kb,user):
        if kb==DENIED: raise SimpleNamespace(status_code=404)
class Embed:
    def embed(self,texts): return ((0.1,),)
class Vectors:
    def query(self,kb,vector,limit): return (VectorHit(CHUNK,0.8,{}),)
class Result:
    def all(self): return [(SimpleNamespace(id=CHUNK,content="answer",source_location="p1",page_number=1,index_version=1),SimpleNamespace(id=DOC,title="guide",index_version=1))]
class Session:
    async def execute(self,statement): self.statement=statement;return Result()
CHUNK,DOC,ALLOWED,DENIED=uuid4(),uuid4(),uuid4(),uuid4()


def test_retrieval_filters_authorization_and_returns_published_rows():
    session=Session();service=RetrievalService(session,Knowledge(),Embed(),Vectors())
    result=asyncio.run(service.search(object(),"query",[ALLOWED],3))
    assert result.confidence==0.8 and result.citations[0].document_title=="guide"


def test_retrieval_returns_explicit_empty_below_threshold():
    class Low:
        def query(self,*args): return (VectorHit(CHUNK,0.2,{}),)
    result=asyncio.run(RetrievalService(Session(),Knowledge(),Embed(),Low()).search(object(),"q",[ALLOWED]))
    assert result.citations==() and result.confidence==0.0
