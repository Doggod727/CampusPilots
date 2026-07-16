from cryptography.fernet import Fernet, InvalidToken
from pydantic import SecretStr

from app.core.errors import AppError


class CommunityEncryptionUnavailable(AppError):
    def __init__(self) -> None:
        super().__init__(
            status_code=503,
            code="COMMUNITY_ENCRYPTION_UNAVAILABLE",
            message="社区敏感数据加密暂不可用",
        )


class CommunityEncryptedDataInvalid(AppError):
    def __init__(self) -> None:
        super().__init__(
            status_code=500,
            code="COMMUNITY_ENCRYPTED_DATA_INVALID",
            message="社区敏感数据无法安全读取",
        )


class CommunityCipher:
    """Authenticated encryption boundary that never retains plaintext."""

    def __init__(self, key: SecretStr | str | bytes | None) -> None:
        if key is None:
            raise CommunityEncryptionUnavailable()
        raw = key.get_secret_value() if isinstance(key, SecretStr) else key
        encoded = raw.encode("ascii") if isinstance(raw, str) else raw
        try:
            self._fernet = Fernet(encoded)
        except (ValueError, TypeError):
            raise CommunityEncryptionUnavailable() from None

    def encrypt(self, plaintext: str) -> bytes:
        return self._fernet.encrypt(plaintext.encode("utf-8"))

    def decrypt(self, ciphertext: bytes) -> str:
        try:
            return self._fernet.decrypt(ciphertext).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError, TypeError):
            raise CommunityEncryptedDataInvalid() from None

    def __repr__(self) -> str:
        return "CommunityCipher(<redacted>)"
