from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Callable
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.community.errors import ContentReportTargetNotFound
from app.modules.community.models import ContentReport, Post
from app.modules.community.repositories import ReportRepository
from app.modules.platform.audit import AuditService
from app.modules.platform.auth import AuthenticatedUser
from app.modules.platform.idempotency import IdempotencyConflict, IdempotencyService
from app.modules.platform.moderation import ModerationService
from app.modules.platform.moderation_scan import ScanResult


@dataclass(frozen=True)
class ContentReportData:
    id: UUID
    target_type: str
    target_id: UUID
    reason_code: str
    status: str
    moderation_case_id: UUID | None
    created_at: datetime


@dataclass(frozen=True)
class ReportMutationResult:
    status_code: int
    request_id: str
    body: dict[str, object] = field(repr=False)


class ReportService:
    def __init__(
        self, *, session: AsyncSession, repository: ReportRepository,
        moderation: ModerationService, idempotency: IdempotencyService,
        audit: AuditService, now: Callable[[], datetime] | None = None,
    ) -> None:
        self._session = session; self._repository = repository
        self._moderation = moderation; self._idempotency = idempotency
        self._audit = audit; self._now = now or (lambda: datetime.now(UTC))

    async def submit(
        self, *, actor: AuthenticatedUser, target_type: str, target_id: UUID,
        reason_code: str, details: str, idempotency_key: str,
        request_id: str, request_body: object,
    ) -> ReportMutationResult:
        async with self._session.begin():
            idem = await self._idempotency.begin(
                user_id=actor.user_id, endpoint="POST /api/v1/reports",
                idempotency_key=idempotency_key, request_body=request_body,
            )
            if idem.replay is not None:
                return ReportMutationResult(idem.replay.response_status,
                    str(idem.replay.response_body["request_id"]), dict(idem.replay.response_body))
            if idem.pending:
                raise IdempotencyConflict()
            target = await self._repository.get_target_for_update(
                target_type=target_type, target_id=target_id, user_id=actor.user_id,
                moderator="community:moderate" in actor.permissions,
            )
            if target is None:
                raise ContentReportTargetNotFound()
            existing = await self._repository.get_existing(
                reporter_user_id=actor.user_id, target_type=target_type, target_id=target_id,
            )
            now = self._time()
            if existing is None:
                case = await self._repository.get_pending_case(
                    target_type=target_type, target_id=target_id,
                )
                if case is None:
                    result = ScanResult("review", "high", (), "community-report-v1", details)
                    case = await self._moderation.submit_case(
                        result=result, target_module="community", target_type=target_type,
                        target_id=target_id, content=details, submitted_by=actor.user_id,
                        actor=actor, request_id=request_id,
                    )
                    assert case is not None
                target.item.moderation_case_id = case.id
                existing = ContentReport(
                    id=uuid4(), reporter_user_id=actor.user_id, target_type=target_type,
                    target_id=target_id, reason_code=reason_code, details=details,
                    status="linked", moderation_case_id=case.id,
                    created_at=now, updated_at=now,
                )
                self._repository.add(existing)
                if isinstance(target.item, Post):
                    await self._repository.increment_post_report_count(target_id)
                await self._session.flush()
                self._audit.record_success(
                    action="community.report.create", resource_type="content_report",
                    resource_id=str(existing.id), request_id=request_id,
                    actor_user_id=actor.user_id, actor_username=actor.username,
                    after_data={"id": str(existing.id), "target_type": target_type,
                                "target_id": str(target_id), "reason_code": reason_code,
                                "status": "linked", "moderation_case_id": str(case.id)},
                )
            body = report_response_body(existing, request_id=request_id, timestamp=now)
            if not await self._idempotency.complete(
                record_id=idem.record_id, response_status=201, response_body=body,
                resource_type="content_report", resource_id=str(existing.id),
            ):
                raise IdempotencyConflict()
            return ReportMutationResult(201, request_id, body)

    def _time(self) -> datetime:
        value = self._now()
        return value if value.tzinfo else value.replace(tzinfo=UTC)


def report_response_body(item: ContentReport, *, request_id: str, timestamp: datetime) -> dict[str, object]:
    return {"code": "OK", "message": "success", "data": {
        "id": str(item.id), "target_type": item.target_type, "target_id": str(item.target_id),
        "reason_code": item.reason_code, "status": item.status,
        "moderation_case_id": str(item.moderation_case_id) if item.moderation_case_id else None,
        "created_at": item.created_at.isoformat(),
    }, "request_id": request_id, "timestamp": timestamp.isoformat()}
