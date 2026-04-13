"""
Schoology OAuth flow helpers (stateless)
"""
import schoolopy


def start_oauth(consumer_key: str, consumer_secret: str, callback_url: str,
                schoology_domain: str = "https://app.schoology.com") -> tuple[str, str, str]:
    """
    Start Schoology OAuth flow

    Args:
        consumer_key: Schoology consumer key
        consumer_secret: Schoology consumer secret
        callback_url: OAuth callback URL
        schoology_domain: Schoology domain URL (default: https://app.schoology.com)

    Returns:
        Tuple of (auth_url, request_token, request_token_secret)
        The caller should store the request tokens associated with the user
        and redirect the user to auth_url
    """
    try:
        oauth_auth = schoolopy.Auth(
            consumer_key,
            consumer_secret,
            three_legged=True,
            domain=schoology_domain,
        )

        auth_url = oauth_auth.request_authorization(callback_url=callback_url)

        request_token = getattr(oauth_auth, "request_token", None)
        request_token_secret = getattr(oauth_auth, "request_token_secret", None)

        if not request_token or not request_token_secret:
            raise Exception("Failed to obtain request tokens from Schoology")

        return auth_url, request_token, request_token_secret

    except Exception as e:
        print(f"[ERROR] Schoology OAuth start error: {e}")
        raise


def complete_oauth(consumer_key: str, consumer_secret: str,
                   request_token: str, request_token_secret: str,
                   schoology_domain: str = "https://app.schoology.com") -> tuple[str, str]:
    """
    Complete Schoology OAuth flow by exchanging request tokens for access tokens

    Args:
        consumer_key: Schoology consumer key
        consumer_secret: Schoology consumer secret
        request_token: OAuth request token (from start_oauth)
        request_token_secret: OAuth request token secret (from start_oauth)
        schoology_domain: Schoology domain URL (default: https://app.schoology.com)

    Returns:
        Tuple of (access_token, access_token_secret)
    """
    try:
        # Reconstruct auth with request tokens
        oauth_auth = schoolopy.Auth(
            consumer_key,
            consumer_secret,
            three_legged=True,
            domain=schoology_domain,
            request_token=request_token,
            request_token_secret=request_token_secret,
        )

        # Exchange request tokens for access tokens
        if not oauth_auth.authorize():
            raise Exception("OAuth authorization failed")

        access_token = oauth_auth.access_token
        access_token_secret = oauth_auth.access_token_secret

        if not access_token or not access_token_secret:
            raise Exception("Failed to obtain access tokens from Schoology")

        return access_token, access_token_secret

    except Exception as e:
        print(f"[ERROR] Schoology OAuth completion error: {e}")
        raise
