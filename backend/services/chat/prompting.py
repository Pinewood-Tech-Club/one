"""
Prompt construction for backend-owned chat generations.
"""
import json
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from db.users import get_user_by_id

from .types import GenerationContext

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"
SYSTEM_PROMPT_PATH = PROMPTS_DIR / "chat_system.txt"


@lru_cache(maxsize=1)
def get_system_prompt() -> str:
    return SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").strip()


def build_responses_request(context: GenerationContext) -> tuple[str, list[dict[str, Any]]]:
    instructions = _build_system_message(context)
    input_items: list[dict[str, Any]] = []

    for transcript_message in context.transcript:
        if transcript_message.role == "system":
            continue

        if transcript_message.role == "assistant" and transcript_message.status != "completed":
            continue

        if not transcript_message.content.strip():
            continue

        message_item: dict[str, Any] = {
            "type": "message",
            "role": transcript_message.role,
            "content": transcript_message.content,
        }
        if transcript_message.role == "assistant":
            message_item["phase"] = "final_answer"
        input_items.append(message_item)

    for tool_call in context.tool_calls:
        if tool_call.status not in {"completed", "failed"}:
            continue
        if not tool_call.arguments_text:
            continue

        input_items.append(
            {
                "id": f"persisted_fc_{tool_call.call_id}",
                "type": "function_call",
                "call_id": tool_call.call_id,
                "name": tool_call.tool_name,
                "arguments": tool_call.arguments_text,
                "status": "completed",
            }
        )

        output_text = tool_call.output_text
        if output_text is None:
            error_payload = {"error": tool_call.error_text or "tool_call_failed"}
            if tool_call.summary_text:
                error_payload["summary"] = tool_call.summary_text
            output_text = json.dumps(error_payload, ensure_ascii=True)

        input_items.append(
            {
                "type": "function_call_output",
                "call_id": tool_call.call_id,
                "output": output_text,
            }
        )

    if not input_items:
        raise ValueError("No usable transcript messages were provided for generation")

    return instructions, input_items


def _build_system_message(context: GenerationContext) -> str:
    sections: list[str] = [get_system_prompt()]

    canonical_user = None
    try:
        canonical_user = get_user_by_id(int(context.user_id))
    except (TypeError, ValueError):
        canonical_user = None

    user_lines = [f"- User ID: {context.user_id}"]
    if canonical_user:
        if canonical_user.get("name"):
            user_lines.append(f"- Name: {canonical_user['name']}")
        if canonical_user.get("email"):
            user_lines.append(f"- Email: {canonical_user['email']}")
    if context.user_record:
        user_lines.append(
            f"- Schoology connected: {'yes' if context.user_record.schoology_connected else 'no'}"
        )
    sections.append("Current user context:\n" + "\n".join(user_lines))

    now_str = datetime.now(timezone.utc).strftime("%A, %B %-d, %Y at %-I:%M %p UTC")
    if context.courses:
        course_lines = []
        for course in context.courses:
            if course.section_title and course.section_title != course.course_title:
                course_lines.append(
                    f"- {course.course_title} (course_id={course.course_id}, section_title={course.section_title})"
                )
            else:
                course_lines.append(f"- {course.course_title} (course_id={course.course_id})")
        sections.append(
            f"Current date/time: {now_str}\n\nKnown Schoology courses:\n" + "\n".join(course_lines)
        )
    else:
        sections.append(f"Current date/time: {now_str}\n\nKnown Schoology courses:\n- None currently cached.")

    completed_tool_calls = [
        tool_call
        for tool_call in context.tool_calls
        if tool_call.status in {"completed", "failed"}
    ]
    if completed_tool_calls:
        tool_lines = []
        for tool_call in completed_tool_calls[-12:]:
            summary = tool_call.summary_text or tool_call.output_text or tool_call.error_text or "(no summary)"
            summary = summary.strip().replace("\n", " ")
            if len(summary) > 400:
                summary = summary[:400].rstrip() + "..."
            tool_lines.append(
                f"- seq={tool_call.sequence} tool={tool_call.tool_name} status={tool_call.status} summary={summary}"
            )
        sections.append(
            "Previously completed tool calls in this thread:\n" + "\n".join(tool_lines)
        )

    return "\n\n".join(section for section in sections if section.strip())
