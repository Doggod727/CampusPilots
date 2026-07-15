from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Callable, Literal
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent_platform.models import AgentRun, ApprovalRequestModel, ToolCall
from app.modules.agent_platform.tool_gateway.errors import ToolApprovalInvalid


@dataclass(frozen=True)
class ToolApprovalContext:
    run_id: UUID; user_id: UUID; tool_call_id: UUID; tool_name: str; tool_version: str


class ApprovalRepository:
    def __init__(self, session: AsyncSession) -> None: self._session = session

    async def get_tool_context(self, tool_call_id: UUID) -> ToolApprovalContext | None:
        stmt=select(ToolCall.run_id, AgentRun.user_id, ToolCall.id, ToolCall.tool_name, ToolCall.tool_version).join(AgentRun, AgentRun.id == ToolCall.run_id).where(ToolCall.id == tool_call_id)
        row=(await self._session.execute(stmt)).one_or_none(); return ToolApprovalContext(*row) if row else None

    def add(self, approval: ApprovalRequestModel) -> None: self._session.add(approval)

    async def get_for_update(self, approval_id: UUID):
        stmt=select(ApprovalRequestModel, ToolCall).join(ToolCall, ToolCall.id == ApprovalRequestModel.tool_call_id).where(ApprovalRequestModel.id == approval_id).with_for_update()
        return (await self._session.execute(stmt)).one_or_none()

    async def set_decision(self, approval_id: UUID, status: str, actor: UUID, now: datetime) -> bool:
        stmt=update(ApprovalRequestModel).where(ApprovalRequestModel.id == approval_id, ApprovalRequestModel.status == "pending").values(status=status, decided_by=actor, decided_at=now)
        return (await self._session.execute(stmt)).rowcount == 1

    async def consume(self, approval_id: UUID, now: datetime) -> bool:
        stmt=update(ApprovalRequestModel).where(ApprovalRequestModel.id == approval_id, ApprovalRequestModel.status == "approved").values(status="consumed", decided_at=now)
        return (await self._session.execute(stmt)).rowcount == 1

    async def expire_due(self, now: datetime) -> int:
        stmt=update(ApprovalRequestModel).where(ApprovalRequestModel.status == "pending", ApprovalRequestModel.expires_at <= now).values(status="expired")
        return (await self._session.execute(stmt)).rowcount


class ApprovalService:
    def __init__(self, repository: ApprovalRepository, *, ttl_seconds: int = 600, now: Callable[[], datetime] | None = None) -> None:
        self._repository=repository; self._ttl=timedelta(seconds=ttl_seconds); self._now=now or (lambda: datetime.now(UTC))

    async def create(self, *, run_id: UUID, tool_call_id: UUID, user_id: UUID, action: str, display_summary: str, arguments_hash: str) -> ApprovalRequestModel:
        context=await self._repository.get_tool_context(tool_call_id)
        if context is None or context.run_id != run_id or context.user_id != user_id or len(arguments_hash) != 64: raise ToolApprovalInvalid()
        now=self._utc(); approval=ApprovalRequestModel(id=uuid4(), run_id=run_id, tool_call_id=tool_call_id, user_id=user_id, action=action, display_summary=display_summary[:1000], arguments_hash=arguments_hash, status="pending", created_at=now, expires_at=now+self._ttl)
        self._repository.add(approval); return approval

    async def decide(self, *, approval_id: UUID, user_id: UUID, decision: Literal["approve", "reject"]) -> ApprovalRequestModel:
        row=await self._repository.get_for_update(approval_id); now=self._utc()
        if row is None: raise ToolApprovalInvalid()
        approval, _=row
        if approval.user_id != user_id or approval.status != "pending" or approval.expires_at <= now: raise ToolApprovalInvalid()
        status="approved" if decision == "approve" else "rejected"
        if not await self._repository.set_decision(approval.id, status, user_id, now): raise ToolApprovalInvalid()
        approval.status=status; approval.decided_by=user_id; approval.decided_at=now; return approval

    async def consume(self, *, approval_id: UUID, user_id: UUID, tool_name: str, tool_version: str, arguments_hash: str) -> bool:
        row=await self._repository.get_for_update(approval_id); now=self._utc()
        if row is None: raise ToolApprovalInvalid()
        approval, call=row
        if approval.user_id != user_id or approval.status != "approved" or approval.expires_at <= now or approval.arguments_hash != arguments_hash or call.tool_name != tool_name or call.tool_version != tool_version: raise ToolApprovalInvalid()
        if not await self._repository.consume(approval.id, now): raise ToolApprovalInvalid()
        approval.status="consumed"; return True

    async def expire(self) -> int: return await self._repository.expire_due(self._utc())

    def _utc(self):
        value=self._now(); return value if value.tzinfo else value.replace(tzinfo=UTC)


class DatabaseApprovalVerifier:
    def __init__(self, service: ApprovalService) -> None: self._service=service
    async def verify_and_consume(self, *, approval_id: UUID, user_id: UUID, tool_name: str, tool_version: str, arguments_hash: str) -> bool:
        return await self._service.consume(approval_id=approval_id, user_id=user_id, tool_name=tool_name, tool_version=tool_version, arguments_hash=arguments_hash)
