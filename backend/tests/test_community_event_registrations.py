from sqlalchemy.dialects import postgresql

from app.main import create_app
from app.modules.community.models import EventRegistration


def test_registration_operation_ids_are_unique() -> None:
    expected = {"listEventRegistrations", "registerCampusEvent", "cancelMyEventRegistration"}
    ids = [route.operation_id for route in create_app().routes if getattr(route, "operation_id", None)]
    assert expected <= set(ids)
    assert all(ids.count(value) == 1 for value in expected)


def test_registration_timeline_has_stable_order_contract() -> None:
    statement = EventRegistration.__table__.select().order_by(
        EventRegistration.registered_at, EventRegistration.user_id,
    )
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "ORDER BY community.event_registrations.registered_at, community.event_registrations.user_id" in sql
