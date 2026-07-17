from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent_platform.models import AgentRun, AgentRunEvent, AgentRuntimeCheckpoint, AgentRuntimeCommand
from app.modules.platform.audit import redact


class RuntimeCommandRepository:
    def __init__(self, session: AsyncSession) -> None: self._session=session
    def add(self, command: AgentRuntimeCommand) -> None: self._session.add(command)

    async def get_processing(self, command_id: UUID, worker_id: str) -> AgentRuntimeCommand | None:
        stmt = select(AgentRuntimeCommand).where(
            AgentRuntimeCommand.id == command_id,
            AgentRuntimeCommand.status == "processing",
            AgentRuntimeCommand.claimed_by == worker_id,
        ).with_for_update()
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def claim_batch(self, *, worker_id:str, now:datetime, stale_after:timedelta, limit:int=10) -> tuple[AgentRuntimeCommand,...]:
        stmt=(select(AgentRuntimeCommand).where(
            or_(
                (AgentRuntimeCommand.status=="pending") & (AgentRuntimeCommand.available_at<=now),
                (AgentRuntimeCommand.status=="processing") & (AgentRuntimeCommand.claimed_at < now-stale_after),
            ), AgentRuntimeCommand.attempt_count < AgentRuntimeCommand.max_attempts,
        ).order_by(AgentRuntimeCommand.available_at,AgentRuntimeCommand.created_at,AgentRuntimeCommand.id).limit(limit).with_for_update(skip_locked=True))
        commands=tuple((await self._session.execute(stmt)).scalars().all())
        for command in commands:
            command.status="processing"; command.claimed_by=worker_id; command.claimed_at=now; command.attempt_count+=1; command.updated_at=now
        return commands

    async def complete(self, command_id:UUID, now:datetime) -> bool:
        stmt=update(AgentRuntimeCommand).where(AgentRuntimeCommand.id==command_id,AgentRuntimeCommand.status=="processing").values(status="succeeded",completed_at=now,updated_at=now,error_code=None)
        return (await self._session.execute(stmt)).rowcount==1

    async def fail_or_retry(self, command_id:UUID, *, now:datetime, retry_at:datetime, error_code:str) -> str | None:
        command=(await self._session.execute(select(AgentRuntimeCommand).where(AgentRuntimeCommand.id==command_id,AgentRuntimeCommand.status=="processing").with_for_update())).scalar_one_or_none()
        if command is None: return None
        terminal=command.attempt_count>=command.max_attempts
        # 保留首次失败的真实错误码：重试时的次生错误（如状态冲突）不得掩盖根因。
        first_error_code=command.error_code or error_code
        command.status="failed" if terminal else "pending"; command.completed_at=now if terminal else None; command.available_at=retry_at; command.claimed_by=None; command.claimed_at=None; command.error_code=first_error_code; command.updated_at=now
        return command.status


class RuntimeCheckpointRepository:
    def __init__(self, session: AsyncSession) -> None: self._session=session
    async def get(self, run_id:UUID, *, for_update:bool=False) -> AgentRuntimeCheckpoint|None:
        stmt=select(AgentRuntimeCheckpoint).where(AgentRuntimeCheckpoint.run_id==run_id)
        if for_update: stmt=stmt.with_for_update()
        return (await self._session.execute(stmt)).scalar_one_or_none()
    def add(self, checkpoint:AgentRuntimeCheckpoint) -> None: self._session.add(checkpoint)
    async def update_if_version(self, run_id:UUID, expected_version:int, **values) -> bool:
        stmt=update(AgentRuntimeCheckpoint).where(AgentRuntimeCheckpoint.run_id==run_id,AgentRuntimeCheckpoint.state_version==expected_version).values(**values)
        return (await self._session.execute(stmt)).rowcount==1
    async def delete(self, run_id:UUID) -> bool:
        return (await self._session.execute(delete(AgentRuntimeCheckpoint).where(AgentRuntimeCheckpoint.run_id==run_id))).rowcount==1


class RuntimeEventRepository:
    def __init__(self, session: AsyncSession) -> None: self._session=session
    async def append(self, *, run_id:UUID, event:str, data:dict, request_id:str|None, occurred_at:datetime) -> AgentRunEvent:
        await self._session.execute(select(AgentRun.id).where(AgentRun.id==run_id).with_for_update())
        current=(await self._session.execute(select(func.max(AgentRunEvent.sequence)).where(AgentRunEvent.run_id==run_id))).scalar_one()
        item=AgentRunEvent(id=uuid4(),run_id=run_id,sequence=(current or 0)+1,event=event,data=redact(data) or {},request_id=request_id,occurred_at=occurred_at)
        self._session.add(item); return item
    async def replay(self, run_id:UUID, *, after_sequence:int, limit:int=500) -> tuple[AgentRunEvent,...]:
        stmt=select(AgentRunEvent).where(AgentRunEvent.run_id==run_id,AgentRunEvent.sequence>after_sequence).order_by(AgentRunEvent.sequence).limit(limit)
        return tuple((await self._session.execute(stmt)).scalars().all())
    async def max_sequence(self, run_id:UUID) -> int:
        return (await self._session.execute(select(func.coalesce(func.max(AgentRunEvent.sequence),0)).where(AgentRunEvent.run_id==run_id))).scalar_one()
