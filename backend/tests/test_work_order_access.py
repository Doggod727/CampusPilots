import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

from sqlalchemy.dialects import postgresql

from app.modules.campus_service.work_order_access import (
    WORK_ORDER_SCOPES_KEY,
    WorkOrderScopeRepository,
    parse_work_order_scopes,
)

USER_ID = UUID("90000000-0000-4000-8000-000000000003")


def _config():
    return {
        "users": {
            str(USER_ID): [
                {"campus_code": "main", "dormitory_areas": ["梅园", "竹园", "梅园"]}
            ]
        }
    }


def test_scope_parser_accepts_uuid_mapping_and_normalizes_areas() -> None:
    scopes = parse_work_order_scopes(_config(), USER_ID)
    assert len(scopes) == 1
    assert scopes[0].campus_code == "main"
    assert scopes[0].dormitory_areas == ("梅园", "竹园")


def test_scope_parser_fails_closed_for_missing_user_or_any_malformed_config() -> None:
    assert parse_work_order_scopes(_config(), UUID(int=9)) == ()
    assert parse_work_order_scopes({"users": {str(USER_ID): [{"campus_code": "main", "dormitory_areas": []}]}}, USER_ID) == ()
    assert parse_work_order_scopes({"users": {"not-a-uuid": []}}, USER_ID) == ()
    assert parse_work_order_scopes({"users": {}, "extra": True}, USER_ID) == ()


def test_scope_repository_requires_json_config_and_does_not_manage_session() -> None:
    session = MagicMock()
    result = MagicMock()
    result.one_or_none.return_value = SimpleNamespace(value=_config(), value_type="json")
    session.execute = AsyncMock(return_value=result)
    session.commit = AsyncMock()
    repository = WorkOrderScopeRepository(session)

    scopes = asyncio.run(repository.get_for_user(USER_ID))

    assert scopes[0].campus_code == "main"
    sql = str(
        session.execute.await_args.args[0].compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert WORK_ORDER_SCOPES_KEY in sql
    session.commit.assert_not_awaited()
