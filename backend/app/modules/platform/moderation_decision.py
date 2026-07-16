from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Callable, Literal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.infrastructure.database import Database
from app.modules.platform.audit import AuditService
from app.modules.platform.auth import AuthenticatedUser
from app.modules.platform.idempotency import IdempotencyConflict, IdempotencyService
from app.modules.platform.moderation_handlers import ModerationHandlerRegistry
from app.modules.platform.moderation_schemas import moderation_case_data
from app.modules.platform.repositories import (
    AuditLogRepository,
    IdempotencyRecordRepository,
    ModerationCaseRepository,
)


class ModerationCaseAlreadyDecided(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=409, code="MODERATION_CASE_ALREADY_DECIDED", message="审核案件已处理")


class ModerationCaseNotFound(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=404, code="MODERATION_CASE_NOT_FOUND", message="审核案件不存在")


class ResourceVersionConflict(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=409, code="RESOURCE_VERSION_CONFLICT", message="数据已被其他操作更新，请刷新后重试")


@dataclass(frozen=True)
class ModerationDecisionResult:
    status_code: int
    request_id: str
    body: dict[str, object] = field(repr=False)


Decision = Literal["approved", "rejected", "escalated"]


class ModerationDecisionService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        repository: ModerationCaseRepository,
        idempotency_service: IdempotencyService,
        audit_service: AuditService,
        handlers: ModerationHandlerRegistry,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._session = session
        self._repository = repository
        self._idempotency = idempotency_service
        self._audit = audit_service
        self._handlers = handlers
        self._now = now if now is not None else lambda: datetime.now(UTC)

    async def decide(
        self,
        *,
        actor: AuthenticatedUser,
        case_id: UUID,
        decision: Decision,
        reason: str,
        expected_version: int,
        idempotency_key: str,
        request_id: str,
        request_body: object,
    ) -> ModerationDecisionResult:
        async with self._session.begin():
            idem = await self._idempotency.begin(
                user_id=actor.user_id,
                endpoint=f"POST /api/v1/moderation/cases/{case_id}/decision",
                idempotency_key=idempotency_key,
                request_body=request_body,
            )
            if idem.replay is not None:
                return ModerationDecisionResult(
                    status_code=idem.replay.response_status,
                    request_id=str(idem.replay.response_body["request_id"]),
                    body=dict(idem.replay.response_body),
                )
            if idem.pending:
                raise IdempotencyConflict()

            case = await self._repository.get_by_id_for_update(case_id)
            if case is None:
                raise ModerationCaseNotFound()
            if case.status != "pending":
                raise ModerationCaseAlreadyDecided()
            if case.version != expected_version:
                raise ResourceVersionConflict()
            handler = self._handlers.resolve(
                target_module=case.target_module, target_type=case.target_type
            )
            await getattr(handler, {"approved": "approve", "rejected": "reject", "escalated": "escalate"}[decision])(
                session=self._session, case_id=case.id, target_id=case.target_id,
                reason=reason, actor=actor
            )
            now = self._current_time()
            if not await self._repository.decide_if_version(
                case_id=case.id, expected_version=expected_version, status=decision,
                reviewer_id=actor.user_id, decision_reason=reason,
                reviewed_at=now, updated_at=now,
            ):
                raise ResourceVersionConflict()
            case.status = decision
            case.reviewer_id = actor.user_id
            case.decision_reason = reason
            case.reviewed_at = now
            case.updated_at = now
            case.version = expected_version + 1
            response = {
                "code": "OK", "message": "success",
                "data": moderation_case_data(case).model_dump(mode="json"),
                "request_id": request_id, "timestamp": now.isoformat(),
            }
            self._audit.record_success(
                action="moderation.case.decide", resource_type="moderation_case",
                resource_id=str(case.id), request_id=request_id,
                actor_user_id=actor.user_id, actor_username=actor.username,
                before_data={"id": str(case.id), "status": "pending", "version": expected_version},
                after_data={"id": str(case.id), "status": decision, "version": case.version},
            )
            if not await self._idempotency.complete(
                record_id=idem.record_id, response_status=200, response_body=response,
                resource_type="moderation_case", resource_id=str(case.id),
            ):
                raise IdempotencyConflict()
            return ModerationDecisionResult(status_code=200, request_id=request_id, body=response)

    def _current_time(self) -> datetime:
        value = self._now()
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


@asynccontextmanager
async def moderation_decision_service_context(settings: Settings) -> AsyncIterator[ModerationDecisionService]:
    database = Database.from_settings(settings)
    try:
        async with database.session() as session:
            yield ModerationDecisionService(
                session=session, repository=ModerationCaseRepository(session),
                idempotency_service=IdempotencyService(
                    session=session, repository=IdempotencyRecordRepository(session)
                ), audit_service=AuditService(AuditLogRepository(session)),
                handlers=default_moderation_handler_registry(),
            )
    finally:
        await database.dispose()


moderation_handler_registry = ModerationHandlerRegistry()


def default_moderation_handler_registry() -> ModerationHandlerRegistry:
    from app.modules.community.moderation_handler import register_community_handlers

    registry = ModerationHandlerRegistry()
    register_community_handlers(registry)
    return registry
