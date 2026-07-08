"""
Out-of-process chat generation worker.
"""
import logging
import sys
import time

from . import (
    ChatConfigurationError,
    ChatContractError,
    ChatGenerationNotFoundError,
    convex_sync,
    live_stream,
    run_generation,
)

logger = logging.getLogger(__name__)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _mark_failed(generation_id: str, error_code: str, error_message: str) -> None:
    completed_at = _now_ms()
    try:
        convex_sync.mark_generation_failed(
            generation_id,
            error_code,
            error_message,
            completed_at,
        )
    except Exception:
        logger.exception(
            "chat_worker_mark_failed_error generation_id=%s error_code=%s",
            generation_id,
            error_code,
        )
    try:
        live_stream.publish_terminal(
            generation_id,
            status="failed",
            content="",
            updated_at=completed_at,
            error_code=error_code,
            error_message=error_message,
        )
    except Exception:
        logger.exception(
            "chat_worker_publish_failed_terminal_error generation_id=%s error_code=%s",
            generation_id,
            error_code,
        )


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO)

    if len(argv) != 2:
        print("Usage: python -m services.chat.worker <generation_id>", file=sys.stderr)
        return 2

    generation_id = argv[1].strip()
    if not generation_id:
        print("generation_id is required", file=sys.stderr)
        return 2

    try:
        result = run_generation(generation_id)
        logger.info(
            "chat_generation_worker_complete generation_id=%s status=%s chars=%s events=%s",
            result.generation_id,
            result.status,
            result.characters_streamed,
            result.event_count,
        )
        return 0
    except ChatGenerationNotFoundError:
        logger.warning("chat_generation_worker_missing generation_id=%s", generation_id)
        return 1
    except ChatConfigurationError as exc:
        logger.warning("chat_generation_worker_config_error generation_id=%s error=%s", generation_id, exc)
        _mark_failed(generation_id, "chat_not_configured", str(exc))
        return 1
    except ChatContractError as exc:
        logger.warning("chat_generation_worker_contract_error generation_id=%s error=%s", generation_id, exc)
        _mark_failed(generation_id, "contract_error", str(exc))
        return 1
    except Exception as exc:
        logger.exception("chat_generation_worker_failed generation_id=%s", generation_id)
        _mark_failed(generation_id, "internal_error", exc.__class__.__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
