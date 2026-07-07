"""
Onboarding module: user onboarding state, backed by db.app_users (SQLite).

Kept as a thin re-export layer so existing call sites keep their import
paths; all functions take an int user_id (the Convex-era convex_url first
parameter is gone).
"""
from db.app_users import (
    ensure_app_state as get_or_create_user,
    get_user_app_state as get_user,
    save_consent,
    set_schoology_connected as update_schoology_connected,
    update_onboarding_step,
)

__all__ = [
    "get_or_create_user",
    "get_user",
    "update_onboarding_step",
    "update_schoology_connected",
    "save_consent",
]
