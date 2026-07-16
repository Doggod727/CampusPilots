from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.main import create_app
from app.modules.community.errors import CommunityMatchConfigInvalid
from app.modules.community.matcher import parse_config, score_match, tokens
from app.modules.community.models import LostFoundItem


def config_values() -> dict[str, object]:
    return {"community.match.category_weight": 0.35,
            "community.match.location_weight": 0.25,
            "community.match.time_weight": 0.20,
            "community.match.keyword_weight": 0.20,
            "community.match.threshold": 0.55,
            "community.match.time_window_days": 30}


def item(kind: str) -> LostFoundItem:
    return LostFoundItem(id=uuid4(), owner_user_id=uuid4(), item_type=kind,
        title="Black Wallet", category="wallet", description="black student card wallet",
        occurred_at=datetime.now(UTC), location="图书馆一楼", contact_type="other",
        contact_ciphertext=b"x", contact_hint="***", status="published", risk_level="low",
        moderation_policy_version="v1", version=1, created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC))


def test_matcher_is_deterministic_and_has_four_reasons() -> None:
    assert tokens("ＡＢＣ 图书馆一楼") == tokens("abc 图书馆一楼")
    score, reasons = score_match(item("lost"), item("found"), parse_config(config_values()))
    assert score.as_tuple().exponent == -5
    assert [reason["factor"] for reason in reasons] == ["category", "location", "time", "keyword"]


def test_invalid_weight_sum_fails_closed() -> None:
    values = config_values()
    values["community.match.keyword_weight"] = 0.25
    with pytest.raises(CommunityMatchConfigInvalid):
        parse_config(values)


def test_match_operation_id_registered_once() -> None:
    ids = [r.operation_id for r in create_app().routes if getattr(r, "operation_id", None)]
    assert ids.count("listLostFoundMatches") == 1
