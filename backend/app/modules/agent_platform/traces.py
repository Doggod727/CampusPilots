from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.modules.agent_platform.models import AgentRun, AgentStep, ToolCall
from app.modules.platform.audit import redact


class AgentRunNotFound(AppError):
    def __init__(self): super().__init__(status_code=404, code="AGENT_RUN_NOT_FOUND", message="Agent运行不存在")
class AgentRunStateConflict(AppError):
    def __init__(self): super().__init__(status_code=409, code="AGENT_RUN_STATE_CONFLICT", message="Agent运行状态冲突")
class AgentLoopDetected(AppError):
    def __init__(self): super().__init__(status_code=409, code="AGENT_LOOP_DETECTED", message="检测到重复执行循环")
class AgentMaxStepsExceeded(AppError):
    def __init__(self): super().__init__(status_code=409, code="AGENT_MAX_STEPS_EXCEEDED", message="Agent运行超过最大步骤数")


@dataclass(frozen=True)
class TraceDetail:
    run: AgentRun; steps: tuple[AgentStep, ...]; tool_calls: tuple[ToolCall, ...]


class TraceRepository:
    def __init__(self, session: AsyncSession): self._session=session
    def add(self, entity): self._session.add(entity)
    async def get_run_for_update(self, run_id): return (await self._session.execute(select(AgentRun).where(AgentRun.id==run_id).with_for_update())).scalar_one_or_none()
    async def count_signature(self, run_id, signature): return (await self._session.execute(select(func.count()).select_from(AgentStep).where(AgentStep.run_id==run_id,AgentStep.signature_hash==signature))).scalar_one()
    async def update_run(self, run_id, expected, **values): return (await self._session.execute(update(AgentRun).where(AgentRun.id==run_id,AgentRun.status.in_(expected)).values(**values))).rowcount==1
    async def update_step(self, step_id, expected, **values): return (await self._session.execute(update(AgentStep).where(AgentStep.id==step_id,AgentStep.status.in_(expected)).values(**values))).rowcount==1
    async def update_tool(self, call_id, expected, **values): return (await self._session.execute(update(ToolCall).where(ToolCall.id==call_id,ToolCall.status.in_(expected)).values(**values))).rowcount==1
    async def get_detail(self, run_id):
        run=(await self._session.execute(select(AgentRun).where(AgentRun.id==run_id))).scalar_one_or_none()
        if run is None: return None
        steps=tuple((await self._session.execute(select(AgentStep).where(AgentStep.run_id==run_id).order_by(AgentStep.sequence_no))).scalars().all())
        calls=tuple((await self._session.execute(select(ToolCall).where(ToolCall.run_id==run_id).order_by(ToolCall.created_at,ToolCall.id))).scalars().all())
        return TraceDetail(run,steps,calls)


class TraceService:
    ACTIVE={"created","routing","running","awaiting_approval"}; TERMINAL={"succeeded","partial","failed","cancelled"}
    def __init__(self, repository: TraceRepository, now: Callable[[],datetime]|None=None): self._repo=repository; self._now=now or (lambda:datetime.now(UTC))
    def create_run(self, *, user_id:UUID, client_request_id:str, input_summary:str, conversation_id:UUID|None=None):
        run=AgentRun(id=uuid4(),user_id=user_id,conversation_id=conversation_id,client_request_id=client_request_id,input_summary=input_summary[:1000],status="created",step_count=0,specialist_count=0,created_at=self._utc(),updated_at=self._utc()); self._repo.add(run); return run
    async def append_step(self, *, run_id:UUID, agent_code:str, task_type:str, input_summary:dict, signature_hash:str|None=None, parent_step_id:UUID|None=None):
        run=await self._repo.get_run_for_update(run_id)
        if run is None: raise AgentRunNotFound()
        if run.status not in self.ACTIVE: raise AgentRunStateConflict()
        if run.step_count>=6: raise AgentMaxStepsExceeded()
        if signature_hash and await self._repo.count_signature(run_id,signature_hash)>=2: raise AgentLoopDetected()
        sequence=run.step_count+1; step=AgentStep(id=uuid4(),run_id=run_id,parent_step_id=parent_step_id,sequence_no=sequence,agent_code=agent_code,task_type=task_type,status="created",input_summary=redact(input_summary),output_summary={},signature_hash=signature_hash,created_at=self._utc()); self._repo.add(step)
        specialists=run.specialist_count+(0 if agent_code in {"supervisor","governance_agent"} else 1)
        if not await self._repo.update_run(run_id,self.ACTIVE,step_count=sequence,specialist_count=min(specialists,3),updated_at=self._utc()): raise AgentRunStateConflict()
        return step
    def append_tool(self, *, run_id:UUID, step_id:UUID, tool_name:str, tool_version:str, arguments_hash:str, arguments_summary:dict, idempotency_key:str|None=None):
        call=ToolCall(id=uuid4(),run_id=run_id,step_id=step_id,tool_name=tool_name,tool_version=tool_version,arguments_hash=arguments_hash,arguments_summary=redact(arguments_summary),result_summary={},status="prepared",idempotency_key=idempotency_key,created_at=self._utc()); self._repo.add(call); return call
    async def transition_tool(self, call_id, expected, status, **safe_values):
        safe_values["result_summary"]=redact(safe_values.get("result_summary",{}))
        if not await self._repo.update_tool(call_id,set(expected),status=status,**safe_values): raise AgentRunStateConflict()
    async def transition_run(self, run_id, expected, status, **safe_values):
        safe_values["updated_at"] = self._utc()
        if not await self._repo.update_run(run_id, set(expected), status=status, **safe_values):
            raise AgentRunStateConflict()
    async def transition_step(self, step_id, expected, status, **safe_values):
        if "input_summary" in safe_values: safe_values["input_summary"] = redact(safe_values["input_summary"])
        if "output_summary" in safe_values: safe_values["output_summary"] = redact(safe_values["output_summary"])
        if not await self._repo.update_step(step_id, set(expected), status=status, **safe_values):
            raise AgentRunStateConflict()
    async def finalize(self, run_id, status, *, finish_reason=None, error_code=None):
        if status not in self.TERMINAL: raise AgentRunStateConflict()
        now=self._utc()
        if not await self._repo.update_run(run_id,self.ACTIVE,status=status,finish_reason=finish_reason,error_code=error_code,finished_at=now,updated_at=now): raise AgentRunStateConflict()
    async def detail(self, run_id):
        result=await self._repo.get_detail(run_id)
        if result is None: raise AgentRunNotFound()
        return result
    def _utc(self):
        value=self._now(); return value if value.tzinfo else value.replace(tzinfo=UTC)
