from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.modules.campus_service.electricity import (
    ElectricityBalance,
    ElectricityTopupResult,
)
from app.shared.responses import SuccessResponse


class ElectricityBalanceData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    room_id: UUID
    room_name: str = Field(max_length=100)
    balance_cny: Decimal = Field(ge=0, decimal_places=2)
    source: Literal["mock"]
    is_simulated: Literal[True]
    as_of: datetime


class ElectricityTopupRequestData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    room_id: UUID
    amount_cny: Decimal = Field(
        ge=Decimal("1.00"),
        le=Decimal("500.00"),
        multiple_of=Decimal("0.01"),
    )
    approval_id: UUID | None = None
    agent_run_id: UUID | None = None


class ElectricityTopupData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    room_id: UUID
    amount_cny: Decimal = Field(decimal_places=2)
    status: Literal["simulated"]
    source: Literal["mock"]
    is_simulated: Literal[True]
    notice: str = Field(max_length=300)
    created_at: datetime


ElectricityBalanceResponse = SuccessResponse[ElectricityBalanceData]
ElectricityTopupResponse = SuccessResponse[ElectricityTopupData]


def electricity_balance_data(item: ElectricityBalance) -> ElectricityBalanceData:
    return ElectricityBalanceData(
        room_id=item.room_id,
        room_name=item.room_name,
        balance_cny=item.balance,
        source=item.source,
        is_simulated=item.is_simulated,
        as_of=item.updated_at,
    )


def electricity_topup_data(item: ElectricityTopupResult) -> ElectricityTopupData:
    return ElectricityTopupData(
        request_id=item.request_id,
        room_id=item.room_id,
        amount_cny=item.amount,
        status=item.status,
        source=item.source,
        is_simulated=item.is_simulated,
        notice=item.notice,
        created_at=item.created_at,
    )
