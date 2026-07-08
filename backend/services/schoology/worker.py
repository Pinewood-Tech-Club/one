"""
Out-of-process Schoology refresh worker.
"""
import logging
import sys

from db.job_leases import release_schoology_refresh_lease

from .refresh import run_schoology_refresh_for_user

logger = logging.getLogger(__name__)


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO)

    if len(argv) != 3:
        print(
            "Usage: python -m services.schoology.worker <user_id> <owner_token>",
            file=sys.stderr,
        )
        return 2

    try:
        user_id = int(argv[1].strip())
    except ValueError:
        print("user_id must be an integer", file=sys.stderr)
        return 2
    owner_token = argv[2].strip()
    if not owner_token:
        print("owner_token is required", file=sys.stderr)
        return 2

    try:
        run_schoology_refresh_for_user(user_id)
        return 0
    except Exception:
        logger.exception("schoology_refresh_worker_failed user_id=%s", user_id)
        return 1
    finally:
        try:
            release_schoology_refresh_lease(user_id, owner_token)
            logger.info("schoology_refresh_complete user_id=%s", user_id)
        except Exception:
            logger.exception("schoology_refresh_release_failed user_id=%s", user_id)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
