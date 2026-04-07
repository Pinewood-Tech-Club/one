"""
Prompt construction for backend-owned chat generations.
"""
from functools import lru_cache
from pathlib import Path

from .types import GenerationContext

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"
SYSTEM_PROMPT_PATH = PROMPTS_DIR / "chat_system.txt"


@lru_cache(maxsize=1)
def get_system_prompt() -> str:
    return SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").strip()


def build_provider_messages(context: GenerationContext) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = [{"role": "system", "content": get_system_prompt()}]

    for transcript_message in context.transcript:
        if transcript_message.role == "system":
            continue

        if transcript_message.role == "assistant" and transcript_message.status != "completed":
            continue

        if not transcript_message.content.strip():
            continue

        messages.append(
            {
                "role": transcript_message.role,
                "content": transcript_message.content,
            }
        )

    if len(messages) == 1:
        raise ValueError("No usable transcript messages were provided for generation")

    return messages
