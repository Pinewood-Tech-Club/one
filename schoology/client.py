"""
Schoology client creation
"""
import schoolopy
import requests_oauthlib
from config import Config
from db.tokens import get_schoology_tokens
from db.encryption import decrypt_token


def create_schoology_client(user_id):
    """Create a Schoology client. Prefer three-legged OAuth if access tokens exist; otherwise fall back to two-legged."""
    try:
        print(f"[DEBUG] create_schoology_client called for user_id: {user_id}")
        tokens = get_schoology_tokens(user_id)
        if not tokens:
            print(f"[DEBUG] No tokens found for user_id: {user_id}")
            return None

        print(f"[DEBUG] Tokens retrieved: access_token={bool(tokens.get('access_token'))}, access_token_secret={bool(tokens.get('access_token_secret'))}, consumer_key={bool(tokens.get('consumer_key'))}, consumer_secret={bool(tokens.get('consumer_secret'))}")

        # Prefer three-legged access tokens if present
        if tokens.get("access_token") and tokens.get("access_token_secret"):
            print(f"[DEBUG] Using three-legged OAuth with access tokens")
            access_token = decrypt_token(tokens["access_token"]) if tokens["access_token"] else None
            access_token_secret = decrypt_token(tokens["access_token_secret"]) if tokens["access_token_secret"] else None
            if access_token and access_token_secret:
                print(f"[DEBUG] Access tokens decrypted successfully, creating Schoology client")
                auth = schoolopy.Auth(
                    Config.SCHOOLOGY_CONSUMER_KEY,
                    Config.SCHOOLOGY_CONSUMER_SECRET,
                    three_legged=True,
                    domain=Config.SCHOOLOGY_DOMAIN,
                    access_token=access_token,
                    access_token_secret=access_token_secret,
                )
                # IMPORTANT: Recreate the oauth session with access tokens
                # The Auth.__init__ only creates the session with consumer key/secret
                # We need to recreate it with the access tokens for API calls to work
                auth.oauth = requests_oauthlib.OAuth1Session(
                    Config.SCHOOLOGY_CONSUMER_KEY,
                    client_secret=Config.SCHOOLOGY_CONSUMER_SECRET,
                    resource_owner_key=access_token,
                    resource_owner_secret=access_token_secret,
                )

                # Debug: Print the OAuth header that will be used
                print(f"[DEBUG] OAuth header: {auth._oauth_header()[:200]}...")
                print(f"[DEBUG] OAuth session auth type: {type(auth.oauth.auth)}")

                client = schoolopy.Schoology(auth)
                print(f"[DEBUG] Schoology client created successfully with access tokens")
                return client

        # Fall back to two-legged if consumer credentials are stored
        if tokens.get("consumer_key") and tokens.get("consumer_secret"):
            print(f"[DEBUG] Falling back to two-legged OAuth with consumer credentials")
            consumer_key = decrypt_token(tokens["consumer_key"]) if tokens["consumer_key"] else None
            consumer_secret = decrypt_token(tokens["consumer_secret"]) if tokens["consumer_secret"] else None
            if consumer_key and consumer_secret:
                auth = schoolopy.Auth(
                    consumer_key,
                    consumer_secret,
                    three_legged=False,
                    domain=Config.SCHOOLOGY_DOMAIN,
                )
                return schoolopy.Schoology(auth)

        print(f"[DEBUG] No valid tokens found for user_id: {user_id}")
        return None

    except Exception as e:
        print(f"[ERROR] Error creating Schoology client: {e}")
        import traceback
        traceback.print_exc()
        return None

