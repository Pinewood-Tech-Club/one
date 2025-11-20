"""
Schoology OAuth flow logic
"""
import sqlite3
import schoolopy
from flask import redirect
from config import Config
from db.tokens import save_schoology_request_tokens, save_schoology_access_tokens
from db.encryption import decrypt_token


def start_oauth_flow(user_id):
    """Start Schoology OAuth flow and return authorization URL"""
    try:
        oauth_auth = schoolopy.Auth(
            Config.SCHOOLOGY_CONSUMER_KEY,
            Config.SCHOOLOGY_CONSUMER_SECRET,
            three_legged=True,
            domain=Config.SCHOOLOGY_DOMAIN,
        )
        callback_url = f"{Config.BACKEND_URL}/oauth/schoology/callback"
        auth_url = oauth_auth.request_authorization(callback_url=callback_url)

        # Persist request token + secret for this user (encrypted)
        if getattr(oauth_auth, "request_token", None) and getattr(oauth_auth, "request_token_secret", None):
            save_schoology_request_tokens(user_id, oauth_auth.request_token, oauth_auth.request_token_secret)

        return auth_url
    except Exception as e:
        print(f"Schoology OAuth start error: {e}")
        raise


def handle_oauth_callback(oauth_token):
    """
    Handle Schoology OAuth callback.
    Returns (user_id, access_token, access_token_secret) or (None, None, None) on error.
    """
    try:
        print(f"[DEBUG] Callback received: oauth_token={oauth_token}")

        if not oauth_token:
            print("[ERROR] No oauth_token in callback")
            return None, None, None

        # Find which user this request token belongs to
        conn = sqlite3.connect(Config.MAIN_DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT user_id, request_token, request_token_secret FROM schoology_tokens WHERE request_token IS NOT NULL"
        )
        all_tokens = cursor.fetchall()
        conn.close()

        user_id = None
        request_token_secret = None

        # Match the oauth_token with stored encrypted request tokens
        for uid, encrypted_req_token, encrypted_req_secret in all_tokens:
            decrypted_token = decrypt_token(encrypted_req_token)
            if decrypted_token == oauth_token:
                user_id = uid
                request_token_secret = decrypt_token(encrypted_req_secret)
                break

        if not user_id or not request_token_secret:
            print(f"[ERROR] Could not find user for oauth_token: {oauth_token}")
            return None, None, None

        print(f"[DEBUG] Found user_id: {user_id}, exchanging tokens...")

        # Reconstruct auth with request tokens
        oauth_auth = schoolopy.Auth(
            Config.SCHOOLOGY_CONSUMER_KEY,
            Config.SCHOOLOGY_CONSUMER_SECRET,
            three_legged=True,
            domain=Config.SCHOOLOGY_DOMAIN,
            request_token=oauth_token,
            request_token_secret=request_token_secret,
        )

        # Exchange request tokens for access tokens using schoolopy's authorize() method
        print(f"[DEBUG] Calling authorize() to exchange tokens...")
        try:
            # Call authorize() which internally exchanges the request tokens for access tokens
            if not oauth_auth.authorize():
                print(f"[ERROR] authorize() returned False")
                return None, None, None

            # Get the access tokens from the Auth object
            access_token = oauth_auth.access_token
            access_token_secret = oauth_auth.access_token_secret

            print(f"[DEBUG] Access tokens obtained successfully")
            print(f"[DEBUG] access_token exists: {access_token is not None}")
            print(f"[DEBUG] access_token_secret exists: {access_token_secret is not None}")

            return user_id, access_token, access_token_secret

        except Exception as e:
            print(f"[ERROR] Failed to exchange tokens: {e}")
            import traceback
            traceback.print_exc()
            return None, None, None

    except Exception as e:
        print(f"[ERROR] Schoology OAuth callback error: {e}")
        import traceback
        traceback.print_exc()
        return None, None, None

