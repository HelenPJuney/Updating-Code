
# [ START ]
#     |
#     v
# +--------------------------+
# | AccessToken()            |
# | * init with credentials  |
# +--------------------------+
#     |
#     |----> .identity = "test-user"
#     |
#     |----> .name = "Test User"
#     |
#     |----> .ttl = timedelta(hours=10)
#     |
#     v
# +--------------------------+
# | VideoGrants()            |
# | * define permissions     |
# +--------------------------+
#     |
#     |----> room_join=True
#     |
#     |----> room="test-room"
#     |
#     v
# +--------------------------+
# | token.to_jwt()           |
# | * sign and encode        |
# +--------------------------+
#     |
#     v
# [ END ]

from livekit.api import AccessToken, VideoGrants
import datetime

api_key = "devkey"
api_secret = "devsecret"

token = AccessToken(api_key, api_secret)

token.identity = "test-user"
token.name = "Test User"

token.ttl = datetime.timedelta(hours=10)   # long expiry

token.grants = VideoGrants(
    room_join=True,
    room="test-room",
    can_publish=True,
    can_subscribe=True
)

print(token.to_jwt())