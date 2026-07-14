import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from sqlalchemy.dialects import postgresql

from app.modules.platform.models import SensitiveWord
from app.modules.platform.repositories import SensitiveWordRepository

NOW = datetime(2026, 7, 14, 8, 0, tzinfo=UTC)


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


def _rule() -> SensitiveWord:
    return SensitiveWord(
        id=uuid4(), word="秘密", match_type="contains", action="mask",
        replacement="***", scope="user_input", enabled=True,
        created_at=NOW, updated_at=NOW,
    )


def test_sensitive_word_repository_lists_page_and_compiles_filters() -> None:
    rule = _rule()
    session = MagicMock()
    session.execute = AsyncMock(side_effect=[_Rows(1), _Rows([rule])])

    items, total = asyncio.run(
        SensitiveWordRepository(session).list_page(
            page=2, page_size=10, query="秘", scope="user_input", enabled=True
        )
    )

    assert items == [rule]
    assert total == 1
    sql = str(session.execute.call_args_list[1].args[0].compile(
        dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
    ))
    assert "platform.sensitive_words.scope = 'user_input'" in sql
    assert "platform.sensitive_words.enabled IS true" in sql
    assert "OFFSET 10" in sql
    session.commit.assert_not_called()
    session.rollback.assert_not_called()
    session.close.assert_not_called()


def test_sensitive_word_repository_rule_lookup_add_and_delete() -> None:
    rule = _rule()
    session = MagicMock()
    session.execute = AsyncMock(side_effect=[_Rows(rule), MagicMock(rowcount=1)])
    repository = SensitiveWordRepository(session)

    found = asyncio.run(
        repository.get_by_rule(word="秘密", match_type="contains", scope="user_input")
    )
    repository.add(rule)
    deleted = asyncio.run(repository.delete(rule.id))

    assert found is rule
    assert deleted is True
    session.add.assert_called_once_with(rule)
    session.commit.assert_not_called()
    session.flush.assert_not_called()
    session.rollback.assert_not_called()
    session.close.assert_not_called()
