import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from app.core.errors import AppError

ALLOWED={"txt","md","docx","pdf"}
class KnowledgeArtifactInvalid(AppError):
    def __init__(self,code="UNSUPPORTED_MEDIA_TYPE",status=415):super().__init__(status_code=status,code=code,message="知识文件无效")
@dataclass(frozen=True)
class StoredArtifact: object_key:str; sha256:str; size_bytes:int; format:str

class KnowledgeArtifactStore:
    def __init__(self,root:Path,max_bytes:int=20*1024*1024):self.root=root;self.max=max_bytes
    async def save(self,stream,filename:str)->StoredArtifact:
        ext=Path(filename).suffix.lower().lstrip(".")
        if ext not in ALLOWED:raise KnowledgeArtifactInvalid()
        self.root.mkdir(parents=True,exist_ok=True)
        key=f"quarantine/{uuid4().hex[:2]}/{uuid4().hex}.{ext}"; target=self._path(key); target.parent.mkdir(parents=True,exist_ok=True); temp=target.with_suffix(target.suffix+".tmp")
        digest=hashlib.sha256();size=0
        try:
            with temp.open("xb") as output:
                while chunk:=await stream.read(1024*1024):
                    size+=len(chunk)
                    if size>self.max:raise KnowledgeArtifactInvalid("PAYLOAD_TOO_LARGE",413)
                    digest.update(chunk);output.write(chunk)
            os.replace(temp,target)
        except BaseException:
            temp.unlink(missing_ok=True);raise
        return StoredArtifact(key,digest.hexdigest(),size,ext)
    def read(self,key:str)->bytes:return self._path(key).read_bytes()
    def delete(self,key:str)->None:self._path(key).unlink(missing_ok=True)
    def _path(self,key:str)->Path:
        candidate=(self.root/key).resolve();root=self.root.resolve()
        if Path(key).is_absolute() or ".." in Path(key).parts or not candidate.is_relative_to(root):raise KnowledgeArtifactInvalid("INVALID_OBJECT_KEY",422)
        if candidate.exists() and candidate.is_symlink():raise KnowledgeArtifactInvalid("INVALID_OBJECT_KEY",422)
        return candidate
