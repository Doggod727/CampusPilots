import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from app.modules.agent_platform.approval_expiry import ApprovalExpiryCoordinator


NOW=datetime(2026,7,19,tzinfo=UTC)


def test_expired_approval_closes_tool_step_run_and_checkpoint():
    run=SimpleNamespace(id=uuid4(),client_request_id="request-1")
    call=SimpleNamespace(id=uuid4(),step_id=uuid4())
    approval=SimpleNamespace(status="pending",expires_at=NOW-timedelta(seconds=1))
    repository=MagicMock();repository.list_due_for_update=AsyncMock(return_value=((approval,call,run),))
    trace=MagicMock();trace.transition_tool=AsyncMock();trace.transition_step=AsyncMock();trace.finalize=AsyncMock()
    terminal=MagicMock();terminal.complete=AsyncMock()
    count=asyncio.run(ApprovalExpiryCoordinator(repository,trace,terminal).expire_due(NOW))
    assert count==1 and approval.status=="expired"
    trace.transition_tool.assert_awaited_once_with(call.id,{"awaiting_approval"},"expired",error_code="TOOL_APPROVAL_EXPIRED",finished_at=NOW)
    trace.transition_step.assert_awaited_once_with(call.step_id,{"awaiting_approval"},"failed",error_code="TOOL_APPROVAL_EXPIRED",finished_at=NOW)
    trace.finalize.assert_awaited_once_with(run.id,"partial",finish_reason="approval_expired",error_code="TOOL_APPROVAL_EXPIRED")
    terminal.complete.assert_awaited_once_with(run_id=run.id,status="partial",request_id="request-1",error_code="TOOL_APPROVAL_EXPIRED")
