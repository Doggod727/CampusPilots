from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import UUID, uuid4
from unittest.mock import patch

import jwt
import pytest

from app.core.config import Settings
from app.infrastructure.database import Database
from app.modules.platform.tokens import (
    JWT_ALGORITHM,
    AccessClaims,
    InvalidAccessToken,
    TokenService,
)

TEST_SECRET = "token-service-test-secret-that-is-at-least-32-bytes"
WRONG_SECRET = "wrong-signing-secret-that-is-also-at-least-32-bytes"
FIXED_NOW = datetime.now(UTC).replace(microsecond=0)


def settings() -> Settings:
    return Settings(
        database_url="postgresql+asyncpg://user:password@localhost:5432/campuspilot",
        redis_url="redis://localhost:6379/0",
        jwt_secret=TEST_SECRET,
        frontend_origin="http://localhost:5173",
        deepseek_api_key="test-deepseek-key",
        access_token_minutes=15,
        refresh_token_days=7,
        _env_file=None,
    )


def token_service() -> TokenService:
    return TokenService(settings(), now=lambda: FIXED_NOW)


def access_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "sub": str(uuid4()),
        "username": "student01",
        "roles": ["student"],
        "permissions": ["community:read"],
        "iat": FIXED_NOW,
        "exp": FIXED_NOW + timedelta(minutes=15),
        "jti": str(uuid4()),
    }
    payload.update(overrides)
    return payload


def test_issue_and_decode_access_token_uses_required_hs256_claims() -> None:
    service = token_service()
    user_id = uuid4()

    issued = service.issue_access(
        user_id=user_id,
        username="student01",
        roles=["student", "student", "community_operator"],
        permissions=["community:write", "community:read", "community:read"],
    )
    header = jwt.get_unverified_header(issued.token)
    payload = jwt.decode(
        issued.token,
        TEST_SECRET,
        algorithms=[JWT_ALGORITHM],
    )
    claims = service.decode_access(issued.token)

    assert header["alg"] == JWT_ALGORITHM
    assert payload["sub"] == str(user_id)
    assert payload["username"] == "student01"
    assert payload["roles"] == ["community_operator", "student"]
    assert payload["permissions"] == ["community:read", "community:write"]
    assert set(payload) == {"sub", "username", "roles", "permissions", "iat", "exp", "jti"}
    assert issued.expires_at == FIXED_NOW + timedelta(minutes=15)
    assert claims == AccessClaims(
        user_id=user_id,
        username="student01",
        roles=("community_operator", "student"),
        permissions=("community:read", "community:write"),
        issued_at=FIXED_NOW,
        expires_at=FIXED_NOW + timedelta(minutes=15),
        jti=issued.jti,
    )
    assert issued.token not in repr(issued)


@pytest.mark.parametrize(
    "token",
    [
        jwt.encode(
            access_payload(exp=datetime.now(UTC) - timedelta(seconds=1)),
            TEST_SECRET,
            algorithm=JWT_ALGORITHM,
        ),
        jwt.encode(access_payload(), WRONG_SECRET, algorithm=JWT_ALGORITHM),
        jwt.encode(access_payload(), key="", algorithm="none"),
        jwt.encode(
            access_payload(permissions=None),
            TEST_SECRET,
            algorithm=JWT_ALGORITHM,
        ),
        jwt.encode(
            {"sub": str(uuid4())},
            TEST_SECRET,
            algorithm=JWT_ALGORITHM,
        ),
    ],
)
def test_decode_access_rejects_invalid_tokens_without_sensitive_details(token: str) -> None:
    with pytest.raises(InvalidAccessToken) as error:
        token_service().decode_access(token)

    assert str(error.value) == "Access token is invalid."
    assert token not in str(error.value)
    assert TEST_SECRET not in str(error.value)


def test_issue_refresh_generates_high_entropy_hashed_tokens() -> None:
    service = token_service()

    first = service.issue_refresh()
    second = service.issue_refresh()

    assert first.token != second.token
    assert first.jti != second.jti
    assert len(first.token) >= 43
    assert len(first.token_hash) == 64
    assert first.token_hash == sha256(first.token.encode("utf-8")).hexdigest()
    assert first.expires_at == FIXED_NOW + timedelta(days=7)
    assert UUID(str(first.jti)) == first.jti
    assert first.token not in repr(first)
    assert first.token_hash not in repr(first)


def test_hash_refresh_matches_the_persisted_token_hash() -> None:
    token = "refresh-token-presented-by-the-client"

    assert token_service().hash_refresh(token) == sha256(token.encode("utf-8")).hexdigest()


def test_token_service_does_not_initialize_database() -> None:
    with patch.object(Database, "from_settings") as from_settings:
        service = token_service()
        service.issue_refresh()

    from_settings.assert_not_called()
