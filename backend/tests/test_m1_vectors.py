from uuid import uuid4

from app.modules.ai_knowledge.vectors import ChromaVectorStore, VectorItem, collection_name


class Collection:
    def __init__(self): self.calls=[]
    def upsert(self, **kwargs): self.calls.append(("upsert",kwargs))
    def query(self, **kwargs): return {"ids":[[str(ID)]],"distances":[[0.2]],"metadatas":[[{"document_id":"d"}]]}
    def delete(self, **kwargs): self.calls.append(("delete",kwargs))
class Client:
    def __init__(self): self.collection=Collection();self.deleted=[]
    def get_or_create_collection(self,name): self.name=name;return self.collection
    def delete_collection(self,name): self.deleted.append(name)
ID=uuid4()


def test_chroma_store_uses_stable_collection_and_safe_metadata():
    kb=uuid4();client=Client();store=ChromaVectorStore(client)
    item=VectorItem(ID,"text",(0.1,0.2),{"document_id":"d"})
    store.upsert(kb,[item])
    assert client.name==collection_name(kb) and client.name.startswith("kb_")
    assert store.query(kb,[0.1,0.2],3)[0].score==0.8
    store.delete_document(kb,uuid4())
    store.rebuild(kb,[item])
    assert client.deleted==[collection_name(kb)]
