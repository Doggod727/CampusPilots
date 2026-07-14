from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from secrets import token_urlsafe
from uuid import UUID, uuid4

import jwt
from jwt import InvalidTokenError as PyJwtInvalidTokenError

from app.core.config import Settings

JWT_ALGORITHM = "HS256"
REQUIRED_ACCESS_CLAIMS = (
    "sub",
    "username",
    "roles",
    "permissions",
    "iat",
    "exp",
    "jti",
)


class InvalidAccessToken(Exception):
    """Raised when an Access Token cannot be safely accepted."""

    def __init__(self) -> None:
        super().__init__("Access token is invalid.")


@dataclass(frozen=True)
class IssuedAccessToken:
    token: str = field(repr=False)
    expires_at: datetime
    jti: UUID


@dataclass(frozen=True)
class AccessClaims:
    user_id: UUID
    username: str
    roles: tuple[str, ...]
    permissions: tuple[str, ...]
    issued_at: datetime
    expires_at: datetime
    jti: UUID


@dataclass(frozen=True)
class IssuedRefreshToken:
    token: str = field(repr=False)
    jti: UUID
    token_hash: str = field(repr=False)
    expires_at: datetime


class TokenService:
    """Issue and validate the stateless token primitives used by platform auth."""

    def __init__(
        self,
        settings: Settings,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._secret = settings.jwt_secret.get_secret_value()
        self._access_token_minutes = settings.access_token_minutes
        self._refresh_token_days = settings.refresh_token_days
        self._now = now if now is not None else lambda: datetime.now(UTC)

    def issue_access(
        self,
        *,
        user_id: UUID,
        username: str,
        roles: Iterable[str],
        permissions: Iterable[str],
    ) -> IssuedAccessToken:
        issued_at = self._current_time()
        expires_at = issued_at + timedelta(minutes=self._access_token_minutes)
        jti = uuid4()
        payload = {
            "sub": str(user_id),
            "username": username,
            "roles": self._normalized_codes(roles),
            "permissions": self._normalized_codes(permissions),
            "iat": issued_at,
            "exp": expires_at,
            "jti": str(jti),
        }
        token = jwt.encode(payload, self._secret, algorithm=JWT_ALGORITHM)
        return IssuedAccessToken(token=token, expires_at=expires_at, jti=jti)

    def decode_access(self, token: str) -> AccessClaims:
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") != JWT_ALGORITHM:
                raise InvalidAccessToken
            payload = jwt.decode(
                token,
                self._secret,
                algorithms=[JWT_ALGORITHM],
                options={"require": list(REQUIRED_ACCESS_CLAIMS)},
            )
            return AccessClaims(
                user_id=UUID(self._required_string(payload, "sub")),
                username=self._required_string(payload, "username"),
                roles=self._claim_codes(payload, "roles"),
                permissions=self._claim_codes(payload, "permissions"),
                issued_at=self._timestamp_claim(payload, "iat"),
                expires_at=self._timestamp_claim(payload, "exp"),
                jti=UUID(self._required_string(payload, "jti")),
            )
        except (InvalidAccessToken, PyJwtInvalidTokenError, TypeError, ValueError):
            raise InvalidAccessToken from None

    def issue_refresh(self) -> IssuedRefreshToken:
        token = token_urlsafe(32)
        return IssuedRefreshToken(
            token=token,
            jti=uuid4(),
            token_hash=self.hash_refresh(token),
            expires_at=self._current_time()
            + timedelta(days=self._refresh_token_days),
        )

    @staticmethod
    def hash_refresh(token: str) -> str:
        """Return the only Refresh Token representation persisted in PostgreSQL."""

        return sha256(token.encode("utf-8")).hexdigest()

    def _current_time(self) -> datetime:
        now = self._now()
        if now.tzinfo is None:
            raise ValueError("Token clock must return a timezone-aware datetime.")
        return now.astimezone(UTC)

    @staticmethod
    def _normalized_codes(values: Iterable[str]) -> list[str]:
        values = list(values)
        if any(not isinstance(value, str) for value in values):
            raise TypeError("Token roles and permissions must be strings.")
        return sorted(set(values))

    @staticmethod
    def _required_string(payload: dict[str, object], name: str) -> str:
        value = payload[name]
        if not isinstance(value, str) or not value:
            raise ValueError(f"{name} must be a non-empty string.")
        return value

    @staticmethod
    def _claim_codes(payload: dict[str, object], name: str) -> tuple[str, ...]:
        values = payload[name]
        if not isinstance(values, list) or any(
            not isinstance(value, str) for value in values
        ):
            raise ValueError(f"{name} must be a list of strings.")
        return tuple(values)

    @staticmethod
    def _timestamp_claim(payload: dict[str, object], name: str) -> datetime:
        value = payload[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{name} must be a Unix timestamp.")
        return datetime.fromtimestamp(value, UTC)
