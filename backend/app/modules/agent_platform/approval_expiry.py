from __future__ import annotations

from datetime import UTC, datetime

from app.modules.agent_platform.approvals import ApprovalRepository
from app.modules.agent_platform.checkpointing import RuntimeTerminalCoordinator
from app.modules.agent_platform.traces import AgentRunStateConflict, TraceService


class ApprovalExpiryCoordinator:
    """Expire approvals and close their waiting runtime state in one transaction."""

    def __init__(self, approvals: ApprovalRepository, trace: TraceService,
                 terminal: RuntimeTerminalCoordinator) -> None:
        self._approvals = approvals
        self._trace = trace
        self._terminal = terminal

    async def expire_due(self, now: datetime | None = None) -> int:
        current = now or datetime.now(UTC)
        rows = await self._approvals.list_due_for_update(current)
        expired = 0
        for approval, call, run in rows:
            approval.status = "expired"
            try:
                await self._trace.transition_tool(
                    call.id, {"awaiting_approval"}, "expired",
                    error_code="TOOL_APPROVAL_EXPIRED", finished_at=current,
                )
                await self._trace.transition_step(
                    call.step_id, {"awaiting_approval"}, "failed",
                    error_code="TOOL_APPROVAL_EXPIRED", finished_at=current,
                )
                await self._trace.finalize(
                    run.id, "partial", finish_reason="approval_expired",
                    error_code="TOOL_APPROVAL_EXPIRED",
                )
            except AgentRunStateConflict:
                # A concurrent cancel/decision already established the terminal fact.
                continue
            await self._terminal.complete(
                run_id=run.id,
                status="partial",
                request_id=run.client_request_id,
                error_code="TOOL_APPROVAL_EXPIRED",
            )
            expired += 1
        return expired
