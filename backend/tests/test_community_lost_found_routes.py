import pytest
from pydantic import ValidationError

from app.main import create_app
from app.modules.community.lost_found_schemas import LostFoundUpdateRequest


def test_lost_found_crud_operation_ids_are_unique() -> None:
    expected = {"listLostFoundItems", "createLostFoundItem", "getLostFoundItem",
                "updateLostFoundItem", "deleteLostFoundItem"}
    ids = [route.operation_id for route in create_app().routes if getattr(route, "operation_id", None)]
    assert expected <= set(ids)
    assert all(ids.count(value) == 1 for value in expected)


@pytest.mark.parametrize("payload", [
    {"version": 1, "contact_type": "phone"},
    {"version": 1, "contact_value": "13800138000"},
    {"version": 1, "match_status": "ready"},
])
def test_lost_found_update_requires_contact_pair_and_forbids_extra(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        LostFoundUpdateRequest.model_validate(payload)
