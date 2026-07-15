import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.modules.agent_platform.models import AgentRun, AgentStep, ApprovalRequestModel, ToolCall
from app.modules.agent_platform.run_queries import AgentRunQueryRepository, AgentRunQueryService, RunAggregate
from app.modules.agent_platform.traces import AgentRunNotFound

NOW=datetime(2026,7,15,tzinfo=UTC); USER=uuid4(); RUN=uuid4(); STEP=uuid4(); CALL=uuid4(); APPROVAL=uuid4()


def run():
    return AgentRun(id=RUN,user_id=USER,client_request_id="request-1",input_summary="safe",status="succeeded",route_decision={"target_agent":"service","confidence":0.9},model_name="rule",step_count=1,specialist_count=1,created_at=NOW,updated_at=NOW,finished_at=NOW)


def test_detail_maps_redacted_aggregate_and_final_answer() -> None:
    step=AgentStep(id=STEP,run_id=RUN,sequence_no=1,agent_code="service_agent",task_type="merge",status="succeeded",input_summary={"password":"secret"},output_summary={"answer":"done","token":"raw"},created_at=NOW,started_at=NOW,finished_at=NOW)
    call=ToolCall(id=CALL,run_id=RUN,step_id=STEP,tool_name="electricity.get_balance",tool_version="1.0.0",arguments_hash="a"*64,arguments_summary={},result_summary={},status="succeeded",created_at=NOW,started_at=NOW,finished_at=NOW)
    approval=ApprovalRequestModel(id=APPROVAL,run_id=RUN,tool_call_id=CALL,user_id=USER,action="read",display_summary="safe",arguments_hash="b"*64,status="consumed",created_at=NOW,expires_at=NOW,decided_at=NOW)
    repo=MagicMock(); repo.get_aggregate=AsyncMock(return_value=RunAggregate(run(),(step,),(call,),(approval,)))
    result=asyncio.run(AgentRunQueryService(repo).get_detail(run_id=RUN,user_id=USER,can_read_all=False))
    assert result.run.final_answer=="done" and result.run.route=="service"
    assert result.steps[0].input_summary["password"]=="***"
    assert result.steps[0].output_summary["token"]=="***"
    assert result.tool_calls[0].approval_id==APPROVAL
    assert result.approvals[0].argument_hash=="b"*64
    assert "raw" not in result.model_dump_json()


def test_missing_or_unowned_run_is_not_found() -> None:
    repo=MagicMock(); repo.get_aggregate=AsyncMock(return_value=None)
    with pytest.raises(AgentRunNotFound) as exc:
        asyncio.run(AgentRunQueryService(repo).get_detail(run_id=RUN,user_id=USER,can_read_all=False))
    assert exc.value.code=="AGENT_RUN_NOT_FOUND"


def test_owner_scope_and_stable_pagination_compile() -> None:
    session=MagicMock(); repository=AgentRunQueryRepository(session)
    statements=[]
    async def execute(statement):
        statements.append(statement)
        result=MagicMock(); result.scalar_one.return_value=0; result.scalars.return_value.all.return_value=[]; return result
    session.execute=AsyncMock(side_effect=execute)
    rows,total=asyncio.run(repository.list_runs(user_id=USER,can_read_all=False,page=2,page_size=10,status="running"))
    sql=[str(item.compile(dialect=postgresql.dialect(),compile_kwargs={"literal_binds":True})) for item in statements]
    assert rows==() and total==0 and len(sql)==2
    assert all("user_id" in item and "status" in item for item in sql)
    assert "ORDER BY agent_platform.agent_runs.created_at DESC" in sql[1]
    assert "LIMIT 10 OFFSET 10" in sql[1]


def test_page_metadata_handles_empty_results() -> None:
    repo=MagicMock(); repo.list_runs=AsyncMock(return_value=((),0))
    result=asyncio.run(AgentRunQueryService(repo).list_runs(user_id=USER,can_read_all=False,page=3,page_size=20,status=None))
    assert result.items==() and result.pagination.total_pages==0
