"""
Configuration module for Pinewood One Backend
"""
import os
import secrets
from datetime import timedelta
from pathlib import Path

from cryptography.fernet import Fernet
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

BACKEND_ROOT = Path(__file__).resolve().parent
KEYS_DIR = BACKEND_ROOT / "keys"
FLASK_ENV = os.environ.get("FLASK_ENV", "development").strip().lower()


def _is_production() -> bool:
    return FLASK_ENV == "production"


def _read_secret_file(path: Path) -> str | None:
    if not path.exists():
        return None
    value = path.read_text(encoding="utf-8").strip()
    return value or None


def _write_secret_file(path: Path, value: str) -> str:
    KEYS_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    return value


def _load_secret(
    env_name: str,
    *,
    filename: str | None = None,
    generator=None,
    required_in_production: bool = True,
) -> str | None:
    value = os.environ.get(env_name)
    if value:
        return value

    file_path = KEYS_DIR / filename if filename else None
    if file_path:
        file_value = _read_secret_file(file_path)
        if file_value:
            return file_value

    if _is_production():
        if required_in_production:
            raise ValueError(
                f"{env_name} is required in production. Set the environment variable"
                f" or provision {file_path} before startup."
            )
        return None

    if generator is None or file_path is None:
        return None

    return _write_secret_file(file_path, generator())


