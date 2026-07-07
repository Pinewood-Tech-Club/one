"""
Tests for db/encryption.py (Fernet token encryption) and the hashed
request-token lookup helper in db/init.py.
"""
import pytest
from cryptography.fernet import Fernet, InvalidToken

from db.encryption import ENCRYPTION_KEY_BYTES, encrypt_token, decrypt_token
from db.init import hash_schoology_request_token


class TestEncryptRoundtrip:
    def test_roundtrip_preserves_plaintext(self):
        plaintext = "oauth-access-token-abc123"
        ciphertext = encrypt_token(plaintext)
        assert decrypt_token(ciphertext) == plaintext

    def test_ciphertext_differs_from_plaintext(self):
        plaintext = "super-secret-token"
        ciphertext = encrypt_token(plaintext)
        assert ciphertext != plaintext
        assert plaintext not in ciphertext

    def test_roundtrip_unicode(self):
        plaintext = "tökén-ünïcødé-😀"
        assert decrypt_token(encrypt_token(plaintext)) == plaintext

    def test_encrypt_is_nondeterministic(self):
        # Fernet includes a random IV: identical plaintexts must not produce
        # identical ciphertexts (prevents equality-based inference in the DB).
        plaintext = "same-input"
        assert encrypt_token(plaintext) != encrypt_token(plaintext)

    def test_empty_and_none_inputs_return_none(self):
        assert encrypt_token(None) is None
        assert encrypt_token("") is None
        assert decrypt_token(None) is None
        assert decrypt_token("") is None


class TestDecryptFailureModes:
    def test_tampered_ciphertext_returns_none(self):
        ciphertext = encrypt_token("victim-token")
        # Flip a character in the middle of the Fernet token body.
        middle = len(ciphertext) // 2
        flipped = "A" if ciphertext[middle] != "A" else "B"
        tampered = ciphertext[:middle] + flipped + ciphertext[middle + 1:]
        assert decrypt_token(tampered) is None

    def test_garbage_ciphertext_returns_none(self):
        assert decrypt_token("not-a-fernet-token") is None
        assert decrypt_token("!!!###") is None

    def test_ciphertext_from_different_key_returns_none(self):
        other = Fernet(Fernet.generate_key())
        foreign_ciphertext = other.encrypt(b"foreign-token").decode()
        assert decrypt_token(foreign_ciphertext) is None

    def test_module_ciphertext_rejected_by_different_key(self):
        # Symmetric check at the raw Fernet level: our ciphertext must not
        # decrypt under any other key.
        ciphertext = encrypt_token("cross-key-check")
        other = Fernet(Fernet.generate_key())
        with pytest.raises(InvalidToken):
            other.decrypt(ciphertext.encode())

    def test_module_key_matches_configured_key(self):
        # The module must use exactly the configured ENCRYPTION_KEY.
        ciphertext = encrypt_token("key-binding-check")
        assert Fernet(ENCRYPTION_KEY_BYTES).decrypt(ciphertext.encode()) == b"key-binding-check"


class TestHashedRequestTokenLookup:
    def test_deterministic_for_same_input(self):
        assert hash_schoology_request_token("req-token-1") == hash_schoology_request_token("req-token-1")

    def test_stable_known_value(self):
        # SHA-256 of the UTF-8 bytes; pinned so accidental algorithm changes
        # (which would orphan every stored hash) fail loudly.
        import hashlib

        token = "stable-request-token"
        assert hash_schoology_request_token(token) == hashlib.sha256(token.encode("utf-8")).hexdigest()

    def test_differs_for_different_inputs(self):
        assert hash_schoology_request_token("token-a") != hash_schoology_request_token("token-b")

    def test_output_is_hex_sha256(self):
        digest = hash_schoology_request_token("anything")
        assert len(digest) == 64
        int(digest, 16)  # raises if not hex
