from datetime import datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict

DataT = TypeVar("DataT")


class SuccessResponse(BaseModel, Generic[DataT]):
    model_config = ConfigDict(extra="forbid")

    code: str = "OK"
    message: str = "success"
    data: DataT
    request_id: str
    timestamp: datetime
