import asyncio, hashlib, os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock

import pytest
from app.modules.agent_platform.artifact_store import DatasetArtifactInvalid, DatasetArtifactStore, DatasetArtifactTooLarge, DatasetArtifactUnsupported

NOW=datetime(2026,7,15,tzinfo=UTC)
class Upload:
    def __init__(self,name,data): self.filename=name; self.data=data; self.done=False
    async def read(self,size=-1):
        if self.done:return b""
        self.done=True; return self.data

def test_store_uses_generated_key_hash_and_does_not_create_root_on_init():
    with TemporaryDirectory() as tmp:
        root=Path(tmp)/"data"; store=DatasetArtifactStore(root,now=lambda:NOW)
        assert not root.exists(); item=asyncio.run(store.store(Upload("../../private.jsonl",b'{"text":"ok"}\n')))
        assert item.artifact_key.startswith("quarantine/") and "private" not in item.artifact_key
        assert item.artifact_sha256==hashlib.sha256(b'{"text":"ok"}\n').hexdigest()
        assert asyncio.run(store.read(item.artifact_key,expected_sha256=item.artifact_sha256)).startswith(b"{")

def test_rejects_format_size_traversal_hash_and_expiry():
    with TemporaryDirectory() as tmp:
        root=Path(tmp); store=DatasetArtifactStore(root,max_bytes=3,ttl_seconds=10,now=lambda:NOW)
        with pytest.raises(DatasetArtifactUnsupported): asyncio.run(store.store(Upload("x.txt",b"x")))
        with pytest.raises(DatasetArtifactTooLarge): asyncio.run(store.store(Upload("x.csv",b"1234")))
        with pytest.raises(DatasetArtifactInvalid): asyncio.run(store.read("../secret.csv"))
        store=DatasetArtifactStore(root,ttl_seconds=10,now=lambda:NOW); item=asyncio.run(store.store(Upload("x.csv",b"a,b\n1,2\n")))
        with pytest.raises(DatasetArtifactInvalid): asyncio.run(store.read(item.artifact_key,expected_sha256="0"*64))
        path=root/item.artifact_key; os.utime(path,(NOW.timestamp()-20,NOW.timestamp()-20))
        with pytest.raises(DatasetArtifactInvalid): asyncio.run(store.read(item.artifact_key))

def test_delete_is_idempotent_and_symlink_is_rejected():
    with TemporaryDirectory() as tmp:
        store=DatasetArtifactStore(Path(tmp),now=lambda:NOW); item=asyncio.run(store.store(Upload("x.jsonl",b'{}\n')))
        asyncio.run(store.delete(item.artifact_key)); asyncio.run(store.delete(item.artifact_key))
        assert not (Path(tmp)/item.artifact_key).exists()
