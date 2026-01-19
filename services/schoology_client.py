import os
import requests
from config import Config

def _call_service(endpoint: str, payload: dict):
    headers = {"X-Service-Key": Config.SCHOOLOGY_SERVICE_KEY}
    url = f"{Config.SCHOOLOGY_SERVICE_URL}{endpoint}"
    try:
        response = requests.post(url, json=payload, headers=headers)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error calling Schoology Service ({endpoint}): {e}")
        if e.response:
            print(f"Response: {e.response.text}")
        raise

def service_oauth_start(callback_url):
    return _call_service("/internal/oauth/start", {"callback_url": callback_url})

def service_oauth_callback(oauth_token, request_token_secret):
    return _call_service("/internal/oauth/callback", {
        "oauth_token": oauth_token,
        "request_token_secret": request_token_secret
    })

def service_status(access_token, access_token_secret):
    return _call_service("/internal/status", {
        "access_token": access_token,
        "access_token_secret": access_token_secret
    })

def service_courses(access_token, access_token_secret):
    return _call_service("/internal/courses", {
        "access_token": access_token,
        "access_token_secret": access_token_secret
    })

def service_upcoming(user_id, access_token, access_token_secret, days=7):
    return _call_service("/internal/upcoming", {
        "user_id": user_id,
        "access_token": access_token,
        "access_token_secret": access_token_secret,
        "days": days
    })

def service_refresh(user_id, access_token, access_token_secret):
    return _call_service("/internal/refresh", {
        "user_id": user_id,
        "access_token": access_token,
        "access_token_secret": access_token_secret
    })

def service_disconnect(user_id):
    return _call_service("/internal/disconnect", {"user_id": user_id})
