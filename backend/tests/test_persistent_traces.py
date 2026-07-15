import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.modules.agent_platform.models import AgentRun
from app.modules.agent_platform.traces import AgentLoopDetected, AgentMaxStepsExceeded, AgentRunStateConflict, TraceService

NOW=datetime(2026,7,15,tzinfo=UTC); RUN=uuid4(); USER=uuid4()


def repository(run=None):
    r=MagicMock(); r.get_run_for_update=AsyncMock(return_value=run); r.count_signature=AsyncMock(return_value=0); r.update_run=AsyncMock(return_value=True); r.update_tool=AsyncMock(return_value=True); r.get_detail=AsyncMock(); return r


def active(steps=0,status="running"):
    return AgentRun(id=RUN,user_id=USER,client_request_id="req",input_summary="safe",status=status,step_count=steps,specialist_count=0)


def test_create_step_and_tool_store_only_redacted_summaries() -> None:
    r=repository(active()); service=TraceService(r,now=lambda:NOW)
    step=asyncio.run(service.append_step(run_id=RUN,agent_code="service_agent",task_type="tool",input_summary={"password":"secret","safe":"ok"},signature_hash="a"*64))
    call=service.append_tool(run_id=RUN,step_id=step.id,tool_name="electricity.get_balance",tool_version="1.0.0",arguments_hash="b"*64,arguments_summary={"token":"raw","room_id":"safe"})
    assert step.input_summary["password"]=="***" and call.arguments_summary["token"]=="***"; assert "secret" not in repr(step) and "raw" not in repr(call)
    r.update_run.assert_awaited_once()


def test_six_step_loop_and_terminal_boundaries_cannot_be_bypassed() -> None:
    r=repository(active(6)); service=TraceService(r,now=lambda:NOW)
    with pytest.raises(AgentMaxStepsExceeded): asyncio.run(service.append_step(run_id=RUN,agent_code="service_agent",task_type="x",input_summary={}))
    r.get_run_for_update.return_value=active(); r.count_signature.return_value=2
    with pytest.raises(AgentLoopDetected): asyncio.run(service.append_step(run_id=RUN,agent_code="service_agent",task_type="x",input_summary={},signature_hash="a"*64))
    r.update_run.return_value=False
    with pytest.raises(AgentRunStateConflict): asyncio.run(service.finalize(RUN,"succeeded"))


def test_tool_transition_and_finalize_use_expected_state_updates() -> None:
    r=repository(active()); service=TraceService(r,now=lambda:NOW)
    asyncio.run(service.transition_tool(uuid4(),{"prepared"},"running",result_summary={"authorization":"secret"}))
    assert r.update_tool.await_args.kwargs["result_summary"]["authorization"]=="***"
    asyncio.run(service.finalize(RUN,"partial",finish_reason="one dependency failed",error_code="TOOL_TIMEOUT")); assert r.update_run.await_count==1
