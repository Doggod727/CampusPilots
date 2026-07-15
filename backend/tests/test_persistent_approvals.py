import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.modules.agent_platform.approvals import ApprovalService, DatabaseApprovalVerifier, ToolApprovalContext
from app.modules.agent_platform.models import ApprovalRequestModel, ToolCall
from app.modules.agent_platform.tool_gateway.errors import ToolApprovalInvalid

NOW=datetime(2026,7,15,tzinfo=UTC); USER=uuid4(); RUN=uuid4(); CALL=uuid4(); APPROVAL=uuid4(); HASH="a"*64


def repo():
    r=MagicMock(); r.get_tool_context=AsyncMock(return_value=ToolApprovalContext(RUN,USER,CALL,"work_order.create","1.0.0")); r.get_for_update=AsyncMock(); r.set_decision=AsyncMock(return_value=True); r.consume=AsyncMock(return_value=True); r.expire_due=AsyncMock(return_value=2); return r


def approval(status="pending", expires=None):
    return ApprovalRequestModel(id=APPROVAL, run_id=RUN, tool_call_id=CALL, user_id=USER, action="work_order.create", display_summary="safe", arguments_hash=HASH, status=status, created_at=NOW, expires_at=expires or NOW+timedelta(minutes=10), decided_by=USER if status=="approved" else None, decided_at=NOW if status=="approved" else None)


def test_create_binds_context_and_ttl_without_transaction_control() -> None:
    r=repo(); service=ApprovalService(r, now=lambda:NOW)
    item=asyncio.run(service.create(run_id=RUN, tool_call_id=CALL, user_id=USER, action="work_order.create", display_summary="safe", arguments_hash=HASH))
    assert item.expires_at == NOW+timedelta(minutes=10); r.add.assert_called_once_with(item)


def test_decide_and_consume_are_one_time_and_hash_bound() -> None:
    r=repo(); call=ToolCall(id=CALL, tool_name="work_order.create", tool_version="1.0.0"); r.get_for_update.return_value=(approval(),call); service=ApprovalService(r,now=lambda:NOW)
    approved=asyncio.run(service.decide(approval_id=APPROVAL,user_id=USER,decision="approve")); assert approved.status=="approved"
    r.get_for_update.return_value=(approval("approved"),call)
    assert asyncio.run(DatabaseApprovalVerifier(service).verify_and_consume(approval_id=APPROVAL,user_id=USER,tool_name="work_order.create",tool_version="1.0.0",arguments_hash=HASH))
    r.consume.assert_awaited_once()


@pytest.mark.parametrize("kind",["missing","expired","wrong_user","wrong_hash","consumed"])
def test_invalid_approval_states_are_indistinguishable(kind) -> None:
    r=repo(); call=ToolCall(id=CALL,tool_name="work_order.create",tool_version="1.0.0"); item=approval("approved")
    if kind=="missing": r.get_for_update.return_value=None
    else:
        if kind=="expired": item.expires_at=NOW-timedelta(seconds=1)
        if kind=="wrong_user": item.user_id=uuid4()
        if kind=="wrong_hash": item.arguments_hash="b"*64
        if kind=="consumed": item.status="consumed"
        r.get_for_update.return_value=(item,call)
    with pytest.raises(ToolApprovalInvalid) as error: asyncio.run(ApprovalService(r,now=lambda:NOW).consume(approval_id=APPROVAL,user_id=USER,tool_name="work_order.create",tool_version="1.0.0",arguments_hash=HASH))
    assert error.value.code=="TOOL_APPROVAL_INVALID"; r.consume.assert_not_awaited()


def test_expire_delegates_atomic_update() -> None:
    r=repo(); assert asyncio.run(ApprovalService(r,now=lambda:NOW).expire())==2; r.expire_due.assert_awaited_once_with(NOW)
