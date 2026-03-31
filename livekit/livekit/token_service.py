
# [ START ]
#     |
#     v
# +--------------------------+
# | generate_token()         |
# | * build participant JWT  |
# +--------------------------+
#     |
#     |----> <livekit.api> -> VideoGrants()
#     |           |
#     |           ----> * room_join = True
#     |           |
#     |           ----> * can_publish
#     |           |
#     |           ----> * can_subscribe
#     |
#     |----> <livekit.api> -> AccessToken()
#     |           |
#     |           ----> .with_identity()
#     |           |
#     |           ----> .with_name()
#     |           |
#     |           ----> .with_grants()
#     |           |
#     |           ----> .to_jwt()
#     |
#     v
# [ END ]

import os

# ── LiveKit server coordinates (read from env, fallback to local dev values) ─
LIVEKIT_URL        = os.getenv("LIVEKIT_URL",        "ws://localhost:7880")
LIVEKIT_API_KEY    = os.getenv("LIVEKIT_API_KEY",    "devkey")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET", "devsecret")

def generate_token(
    room_name:     str,
    identity:      str,
    name:          str  = "",
    *,
    can_publish:   bool = True,
    can_subscribe: bool = True,
) -> str:

    from livekit.api import AccessToken, VideoGrants

    grants = VideoGrants(
        room_join    = True,
        room         = room_name,
        can_publish  = can_publish,
        can_subscribe= can_subscribe,
    )

    token = (
        AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
        .with_identity(identity)
        .with_name(name or identity)
        .with_grants(grants)
        .to_jwt()
    )
    return token
