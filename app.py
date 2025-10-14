from flask import Flask, jsonify, request, session, redirect, url_for
from flask_cors import CORS
import json
import os
import sqlite3
import requests
from urllib.parse import urlencode
import secrets
from datetime import datetime, timedelta
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

app = Flask(__name__)

# CORS configuration for Next.js frontend
CORS(app, origins=["http://localhost:3112"], supports_credentials=True)

# Configuration
app.secret_key = os.environ.get("FLASK_SECRET_KEY", secrets.token_hex(16))
app.config["SESSION_COOKIE_SECURE"] = False  # Set to True in production with HTTPS
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"  # Allow cross-origin requests
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=7)

# Google OAuth Configuration
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "your-client-id")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "your-client-secret")

# Frontend URL for redirects
FRONTEND_URL = "http://localhost:3112"

# Initialize SQLite database for sessions
def init_db():
    conn = sqlite3.connect("api_sessions.db")
    cursor = conn.cursor()
    cursor.execute(
        """
    CREATE TABLE IF NOT EXISTS sessions (
        session_id TEXT PRIMARY KEY,
        user_id TEXT,
        email TEXT,
        name TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        expires_at TIMESTAMP
    )
    """
    )
    conn.commit()
    conn.close()

# Initialize the database when the app starts
init_db()

# Session management functions
def create_session(user_id, email, name):
    session_id = secrets.token_hex(32)
    expires_at = datetime.now() + timedelta(days=7)

    conn = sqlite3.connect("api_sessions.db")
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO sessions (session_id, user_id, email, name, expires_at) VALUES (?, ?, ?, ?, ?)",
        (session_id, user_id, email, name, expires_at),
    )
    conn.commit()
    conn.close()

    return session_id

def get_session(session_id):
    conn = sqlite3.connect("api_sessions.db")
    cursor = conn.cursor()
    cursor.execute(
        "SELECT user_id, email, name, expires_at FROM sessions WHERE session_id = ?",
        (session_id,),
    )
    result = cursor.fetchone()
    conn.close()

    if not result:
        return None

    user_id, email, name, expires_at = result
    expires_at = datetime.fromisoformat(expires_at)

    if expires_at < datetime.now():
        delete_session(session_id)
        return None

    return {"user_id": user_id, "email": email, "name": name}

def delete_session(session_id):
    conn = sqlite3.connect("api_sessions.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
    conn.commit()
    conn.close()

# Authentication middleware for API routes
def auth_required(func):
    def wrapper(*args, **kwargs):
        session_id = session.get("session_id")
        if not session_id:
            return jsonify({"error": "Authentication required"}), 401

        user_session = get_session(session_id)
        if not user_session:
            session.pop("session_id", None)
            return jsonify({"error": "Invalid session"}), 401

        # Add user info to kwargs
        kwargs["user"] = user_session
        return func(*args, **kwargs)

    wrapper.__name__ = func.__name__
    wrapper.__doc__ = func.__doc__
    return wrapper

# API Routes
@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "message": "Pinewood One API is running"})

@app.route("/api/user")
@auth_required
def get_current_user(user):
    return jsonify({
        "user_id": user["user_id"],
        "email": user["email"],
        "name": user["name"]
    })

@app.route("/auth/google")
def auth_google():
    google_auth_url = "https://accounts.google.com/o/oauth2/auth?" + urlencode(
        {
            "client_id": GOOGLE_CLIENT_ID,
            "redirect_uri": url_for("auth_google_callback", _external=True),
            "response_type": "code",
            "scope": "email profile",
        }
    )
    return redirect(google_auth_url)

@app.route("/auth/google/callback")
def auth_google_callback():
    try:
        code = request.args.get("code")
        if not code:
            return redirect(f"{FRONTEND_URL}?error=no_code")

        token_response = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": url_for("auth_google_callback", _external=True),
            },
        ).json()

        if "error" in token_response or "access_token" not in token_response:
            return redirect(f"{FRONTEND_URL}?error=token_failed")

        user_response = requests.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {token_response['access_token']}"},
        ).json()

        if "error" in user_response or "email" not in user_response:
            return redirect(f"{FRONTEND_URL}?error=user_info_failed")

        # Check if the email domain is allowed
        email = user_response["email"]
        if not email.endswith("@pinewood.edu"):
            return redirect(f"{FRONTEND_URL}?error=invalid_domain")

        # Create a session for the user
        user_id = user_response.get("id", "")
        name = user_response.get("name", email.split("@")[0])

        # Create session-based authentication
        session_id = create_session(user_id, email, name)
        session["session_id"] = session_id

        # Redirect to frontend success page
        return redirect(f"{FRONTEND_URL}?success=true")

    except Exception as e:
        print(f"Authentication error: {str(e)}")
        return redirect(f"{FRONTEND_URL}?error=unexpected")

@app.route("/auth/logout", methods=["POST"])
def logout():
    session_id = session.get("session_id")
    if session_id:
        delete_session(session_id)
    session.pop("session_id", None)
    return jsonify({"message": "Logged out successfully"})

if __name__ == "__main__":
    app.run(debug=True, port=3111, host="0.0.0.0")
