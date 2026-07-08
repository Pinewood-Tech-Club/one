"""
Tests for db/encryption.py (Fernet token encryption) and the hashed
request-token lookup helper in db/init.py.
"""
import pytest
from cryptography.fernet import Fernet, InvalidToken

from db.encryption import decrypt_token, encrypt_token
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
        # The module must use exactly the configured ENCRYPTION_KEY. Assert
        # against the env var directly (not the module's own re-exported
        # constant) so this fails if the module ever derives its key from a
        # different source while staying internally consistent.
        import os

        configured = Fernet(os.environ["ENCRYPTION_KEY"].encode())
        ciphertext = encrypt_token("key-binding-check")
        assert configured.decrypt(ciphertext.encode()) == b"key-binding-check"


class TestDecryptGuardVsExceptionPath:
    """Distinguish the empty-input guard from the exception fallback.

    Both paths return None, so a return-value assertion alone cannot tell them
    apart: deleting the `if not encrypted_token` guard leaves every test green
    because ""/None then fall through to the Fernet call, raise, get caught,
    and still return None. These tests pin the *side effect* — the guard must
    short-circuit silently, while genuinely invalid input must hit the logging
    except branch — so removing the guard (or the log) fails a test.
    """

    def test_empty_and_none_use_guard_not_exception_path(self, capsys):
        assert decrypt_token("") is None
        assert decrypt_token(None) is None
        captured = capsys.readouterr()
        # Guard returns before Fernet runs: the except branch must NOT execute.
        assert "Error decrypting token" not in captured.out
        assert "Error decrypting token" not in captured.err

    def test_invalid_input_reaches_logging_except_branch(self, capsys):
        # Non-empty but undecryptable input must flow through the except branch,
        # which logs. Asserts both that the branch is taken and its message.
        assert decrypt_token("not-a-fernet-token") is None
        captured = capsys.readouterr()
        assert "Error decrypting token" in captured.out

    def test_encrypt_empty_uses_guard_not_fernet(self):
        # Symmetric guard on the encrypt side: if the `if not token` guard were
        # removed, encrypt("") would return a real (decryptable) ciphertext
        # instead of None. Pin that "" -> None and any real value -> not None.
        assert encrypt_token("") is None
        assert encrypt_token(None) is None
        assert encrypt_token("x") is not None


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
