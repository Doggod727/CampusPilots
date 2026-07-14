import asyncio
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from sqlalchemy.dialects import postgresql

from app.modules.platform.models import ModerationCase
from app.modules.platform.repositories import ModerationCaseRepository


class _Rows:
    def __init__(self, values):
        self.values = values

    def scalars(self):
        return self

    def all(self):
        return self.values

    def scalar_one(self):
        return self.values

    def scalar_one_or_none(self):
        return self.values


def test_moderation_repository_lists_and_compiles_filters() -> None:
    case = ModerationCase(id=uuid4(), target_module="community", target_type="post", target_id=uuid4(),
                          content_excerpt="safe", risk_level="high", rule_hits=[], status="pending", version=1)
    session = MagicMock()
    session.execute = AsyncMock(side_effect=[_Rows(1), _Rows([case])])

    items, total = asyncio.run(ModerationCaseRepository(session).list_page(
        page=2, page_size=5, status="pending", risk_level="high",
        target_module="community", sort="-created_at"
    ))
    assert items == [case]
    assert total == 1
    sql = str(session.execute.call_args_list[1].args[0].compile(
        dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
    ))
    assert "platform.moderation_cases.status = 'pending'" in sql
    assert "platform.moderation_cases.target_module = 'community'" in sql
    assert "OFFSET 5" in sql
    session.commit.assert_not_called()
    session.rollback.assert_not_called()
    session.close.assert_not_called()


def test_moderation_repository_for_update_and_decision_update_compile() -> None:
    case_id = uuid4()
    session = MagicMock()
    session.execute = AsyncMock(side_effect=[_Rows(None), MagicMock(rowcount=1)])
    repository = ModerationCaseRepository(session)
    asyncio.run(repository.get_by_id_for_update(case_id))
    asyncio.run(repository.decide_if_version(
        case_id=case_id, expected_version=1, status="approved", reviewer_id=uuid4(),
        decision_reason="通过", reviewed_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
        updated_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
    ))
    lock_sql = str(session.execute.call_args_list[0].args[0].compile(
        dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
    ))
    update_sql = str(session.execute.call_args_list[1].args[0].compile(
        dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
    ))
    assert "FOR UPDATE" in lock_sql
    assert "status = 'pending'" in update_sql
    assert "version" in update_sql
    session.commit.assert_not_called()
    session.flush.assert_not_called()
    session.rollback.assert_not_called()
