"""CAPTCHA challenge and answer routes."""

import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from fastapi_limiter.depends import RateLimiter
from redis.exceptions import WatchError

from core.deps import AuthStateConn, MediaConn
from core.settings import settings
from helpers.auth import get_captcha_challenge
from helpers.jwt import AvailableTokenTypes
from helpers.media_pool.consumer import (
    claim_captcha,
    get_captcha_image as load_captcha_image,
)
from helpers.token_budget import mint_token, queue_token_record
from models.schema import CaptchaAnswerRequest
from redis_service import RedisConn, build_auth_state_key, set_auth_state_json

router = APIRouter()

# Parallel answers on one challenge can race. Retry a few times if another
# request commits first.
CAPTCHA_STATE_UPDATE_ATTEMPTS = 3

CAPTCHA_EXPIRED = {
    "code": "CAPTCHA_EXPIRED",
    "message": "CAPTCHA took too long. Restart the CAPTCHA.",
}

# KEYS[1] is the CAPTCHA challenge, KEYS[2] its wrong-answer counter. ARGV[1]
# is the number of wrong answers allowed. Returns the attempts left, 0 once the
# challenge is dropped, or -1 if it was already gone. The counter takes the
# challenge's remaining TTL, so it never outlives it, and counting and dropping
# in one step stops parallel wrong answers from all getting another try.
FAIL_CAPTCHA_SCRIPT = """
local ttl = redis.call('PTTL', KEYS[1])
if ttl < 0 then
    return -1
end
local attempts = redis.call('INCR', KEYS[2])
if attempts == 1 then
    redis.call('PEXPIRE', KEYS[2], ttl)
end
local left = tonumber(ARGV[1]) - attempts
if left <= 0 then
    redis.call('DEL', KEYS[1], KEYS[2])
    return 0
end
return left
"""


@router.post(
    "/captcha_id",
    dependencies=[Depends(RateLimiter(times=5, seconds=60))],
    responses={
        429: {"description": "Rate limit exceeded, see Retry-After"},
        503: {"description": "No CAPTCHA ready (IMAGES_NOT_READY)"},
    },
)
async def get_captcha_id(auth_state_conn: AuthStateConn, media_conn: MediaConn):
    """Claim a pre-rendered CAPTCHA and store its answer in auth state.

    Challenge keys have no cache version. They remain readable until their TTL
    expires, regardless of data-scraper cache invalidation.

    Args:
        auth_state_conn (AuthStateConn): Versionless auth-state Redis connection.
        media_conn (MediaConn): Media Redis with the media worker's CAPTCHAs.

    Returns:
        dict: Dictionary containing the claimed captcha_id.
    """
    # NOTE CAPTCHAs come from the media pool for surge capacity, not for CPU:
    # one costs ~8 ms, far less than a Fruit Buzz card. The stock absorbs
    # bursts; a sustained flood empties it, and the auth flow then fails with
    # IMAGES_NOT_READY (503) instead of rendering in the API and dragging the
    # regular routes down with it. Keep rendering out of this route.
    # The media worker rendered it in advance; claiming hands it out once.
    captcha_id, text = await claim_captcha(media_conn)

    key = build_auth_state_key("captcha", captcha_id)
    await set_auth_state_json(
        auth_state_conn,
        key,
        value=text,
        ttl=settings.CACHE_TTL_CAPTCHA_CHALLENGE,
    )

    return {"captcha_id": captcha_id}


@router.get(
    "/captcha_image/{captcha_id}",
    dependencies=[Depends(RateLimiter(times=5, seconds=60))],
    responses={
        404: {"description": "CAPTCHA challenge expired or not found"},
        429: {"description": "Rate limit exceeded, see Retry-After"},
    },
)
async def get_captcha_image(
    auth_state_conn: AuthStateConn, media_conn: MediaConn, captcha_id: str
):

    challenge = await get_captcha_challenge(auth_state_conn, captcha_id=captcha_id)
    # The image lives CAPTCHA_IMAGE_TTL_SECONDS, the answer the whole challenge;
    # either one missing means this CAPTCHA has to be restarted.
    image = await load_captcha_image(media_conn, captcha_id) if challenge else None

    if not image:
        raise HTTPException(status_code=404, detail=CAPTCHA_EXPIRED)

    return Response(
        content=image,
        media_type="image/png",
    )


