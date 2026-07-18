import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.modules.agent_platform.dataset_routes import DatasetApiService
from app.modules.agent_platform.datasets import DatasetNotFound
from app.modules.platform.idempotency import IdempotencyDecision

NOW = datetime(2026, 7, 18, tzinfo=UTC)
DID = uuid4()


class StrictSession:
    """模拟真实会话：裸读占用事务（autobegin），事务未结束时重复 begin 即失败。"""

    def __init__(self) -> None:
        self.open = False

    def autobegin(self) -> None:
        self.open = True

    def begin(self):
        if self.open:
            raise AssertionError("transaction already begun on this session")
        return _Begin(self)


class _Begin:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        self.session.open = True
        return self.session

    async def __aexit__(self, *_args):
        self.session.open = False
        return False


def build(detail_error: Exception | None = None):
    session = StrictSession()
    calls: list[str] = []
    core = MagicMock()

    async def detail(_dataset_id):
        session.autobegin()
        calls.append("detail")
        if detail_error is not None:
            raise detail_error
        return SimpleNamespace(id=DID)

    core.detail = detail
    artifact = SimpleNamespace(
        artifact_key="quarantine/x.jsonl", artifact_sha256="a" * 64,
        file_name="x.jsonl", format="jsonl", size_bytes=3, expires_at=NOW,
    )
    store = MagicMock()
    store.store = AsyncMock(return_value=artifact)
    store.delete = AsyncMock()
    idem = MagicMock()
    idem.begin = AsyncMock(return_value=IdempotencyDecision(record_id=uuid4()))
    idem.complete = AsyncMock(return_value=True)
    service = DatasetApiService(session, core, store, idem, MagicMock(), now=lambda: NOW)
    return service, store, calls


def test_upload_checks_dataset_inside_mutation_transaction():
    service, store, calls = build()
    status, body, _rid = asyncio.run(service.upload(MagicMock(user_id=uuid4(), username="model01"), "key-1", "req-1", DID, MagicMock()))
    assert status == 201
    assert calls == ["detail"]
    assert body["data"]["artifact_key"] == "quarantine/x.jsonl"
    store.delete.assert_not_called()


def test_upload_deletes_artifact_when_dataset_missing():
    service, store, _calls = build(DatasetNotFound())
    with pytest.raises(DatasetNotFound):
        asyncio.run(service.upload(MagicMock(user_id=uuid4(), username="model01"), "key-2", "req-2", DID, MagicMock()))
    store.delete.assert_awaited_once_with("quarantine/x.jsonl")
