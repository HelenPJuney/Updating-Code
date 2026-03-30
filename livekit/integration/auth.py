# [ START: Security Dependency ]
#       |
#       v
# +-----------------------------+
# | validate_api_key()          |
# | * Extract: X-API-Key Header |
# +-----------------------------+
#       |
#       |--- [ Key NOT in VALID_API_KEYS ] ---+
#       |                                     |
#       |--- [ Key IS Valid ]                 v
#       v                      [ RAISE: HTTPException(401) ]
# +-----------------------------+
# | Identify client_id          |
# | Get current time.time()     |
# +-----------------------------+
#       |
#       |----> rate_limit_lock.acquire()
#       v
# +---------------------------------------+
# | Check request_counts[client_id]       |
# +---------------------------------------+
#       |
#       |--- [ New Client ] ---> Initialize Empty List []
#       |
#       |--- [ Existing ] -----> Clean old timestamps
#       v                        (now - t < 60s)
# +---------------------------------------+
# | len(request_counts) >= MAX_REQUESTS?  |
# +---------------------------------------+
#       |                                 |
#       |--- [ YES ] ---------------------+--> [ RAISE: HTTPException(429) ]
#       |                                 |    (Release Lock)
#       |--- [ NO ]                       |
#       v                                 |
# +---------------------------------------+
# | 1. append(now)                        |
# | 2. rate_limit_lock.release()          |
# +---------------------------------------+
#       |
#       v
# [ RETURN: client_id ]
#       |
# [ END ]

import time
import asyncio
import logging
from typing import Dict, List

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
from fastapi import Security, HTTPException, status
from fastapi.security import APIKeyHeader

logger = logging.getLogger("callcenter.integration.auth")

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=True)

# In-memory config
VALID_API_KEYS = {
    "test-integration-key-123": "client_A",
    "test-integration-key-456": "client_B"
}

# Rate limiting
RATE_LIMIT_WINDOWS_SEC = 60
RATE_LIMIT_MAX_REQUESTS = 100

request_counts: Dict[str, List[float]] = {}
rate_limit_lock = asyncio.Lock()

async def validate_api_key(api_key: str = Security(api_key_header)) -> str:
    """
    Validate X-API-Key header and enforce per-client rate limiting.
    Returns the client_id string on success.
    Raises HTTP 401 for invalid keys, HTTP 429 when rate-limited.

    Note: request_counts is purged periodically by IntegrationService._cleanup_loop()
    to prevent memory leaks from inactive clients.
    """
    _start = time.perf_counter()
    logger.debug("[validate_api_key] START")

    if api_key not in VALID_API_KEYS:
        logger.warning("[validate_api_key] rejected unknown API key")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API Key",
        )

    client_id = VALID_API_KEYS[api_key]
    now = time.time()

    async with rate_limit_lock:
        if client_id not in request_counts:
            request_counts[client_id] = []

        # prune old timestamps (sliding window)
        request_counts[client_id] = [
            t for t in request_counts[client_id]
            if now - t < RATE_LIMIT_WINDOWS_SEC
        ]

        if len(request_counts[client_id]) >= RATE_LIMIT_MAX_REQUESTS:
            logger.warning(
                "[validate_api_key] rate-limit exceeded  client=%s", client_id,
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded",
            )

        request_counts[client_id].append(now)

    logger.debug(
        "[validate_api_key] OK  client=%s  elapsed=%.4fs",
        client_id, time.perf_counter() - _start,
    )
    return client_id  # explicit return on every successful path
