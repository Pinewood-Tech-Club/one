"""
User app-state (onboarding, consent, profile picture) and preferences.

Ports frontend/convex/users.ts + userPreferences.ts onto columns of the
existing main.db users table. Every mutator stamps app_state_updated_at and
publishes a user.updated (or preferences.updated) app event after commit.
"""
import json
import time

from config import Config
from db.pool import get_conn
from services import events

ONBOARDING_STEPS = ("welcome", "connect_lms", "smart_consent", "completed")

_USER_COLUMNS = (
    "id, email, name, created_at, last_login, onboarding_step, "
    "schoology_connected, smart_features_consent_json, profile_picture_url, "
    "app_state_updated_at"
)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _fetch_user_row(executor, user_id: int):
    return executor.execute(
        f"SELECT {_USER_COLUMNS} FROM users WHERE id = ?", (user_id,)
    ).fetchone()


def _parse_consent(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def get_api_user(user_id: int) -> dict | None:
    """The GET /api/user response object; also the user.updated event payload."""
    conn = get_conn(Config.MAIN_DB_PATH)
    row = _fetch_user_row(conn, user_id)
    if not row:
        return None
    return {
        "user_id": row["id"],
        "email": row["email"],
        "name": row["name"],
        "created_at": row["created_at"],
        "last_login": row["last_login"],
        "onboarding_step": row["onboarding_step"],
        "schoology_connected": bool(row["schoology_connected"]),
        "profile_picture_url": row["profile_picture_url"],
        "smart_features_consent": _parse_consent(row["smart_features_consent_json"]),
    }


def get_user_app_state(user_id: int) -> dict | None:
    conn = get_conn(Config.MAIN_DB_PATH)
    row = _fetch_user_row(conn, user_id)
    if not row:
        return None
    return {
        "userId": str(row["id"]),
        "onboardingStep": row["onboarding_step"],
        "schoologyConnected": bool(row["schoology_connected"]),
        "smartFeaturesConsent": _parse_consent(row["smart_features_consent_json"]),
        "profilePictureUrl": row["profile_picture_url"],
        "updatedAt": row["app_state_updated_at"],
    }


def ensure_app_state(user_id: int) -> dict:
    """Convex users.getOrCreate: the users row already exists from Google login;
    app-state defaults are column defaults. Publishes user.updated only on
    first touch (app_state_updated_at still NULL)."""
    conn = get_conn(Config.MAIN_DB_PATH)
    cursor = conn.cursor()
    first_touch = False
    try:
        cursor.execute("BEGIN IMMEDIATE")
        row = _fetch_user_row(cursor, user_id)
        if not row:
            conn.commit()
            raise ValueError("User not found")
        if row["app_state_updated_at"] is None:
            first_touch = True
            cursor.execute(
                "UPDATE users SET app_state_updated_at = ? WHERE id = ?",
                (_now_ms(), user_id),
            )
        conn.commit()
    except ValueError:
        raise
    except Exception:
        conn.rollback()
        raise

    if first_touch:
        events.publish_user_event(user_id, "user.updated", get_api_user(user_id))
    state = get_user_app_state(user_id)
    assert state is not None
    return state


def _apply_user_update(user_id: int, assignments: str, params: tuple) -> dict:
    conn = get_conn(Config.MAIN_DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        f"UPDATE users SET {assignments}, app_state_updated_at = ? WHERE id = ?",
        (*params, _now_ms(), user_id),
    )
    if cursor.rowcount == 0:
        conn.rollback()
        raise ValueError("User not found")
    conn.commit()

    user = get_api_user(user_id)
    events.publish_user_event(user_id, "user.updated", user)
    return user


def update_onboarding_step(user_id: int, step: str) -> dict:
    if step not in ONBOARDING_STEPS:
        raise ValueError(f"Invalid onboarding step: {step}")
    return _apply_user_update(user_id, "onboarding_step = ?", (step,))


def set_schoology_connected(user_id: int, connected: bool) -> dict:
    return _apply_user_update(
        user_id, "schoology_connected = ?", (1 if connected else 0,)
    )


def set_profile_picture_url(user_id: int, url: str) -> dict:
    return _apply_user_update(user_id, "profile_picture_url = ?", (url,))


def save_consent(user_id: int, consent: dict) -> dict:
    """Sets consent AND completes onboarding, mirroring Convex users.saveConsent."""
    if not isinstance(consent, dict) or not isinstance(consent.get("enabled"), bool):
        raise ValueError("consent must be an object with a boolean 'enabled'")
    return _apply_user_update(
        user_id,
        "smart_features_consent_json = ?, onboarding_step = 'completed'",
        (json.dumps(consent, separators=(",", ":")),),
    )


def is_chat_entitled(user_id: int) -> bool:
    """chatModel.ts entitlement: record exists, onboarding completed, consent enabled."""
    state = get_user_app_state(user_id)
    if not state:
        return False
    if state["onboardingStep"] != "completed":
        return False
    consent = state["smartFeaturesConsent"]
    return bool(consent) and consent.get("enabled") is True


def list_eligible_scraper_users() -> list[dict]:
    conn = get_conn(Config.MAIN_DB_PATH)
    rows = conn.execute(
        """
        SELECT id, schoology_connected, smart_features_consent_json, app_state_updated_at
        FROM users
        WHERE schoology_connected = 1
        ORDER BY COALESCE(app_state_updated_at, 0) DESC
        """
    ).fetchall()

    eligible = []
    for row in rows:
        consent = _parse_consent(row["smart_features_consent_json"])
        if not consent or consent.get("enabled") is not True:
            continue
        eligible.append(
            {
                "userId": str(row["id"]),
                "schoologyConnected": True,
                "smartFeaturesConsent": consent,
                "updatedAt": row["app_state_updated_at"],
            }
        )
    return eligible


def get_sidebar_collapsed(user_id: int) -> bool:
    conn = get_conn(Config.MAIN_DB_PATH)
    row = conn.execute(
        "SELECT sidebar_collapsed FROM user_preferences WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    return bool(row["sidebar_collapsed"]) if row else False


def set_sidebar_collapsed(user_id: int, collapsed: bool) -> None:
    conn = get_conn(Config.MAIN_DB_PATH)
    conn.execute(
        """
        INSERT INTO user_preferences (user_id, sidebar_collapsed, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            sidebar_collapsed = excluded.sidebar_collapsed,
            updated_at = excluded.updated_at
        """,
        (user_id, 1 if collapsed else 0, _now_ms()),
    )
    conn.commit()
    events.publish_user_event(
        user_id, "preferences.updated", {"sidebar_collapsed": bool(collapsed)}
    )
