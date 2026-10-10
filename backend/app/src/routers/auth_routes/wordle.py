"""Wordle challenge and guess routes."""

import json
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from fastapi_limiter.depends import RateLimiter
from redis.exceptions import WatchError

from core.deps import AuthStateConn
from core.settings import settings
from helpers.jwt import AvailableTokenTypes, get_access_token_claims
from helpers.token_budget import (
    mint_token,
    open_session,
    queue_token_record,
    queue_token_spend,
)
from helpers.wordle import (
    pick_random_wordle_solution,
    is_valid_guess,
    evaluate_guess,
    is_guess_solution,
)
from models.schema import WordleAnswerRequest
from redis_service import build_auth_state_key
from routers.auth_routes.common import round_token_scheme

router = APIRouter()

# Two guesses on one session can race. Retry a few times if another request
# commits first.
WORDLE_STATE_UPDATE_ATTEMPTS = 3

CAPTCHA_TOKEN_EXPIRED = {
    "code": "CAPTCHA_TOKEN_EXPIRED",
    "message": "CAPTCHA took too long. Restart verification.",
}
WORDLE_EXPIRED = {
    "code": "WORDLE_EXPIRED",
    "message": "Wordle took too long. Restart Wordle.",
}


def _captcha_claims(credentials: HTTPAuthorizationCredentials | None) -> dict:
    """Return the verified CAPTCHA token claims or raise CAPTCHA_TOKEN_EXPIRED."""

    claims = (
        get_access_token_claims(credentials.credentials, AvailableTokenTypes.CAPTCHA)
        if credentials
        else None
    )
    if claims is None:
        raise HTTPException(status_code=401, detail=CAPTCHA_TOKEN_EXPIRED)
    return claims


@router.post(
    "/wordle_id",
    dependencies=[Depends(RateLimiter(times=5, seconds=60))],
    responses={
        401: {
            "description": (
                "CAPTCHA token missing or expired (CAPTCHA_TOKEN_EXPIRED), or "
                "solved or its Wordle sessions used up (CAPTCHA_TOKEN_USED_UP)"
            )
        },
        429: {"description": "Rate limit exceeded, see Retry-After"},
    },
)
async def get_wordle_id(
    auth_state_conn: AuthStateConn,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(round_token_scheme)
    ],
):
    """Open a Wordle session, spending one redemption of the CAPTCHA token.

    The session replaces the token's previous one, which is deleted.

    Args:
        auth_state_conn (AuthStateConn): Auth-state Redis for session and budget.
        credentials (HTTPAuthorizationCredentials | None): The CAPTCHA token.

    Returns:
        dict: The Wordle session ID.

    Raises:
        HTTPException: 401 with CAPTCHA_TOKEN_EXPIRED or CAPTCHA_TOKEN_USED_UP.
    """
    claims = _captcha_claims(credentials)

    wordle_id = str(uuid.uuid4())
    # The session belongs to the token that paid for it, so another CAPTCHA
    # token cannot guess on it.
    session = {
        "solution": pick_random_wordle_solution(),
        "guesses": 0,
        "captcha_jti": claims["jti"],
    }
    opened = await open_session(
        auth_state_conn,
        claims,
        build_auth_state_key("wordle", wordle_id),
        json.dumps(session),
        # Challenge TTL is exact; cache jitter is only for rebuildable data.
        settings.CACHE_TTL_WORDLE_CHALLENGE,
    )
    if not opened:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "CAPTCHA_TOKEN_USED_UP",
                "message": "This CAPTCHA can't start another Wordle. Restart verification.",
            },
        )

    return {"wordle_id": wordle_id}


