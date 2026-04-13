"""
Helpers for constructing Schoology service instances from app state.
"""
from config import Config
from db.encryption import decrypt_token
from db.tokens import get_schoology_tokens

from .client import SchoologyService


def create_schoology_service(user_id: int) -> SchoologyService | None:
    """
    Create a SchoologyService instance for the given user.
    """
    tokens = get_schoology_tokens(user_id)
    if not tokens:
        return None

    access_token = decrypt_token(tokens.get("access_token"))
    access_token_secret = decrypt_token(tokens.get("access_token_secret"))

    if access_token and access_token_secret:
        # Three-legged tokens are issued using the backend's configured consumer
        # key/secret, so always sign with those.
        if not Config.SCHOOLOGY_CONSUMER_KEY or not Config.SCHOOLOGY_CONSUMER_SECRET:
            return None
        return SchoologyService(
            user_id=str(user_id),
            access_token=access_token,
            access_token_secret=access_token_secret,
            consumer_key=Config.SCHOOLOGY_CONSUMER_KEY,
            consumer_secret=Config.SCHOOLOGY_CONSUMER_SECRET,
            convex_url=Config.CONVEX_URL,
            schoology_domain=Config.SCHOOLOGY_DOMAIN,
            schoology_api_domain=Config.SCHOOLOGY_API_DOMAIN,
        )

    # Prefer per-user credentials if present; fall back to server config.
    consumer_key = decrypt_token(tokens.get("consumer_key")) or Config.SCHOOLOGY_CONSUMER_KEY
    consumer_secret = decrypt_token(tokens.get("consumer_secret")) or Config.SCHOOLOGY_CONSUMER_SECRET

    if consumer_key and consumer_secret:
        return SchoologyService(
            user_id=str(user_id),
            access_token=None,
            access_token_secret=None,
            consumer_key=consumer_key,
            consumer_secret=consumer_secret,
            convex_url=Config.CONVEX_URL,
            schoology_domain=Config.SCHOOLOGY_DOMAIN,
            schoology_api_domain=Config.SCHOOLOGY_API_DOMAIN,
        )

    return None
