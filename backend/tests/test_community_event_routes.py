from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.main import create_app
from app.modules.community.event_schemas import EventCreateRequest, EventUpdateRequest


def test_event_operation_ids_are_registered_once() -> None:
    expected = {"listCampusEvents", "createCampusEvent", "getCampusEvent",
                "updateCampusEvent", "cancelCampusEvent"}
    ids = [route.operation_id for route in create_app().routes if getattr(route, "operation_id", None)]
    assert expected <= set(ids)
    assert all(ids.count(value) == 1 for value in expected)


def test_event_create_schema_is_strict_and_checks_time_order() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValidationError):
        EventCreateRequest.model_validate({
            "title": "活动", "description_markdown": "说明", "category": "club",
            "location": "礼堂", "starts_at": now + timedelta(days=1),
            "ends_at": now, "registration_deadline": now, "capacity": 10,
            "unexpected": True,
        })


def test_event_update_rejects_contract_extra_fields() -> None:
    with pytest.raises(ValidationError):
        EventUpdateRequest.model_validate({"version": 1, "status": "published"})
