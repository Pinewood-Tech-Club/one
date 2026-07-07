"""
Google OAuth logic
"""
import logging
import os
import queue
import threading
from urllib.parse import urlencode

import requests

from config import Config

logger = logging.getLogger(__name__)

# Explicit timeouts prevent OAuth callback handlers from hanging indefinitely.
GOOGLE_REQUEST_TIMEOUT = (3.05, 15)
GOOGLE_HARD_TIMEOUT_SECONDS = float(os.environ.get("GOOGLE_HARD_TIMEOUT_SECONDS", "20"))


def _execute_google_call(operation: str, callback, fallback_error: str):
    result_queue: queue.Queue[tuple[bool, object]] = queue.Queue(maxsize=1)

    def runner():
        try:
            result_queue.put((True, callback()))
        except Exception as exc:
            result_queue.put((False, exc))

    thread = threading.Thread(
        target=runner,
        daemon=True,
        name=f"google-{operation}",
    )
    thread.start()

    try:
        success, payload = result_queue.get(timeout=GOOGLE_HARD_TIMEOUT_SECONDS)
    except queue.Empty:
        logger.warning(
            "Google OAuth %s timed out after %ss",
            operation,
            GOOGLE_HARD_TIMEOUT_SECONDS,
        )
        return {"error": fallback_error}

    if success:
        return payload

    exc = payload
    if isinstance(exc, (requests.RequestException, ValueError)):
        logger.warning("Google OAuth %s failed: %s", operation, exc)
        return {"error": fallback_error}

    logger.warning("Google OAuth %s failed unexpectedly: %s", operation, exc)
    return {"error": fallback_error}


def get_google_auth_url(state: str | None = None):
    """Generate Google OAuth authorization URL"""
    redirect_uri = f"{Config.BACKEND_URL}/auth/google/callback"
    params = {
        "client_id": Config.GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "email profile",
        "hd": "pinewood.edu",
    }
    if state:
        params["state"] = state
    google_auth_url = "https://accounts.google.com/o/oauth2/auth?" + urlencode(params)
    return google_auth_url


def exchange_code_for_token(code):
    """Exchange authorization code for access token"""
    def do_request():
        redirect_uri = f"{Config.BACKEND_URL}/auth/google/callback"
        return requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": Config.GOOGLE_CLIENT_ID,
                "client_secret": Config.GOOGLE_CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
            },
            timeout=GOOGLE_REQUEST_TIMEOUT,
        ).json()

    return _execute_google_call(
        operation="token-exchange",
        callback=do_request,
        fallback_error="token_request_failed",
    )


def get_user_info(access_token):
    """Get user info from Google"""
    def do_request():
        return requests.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=GOOGLE_REQUEST_TIMEOUT,
        ).json()

    return _execute_google_call(
        operation="userinfo",
        callback=do_request,
        fallback_error="user_info_request_failed",
    )
