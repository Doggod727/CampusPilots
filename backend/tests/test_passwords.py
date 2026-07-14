from argon2 import PasswordHasher as Argon2PasswordHasher
from argon2.low_level import Type

from app.modules.platform.passwords import PasswordHasher


def test_hash_uses_argon2id_and_verifies_password() -> None:
    hasher = PasswordHasher()

    password_hash = hasher.hash("DemoPass123!")

    assert password_hash.startswith("$argon2id$")
    assert hasher.verify(password_hash, "DemoPass123!") is True
    assert hasher.verify(password_hash, "incorrect-password") is False
    assert hasher.verify("not-an-argon2-hash", "DemoPass123!") is False


def test_hash_uses_a_unique_random_salt() -> None:
    hasher = PasswordHasher()

    first_hash = hasher.hash("DemoPass123!")
    second_hash = hasher.hash("DemoPass123!")

    assert first_hash != second_hash
    assert hasher.verify(first_hash, "DemoPass123!") is True
    assert hasher.verify(second_hash, "DemoPass123!") is True


def test_needs_rehash_detects_outdated_parameters() -> None:
    hasher = PasswordHasher()
    current_hash = hasher.hash("DemoPass123!")
    outdated_hasher = Argon2PasswordHasher(
        time_cost=1,
        memory_cost=8 * 1024,
        parallelism=1,
        type=Type.ID,
    )
    outdated_hash = outdated_hasher.hash("DemoPass123!")

    assert hasher.needs_rehash(current_hash) is False
    assert hasher.needs_rehash(outdated_hash) is True
