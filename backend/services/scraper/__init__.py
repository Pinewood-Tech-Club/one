"""
Schoology shared-content scraper package.
"""

from typing import Any


def run_scheduler_once() -> dict[str, Any]:
    from .scheduler import run_scheduler_once as _run_scheduler_once

    return _run_scheduler_once()


__all__ = ["run_scheduler_once"]
