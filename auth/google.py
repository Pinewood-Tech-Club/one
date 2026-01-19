"""
Google OAuth logic
"""
import requests
from urllib.parse import urlencode
from config import Config


def get_google_auth_url():
    """Generate Google OAuth authorization URL"""
    redirect_uri = f"{Config.BACKEND_URL}/auth/google/callback"
    # print(redirect_uri)
    google_auth_url = "https://accounts.google.com/o/oauth2/auth?" + urlencode(
        {
            "client_id": Config.GOOGLE_CLIENT_ID,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "email profile",
            "hd": "pinewood.edu",
        }
    )
    return google_auth_url


def exchange_code_for_token(code):
    """Exchange authorization code for access token"""
    redirect_uri = f"{Config.BACKEND_URL}/auth/google/callback"
    token_response = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": Config.GOOGLE_CLIENT_ID,
            "client_secret": Config.GOOGLE_CLIENT_SECRET,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        },
    ).json()
    
    return token_response


def get_user_info(access_token):
    """Get user info from Google"""
    user_response = requests.get(
        "https://www.googleapis.com/oauth2/v2/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
    ).json()
    
    return user_response

