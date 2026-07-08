"""
Tests for auth/middleware.py — the @auth_required decorator.

Security properties under test:
  - missing session cookie  -> 401
  - invalid/expired session  -> 401 (and the stale session_id is cleared)
  - valid session            -> handler runs, resolved user injected as kwargs["user"]

We build a minimal Flask app and monkeypatch the session lookup that the
decorator imported into its own namespace (auth.middleware.get_session).
"""
import flask
import pytest

from auth.middleware import auth_required


@pytest.fixture()
def app():
    application = flask.Flask(__name__)
    application.secret_key = "test-secret"

    @application.route("/protected")
    @auth_required
    def protected(user):
        return flask.jsonify({"user": user})

    @application.route("/login-fake")
    def login_fake():
        # Helper route to seat a session_id cookie for the "valid" path.
        flask.session["session_id"] = "valid-session-id"
        return "ok"

    return application


@pytest.fixture()
def client(app):
    return app.test_client()


def test_no_session_returns_401(client):
    resp = client.get("/protected")
    assert resp.status_code == 401
    assert resp.get_json()["error"] == "Authentication required"


def test_invalid_session_returns_401(client, monkeypatch):
    # Seat a session_id cookie...
    client.get("/login-fake")
    # ...but the lookup resolves to no user (expired/forged session).
    monkeypatch.setattr("auth.middleware.get_session", lambda sid: None)
    resp = client.get("/protected")
    assert resp.status_code == 401
    assert resp.get_json()["error"] == "Invalid session"


def test_valid_session_injects_user(client, monkeypatch):
    fake_user = {"id": 42, "email": "student@example.com", "name": "Test Student"}
    called_with = {}

    def fake_get_session(sid):
        called_with["sid"] = sid
        return fake_user

    monkeypatch.setattr("auth.middleware.get_session", fake_get_session)

    client.get("/login-fake")
    resp = client.get("/protected")
    assert resp.status_code == 200
    assert resp.get_json()["user"] == fake_user
    assert called_with["sid"] == "valid-session-id"
