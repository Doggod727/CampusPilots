import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

import app.infrastructure.database as database_module
from app.infrastructure.database import Database
from app.main import create_app

TEST_DATABASE_URL = "postgresql+asyncpg://user:password@127.0.0.1:1/campuspilot"


class FakeSession:
    def __init__(self) -> None:
        self.rollback = AsyncMock()
        self.close = AsyncMock()
        self.commit = AsyncMock()


def test_database_creation_is_lazy_and_uses_async_postgresql_driver() -> None:
    database = Database(TEST_DATABASE_URL)

    assert database.engine.url.drivername == "postgresql+asyncpg"
    assert database.engine.url.host == "127.0.0.1"
    assert database.engine.url.port == 1
    assert database.engine.sync_engine.pool.checkedout() == 0

    asyncio.run(database.dispose())


def test_real_session_factory_uses_expected_configuration_without_connecting() -> None:
    database = Database(TEST_DATABASE_URL)

    async def exercise_session() -> None:
        async with database.session() as session:
            assert isinstance(session, AsyncSession)
            assert session.bind is database.engine
            assert session.sync_session.expire_on_commit is False
            assert session.in_transaction() is False
        assert database.engine.sync_engine.pool.checkedout() == 0
        await database.dispose()

    asyncio.run(exercise_session())


def test_session_context_closes_without_automatic_commit() -> None:
    database = Database(TEST_DATABASE_URL)
    fake_session = FakeSession()
    database.session_factory = lambda: fake_session  # type: ignore[assignment]

    async def exercise_session() -> None:
        async with database.session() as yielded_session:
            assert yielded_session is fake_session
        await database.dispose()

    asyncio.run(exercise_session())

    fake_session.commit.assert_not_awaited()
    fake_session.rollback.assert_not_awaited()
    fake_session.close.assert_awaited_once()


def test_session_context_rolls_back_and_closes_on_error() -> None:
    database = Database(TEST_DATABASE_URL)
    fake_session = FakeSession()
    database.session_factory = lambda: fake_session  # type: ignore[assignment]

    async def exercise_session() -> None:
        with pytest.raises(RuntimeError, match="service failed"):
            async with database.session():
                raise RuntimeError("service failed")
        await database.dispose()

    asyncio.run(exercise_session())

    fake_session.commit.assert_not_awaited()
    fake_session.rollback.assert_awaited_once()
    fake_session.close.assert_awaited_once()


def test_settings_are_read_only_when_explicitly_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings_calls = 0

    def fake_get_settings() -> SimpleNamespace:
        nonlocal settings_calls
        settings_calls += 1
        return SimpleNamespace(database_url=TEST_DATABASE_URL)

    monkeypatch.setattr(database_module, "get_settings", fake_get_settings)

    direct_database = Database(TEST_DATABASE_URL)
    assert settings_calls == 0

    configured_database = Database.from_settings()
    assert settings_calls == 1

    asyncio.run(direct_database.dispose())
    asyncio.run(configured_database.dispose())


def test_liveness_does_not_create_database_or_read_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_settings_read() -> None:
        raise AssertionError("liveness must not read database settings")

    monkeypatch.setattr(database_module, "get_settings", unexpected_settings_read)

    response = TestClient(create_app()).get("/health/live")

    assert response.status_code == 200
    assert response.json()["data"] == {"status": "alive"}
