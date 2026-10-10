"""CAPTCHA challenge and answer routes."""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from fastapi_limiter.depends import RateLimiter

from core.deps import AuthStateConn, MediaConn
from core.settings import settings
from helpers.auth import get_captcha_text_from_state
from helpers.jwt import AvailableTokenTypes, create_access_token
from helpers.media_pool.consumer import (
    claim_captcha,
    get_captcha_image as load_captcha_image,
)
from models.schema import CaptchaAnswerRequest
from redis_service import build_auth_state_key, set_auth_state_json

router = APIRouter()


@router.get(
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
    # one costs ~8 ms, far less than a Halli Galli card. The stock absorbs
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

    text = await get_captcha_text_from_state(auth_state_conn, captcha_id=captcha_id)
    # The image lives CAPTCHA_IMAGE_TTL_SECONDS, the answer the whole challenge;
    # either one missing means this CAPTCHA has to be restarted.
    image = await load_captcha_image(media_conn, captcha_id) if text else None

    if not image:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "CAPTCHA_EXPIRED",
                "message": "CAPTCHA took too long. Restart the CAPTCHA.",
            },
        )

    # Return the image with the session ID in headers
    return Response(
        content=image,
        media_type="image/png",
    )


@router.post(
    "/verify_captcha",
    dependencies=[Depends(RateLimiter(times=5, seconds=60))],
    responses={
        401: {"description": "CAPTCHA answer incorrect"},
        404: {"description": "CAPTCHA challenge expired or not found"},
        429: {"description": "Rate limit exceeded, see Retry-After"},
    },
)
async def get_captcha_token(auth_state_conn: AuthStateConn, req: CaptchaAnswerRequest):

    text = await get_captcha_text_from_state(auth_state_conn, captcha_id=req.captcha_id)

    if not text:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "CAPTCHA_EXPIRED",
                "message": "CAPTCHA took too long. Restart the CAPTCHA.",
            },
        )

    # TODO Lock the challenge once it is used up. Today the answer stays valid
    # for the whole CACHE_TTL_CAPTCHA_CHALLENGE: one solved CAPTCHA mints a new
    # CAPTCHA token (and so a new Wordle) on every call, and one challenge
    # takes unlimited wrong guesses, bounded only by the per-IP rate limit,
    # which rotating IPs bypass. Delete the challenge on a correct answer
    # (atomically, e.g. GETDEL, so two parallel requests cannot both succeed),
    # and count wrong answers on it, deleting it after a few (3?) so a new
    # CAPTCHA is needed. The frontend then shows CAPTCHA_EXPIRED and reloads.
    # NOTE: compare with lowercase answer and text, otherwise the captcha is very hard to solve
    # even for a human
    # Check if stored and given answer match
    if req.answer.lower() == text.lower():
        return {
            "captcha_token": create_access_token(
                type=AvailableTokenTypes.CAPTCHA.value,
                expires_minutes=settings.CAPTCHA_TOKEN_EXPIRES_IN,
            )
        }

    raise HTTPException(
        status_code=401,
        detail={
            "code": "CAPTCHA_INCORRECT",
            "message": "The CAPTCHA text doesn't match. Check the image and try again.",
        },
    )
