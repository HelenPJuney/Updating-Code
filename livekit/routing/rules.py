# [ START ]
#     |
#     v
# +--------------------------+
# | load()                   |
# | * Reads JSON from disk   |
# | * Atomic reload logic    |
# +--------------------------+
#     |
#     | 
#     |
#     v
# +--------------------------+
# | to_dict_list()           |
# | * API helper             |
# | * Serialization logic    |
# +--------------------------+
#     |
#     |----> Iterates self._rules
#     |----> Converts dataclasses back to JSON-friendly Dicts
#     v
# [ YIELD ]

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("callcenter.routing.rules")

_DEFAULT_RULES_PATH = Path(__file__).parent / "routing_rules.json"


@dataclass
class RuleConditions:
    """Conditions that must ALL match for a rule to fire."""
    lang:                    Optional[List[str]] = None   # match if req.lang in list
    source:                  Optional[List[str]] = None   # match if req.source in list
    priority_gte:            int                 = 0      # req.priority >= this
    caller_number_prefix:    Optional[List[str]] = None   # SIP: starts-with check on caller_number
    caller_id_prefix:        Optional[List[str]] = None   # Browser: starts-with check on caller_id
    time_of_day_utc_between: Optional[List[str]] = None   # ["HH:MM", "HH:MM"] inclusive
    time_of_day_utc_outside: Optional[List[str]] = None   # outside window


@dataclass
class RuleTarget:
    """What to do when the rule matches."""
    queue_name:       str              = "default"
    priority_override: Optional[int]  = None       # None = keep original
    required_skills:  List[str]        = field(default_factory=list)
    ai_config:        Dict[str, Any]   = field(default_factory=dict)
    fallback_action:  str              = "queue"    # queue|voicemail|callback|ai_bot


@dataclass
class RoutingRule:
    name:       str
    priority:   int
    enabled:    bool
    conditions: RuleConditions
    target:     RuleTarget


class RuleLoader:
   

    def __init__(self, path: Optional[Path] = None) -> None:
        env_path = os.getenv("ROUTING_RULES_PATH")
        self._path: Path = Path(env_path) if env_path else (path or _DEFAULT_RULES_PATH)
        self._rules: List[RoutingRule] = []
        self._raw: Dict = {}

    def load(self) -> None:
        """Load rules from disk. Call at startup and on hot-reload."""
        try:
            with open(self._path, encoding="utf-8") as fh:
                raw = json.load(fh)
            rules = [self._parse_rule(r) for r in raw.get("rules", [])]
            # Sort ascending by priority so lower numbers fire first
            rules.sort(key=lambda r: r.priority)
            self._rules = rules
            self._raw = raw
            logger.info("[RuleLoader] loaded %d rules from %s", len(rules), self._path)
        except FileNotFoundError:
            logger.warning("[RuleLoader] rules file not found: %s — using empty ruleset", self._path)
            self._rules = []
        except Exception as exc:
            logger.exception("[RuleLoader] failed to load rules: %s", exc)
            # Keep old rules on reload failure

    def _parse_rule(self, raw: Dict) -> RoutingRule:
        cond_raw = raw.get("conditions", {})
        tgt_raw  = raw.get("target", {})
        cond = RuleConditions(
            lang                    = cond_raw.get("lang"),
            source                  = cond_raw.get("source"),
            priority_gte            = cond_raw.get("priority_gte", 0),
            caller_number_prefix    = cond_raw.get("caller_number_prefix"),
            caller_id_prefix        = cond_raw.get("caller_id_prefix"),
            time_of_day_utc_between = cond_raw.get("time_of_day_utc_between"),
            time_of_day_utc_outside = cond_raw.get("time_of_day_utc_outside"),
        )
        target = RuleTarget(
            queue_name        = tgt_raw.get("queue_name", "default"),
            priority_override = tgt_raw.get("priority_override"),
            required_skills   = tgt_raw.get("required_skills", []),
            ai_config         = tgt_raw.get("ai_config", {}),
            fallback_action   = tgt_raw.get("fallback_action", "queue"),
        )
        return RoutingRule(
            name       = raw.get("name", "unnamed"),
            priority   = raw.get("priority", 999),
            enabled    = raw.get("enabled", True),
            conditions = cond,
            target     = target,
        )

    @property
    def rules(self) -> List[RoutingRule]:
        return self._rules

    @property
    def raw(self) -> Dict:
        return self._raw

    def to_dict_list(self) -> List[Dict]:
        """Serialize rules for the /routing/rules API response."""
        out = []
        for r in self._rules:
            out.append({
                "name":     r.name,
                "priority": r.priority,
                "enabled":  r.enabled,
                "conditions": {
                    "lang":                    r.conditions.lang,
                    "source":                  r.conditions.source,
                    "priority_gte":            r.conditions.priority_gte,
                    "caller_number_prefix":    r.conditions.caller_number_prefix,
                    "caller_id_prefix":        r.conditions.caller_id_prefix,
                    "time_of_day_utc_between": r.conditions.time_of_day_utc_between,
                    "time_of_day_utc_outside": r.conditions.time_of_day_utc_outside,
                },
                "target": {
                    "queue_name":       r.target.queue_name,
                    "priority_override": r.target.priority_override,
                    "required_skills":  r.target.required_skills,
                    "ai_config":        r.target.ai_config,
                    "fallback_action":  r.target.fallback_action,
                },
            })
        return out
