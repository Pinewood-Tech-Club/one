"""
Hermetic test environment for the Pinewood One backend.

config.Config reads environment variables at import time, and several modules
(db.encryption, auth.jwt_utils) derive module-level state from it. This
conftest therefore provisions throwaway secrets into os.environ BEFORE any
application module is imported, so:

  * no real secrets are required,
  * nothing is written into backend/keys/ (Config/_load_secret and
    jwt_utils._ensure_keys_exist only generate files when env vars are absent),
  * no network access or pre-existing database is needed.

Database paths are per-test tmp files (see fixtures below); db.pool caches
connections per path, so unique paths give full test isolation.
"""
import os
import secrets

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

# --- Hermetic environment: MUST run before importing any app module -------
#
# Everything here must be IDEMPOTENT (setdefault, not assignment): pytest
# imports this file as module `conftest`, and `from tests.conftest import ...`
# in a test module executes it a second time under the name `tests.conftest`.
# Unconditional assignments would regenerate the secrets on that second pass,
# silently desyncing os.environ from the app modules that already imported
# the first-pass values.

# Forced (not setdefault): a stray FLASK_ENV=production in the invoking shell
# would make _load_secret raise. Assigning a constant is naturally idempotent.
os.environ["FLASK_ENV"] = "development"
os.environ.setdefault("ENCRYPTION_KEY", Fernet.generate_key().decode("utf-8"))
os.environ.setdefault("FLASK_SECRET_KEY", secrets.token_urlsafe(48))
os.environ.setdefault("MOBILE_TOKEN_HASH_SECRET", secrets.token_urlsafe(48))

if "JWT_PRIVATE_KEY_PEM" not in os.environ:
    _private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    os.environ["JWT_PRIVATE_KEY_PEM"] = _private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    os.environ["JWT_PUBLIC_KEY_PEM"] = _private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")

TEST_PRIVATE_KEY_PEM = os.environ["JWT_PRIVATE_KEY_PEM"]
TEST_PUBLIC_KEY_PEM = os.environ["JWT_PUBLIC_KEY_PEM"]

# Point default DB paths somewhere harmless; individual tests override these
# with tmp_path-scoped files via the fixtures below.
os.environ.setdefault("SCRAPER_DB_PATH", "/nonexistent/should-be-overridden.db")
os.environ.setdefault("MAIN_DB_PATH", "/nonexistent/should-be-overridden.db")
os.environ.setdefault("SESSIONS_DB_PATH", "/nonexistent/should-be-overridden.db")

import pytest  # noqa: E402

from config import Config  # noqa: E402
from db.init import init_main_db, init_scraper_db  # noqa: E402

# Config currently hardcodes cwd-relative defaults for these two (the env vars
# above only take effect once config.py reads them), so also stomp the class
# attributes: a test that forgets the main_db fixture must fail loudly instead
# of silently writing a real main.db into the working directory.
Config.MAIN_DB_PATH = os.environ["MAIN_DB_PATH"]
Config.SESSIONS_DB_PATH = os.environ["SESSIONS_DB_PATH"]


@pytest.fixture()
def main_db(tmp_path, monkeypatch):
    """A fresh main.db (users, tokens, leases, mobile tables) in tmp_path."""
    db_path = str(tmp_path / "main.db")
    monkeypatch.setattr(Config, "MAIN_DB_PATH", db_path)
    init_main_db()
    return db_path


@pytest.fixture()
def scraper_db(tmp_path, monkeypatch):
    """A fresh scraper.db (sections, section_sync_runs, ...) in tmp_path."""
    db_path = str(tmp_path / "scraper.db")
    monkeypatch.setattr(Config, "SCRAPER_DB_PATH", db_path)
    monkeypatch.setattr(Config, "SCRAPER_STORAGE_ROOT", str(tmp_path / "storage"))
    init_scraper_db()
    return db_path
