from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.campus_service.models import (
    ElectricityAccount,
    ElectricityAccountMember,
    ElectricityTopupRequest,
)


class ElectricityRepository:
    """Caller-owned-session persistence for mock electricity operations."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_account_for_user(
        self, room_id: UUID, user_id: UUID
    ) -> ElectricityAccount | None:
        statement = (
            select(ElectricityAccount)
            .join(
                ElectricityAccountMember,
                ElectricityAccountMember.room_id == ElectricityAccount.room_id,
            )
            .where(
                ElectricityAccount.room_id == room_id,
                ElectricityAccountMember.user_id == user_id,
            )
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def get_topup_for_update(
        self, requested_by: UUID, idempotency_key: str
    ) -> ElectricityTopupRequest | None:
        statement = (
            select(ElectricityTopupRequest)
            .where(
                ElectricityTopupRequest.requested_by == requested_by,
                ElectricityTopupRequest.idempotency_key == idempotency_key,
            )
            .with_for_update()
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    def add_topup(self, request: ElectricityTopupRequest) -> None:
        self._session.add(request)
