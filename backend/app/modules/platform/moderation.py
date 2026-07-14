from datetime import UTC, datetime
from typing import Callable
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.modules.platform.audit import AuditService
from app.modules.platform.auth import AuthenticatedUser
from app.modules.platform.models import ModerationCase
from app.modules.platform.moderation_scan import ScanResult, SensitiveWordScanner
from app.modules.platform.repositories import ModerationCaseRepository

VALID_TARGET_MODULES = {"ai_knowledge", "campus_service", "community"}


class InvalidModerationTarget(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=422, code="INVALID_MODERATION_TARGET", message="审核目标无效")


class ModerationService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        scanner: SensitiveWordScanner,
        repository: ModerationCaseRepository,
        audit_service: AuditService,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._session = session
        self._scanner = scanner
        self._repository = repository
        self._audit_service = audit_service
        self._now = now if now is not None else lambda: datetime.now(UTC)

    async def scan(self, *, scope: str, text: str) -> ScanResult:
        return await self._scanner.scan(scope=scope, text=text)

    async def submit_case(
        self,
        *,
        result: ScanResult,
        target_module: str,
        target_type: str,
        target_id: UUID,
        content: str,
        submitted_by: UUID | None,
        actor: AuthenticatedUser | None,
        request_id: str,
    ) -> ModerationCase | None:
        if target_module not in VALID_TARGET_MODULES:
            raise InvalidModerationTarget()
        if result.action not in {"review", "block"}:
            return None
        now = self._current_time()
        case = ModerationCase(
            id=uuid4(), target_module=target_module, target_type=target_type,
            target_id=target_id, content_excerpt=content[:500],
            risk_level=result.risk_level,
            rule_hits=[{"rule": hit.rule, "action": hit.action} for hit in result.hits],
            status="pending", submitted_by=submitted_by, version=1,
            created_at=now, updated_at=now,
        )
        self._repository.add(case)
        self._audit_service.record_success(
            action="moderation.case.submit", resource_type="moderation_case",
            resource_id=str(case.id), request_id=request_id,
            actor_user_id=actor.user_id if actor else submitted_by,
            actor_username=actor.username if actor else None,
            after_data={
                "id": str(case.id), "target_module": target_module,
                "target_type": target_type, "target_id": str(target_id),
                "risk_level": result.risk_level, "status": "pending",
                "rule_hits": case.rule_hits,
            },
        )
        return case

    def _current_time(self) -> datetime:
        value = self._now()
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
