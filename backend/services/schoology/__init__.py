"""
Schoology Service - Python package for Schoology API wrapper and Convex sync

This package provides a clean interface to interact with the Schoology API
and synchronize data to Convex cache.
"""

from .client import SchoologyService
from .oauth import complete_oauth, start_oauth

__version__ = "0.1.0"
__all__ = ["SchoologyService", "start_oauth", "complete_oauth"]
