import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from app.modules.community.encryption import (
    CommunityCipher,
    CommunityEncryptedDataInvalid,
    CommunityEncryptionUnavailable,
)


def test_cipher_round_trip_uses_randomized_authenticated_ciphertext() -> None:
    key = Fernet.generate_key()
    cipher = CommunityCipher(SecretStr(key.decode("ascii")))

    first = cipher.encrypt("虚构联系方式 13800001234")
    second = cipher.encrypt("虚构联系方式 13800001234")

    assert first != second
    assert cipher.decrypt(first) == "虚构联系方式 13800001234"
    assert "13800001234" not in repr(cipher)
    assert key.decode("ascii") not in repr(cipher)


def test_cipher_missing_or_invalid_key_fails_without_import_side_effects() -> None:
    with pytest.raises(CommunityEncryptionUnavailable) as missing:
        CommunityCipher(None)
    with pytest.raises(CommunityEncryptionUnavailable) as invalid:
        CommunityCipher("not-a-fernet-key")

    rendered = f"{missing.value!r} {invalid.value!r}"
    assert "not-a-fernet-key" not in rendered


def test_cipher_rejects_tampering_wrong_keys_and_invalid_utf8_safely() -> None:
    cipher = CommunityCipher(Fernet.generate_key())
    ciphertext = cipher.encrypt("private-value")
    tampered = ciphertext[:-1] + bytes([ciphertext[-1] ^ 1])
    wrong = CommunityCipher(Fernet.generate_key())
    invalid_utf8 = cipher._fernet.encrypt(b"\xff")

    for candidate, decoder in ((tampered, cipher), (ciphertext, wrong), (invalid_utf8, cipher)):
        with pytest.raises(CommunityEncryptedDataInvalid) as error:
            decoder.decrypt(candidate)
        assert "private-value" not in repr(error.value)
