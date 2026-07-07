"""
Schoology Service - Python package for Schoology API wrapper and cache sync

This package provides a clean interface to interact with the Schoology API
and synchronize data to the local cache.
"""

from .client import SchoologyService
from .oauth import start_oauth, complete_oauth

__version__ = "0.1.0"
__all__ = ["SchoologyService", "start_oauth", "complete_oauth"]
