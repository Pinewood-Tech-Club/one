"""
App-wide per-user SSE event channel.
"""
import json

from flask import Blueprint, Response, jsonify, request, stream_with_context

from auth.middleware import auth_required
from config import Config
from services import events

events_bp = Blueprint("events", __name__, url_prefix="/api")


@events_bp.route("/events")
@auth_required
def stream_user_events(user):
    if not events.events_configured():
        return jsonify({"error": "events_not_configured"}), 503

    user_id = user["id"]
    last_event_id = request.headers.get("Last-Event-ID", "").strip()

    @stream_with_context
    def generate():
        if last_event_id:
            current_event_id = last_event_id
            for event in events.replay_events_after(user_id, last_event_id):
                current_event_id = event["id"]
                yield _format_sse(event["type"], event["data"], event_id=event["id"])
        else:
            # Concrete cursor instead of "$" so events published between
            # blocking reads are never dropped.
            current_event_id = events.latest_event_id(user_id)

        while True:
            new_events = events.block_for_new_events(
                user_id,
                current_event_id,
                block_ms=Config.APP_EVENTS_SSE_HEARTBEAT_SECONDS * 1000,
            )
            if not new_events:
                yield ": heartbeat\n\n"
                continue

            for event in new_events:
                current_event_id = event["id"]
                yield _format_sse(event["type"], event["data"], event_id=event["id"])

    response = Response(generate(), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["Connection"] = "keep-alive"
    response.headers["X-Accel-Buffering"] = "no"
    return response


def _format_sse(event_name: str, payload, *, event_id: str | None = None):
    lines = []
    if event_id:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event_name}")
    lines.append(f"data: {json.dumps(payload, separators=(',', ':'))}")
    return "\n".join(lines) + "\n\n"
