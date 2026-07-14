from argon2 import PasswordHasher as Argon2PasswordHasher
from argon2.exceptions import InvalidHash, VerificationError
from argon2.low_level import Type


class PasswordHasher:
    """Argon2id adapter for hashing and verifying stored passwords."""

    def __init__(self) -> None:
        self._hasher = Argon2PasswordHasher(type=Type.ID)

    def hash(self, password: str) -> str:
        return self._hasher.hash(password)

    def verify(self, password_hash: str, password: str) -> bool:
        try:
            return self._hasher.verify(password_hash, password)
        except (InvalidHash, VerificationError):
            return False

    def needs_rehash(self, password_hash: str) -> bool:
        return self._hasher.check_needs_rehash(password_hash)
