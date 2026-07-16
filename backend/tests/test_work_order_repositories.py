import asyncio
from datetime import date
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.modules.campus_service.models import WorkOrder, WorkOrderEvent
from app.modules.campus_service.repositories import (
    WorkOrderEventRepository,
    WorkOrderRepository,
)
from app.modules.campus_service.work_orders import WorkOrderNumberExhausted

WORK_ORDER_ID = UUID("70000000-0000-4000-8000-000000000001")


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
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
