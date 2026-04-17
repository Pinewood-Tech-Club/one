"""
Out-of-process worker for a single section sync run.
"""
from __future__ import annotations

import logging
import sys

from . import store
from .section_sync import run_section_sync

logger = logging.getLogger(__name__)


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO)

    if len(argv) != 4:
        print(
            "Usage: python -m services.scraper.worker <section_id> <credential_user_id> <owner_token>",
            file=sys.stderr,
        )
        return 2

    section_id = argv[1].strip()
    if not section_id:
        print("section_id is required", file=sys.stderr)
        return 2

    try:
        credential_user_id = int(argv[2].strip())
    except ValueError:
        print("credential_user_id must be an integer", file=sys.stderr)
        return 2

    owner_token = argv[3].strip()
    if not owner_token:
        print("owner_token is required", file=sys.stderr)
        return 2

    try:
        run_section_sync(section_id, credential_user_id, owner_token)
        return 0
    except Exception as exc:
        logger.exception(
            "scraper_section_worker_failed section_id=%s credential_user_id=%s",
            section_id,
            credential_user_id,
        )
        store.fail_section_run(section_id, owner_token, store.utcnow(), exc.__class__.__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
