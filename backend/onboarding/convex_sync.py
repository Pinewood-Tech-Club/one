"""
Convex synchronization functions for onboarding state.
"""
from typing import Any

from services.convex_bridge import call_bridge_action


def _action(name: str, args: dict[str, Any]) -> Any:
    return call_bridge_action(name, args)


def get_or_create_user(convex_url: str, user_id: str) -> dict:
    """
    Get or create user record in Convex.
    """
    _ = convex_url
    return _action("getOrCreateUser", {"userId": user_id})


def get_user(convex_url: str, user_id: str) -> dict | None:
    """
    Get user record from Convex.
    """
    _ = convex_url
    return _action("getUserByUserId", {"userId": user_id})


def list_eligible_scraper_users(convex_url: str) -> list[dict]:
    """
    List users currently eligible to act as scraper credential sources.
    """
    _ = convex_url
    result = _action("listEligibleScraperUsers", {})
    return result if isinstance(result, list) else []


def update_onboarding_step(convex_url: str, user_id: str, step: str) -> dict:
    """
    Update user's onboarding step.
    """
    _ = convex_url
    return _action("updateOnboardingStep", {"userId": user_id, "step": step})


def update_schoology_connected(convex_url: str, user_id: str, connected: bool) -> dict:
    """
    Update user's Schoology connection status.
    """
    _ = convex_url
    return _action(
        "updateSchoologyConnected",
        {"userId": user_id, "connected": connected},
    )


def save_consent(convex_url: str, user_id: str, consent: dict) -> dict:
    """
    Save smart features consent and mark onboarding as completed.
    """
    _ = convex_url
    return _action("saveConsent", {"userId": user_id, "consent": consent})
