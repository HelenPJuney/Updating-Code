import logging
logger = logging.getLogger(__name__)

from .router import browser_router

__all__ = ["browser_router"]
