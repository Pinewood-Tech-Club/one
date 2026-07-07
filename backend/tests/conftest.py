"""
Shared pytest fixtures and environment bootstrap.

The backend is not an installed package and several modules read required
configuration at import time (e.g. db/encryption.py builds a Fernet instance
from Config.ENCRYPTION_KEY on import). So we must:
  1. Put the backend root on sys.path so `import config`, `import app`, etc. work.
  2. Populate the required env vars BEFORE any backend module is imported,
     using a *valid* Fernet key, so imports are deterministic and never touch
     the real keys/ files.
"""
import os
import sys
from pathlib import Path

# --- 1. Make the backend root importable -----------------------------------
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# --- 2. Set required env vars before importing anything backend-related ----
# Force development mode so Config never raises on missing production secrets.
os.environ.setdefault("FLASK_ENV", "development")

# A valid Fernet key is required by db/encryption.py at import time.
from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("ENCRYPTION_KEY", Fernet.generate_key().decode("utf-8"))
os.environ.setdefault("FLASK_SECRET_KEY", "test-secret-key-not-for-production")

# Deterministic RS256 keypair location / issuer for JWT tests.
os.environ.setdefault("JWT_ISSUER", "http://localhost:3111")

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def app():
    """Full Flask app from the application factory."""
    from app import create_app

    application = create_app()
    application.config.update(TESTING=True)
    return application


@pytest.fixture()
def client(app):
    """Flask test client."""
    return app.test_client()
