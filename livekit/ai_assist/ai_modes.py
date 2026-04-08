import logging
logger = logging.getLogger(__name__)

from enum import Enum

class AIMode(str, Enum):
    ASSIST = "assist_mode"
    PARALLEL = "parallel_mode"
    TAKEOVER = "takeover_mode"
