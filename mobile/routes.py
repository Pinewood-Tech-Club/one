"""
Mobile API routes (/api/mobile/v1/*).
"""
from flask import Blueprint, jsonify, redirect, request, session, g
from flask_limiter.util import get_remote_address

from auth.mobile_middleware import mobile_auth_required
from config import Config
from db.sessions import create_session
from extensions import limiter
from mobile import service
from onboarding import get_user as convex_get_user

mobile_bp = Blueprint("mobile_api", __name__, url_prefix="/api/mobile/v1")


def _json_error(code: str, status_code: int):
    return jsonify({"error": code}), status_code


def _limit_by_mobile_user():
    mobile_user = getattr(g, "mobile_user", None)
    if mobile_user and mobile_user.get("id") is not None:
        return f"user:{mobile_user['id']}"
    return get_remote_address()


def _redirect_for_callback_error(state_token: str | None, error_code: str):
    redirect_uri = service.fallback_mobile_redirect_uri()
    client_state = None
    if state_token:
        try:
            state_data = service.parse_mobile_state(state_token)
            redirect_uri = state_data.get("redirect_uri") or redirect_uri
            client_state = state_data.get("client_state")
        except service.MobileAuthError:
            pass
    return redirect(service.build_mobile_callback_redirect(redirect_uri, error=error_code, state=client_state))


@mobile_bp.route("/auth/google/start")
@limiter.limit("10 per minute")
def mobile_google_start():
    try:
        redirect_uri = (request.args.get("redirect_uri") or "").strip()
        device_id = (request.args.get("device_id") or "").strip()
        code_challenge = (request.args.get("code_challenge") or "").strip()
        code_challenge_method = (request.args.get("code_challenge_method") or "").strip()
        client_state = request.args.get("state")

        google_auth_url = service.get_mobile_google_start_redirect(
            redirect_uri=redirect_uri,
            device_id=device_id,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
            client_state=client_state,
        )
        return redirect(google_auth_url)
    except service.MobileAuthError as exc:
        return _json_error(exc.code, exc.status_code)


@mobile_bp.route("/auth/google/callback")
def mobile_google_callback():
    state_token = request.args.get("state")
    provider_error = request.args.get("error")
    code = request.args.get("code")

    if provider_error:
        return _redirect_for_callback_error(state_token, provider_error)

    if not state_token or not code:
        return _redirect_for_callback_error(state_token, "invalid_request")

    try:
        auth_code, redirect_uri, client_state = service.process_google_callback(
            code=code,
            state_token=state_token,
        )
        return redirect(
            service.build_mobile_callback_redirect(
                redirect_uri,
                code=auth_code,
                state=client_state,
            )
        )
    except service.MobileAuthError as exc:
        return _redirect_for_callback_error(state_token, exc.code)
    except Exception:
        return _redirect_for_callback_error(state_token, "unexpected")


@mobile_bp.route("/auth/exchange", methods=["POST"])
@limiter.limit("15 per minute")
def mobile_auth_exchange():
    data = request.get_json(silent=True) or {}
    try:
        payload = service.exchange_auth_code(
            code=str(data.get("code", "")),
            code_verifier=str(data.get("code_verifier", "")),
            device_id=str(data.get("device_id", "")),
            platform=str(data.get("platform", "")),
            app_version=str(data.get("app_version", "")),
            locale=data.get("locale"),
            timezone_value=data.get("timezone"),
        )
        return jsonify(payload)
    except service.MobileAuthError as exc:
        return _json_error(exc.code, exc.status_code)


@mobile_bp.route("/auth/refresh", methods=["POST"])
@limiter.limit("30 per minute")
def mobile_auth_refresh():
    data = request.get_json(silent=True) or {}
    try:
        payload = service.refresh_mobile_tokens(
            refresh_token=str(data.get("refresh_token", "")),
            device_id=str(data.get("device_id", "")),
        )
        return jsonify(payload)
    except service.MobileAuthError as exc:
        return _json_error(exc.code, exc.status_code)


