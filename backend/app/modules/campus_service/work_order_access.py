import logging
import re
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.platform.models import AppConfig

logger = logging.getLogger(__name__)

WORK_ORDER_SCOPES_KEY = "campus_service.work_order_service_scopes"
_CAMPUS_CODE = re.compile(r"^[a-z][a-z0-9_-]{1,29}$")


@dataclass(frozen=True)
class WorkOrderScope:
    campus_code: str
    dormitory_areas: tuple[str, ...]


def parse_work_order_scopes(value: object, user_id: UUID) -> tuple[WorkOrderScope, ...]:
    """Parse the complete config strictly; malformed configuration grants nothing."""

    try:
        if not isinstance(value, dict) or set(value) != {"users"}:
            raise ValueError
        users = value["users"]
        if not isinstance(users, dict):
            raise ValueError
        parsed: dict[UUID, tuple[WorkOrderScope, ...]] = {}
        for raw_user_id, raw_scopes in users.items():
            parsed_user_id = UUID(raw_user_id) if isinstance(raw_user_id, str) else None
            if parsed_user_id is None or not isinstance(raw_scopes, list):
                raise ValueError
            scopes: list[WorkOrderScope] = []
            for raw_scope in raw_scopes:
                if not isinstance(raw_scope, dict) or set(raw_scope) != {
                    "campus_code",
                    "dormitory_areas",
                }:
                    raise ValueError
                campus_code = raw_scope["campus_code"]
                areas = raw_scope["dormitory_areas"]
                if (
                    not isinstance(campus_code, str)
                    or _CAMPUS_CODE.fullmatch(campus_code) is None
                    or not isinstance(areas, list)
                    or not areas
                    or any(
                        not isinstance(area, str)
                        or not area.strip()
                        or len(area.strip()) > 100
                        for area in areas
                    )
                ):
                    raise ValueError
                normalized = tuple(dict.fromkeys(area.strip() for area in areas))
                scopes.append(WorkOrderScope(campus_code, normalized))
            parsed[parsed_user_id] = tuple(scopes)
        return parsed.get(user_id, ())
    except (ValueError, TypeError, AttributeError):
        logger.warning("Invalid work-order service scope configuration; denying staff scope")
        return ()


class WorkOrderScopeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_for_user(self, user_id: UUID) -> tuple[WorkOrderScope, ...]:
        statement = select(AppConfig.value, AppConfig.value_type).where(
            AppConfig.key == WORK_ORDER_SCOPES_KEY
        )
        row = (await self._session.execute(statement)).one_or_none()
        if row is None or row.value_type != "json":
            return ()
        return parse_work_order_scopes(row.value, user_id)