class Config:
    """Application configuration"""

    ENVIRONMENT = FLASK_ENV

    # Flask configuration
    SECRET_KEY = _load_secret(
        "FLASK_SECRET_KEY",
        filename="flask_secret.txt",
        generator=lambda: secrets.token_urlsafe(48),
    )
    SESSION_LIFETIME = timedelta(days=7)

    # URLs
    FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:3112")
    BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:3111")

    # Google OAuth
    GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "your-client-id")
    GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "your-client-secret")

    # Schoology OAuth
    SCHOOLOGY_CONSUMER_KEY = os.environ.get("SCHOOLOGY_CONSUMER_KEY")
    SCHOOLOGY_CONSUMER_SECRET = os.environ.get("SCHOOLOGY_CONSUMER_SECRET")
    SCHOOLOGY_DOMAIN = os.environ.get("SCHOOLOGY_DOMAIN", "https://app.schoology.com")
    SCHOOLOGY_API_DOMAIN = os.environ.get("SCHOOLOGY_API_DOMAIN", "https://api.schoology.com")
    SCHOOLOGY_REFRESH_LEASE_TTL_SECONDS = int(
        os.environ.get("SCHOOLOGY_REFRESH_LEASE_TTL_SECONDS", "1800")
    )

    # Encryption
    ENCRYPTION_KEY = _load_secret(
        "ENCRYPTION_KEY",
        filename="encryption.key",
        generator=lambda: Fernet.generate_key().decode("utf-8"),
    )

    # Database paths
    MAIN_DB_PATH = os.environ.get("MAIN_DB_PATH", str(BACKEND_ROOT / "main.db"))
    SESSIONS_DB_PATH = os.environ.get("SESSIONS_DB_PATH", str(BACKEND_ROOT / "api_sessions.db"))
    SCRAPER_DB_PATH = os.environ.get("SCRAPER_DB_PATH", str(BACKEND_ROOT / "scraper.db"))
    CHAT_DB_PATH = os.environ.get("CHAT_DB_PATH", str(BACKEND_ROOT / "chat.db"))

    # Chat / LLM configuration
    LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://openrouter.ai/api/v1")
    LLM_API_KEY = os.environ.get("LLM_API_KEY")
    LLM_MODEL = os.environ.get("LLM_MODEL", "")

    # TinyFish web tools (Search + Fetch) for chat. Single shared account key,
    # passed as the X-API-Key header. Free tier, no credits.
    TINYFISH_API_KEY = os.environ.get("TINYFISH_API_KEY")
    LLM_CONNECT_TIMEOUT_SECONDS = float(os.environ.get("LLM_CONNECT_TIMEOUT_SECONDS", "10"))
    LLM_IDLE_TIMEOUT_SECONDS = float(os.environ.get("LLM_IDLE_TIMEOUT_SECONDS", "30"))
    CHAT_STALE_AFTER_SECONDS = int(os.environ.get("CHAT_STALE_AFTER_SECONDS", "120"))
    CHAT_HEARTBEAT_MS = int(
        os.environ.get("CHAT_HEARTBEAT_MS")
        or os.environ.get("CHAT_CONVEX_HEARTBEAT_MS", "5000")
    )
    CHAT_REAPER_INTERVAL_SECONDS = int(os.environ.get("CHAT_REAPER_INTERVAL_SECONDS", "60"))
    CHAT_SSE_HEARTBEAT_SECONDS = int(os.environ.get("CHAT_SSE_HEARTBEAT_SECONDS", "15"))
    APP_EVENTS_TTL_SECONDS = int(os.environ.get("APP_EVENTS_TTL_SECONDS", "3600"))
    APP_EVENTS_SSE_HEARTBEAT_SECONDS = int(
        os.environ.get("APP_EVENTS_SSE_HEARTBEAT_SECONDS", "15")
    )
    CHAT_REDIS_ACTIVE_TTL_SECONDS = int(os.environ.get("CHAT_REDIS_ACTIVE_TTL_SECONDS", "3600"))
    CHAT_REDIS_FINAL_TTL_SECONDS = int(os.environ.get("CHAT_REDIS_FINAL_TTL_SECONDS", "600"))
    UPSTASH_REDIS_URL = os.environ.get("UPSTASH_REDIS_URL")
    DOCUMENT_SUMMARY_MODEL = os.environ.get("DOCUMENT_SUMMARY_MODEL", "gpt-5-mini")
    DOC_DETAIL_MAX_CHARS = int(os.environ.get("DOC_DETAIL_MAX_CHARS", "65536"))

    # Mobile auth/token configuration
    MOBILE_ACCESS_TOKEN_TTL_SECONDS = int(os.environ.get("MOBILE_ACCESS_TOKEN_TTL_SECONDS", "900"))
    MOBILE_REFRESH_TOKEN_TTL_DAYS = int(os.environ.get("MOBILE_REFRESH_TOKEN_TTL_DAYS", "30"))
    MOBILE_AUTH_CODE_TTL_SECONDS = int(os.environ.get("MOBILE_AUTH_CODE_TTL_SECONDS", "120"))
    MOBILE_WEB_TICKET_TTL_SECONDS = int(os.environ.get("MOBILE_WEB_TICKET_TTL_SECONDS", "60"))
    MOBILE_STATE_MAX_AGE_SECONDS = int(os.environ.get("MOBILE_STATE_MAX_AGE_SECONDS", "300"))
    MOBILE_SCHOOLOGY_REQUEST_TTL_SECONDS = int(
        os.environ.get("MOBILE_SCHOOLOGY_REQUEST_TTL_SECONDS", "300")
    )
    MOBILE_TOKEN_HASH_SECRET = _load_secret(
        "MOBILE_TOKEN_HASH_SECRET",
        filename="mobile_token_hash_secret.txt",
        generator=lambda: secrets.token_urlsafe(48),
    )
    MOBILE_ALLOWED_REDIRECT_URIS = [
        value.strip()
        for value in os.environ.get(
            "MOBILE_ALLOWED_REDIRECT_URIS",
            "pinewoodone://auth/callback",
        ).split(",")
        if value.strip()
    ]

    # Rate limiting
    RATELIMIT_STORAGE_URI = os.environ.get("RATELIMIT_STORAGE_URI", "memory://")

    # Mobile banner metadata
    BANNER_UPCOMING_IMAGE_URL = os.environ.get("BANNER_UPCOMING_IMAGE_URL")
    BANNER_UPCOMING_VERSION = os.environ.get("BANNER_UPCOMING_VERSION", "v1")
    BANNER_UPCOMING_CACHE_TTL_SECONDS = int(
        os.environ.get("BANNER_UPCOMING_CACHE_TTL_SECONDS", "86400")
    )

    # Scraper configuration
    SCRAPER_SYNC_INTERVAL_MINUTES = int(os.environ.get("SCRAPER_SYNC_INTERVAL_MINUTES", "10"))
    SCRAPER_SCHEDULER_POLL_SECONDS = int(
        os.environ.get("SCRAPER_SCHEDULER_POLL_SECONDS", "60")
    )
    SCRAPER_MAX_SECTION_CONCURRENCY = int(os.environ.get("SCRAPER_MAX_SECTION_CONCURRENCY", "2"))
    SCRAPER_MULTIGET_BATCH_SIZE = int(os.environ.get("SCRAPER_MULTIGET_BATCH_SIZE", "50"))
    SCRAPER_LEASE_STALE_SECONDS = int(os.environ.get("SCRAPER_LEASE_STALE_SECONDS", "300"))
    SCRAPER_HEARTBEAT_SECONDS = int(os.environ.get("SCRAPER_HEARTBEAT_SECONDS", "30"))
    SCRAPER_SECTION_MAX_RETRIES = int(os.environ.get("SCRAPER_SECTION_MAX_RETRIES", "3"))
    SCRAPER_STORAGE_ROOT = os.environ.get(
        "SCRAPER_STORAGE_ROOT",
        str(BACKEND_ROOT / "storage" / "schoology"),
    )
    GOOGLE_DRIVE_TOKEN_FILE = os.environ.get(
        "GOOGLE_DRIVE_TOKEN_FILE",
        str(KEYS_DIR / "google_drive_token.json"),
    )
    GOOGLE_DRIVE_CLIENT_SECRET_FILE = os.environ.get(
        "GOOGLE_DRIVE_CLIENT_SECRET_FILE",
        str(KEYS_DIR / "google_drive_client_secret.json"),
    )
    GOOGLE_DRIVE_ENABLE_INTERACTIVE_AUTH = os.environ.get(
        "GOOGLE_DRIVE_ENABLE_INTERACTIVE_AUTH",
        "0",
    ).strip().lower() in {"1", "true", "yes", "on"}

    @classmethod
    def is_production(cls) -> bool:
        return cls.ENVIRONMENT == "production"

    @classmethod
    def validate(cls):
        """Validate configuration and print status"""
        if not cls.SCHOOLOGY_CONSUMER_KEY or not cls.SCHOOLOGY_CONSUMER_SECRET:
            print("WARNING: Schoology OAuth credentials not found in environment variables.")
            print("Set SCHOOLOGY_CONSUMER_KEY and SCHOOLOGY_CONSUMER_SECRET in backend/.env.")
        else:
            print("Schoology OAuth configured with official developer credentials.")
            print(f"Domain: {cls.SCHOOLOGY_DOMAIN}")

        if not Path(cls.GOOGLE_DRIVE_TOKEN_FILE).exists():
            print(
                "INFO: Google Drive scraper token not found. "
                "Google Drive link attachments will stay metadata-only until "
                "GOOGLE_DRIVE_TOKEN_FILE is provisioned."
            )

        if not cls.TINYFISH_API_KEY:
            print(
                "INFO: TINYFISH_API_KEY not set. Chat web tools (fetch_url, "
                "web_search) will be offered but fail until it is configured in "
                "backend/.env."
            )
