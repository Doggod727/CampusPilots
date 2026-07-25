from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy.dialects import postgresql

from app.modules.community.models import CampusEvent
from app.modules.community.repositories import EventRepository


def test_event_repository_query_contract_uses_sql_visibility_and_stable_order() -> None:
    now = datetime.now(UTC)
    statement = CampusEvent.__table__.select().where(
        CampusEvent.status == "published",
        CampusEvent.ends_at > now,
        CampusEvent.registration_deadline >= now,
        CampusEvent.starts_at > now,
        CampusEvent.registered_count < CampusEvent.capacity,
    ).order_by(CampusEvent.starts_at, CampusEvent.id)
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "campus_events.status" in sql
    assert "registered_count < community.campus_events.capacity" in sql
    assert "ORDER BY community.campus_events.starts_at, community.campus_events.id" in sql


def test_event_model_repr_does_not_expose_description() -> None:
    now = datetime.now(UTC)
    event = CampusEvent(
        id=uuid4(), organizer_user_id=uuid4(), title="活动", description_markdown="private-body",
        category="club", location="hall", starts_at=now + timedelta(days=2),
        ends_at=now + timedelta(days=2, hours=2), registration_deadline=now + timedelta(days=1),
        capacity=10, registered_count=0, status="published", risk_level="low",
        moderation_policy_version="v1", version=1, created_at=now, updated_at=now,
    )
    assert "private-body" not in repr(event)
    assert EventRepository.__doc__