def _evaluate(session: dict, guess: str) -> dict:
    """Score one guess against a live session, raising for unusable guesses.

    Args:
        session (dict): Stored session with solution and guess count.
        guess (str): Lowercased guess.

    Returns:
        dict: The guess's response, with the solution once revealed.

    Raises:
        HTTPException: 429 with WORDLE_GUESSES_EXHAUSTED, 422 with
            WORDLE_INVALID_GUESS, or 500 for a corrupted session.
    """
    # The session JSON is corrupted, abort session to not give up token on empty solution or similar problems
    if "guesses" not in session or "solution" not in session:
        raise HTTPException(
            status_code=500,
            detail="Something went wrong, try again with a new wordle challenge.",
        )

    solution = session["solution"]
    guesses = session["guesses"]

    if guesses >= settings.MAX_WORDLE_GUESSES:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "WORDLE_GUESSES_EXHAUSTED",
                "message": "You've used all your guesses. Start a new Wordle.",
            },
        )

    # TODO Hard mode (revealed hints must be reused in later guesses) is not implemented.
    # A word outside the list costs no guess; it is likely a typo.
    if not is_valid_guess(guess):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "WORDLE_INVALID_GUESS",
                "message": "That word isn't in the word list. Try another five-letter word.",
            },
        )

    is_solution = is_guess_solution(solution, guess)
    # Never negative, even for a session stored with too many guesses.
    remaining_guesses = max(settings.MAX_WORDLE_GUESSES - (guesses + 1), 0)
    result = {
        "evaluation": evaluate_guess(solution, guess),
        "remaining_guesses": remaining_guesses,
        "is_solution": is_solution,
        "wordle_token": "",
    }
    # Reveal the answer upon no more guesses left or when the request solved the wordle
    if remaining_guesses == 0 or is_solution:
        result["solution"] = solution
    return result


@router.post(
    "/verify_wordle",
    dependencies=[Depends(RateLimiter(times=15, seconds=60))],
    responses={
        401: {"description": "CAPTCHA token missing or expired"},
        404: {
            "description": (
                "Wordle challenge expired, replaced by a newer one, not found, or "
                "opened by another CAPTCHA token"
            )
        },
        409: {"description": "Wordle changed by parallel guesses; retry"},
        422: {"description": "Invalid Wordle guess or request"},
        429: {
            "description": "Wordle guesses exhausted, or rate limit exceeded (Retry-After)"
        },
        500: {"description": "Wordle challenge state invalid"},
    },
)
async def get_wordle_token(
    auth_state_conn: AuthStateConn,
    req: WordleAnswerRequest,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(round_token_scheme)
    ],
):
    """Score a guess; the solving guess mints the one Wordle token.

    A solved session keeps its result, so a client that lost the response gets
    the same token again. Solving spends the CAPTCHA token: one CAPTCHA, one
    Wordle token.

    Args:
        auth_state_conn (AuthStateConn): Auth-state Redis with the session.
        req (WordleAnswerRequest): Session ID and the guess.
        credentials (HTTPAuthorizationCredentials | None): The CAPTCHA token.

    Returns:
        dict: Evaluation, guesses left, whether it solved the Wordle, the
            Wordle token on a solve, and the solution once revealed.

    Raises:
        HTTPException: 401 with CAPTCHA_TOKEN_EXPIRED, 404 with WORDLE_EXPIRED,
            422 with WORDLE_INVALID_GUESS, 429 with WORDLE_GUESSES_EXHAUSTED,
            409 if parallel guesses kept changing the session, or 500 for a
            corrupted session.
    """
    # Guesses spend no redemption: the session already caps them, and opening
    # it was charged.
    claims = _captcha_claims(credentials)
    key = build_auth_state_key("wordle", req.wordle_id)
    guess = req.wordle_guess.lower()

    for _ in range(WORDLE_STATE_UPDATE_ATTEMPTS):
        # WATCH makes the write conditional on nobody changing the session
        # after the read: parallel guesses cannot all pass the guess limit, and
        # a solve cannot race with another guess or with a newer session
        # deleting this one.
        async with auth_state_conn.client.pipeline() as pipe:
            await pipe.watch(key)
            raw = await pipe.get(key)
            session = json.loads(raw) if raw else None

            # Not found (expired, replaced or unknown ID) and owned by another
            # token look the same, so a foreign session ID reveals nothing.
            if not session or session.get("captcha_jti") != claims["jti"]:
                raise HTTPException(status_code=404, detail=WORDLE_EXPIRED)
            if "result" in session:
                return session["result"]

            result = _evaluate(session, guess)

            pipe.multi()
            if result["is_solution"]:
                token = mint_token(AvailableTokenTypes.WORDLE)
                result["wordle_token"] = token
                # KEEPTTL: the replay lasts as long as the session would have.
                pipe.set(key, json.dumps({**session, "result": result}), keepttl=True)
                # Starting Halli Galli with the token closes this session.
                queue_token_record(pipe, token, origin=key)
                queue_token_spend(pipe, claims)
            else:
                # NOTE: resets the previous TTL, so the TTL is a PER GUESS TTL
                pipe.set(
                    key,
                    json.dumps({**session, "guesses": session["guesses"] + 1}),
                    ex=settings.CACHE_TTL_WORDLE_CHALLENGE,
                )
            try:
                await pipe.execute()
            except WatchError:
                continue
        return result

    raise HTTPException(status_code=409, detail="Wordle changed; retry the guess")


