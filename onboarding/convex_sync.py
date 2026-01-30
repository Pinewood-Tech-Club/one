"""
Convex synchronization functions for onboarding state
"""
from convex import ConvexClient


def _get_client(convex_url: str) -> ConvexClient:
    """Get a Convex client instance"""
    return ConvexClient(convex_url)


def get_or_create_user(convex_url: str, user_id: str) -> dict:
    """
    Get or create user record in Convex

    Args:
        convex_url: Convex deployment URL
        user_id: User ID (string)

    Returns:
        User record from Convex
    """
    client = _get_client(convex_url)

    result = client.mutation("users:getOrCreate", {
        "userId": user_id,
    })

    return result


def get_user(convex_url: str, user_id: str) -> dict | None:
    """
    Get user record from Convex

    Args:
        convex_url: Convex deployment URL
        user_id: User ID (string)

    Returns:
        User record or None if not found
    """
    client = _get_client(convex_url)

    result = client.query("users:getUserByUserId", {
        "userId": user_id,
    })

    return result


def update_onboarding_step(convex_url: str, user_id: str, step: str) -> dict:
    """
    Update user's onboarding step

    Args:
        convex_url: Convex deployment URL
        user_id: User ID (string)
        step: Onboarding step ("welcome", "connect_lms", "smart_consent", "completed")

    Returns:
        Result dict with success status
    """
    client = _get_client(convex_url)

    result = client.mutation("users:updateOnboardingStep", {
        "userId": user_id,
        "step": step,
    })

    return result


def update_schoology_connected(convex_url: str, user_id: str, connected: bool) -> dict:
    """
    Update user's Schoology connection status

    Args:
        convex_url: Convex deployment URL
        user_id: User ID (string)
        connected: Whether Schoology is connected

    Returns:
        Result dict with success status
    """
    client = _get_client(convex_url)

    result = client.mutation("users:updateSchoologyConnected", {
        "userId": user_id,
        "connected": connected,
    })

    return result


def save_consent(convex_url: str, user_id: str, consent: dict) -> dict:
    """
    Save smart features consent and mark onboarding as completed

    Args:
        convex_url: Convex deployment URL
        user_id: User ID (string)
        consent: Consent object with enabled, timestamp, version

    Returns:
        Result dict with success status
    """
    client = _get_client(convex_url)

    result = client.mutation("users:saveConsent", {
        "userId": user_id,
        "consent": consent,
    })

    return result
