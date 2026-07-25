from app.modules.community.encryption import CommunityCipher
from app.modules.community.lost_found import contact_hint


def test_contact_hints_are_deterministic_and_minimal() -> None:
    assert contact_hint("email", "alice@example.edu") == "a***@example.edu"
    assert contact_hint("phone", "13800138000") == "***8000"
    assert contact_hint("wechat", "abc") == "***"
    assert contact_hint("other", "station-contact") == "***tact"


def test_contact_ciphertext_is_random_and_round_trips() -> None:
    from cryptography.fernet import Fernet
    cipher = CommunityCipher(Fernet.generate_key())
    first = cipher.encrypt("private-contact")
    second = cipher.encrypt("private-contact")
    assert first != second
    assert cipher.decrypt(first) == "private-contact"
    assert "private-contact" not in repr(cipher)
