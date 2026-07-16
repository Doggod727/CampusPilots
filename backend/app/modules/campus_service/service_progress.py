import asyncio
import hashlib
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.modules.platform.audit import AuditService

logger = logging.getLogger(__name__)

SUPPORTED_SYSTEMS = frozenset({"student_affairs", "academic_affairs"})
ALLOWED_STATUSES = frozenset(
    {"submitted", "reviewing", "approved", "rejected", "completed"}
)


class CampusSystemTimeout(Exception):
    pass


class CampusSystemPort(Protocol):
    async def query_progress(
        self, *, system_code: str, business_no: str, actor_user_id: UUID
    ) -> Mapping[str, object] | None: ...


class CampusSystemUnsupported(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=422, code="CAMPUS_SYSTEM_UNSUPPORTED", message="不支持的校园系统")


class ServiceProgressNotFound(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=404, code="SERVICE_PROGRESS_NOT_FOUND", message="事项进度不存在")


class CampusSystemUnavailable(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=503, code="CAMPUS_SYSTEM_UNAVAILABLE", message="校园系统暂不可用")


class CampusSystemTimedOut(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=503, code="CAMPUS_SYSTEM_TIMEOUT", message="校园系统查询超时")


class CampusSystemInvalidResponse(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=503, code="CAMPUS_SYSTEM_INVALID_RESPONSE", message="校园系统返回无效数据")


@dataclass(frozen=True)
class ServiceProgress:
    system_code: str
    business_no_masked: str
    status: str
    status_text: str
    next_action: str | None
    updated_at: datetime
    source: str


class MockCampusSystemAdapter:
    """Actor-scoped deterministic fixture adapter used only in demo mode."""

    def __init__(
        self,
        owner_user_id: UUID,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._owner_user_id = owner_user_id
        self._now = now or (lambda: datetime.now(UTC))

    async def query_progress(
        self, *, system_code: str, business_no: str, actor_user_id: UUID
    ) -> Mapping[str, object] | None:
        if actor_user_id != self._owner_user_id:
            return None
        if business_no == "MOCK-TIMEOUT":
            raise CampusSystemTimeout
        if business_no == "MOCK-INVALID":
            return {"status": "unknown", "updated_at": "not-a-date"}
        if system_code == "student_affairs" and business_no == "SA20260001":
            return self._fixture("reviewing", "审核中", "等待审核结果")
        if system_code == "academic_affairs" and business_no == "AA20260002":
            return self._fixture("completed", "已完成", None)
        return None

    def _fixture(self, status: str, status_text: str, next_action: str | None) -> Mapping[str, object]:
        return {
            "status": status,
            "status_text": status_text,
            "next_action": next_action,
            "updated_at": self._now(),
            "source": "mock",
        }


class ServiceProgressService:
    def __init__(
        self,
        *,
        adapter: CampusSystemPort | None,
        session: AsyncSession | None = None,
        audit: AuditService | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._adapter = adapter
        self._session = session
        self._audit = audit
        self._sleep = sleep
        self._monotonic = monotonic

    async def query(
        self,
        *,
        actor_user_id: UUID,
        actor_username: str,
        system_code: str,
        business_no: str,
        request_id: str,
    ) -> ServiceProgress:
        started = self._monotonic()
        digest = hashlib.sha256(business_no.encode("utf-8")).hexdigest()
        safe = {
            "system_code": system_code,
            "business_no_sha256": digest,
            "business_no_last4": business_no[-4:],
        }
        if system_code not in SUPPORTED_SYSTEMS:
            error = CampusSystemUnsupported()
            await self._finish(actor_user_id, actor_username, request_id, safe, started, error.code)
            raise error
        if self._adapter is None:
            error = CampusSystemUnavailable()
            await self._finish(actor_user_id, actor_username, request_id, safe, started, error.code)
            raise error

        raw: Mapping[str, object] | None = None
        try:
            for attempt in range(2):
                try:
                    raw = await self._adapter.query_progress(
                        system_code=system_code,
                        business_no=business_no,
                        actor_user_id=actor_user_id,
                    )
                    break
                except CampusSystemTimeout:
                    if attempt == 0:
                        await self._sleep(0.05)
                    else:
                        raise
        except CampusSystemTimeout:
            error = CampusSystemTimedOut()
            await self._finish(actor_user_id, actor_username, request_id, safe, started, error.code)
            raise error

        if raw is None:
            error = ServiceProgressNotFound()
            await self._finish(actor_user_id, actor_username, request_id, safe, started, error.code)
            raise error
        try:
            result = self._validated(system_code, business_no, raw)
        except (KeyError, TypeError, ValueError):
            error = CampusSystemInvalidResponse()
            await self._finish(actor_user_id, actor_username, request_id, safe, started, error.code)
            raise error
        await self._finish(actor_user_id, actor_username, request_id, safe, started, "success")
        return result

    @staticmethod
    def _validated(system_code: str, business_no: str, raw: Mapping[str, object]) -> ServiceProgress:
        status = raw["status"]
        status_text = raw["status_text"]
        updated_at = raw["updated_at"]
        source = raw["source"]
        next_action = raw.get("next_action")
        if not isinstance(status, str) or status not in ALLOWED_STATUSES:
            raise ValueError
        if not isinstance(status_text, str) or not status_text:
            raise ValueError
        if next_action is not None and not isinstance(next_action, str):
            raise ValueError
        if not isinstance(updated_at, datetime) or updated_at.tzinfo is None:
            raise ValueError
        if source not in {"mock", "external"}:
            raise ValueError
        return ServiceProgress(
            system_code=system_code,
            business_no_masked="*" * max(0, len(business_no) - 4) + business_no[-4:],
            status=status,
            status_text=status_text,
            next_action=next_action,
            updated_at=updated_at.astimezone(UTC),
            source=str(source),
        )

    async def _finish(
        self,
        actor_user_id: UUID,
        actor_username: str,
        request_id: str,
        safe: dict[str, object],
        started: float,
        result: str,
    ) -> None:
        details = {**safe, "duration_ms": max(0, round((self._monotonic() - started) * 1000)), "result": result}
        logger.info("Campus service progress query finished", extra=details)
        if self._session is None or self._audit is None:
            return
        async with self._session.begin():
            recorder = self._audit.record_success if result == "success" else self._audit.record_failure
            kwargs = dict(
                action="service_progress.query",
                resource_type="service_progress",
                request_id=request_id,
                actor_user_id=actor_user_id,
                actor_username=actor_username,
                resource_id=str(safe["business_no_sha256"]),
                after_data=details,
            )
            if result == "success":
                recorder(**kwargs)
            else:
                recorder(**kwargs, error_code=result)
