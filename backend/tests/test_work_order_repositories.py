import asyncio
from datetime import date
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.modules.campus_service.models import WorkOrder, WorkOrderEvent
from app.modules.campus_service.repositories import (
    ElectricityRepository,
    WorkOrderEventRepository,
    WorkOrderRepository,
)
from app.modules.campus_service.work_order_access import WorkOrderScope
from app.modules.campus_service.work_order_errors import WorkOrderNumberExhausted

WORK_ORDER_ID = UUID("70000000-0000-4000-8000-000000000001")


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalar_one(self):
        return self._value


class _Scalars:
    def __init__(self, values):
        self._values = values

    def all(self):
        return self._values


class _RowsResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return _Scalars(self._values)

    def all(self):
        return self._values

    def one_or_none(self):
        return self._values[0] if self._values else None


def _session(*results):
    session = MagicMock()
    session.execute = AsyncMock(side_effect=results)
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.close = AsyncMock()
    return session


def _sql(statement) -> str:
    return str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


def test_number_allocator_locks_before_reading_and_returns_first_number() -> None:
    session = _session(MagicMock(), _ScalarResult(None))
    repository = WorkOrderRepository(session)

    result = asyncio.run(repository.allocate_order_no(date(2026, 7, 16)))

    assert result == "WO-20260716-0001"
    assert session.execute.await_count == 2
    lock_sql = _sql(session.execute.await_args_list[0].args[0])
    number_sql = _sql(session.execute.await_args_list[1].args[0])
    assert "pg_advisory_xact_lock(hashtext('work_order_number:20260716'))" in lock_sql
    assert "work_orders.order_no ~ '^WO-20260716-[0-9]{4}$'" in number_sql
    assert "ORDER BY campus_service.work_orders.order_no DESC" in number_sql
    assert "LIMIT 1" in number_sql


def test_number_allocator_increments_fixed_width_sequence_and_is_daily() -> None:
    first_session = _session(MagicMock(), _ScalarResult("WO-20260716-0042"))
    second_session = _session(MagicMock(), _ScalarResult(None))

    first = asyncio.run(
        WorkOrderRepository(first_session).allocate_order_no(date(2026, 7, 16))
    )
    second = asyncio.run(
        WorkOrderRepository(second_session).allocate_order_no(date(2026, 7, 17))
    )

    assert first == "WO-20260716-0043"
    assert second == "WO-20260717-0001"
    assert "20260717" in _sql(second_session.execute.await_args_list[1].args[0])