@mobile_bp.route("/auth/logout", methods=["POST"])
@limiter.limit("30 per minute")
@mobile_auth_required
def mobile_auth_logout(user, token_payload):
    data = request.get_json(silent=True) or {}
    refresh_token = data.get("refresh_token")
    all_devices = bool(data.get("all_devices", False))

    try:
        service.logout_mobile_session(
            user_id=user["id"],
            token_payload=token_payload,
            refresh_token=refresh_token,
            all_devices=all_devices,
        )
        return "", 204
    except service.MobileAuthError as exc:
        return _json_error(exc.code, exc.status_code)


@mobile_bp.route("/me")
@mobile_auth_required
def mobile_me(user, token_payload):
    onboarding_step = "welcome"
    schoology_connected = False

    try:
        convex_user = convex_get_user(Config.CONVEX_URL, str(user["id"]))
        if convex_user:
            onboarding_step = convex_user.get("onboardingStep", "welcome")
            schoology_connected = convex_user.get("schoologyConnected", False)
    except Exception:
        pass

    return jsonify(
        {
            "user_id": user["id"],
            "email": user["email"],
            "name": user["name"],
            "onboarding_step": onboarding_step,
            "schoology_connected": schoology_connected,
        }
    )


@mobile_bp.route("/convex/token")
@mobile_auth_required
def mobile_convex_token(user, token_payload):
    return jsonify(service.create_mobile_convex_token(user))


@mobile_bp.route("/web/session-ticket", methods=["POST"])
@mobile_auth_required
@limiter.limit("30 per minute", key_func=_limit_by_mobile_user)
def mobile_web_session_ticket(user, token_payload):
    data = request.get_json(silent=True) or {}
    device_id = str(data.get("device_id", ""))

    if device_id != token_payload.get("device_id"):
        return _json_error("invalid_request", 400)

    try:
        return jsonify(service.create_web_session_ticket(user["id"], device_id))
    except service.MobileAuthError as exc:
        return _json_error(exc.code, exc.status_code)


@mobile_bp.route("/web/session/bootstrap")
def mobile_web_session_bootstrap():
    ticket = request.args.get("ticket", "")
    redirect_target = request.args.get("redirect", "")

    if not service.is_valid_bootstrap_redirect(redirect_target):
        return _json_error("invalid_redirect", 400)

    status, ticket_row = service.consume_web_session_ticket(ticket)
    if status in {"invalid", "consumed"}:
        return _json_error("invalid_ticket", 400)
    if status == "expired":
        return _json_error("expired_ticket", 410)
    if status != "ok" or not ticket_row:
        return _json_error("invalid_ticket", 400)

    session_id = create_session(ticket_row["user_id"])
    session["session_id"] = session_id
    return redirect(redirect_target)


@mobile_bp.route("/devices/register", methods=["POST"])
@mobile_auth_required
@limiter.limit("30 per minute", key_func=_limit_by_mobile_user)
def mobile_register_device(user, token_payload):
    data = request.get_json(silent=True) or {}
    try:
        service.register_mobile_device(
            user_id=user["id"],
            device_id=str(data.get("device_id", "")),
            platform=str(data.get("platform", "")),
            app_version=str(data.get("app_version", "")),
            push_token=data.get("push_token"),
            push_env=data.get("push_env"),
            locale=data.get("locale"),
            timezone_value=data.get("timezone"),
        )
        return jsonify({"success": True})
    except service.MobileAuthError as exc:
        return _json_error(exc.code, exc.status_code)


@mobile_bp.route("/devices/register", methods=["DELETE"])
@mobile_auth_required
@limiter.limit("30 per minute", key_func=_limit_by_mobile_user)
def mobile_unregister_device(user, token_payload):
    data = request.get_json(silent=True) or {}
    try:
        service.unregister_mobile_device(
            user_id=user["id"],
            device_id=str(data.get("device_id", "")),
        )
        return "", 204
    except service.MobileAuthError as exc:
        return _json_error(exc.code, exc.status_code)


@mobile_bp.route("/banner/upcoming")
def mobile_banner_upcoming():
    payload = {
        "image_url": Config.BANNER_UPCOMING_IMAGE_URL,
        "version": Config.BANNER_UPCOMING_VERSION,
        "cache_ttl_seconds": Config.BANNER_UPCOMING_CACHE_TTL_SECONDS,
    }
    response = jsonify(payload)
    response.headers["Cache-Control"] = f"public, max-age={Config.BANNER_UPCOMING_CACHE_TTL_SECONDS}"
    return response
