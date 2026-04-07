"""
Internal chat execution routes.
"""
from flask import Blueprint, jsonify, request

from config import Config
from services.chat import (
    ChatConfigurationError,
    ChatContractError,
    ChatGenerationNotFoundError,
    run_generation,
)

internal_chat_bp = Blueprint("internal_chat", __name__, url_prefix="/api/internal/chat")


def _json_error(code: str, status_code: int, **extra):
    payload = {"error": code}
    payload.update(extra)
    return jsonify(payload), status_code


def _is_valid_internal_secret() -> bool:
    configured_secret = Config.CHAT_INTERNAL_SECRET
    request_secret = request.headers.get("X-Internal-Chat-Secret", "")
    return bool(configured_secret) and request_secret == configured_secret


@internal_chat_bp.route("/generate", methods=["POST"])
def generate_chat_completion():
    if not Config.CHAT_INTERNAL_SECRET:
        return _json_error("chat_not_configured", 503)

    if not _is_valid_internal_secret():
        return _json_error("invalid_internal_secret", 401)

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _json_error("invalid_request", 400)

    generation_id = data.get("generationId")
    if not isinstance(generation_id, str) or not generation_id.strip():
        return _json_error("generation_id_required", 400)

    try:
        result = run_generation(generation_id.strip())
    except ChatGenerationNotFoundError:
        return _json_error("generation_not_found", 404)
    except ChatConfigurationError as exc:
        return _json_error("chat_not_configured", 503, detail=str(exc))
    except ChatContractError as exc:
        return _json_error("invalid_generation_context", 502, detail=str(exc))
    except Exception as exc:
        return _json_error("generation_failed", 500, detail=exc.__class__.__name__)

    return jsonify(
        {
            "generationId": result.generation_id,
            "status": result.status,
            "charsStreamed": result.characters_streamed,
            "eventCount": result.event_count,
            "providerMessageId": result.provider_message_id,
            "usage": result.usage,
        }
    )
