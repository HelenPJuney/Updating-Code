from enum import Enum

class AIMode(str, Enum):
    ASSIST = "assist_mode"
    PARALLEL = "parallel_mode"
    TAKEOVER = "takeover_mode"
