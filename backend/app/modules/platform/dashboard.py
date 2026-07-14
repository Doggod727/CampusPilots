from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import date, timedelta
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.infrastructure.database import Database
from app.modules.platform.repositories import DashboardRepository


class DashboardProvider(Protocol):
    async def summary(self, *, from_date: date, to_date: date) -> Mapping[str, int]: ...


class InvalidDashboardRange(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=422, code="INVALID_DATE_RANGE", message="看板日期范围无效")


class DashboardQueryService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        repository: DashboardRepository,
        providers: Mapping[str, DashboardProvider] | None = None,
    ) -> None:
        self._session = session
        self._repository = repository
        self._providers = dict(providers or {})

    async def get_metrics(
        self, *, from_date: date | None = None, to_date: date | None = None,
        granularity: str = "day",
    ) -> dict[str, object]:
        today = date.today()
        start = from_date or (today - timedelta(days=6))
        end = to_date or today
        if start > end or (end - start).days > 366 or granularity not in {"day", "week"}:
            raise InvalidDashboardRange()
        summary = await self._repository.summary()
        for name, provider in self._providers.items():
            values = await provider.summary(from_date=start, to_date=end)
            for key, value in values.items():
                summary[key] = int(value)
        periods: list[date] = []
        cursor = start
        step = 7 if granularity == "week" else 1
        while cursor <= end:
            periods.append(cursor)
            cursor += timedelta(days=step)
        series = {
            key: [{"period": point.isoformat(), "value": summary.get(key, 0)} for point in periods]
            for key in (
                "active_users", "chat_messages", "work_orders", "posts",
                "lost_found_items", "moderation_pending", "llm_tokens",
            )
        }
        return {
            "from": start.isoformat(), "to": end.isoformat(),
            "granularity": granularity,
            "summary": {
                "active_users": summary.get("active_users", 0),
                "chat_messages": summary.get("chat_messages", 0),
                "work_orders": summary.get("work_orders", 0),
                "posts": summary.get("posts", 0),
                "lost_found_items": summary.get("lost_found_items", 0),
                "moderation_pending": summary.get("moderation_pending", 0),
                "llm_tokens": summary.get("llm_tokens", 0),
            },
            "series": series,
        }


@asynccontextmanager
async def dashboard_service_context(settings: Settings) -> AsyncIterator[DashboardQueryService]:
    database = Database.from_settings(settings)
    try:
        async with database.session() as session:
            yield DashboardQueryService(session=session, repository=DashboardRepository(session))
    finally:
        await database.dispose()