# NOTE: DEPRECATED Wordle token endpoint
# This route requires users to solve the actual New York Times daily Wordle.
# While simpler to implement, it is much easier to bypass (the answer can be looked up
# or brute-retrieved after the 6 allowed attempts). The custom Wordle challenge endpoint
# is preferred, as it is more secure and more engaging for human users [and definitely more frustrating too ;)].
"""
@router.post("/verify_wordle_nyt", dependencies=[Depends(RateLimiter(times=5, seconds=60))])
async def get_nyt_wordle_token(redis_conn: RedConn, req: NYTWordleAnswerRequest):

    if not validate_access_token(req.captcha_token, AvailableTokenTypes.CAPTCHA.value):
        raise HTTPException(
            status_code=401,
            detail="No authorization token generated, invalid captcha token given",
        )

    if not valid_timezone(req.timezone):
        raise HTTPException(
            status_code=422,
            detail=f"No wordle token generated, timezone {req.timezone} doesn't exist.",
        )

    todays_wordle = ""
    # Convert current time to user's timezone to get correct date for their location
    today_str = datetime.now(ZoneInfo(req.timezone)).date().isoformat()

    key = build_redis_key(
        service="crApi",
        resource="wordleAnswer",
        params={"timezone": req.timezone},
    )
    cached_wordle_answer = await get_redis_json(redis_conn, key)

    # Verify cached answer is from today's date to prevent using yesterday's answer
    if cached_wordle_answer and cached_wordle_answer.get("print_date", "") == today_str:
        todays_wordle = cached_wordle_answer.get("solution", "").lower()
    # If not get todays answer from the Wordle-API
    else:
        try:
            response = await get_todays_nyt_wordle(req.timezone)
        # Prevent authentication if the correct answer cannot be verified
        except Exception:
            raise HTTPException(
                status_code=500,
                detail="Today's wordle answer can't be checked, the Wordle API seems to be having issues.",
            )
        if response:
            todays_wordle = response.get("solution", "").lower()
            # Store in cache to avoid repeated API calls throughout the day
            await set_redis_json(
                redis_conn, key, response, ttl=settings.CACHE_TTL_NYT_WORDLE_ANSWER
            )

    # If the guess and answer are the matching, generate the wordle token
    if todays_wordle.lower() == req.wordle_guess.lower():
        return {
            "wordle_token": create_access_token(
            type=AvailableTokenTypes.WORDLE.value,
            expires_minutes=settings.WORDLE_TOKEN_EXPIRES_IN,
            )
        }

    raise HTTPException(
        status_code=401,
        detail="No wordle token generated, incorrect answer given.",
    )
"""
