"""
Tests for db/encryption.py — token encryption at rest (Fernet).

Security properties under test:
  - round-trip fidelity (encrypt then decrypt yields the original secret)
  - ciphertext is not plaintext
  - None / empty inputs are handled without raising
  - tampered / malformed ciphertext FAILS CLOSED (returns None, never the plaintext)
"""
from db.encryption import decrypt_token, encrypt_token


def test_round_trip():
    token = "super-secret-oauth-access-token-12345"
    encrypted = encrypt_token(token)
    assert encrypted is not None
    assert encrypted != token  # must not store plaintext
    assert decrypt_token(encrypted) == token


def test_round_trip_unicode():
    token = "tökén-ünïcodé-🔐"
    assert decrypt_token(encrypt_token(token)) == token


def test_encrypt_is_nondeterministic():
    # Fernet includes a random IV, so two encryptions differ but both decrypt.
    token = "same-input"
    a = encrypt_token(token)
    b = encrypt_token(token)
    assert a != b
    assert decrypt_token(a) == token
    assert decrypt_token(b) == token


def test_encrypt_none_and_empty_return_none():
    assert encrypt_token(None) is None
    assert encrypt_token("") is None


def test_decrypt_none_and_empty_return_none():
    assert decrypt_token(None) is None
    assert decrypt_token("") is None


def test_tampered_ciphertext_fails_closed():
    token = "another-secret"
    encrypted = encrypt_token(token)
    # Flip the last character to corrupt the token / auth tag.
    tampered = encrypted[:-1] + ("A" if encrypted[-1] != "A" else "B")
    result = decrypt_token(tampered)
    assert result is None  # must NOT return the plaintext, must NOT raise


def test_garbage_ciphertext_fails_closed():
    assert decrypt_token("this-is-not-a-valid-fernet-token") is None
