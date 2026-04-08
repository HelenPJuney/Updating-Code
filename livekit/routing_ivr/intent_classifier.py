# [ START: CALL TRANSCRIPT ]
#       |
#       v
# +------------------------------------------+
# | classify_intent(transcript)              |
# | * Entry point for AI classification      |
# +------------------------------------------+
#       |
#       |----> _get_client()
#       |      * Lazy-init Gemini Client
#       |      * Check API Key & Installation
#       |
#       | (If Client Fails)
#       |----> [ RETURN Fallback ]
#       |      * ("Support Department", 3)
#       |
#       | (If Client Ready)
#       v
# +------------------------------------------+
# | Gemini AI processing                     |
# | * Apply SYSTEM_PROMPT instructions       |
# | * Request: "DEPARTMENT|URGENCY"          |
# +------------------------------------------+
#       |
#       |----> [ Regex Parsing ]
#       |      * Extract Dept and Score
#       |
#       |----> [ Validation / Fuzzy Match ]
#       |      * Check against VALID_DEPARTMENTS
#       |      * Handle minor string mismatches
#       v
# +------------------------------------------+
# | Final Output                             |
# | * Return (Validated Dept, Urgency)       |
# +------------------------------------------+
#       |
#       v
# [ END: ROUTING DECISION MADE ]

import logging
import os
import re
from typing import Tuple

logger = logging.getLogger("ivr.intent")

# Lazy import to avoid loading google-genai at module level
_client = None


def _get_client():
    """Lazy-init the Gemini client."""
    logger.debug("Executing _get_client")
    global _client
    if _client is None:
        try:
            from google import genai
            api_key = os.getenv("GEMINI_API_KEY", "")
            if not api_key:
                logger.error("GEMINI_API_KEY not set in .env")
                return None
            _client = genai.Client(api_key=api_key)
        except ImportError:
            logger.error("google-genai not installed. Run: pip install google-genai")
            return None
    return _client


SYSTEM_PROMPT = """You are an intent and sentiment classifier for a call center.
Based on the user's issue, output ONLY a response in this EXACT format:
DEPARTMENT|URGENCY

Where DEPARTMENT MUST BE EXACTLY one of:
- Tech Department
- Billing Department
- Sales Department
- Support Department

And URGENCY is a number from 1 to 5 based on how frustrated or urgent the caller sounds:
1 = calm, routine inquiry
3 = moderate concern
5 = extremely frustrated, angry, or critical emergency

If the user's request is vague, general, or doesn't perfectly fit Tech, Billing, or Sales, you MUST route them to Support Department.

Examples:
"My internet has been down for 3 days and nobody is helping" -> Tech Department|5
"I'd like to know your pricing plans" -> Sales Department|1
"I paid fees but they said I haven't" -> Billing Department|4
"Why did my credit card get charged?" -> Billing Department|4
"I need help with my account settings" -> Support Department|2
"Hello? Who is this?" -> Support Department|3

Output NOTHING else. No explanation. No filler. Just DEPARTMENT|URGENCY."""


async def classify_intent(transcript: str) -> Tuple[str, int]:
    """
    Returns (department_name, urgency_score).
    Falls back to ("Support Department", 3) on any failure.
    """
    logger.debug("Executing classify_intent")
    fallback = ("Support Department", 3)

    client = _get_client()
    if client is None:
        logger.warning("Gemini client unavailable, using fallback")
        return fallback

    try:
        from google.genai import types
        from .config import GEMINI_MODEL

        response = client.models.generate_content(
            model=GEMINI_MODEL,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=0.1,
                max_output_tokens=50,
            ),
            contents=transcript,
        )

        raw = response.text.strip()
        logger.info("Gemini raw response: %s", raw)

        # Parse "Department Name|N"
        match = re.match(r"^(.+?)(?:\|(\d))?$", raw)
        if match:
            dept = match.group(1).strip()
            urgency = int(match.group(2)) if match.group(2) else 3

            # Validate department name
            from .config import VALID_DEPARTMENTS
            if dept in VALID_DEPARTMENTS:
                return dept, min(max(urgency, 1), 5)

            # Fuzzy match: check if any valid dept is a substring
            for valid in VALID_DEPARTMENTS:
                if valid.lower() in dept.lower() or dept.lower() in valid.lower():
                    return valid, min(max(urgency, 1), 5)

        logger.warning("Gemini response didn't match pattern: %s", raw)
        return fallback

    except Exception as e:
        logger.error("Gemini classification failed: %s", e)
        return fallback