def test_number_allocator_ignores_malformed_numbers_in_sql_and_fails_at_capacity() -> None:
    session = _session(MagicMock(), _ScalarResult("WO-20260716-9999"))

    with pytest.raises(WorkOrderNumberExhausted) as exc_info:
        asyncio.run(
            WorkOrderRepository(session).allocate_order_no(date(2026, 7, 16))
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.code == "WORK_ORDER_NUMBER_EXHAUSTED"
    statement_sql = _sql(session.execute.await_args_list[1].args[0])
    assert "^WO-20260716-[0-9]{4}$" in statement_sql


def test_work_order_and_event_writes_keep_session_ownership_with_caller() -> None:
    session = _session()
    work_order = MagicMock(spec=WorkOrder)
    event = MagicMock(spec=WorkOrderEvent)

    WorkOrderRepository(session).add(work_order)
    WorkOrderEventRepository(session).append(event)

    assert session.add.call_args_list[0].args == (work_order,)
    assert session.add.call_args_list[1].args == (event,)
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()
    session.close.assert_not_awaited()


def test_event_timeline_is_filtered_and_stably_ordered_without_transaction_control() -> None:
    event_two = WorkOrderEvent(
        id=uuid4(),
        work_order_id=WORK_ORDER_ID,
        sequence_no=2,
        event_type="accepted",
        from_status="submitted",
        to_status="accepted",
        actor_user_id=uuid4(),
        actor_role="staff",
    )
    event_one = WorkOrderEvent(
        id=uuid4(),
        work_order_id=WORK_ORDER_ID,
        sequence_no=1,
        event_type="submitted",
        from_status=None,
        to_status="submitted",
        actor_user_id=uuid4(),
        actor_role="student",
    )
    session = _session(_RowsResult([event_one, event_two]))

    result = asyncio.run(
        WorkOrderEventRepository(session).list_timeline(WORK_ORDER_ID)
    )

    assert result == (event_one, event_two)
    statement_sql = _sql(session.execute.await_args.args[0])
    assert f"work_order_events.work_order_id = '{WORK_ORDER_ID}'" in statement_sql
    assert "ORDER BY campus_service.work_order_events.sequence_no" in statement_sql
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()
    session.close.assert_not_awaited()


def test_visible_list_applies_owner_or_scope_and_filters_in_sql() -> None:
    order = MagicMock(spec=WorkOrder)
    session = _session(_ScalarResult(1), _RowsResult([(order, None)]))
    repository = WorkOrderRepository(session)

    rows, total = asyncio.run(
        repository.list_visible(
            actor_user_id=UUID(int=7),
            scopes=(WorkOrderScope("main", ("梅园", "竹园")),),
            page=2,
            page_size=20,
            status="submitted",
            campus_code="main",
            assigned_to_me=True,
        )
    )

    assert rows == ((order, None),)
    assert total == 1
    list_sql = _sql(session.execute.await_args_list[1].args[0])
    assert "work_orders.created_by" in list_sql
    assert "work_orders.campus_code = 'main'" in list_sql
    assert "work_orders.dormitory_area IN ('梅园', '竹园')" in list_sql
    assert "work_orders.assigned_to" in list_sql
    assert "work_orders.status = 'submitted'" in list_sql
    assert "ORDER BY campus_service.work_orders.created_at DESC" in list_sql
    assert "LIMIT 20 OFFSET 20" in list_sql


def test_visible_detail_uses_owner_only_when_scope_is_empty() -> None:
    session = _session(_RowsResult([]))
    repository = WorkOrderRepository(session)

    assert asyncio.run(
        repository.get_visible(
            WORK_ORDER_ID, actor_user_id=UUID(int=7), scopes=()
        )
    ) is None
    sql = _sql(session.execute.await_args.args[0])
    assert "work_orders.created_by" in sql
    assert "dormitory_area IN" not in sql


def test_transition_load_uses_scope_and_for_update_before_event_sequence() -> None:
    order = MagicMock(spec=WorkOrder)
    session = _session(_ScalarResult(order), _ScalarResult(3))
    repository = WorkOrderRepository(session)
    events = WorkOrderEventRepository(session)
    loaded = asyncio.run(repository.get_visible_for_update(
        WORK_ORDER_ID,
        actor_user_id=UUID(int=7),
        scopes=(WorkOrderScope("main", ("梅园",)),),
    ))
    sequence = asyncio.run(events.next_sequence(WORK_ORDER_ID))
    assert loaded is order and sequence == 3
    lock_sql = _sql(session.execute.await_args_list[0].args[0])
    sequence_sql = _sql(session.execute.await_args_list[1].args[0])
    assert "FOR UPDATE" in lock_sql
    assert "max(campus_service.work_order_events.sequence_no)" in sequence_sql


def test_rating_owner_load_is_locked_and_rating_write_keeps_session_ownership() -> None:
    order = MagicMock(spec=WorkOrder)
    rating = MagicMock()
    session = _session(_ScalarResult(order), _ScalarResult(None))
    repository = WorkOrderRepository(session)
    loaded = asyncio.run(repository.get_owner_for_update(WORK_ORDER_ID, UUID(int=7)))
    existing = asyncio.run(repository.get_rating(WORK_ORDER_ID))
    repository.add_rating(rating)
    assert loaded is order and existing is None
    assert "FOR UPDATE" in _sql(session.execute.await_args_list[0].args[0])
    assert "work_orders.created_by" in _sql(session.execute.await_args_list[0].args[0])
    assert "work_order_ratings.work_order_id" in _sql(session.execute.await_args_list[1].args[0])
    session.add.assert_called_once_with(rating)
    session.commit.assert_not_awaited()


def test_room_lookup_for_work_order_location_is_user_scoped() -> None:
    account = MagicMock()
    session = _session(_ScalarResult(account))
    repository = ElectricityRepository(session)
    result = asyncio.run(repository.get_account_for_location(
        user_id=UUID(int=7), campus_code="main", dormitory_area="梅园",
        building="3号楼", room="301",
    ))
    assert result is account
    sql = _sql(session.execute.await_args.args[0])
    assert "electricity_account_members.user_id" in sql
    assert "electricity_accounts.campus_code = 'main'" in sql
    assert "electricity_accounts.dormitory_area = '梅园'" in sql
