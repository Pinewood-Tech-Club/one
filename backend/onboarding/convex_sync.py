"""
Convex synchronization functions for onboarding state
"""
import os
import queue
import threading
from typing import Any, Callable

from convex import ConvexClient

CONVEX_CALL_TIMEOUT_SECONDS = float(os.environ.get("CONVEX_CALL_TIMEOUT_SECONDS", "8"))


def _get_client(convex_url: str) -> ConvexClient:
    """Get a Convex client instance"""
    return ConvexClient(convex_url)


def _run_with_timeout(operation: str, callback: Callable[[], Any]) -> Any:
    result_queue: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)

    def runner():
        try:
            result_queue.put((True, callback()))
        except Exception as exc:
            result_queue.put((False, exc))

    thread = threading.Thread(
        target=runner,
        daemon=True,
        name=f"convex-{operation.replace(':', '-')}",
    )
    thread.start()

    try:
        success, payload = result_queue.get(timeout=CONVEX_CALL_TIMEOUT_SECONDS)
    except queue.Empty as exc:
        raise TimeoutError(
            f"Convex call timed out after {CONVEX_CALL_TIMEOUT_SECONDS}s ({operation})"
        ) from exc

    if success:
        return payload
    raise payload


def _mutation(convex_url: str, name: str, args: dict) -> Any:
    return _run_with_timeout(
        f"mutation {name}",
        lambda: _get_client(convex_url).mutation(name, args),
    )


def _query(convex_url: str, name: str, args: dict) -> Any:
    return _run_with_timeout(
        f"query {name}",
        lambda: _get_client(convex_url).query(name, args),
    )


def get_or_create_user(convex_url: str, user_id: str) -> dict:
    """
    Get or create user record in Convex

    Args:
        convex_url: Convex deployment URL
        user_id: User ID (string)

    Returns:
        User record from Convex
    """
    result = _mutation(convex_url, "users:getOrCreate", {
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
    result = _query(convex_url, "users:getUserByUserId", {
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
    result = _mutation(convex_url, "users:updateOnboardingStep", {
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
    result = _mutation(convex_url, "users:updateSchoologyConnected", {
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
    result = _mutation(convex_url, "users:saveConsent", {
        "userId": user_id,
        "consent": consent,
    })

    return result
