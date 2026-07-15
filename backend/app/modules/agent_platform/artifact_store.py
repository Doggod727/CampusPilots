from __future__ import annotations

import asyncio
import hashlib
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Protocol
from uuid import uuid4

from app.core.errors import AppError

MAX_DATASET_BYTES = 100 * 1024 * 1024
ALLOWED_FORMATS = {"jsonl", "csv"}


class AsyncUploadPort(Protocol):
    filename: str | None
    async def read(self, size: int = -1) -> bytes: ...


class DatasetArtifactTooLarge(AppError):
    def __init__(self) -> None: super().__init__(status_code=413,code="DATASET_ARTIFACT_TOO_LARGE",message="数据集文件超过大小限制")
class DatasetArtifactUnsupported(AppError):
    def __init__(self) -> None: super().__init__(status_code=415,code="DATASET_ARTIFACT_UNSUPPORTED",message="仅支持 JSONL 或 CSV 数据集")
class DatasetArtifactInvalid(AppError):
    def __init__(self) -> None: super().__init__(status_code=409,code="DATASET_ARTIFACT_INVALID",message="数据集产物无效或已过期")


@dataclass(frozen=True)
class StoredDatasetArtifact:
    artifact_key: str
    artifact_sha256: str
    file_name: str
    format: str
    size_bytes: int
    expires_at: datetime


class DatasetArtifactStore:
    def __init__(self, root: Path, *, ttl_seconds: int = 3600, max_bytes: int = MAX_DATASET_BYTES,
                 now=None) -> None:
        self._root=root; self._ttl=timedelta(seconds=ttl_seconds); self._max=max_bytes
        self._now=now or (lambda:datetime.now(UTC))

    async def store(self, upload: AsyncUploadPort) -> StoredDatasetArtifact:
        filename=Path(upload.filename or "dataset").name
        suffix=Path(filename).suffix.lower().lstrip(".")
        if suffix not in ALLOWED_FORMATS: raise DatasetArtifactUnsupported()
        key=f"quarantine/{uuid4().hex}.{suffix}"; target=self._resolve(key)
        await asyncio.to_thread(target.parent.mkdir,parents=True,exist_ok=True)
        temporary=target.with_suffix(target.suffix+".part"); digest=hashlib.sha256(); size=0
        try:
            with temporary.open("xb") as stream:
                while chunk:=await upload.read(1024*1024):
                    size+=len(chunk)
                    if size>self._max: raise DatasetArtifactTooLarge()
                    digest.update(chunk); stream.write(chunk)
            if size==0: raise DatasetArtifactInvalid()
            await asyncio.to_thread(os.replace,temporary,target)
        except BaseException:
            if temporary.exists(): await asyncio.to_thread(temporary.unlink)
            raise
        now=self._utc()
        return StoredDatasetArtifact(key,digest.hexdigest(),filename,suffix,size,now+self._ttl)

    async def read(self, key: str, *, expected_sha256: str | None = None) -> bytes:
        path=self._resolve(key)
        if not path.is_file() or path.is_symlink() or self._expired(path): raise DatasetArtifactInvalid()
        data=await asyncio.to_thread(path.read_bytes)
        if len(data)>self._max: raise DatasetArtifactTooLarge()
        if expected_sha256 and hashlib.sha256(data).hexdigest()!=expected_sha256: raise DatasetArtifactInvalid()
        return data

    async def delete(self, key: str) -> None:
        path=self._resolve(key)
        if path.is_file() and not path.is_symlink(): await asyncio.to_thread(path.unlink)

    def _resolve(self,key:str)->Path:
        pure=PurePosixPath(key)
        if pure.is_absolute() or ".." in pure.parts or len(pure.parts)!=2 or pure.parts[0]!="quarantine": raise DatasetArtifactInvalid()
        candidate=(self._root/pure.parts[0]/pure.parts[1]).resolve()
        root=self._root.resolve()
        if root not in candidate.parents: raise DatasetArtifactInvalid()
        return candidate

    def _expired(self,path:Path)->bool:
        modified=datetime.fromtimestamp(path.stat().st_mtime,tz=UTC)
        return modified+self._ttl<=self._utc()
    def _utc(self):
        value=self._now(); return value if value.tzinfo else value.replace(tzinfo=UTC)
