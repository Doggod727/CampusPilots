from datetime import UTC, datetime
from uuid import uuid4

from app.main import create_app
from app.modules.community.claims import ClaimData
from app.modules.community.claim_schemas import ClaimDecisionRequest
from app.modules.community.lost_found import LostFoundItemData
from app.modules.community.posts import PublicAuthorData


def test_claim_operation_ids_are_unique() -> None:
    expected = {"createLostFoundClaim", "listMyLostFoundClaims", "getLostFoundClaim"}
    ids = [route.operation_id for route in create_app().routes if getattr(route, "operation_id", None)]
    assert expected <= set(ids)
    assert all(ids.count(value) == 1 for value in expected)


def test_claim_repr_does_not_expose_evidence() -> None:
    now = datetime.now(UTC)
    user = uuid4()
    author = PublicAuthorData(user, "user", None, False)
    item = LostFoundItemData(uuid4(), author, "lost", "title", "card", "description",
        now, "library", "other", "***", "claiming", None, now, None, 1, now, now)
    claim = ClaimData(uuid4(), item, None, author, "private-evidence", created_at=now, updated_at=now)
    assert "private-evidence" not in repr(claim)


def test_claim_completion_operation_ids_and_rejection_reason() -> None:
    import pytest
    from pydantic import ValidationError
    expected = {"decideLostFoundClaim", "getLostFoundClaimContact",
                "confirmLostFoundClaimCompletion"}
    ids = [route.operation_id for route in create_app().routes if getattr(route, "operation_id", None)]
    assert expected <= set(ids)
    assert all(ids.count(value) == 1 for value in expected)
    with pytest.raises(ValidationError):
        ClaimDecisionRequest.model_validate({"decision": "rejected", "reason": "", "version": 1})