async def _solve(
    conn: RedisConn, challenge_key: str, attempts_key: str, answer: str
) -> str | None:
    """Return the challenge's token for a correct answer, None for a wrong one.

    The first correct answer mints the token and stores it on the challenge,
    later ones get it back. WATCH lets only one of parallel correct answers
    commit; the others retry and find its token.

    Args:
        conn (RedisConn): Auth-state Redis with the challenge.
        challenge_key (str): Key of the challenge.
        attempts_key (str): Key of its wrong-answer counter.
        answer (str): The typed text.

    Returns:
        str | None: The CAPTCHA token, or None if the answer is wrong.

    Raises:
        HTTPException: 404 if the challenge is gone, 409 if parallel answers
            kept changing it.
    """
    for _ in range(CAPTCHA_STATE_UPDATE_ATTEMPTS):
        async with conn.client.pipeline() as pipe:
            await pipe.watch(challenge_key)
            raw = await pipe.get(challenge_key)
            if raw is None:
                raise HTTPException(status_code=404, detail=CAPTCHA_EXPIRED)
            # The answer text, or {"text", "token"} once solved.
            state = json.loads(raw)
            text = state["text"] if isinstance(state, dict) else state

            # Case-insensitive: some glyphs look alike in both cases, which would
            # make the CAPTCHA hard even for a human.
            if answer.lower() != text.lower():
                return None
            if isinstance(state, dict):
                return state["token"]

            token = mint_token(AvailableTokenTypes.CAPTCHA)
            pipe.multi()
            pipe.set(
                challenge_key, json.dumps({"text": text, "token": token}), keepttl=True
            )
            pipe.delete(attempts_key)
            # Opening a Word Guess with the token closes this challenge.
            queue_token_record(pipe, token, origin=challenge_key)
            try:
                await pipe.execute()
            except WatchError:
                continue
        return token
    raise HTTPException(status_code=409, detail="CAPTCHA changed; retry")


@router.post(
    "/verify_captcha",
    dependencies=[Depends(RateLimiter(times=5, seconds=60))],
    responses={
        401: {
            "description": (
                "CAPTCHA answer incorrect (CAPTCHA_INCORRECT), or incorrect too "
                "often and dropped (CAPTCHA_ATTEMPTS_EXHAUSTED)"
            )
        },
        404: {"description": "CAPTCHA challenge expired or not found"},
        409: {"description": "Parallel answers changed the challenge; retry"},
        429: {"description": "Rate limit exceeded, see Retry-After"},
    },
)
async def get_captcha_token(auth_state_conn: AuthStateConn, req: CaptchaAnswerRequest):
    """Trade a correct CAPTCHA answer for a CAPTCHA token.

    A challenge mints one token. The solved challenge keeps it, so a repeated
    correct answer (a client that lost the response) gets the same token
    again; using the token for a Word Guess closes the challenge. Wrong answers are
    counted, and after MAX_CAPTCHA_ATTEMPTS the challenge is deleted, so a
    rotating set of IPs cannot guess on one image indefinitely.

    Args:
        auth_state_conn (AuthStateConn): Auth-state Redis with the challenge.
        req (CaptchaAnswerRequest): Challenge ID and the typed text.

    Returns:
        dict: The CAPTCHA token for the Word Guess step.

    Raises:
        HTTPException: 401 with CAPTCHA_INCORRECT or CAPTCHA_ATTEMPTS_EXHAUSTED,
            404 with CAPTCHA_EXPIRED, or 409 if parallel answers kept
            changing the challenge.
    """
    challenge_key = build_auth_state_key("captcha", req.captcha_id)
    attempts_key = build_auth_state_key("captcha_attempts", req.captcha_id)

    token = await _solve(auth_state_conn, challenge_key, attempts_key, req.answer)
    if token is not None:
        return {"captcha_token": token}

    attempts_left = await auth_state_conn.client.eval(
        FAIL_CAPTCHA_SCRIPT,
        2,
        challenge_key,
        attempts_key,
        settings.MAX_CAPTCHA_ATTEMPTS,
    )
    if attempts_left < 0:
        raise HTTPException(status_code=404, detail=CAPTCHA_EXPIRED)
    if attempts_left == 0:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "CAPTCHA_ATTEMPTS_EXHAUSTED",
                "message": "Too many wrong answers. Load a new CAPTCHA.",
            },
        )
    raise HTTPException(
        status_code=401,
        detail={
            "code": "CAPTCHA_INCORRECT",
            "message": "The CAPTCHA text doesn't match. Check the image and try again.",
        },
    )
