"""Wordle challenge and guess routes."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from fastapi_limiter.depends import RateLimiter

from core.deps import AuthStateConn
from core.settings import settings
from helpers.auth import get_wordle_challenge_from_state
from helpers.jwt import AvailableTokenTypes, create_access_token, validate_access_token
from helpers.wordle import (
    pick_random_wordle_solution,
    is_valid_guess,
    evaluate_guess,
    is_guess_solution,
)
from models.schema import WordleAnswerRequest
from redis_service import build_auth_state_key, set_auth_state_json
from routers.auth_routes.common import round_token_scheme

router = APIRouter()


@router.get(
    "/wordle_id",
    dependencies=[Depends(RateLimiter(times=5, seconds=60))],
    responses={
        401: {"description": "CAPTCHA token missing or expired"},
        429: {"description": "Rate limit exceeded, see Retry-After"},
    },
)
async def get_wordle_id(
    auth_state_conn: AuthStateConn,
    credentials: HTTPAuthorizationCredentials | None = Depends(round_token_scheme),
):
    # Require the CAPTCHA token as Authorization: Bearer before opening a Wordle session.
    if not credentials or not validate_access_token(
        credentials.credentials, AvailableTokenTypes.CAPTCHA.value
    ):
        raise HTTPException(
            status_code=401,
            detail={
                "code": "CAPTCHA_TOKEN_EXPIRED",
                "message": "CAPTCHA took too long. Restart verification.",
            },
        )

    wordle = pick_random_wordle_solution()
    wordle_id = str(uuid.uuid4())

    key = build_auth_state_key("wordle", wordle_id)
    await set_auth_state_json(
        auth_state_conn,
        key,
        value={"solution": wordle, "guesses": 0},
        # Challenge TTL is exact; cache jitter is only for rebuildable data.
        ttl=settings.CACHE_TTL_WORDLE_CHALLENGE,
    )

    return {"wordle_id": wordle_id}


@router.post(
    "/verify_wordle",
    dependencies=[Depends(RateLimiter(times=15, seconds=60))],
    responses={
        401: {"description": "CAPTCHA token missing or expired"},
        404: {"description": "Wordle challenge expired or not found"},
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
    credentials: HTTPAuthorizationCredentials | None = Depends(round_token_scheme),
):
    if not credentials or not validate_access_token(
        credentials.credentials,
        AvailableTokenTypes.CAPTCHA.value,
    ):
        raise HTTPException(
            status_code=401,
            detail={
                "code": "CAPTCHA_TOKEN_EXPIRED",
                "message": "CAPTCHA took too long. Restart verification.",
            },
        )

    # Extract the wordle session to the given wordle_id
    wordle_session, key = await get_wordle_challenge_from_state(
        auth_state_conn, req.wordle_id
    )

    # Session not found (either expired or non existent id given)
    if not wordle_session:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "WORDLE_EXPIRED",
                "message": "Wordle took too long. Restart Wordle.",
            },
        )

    # The session JSON is corrupted, abort session to not give up token on empty solution or similar problems
    if "guesses" not in wordle_session or "solution" not in wordle_session:
        raise HTTPException(
            status_code=500,
            detail="Something went wrong, try again with a new wordle challenge.",
        )

    # Extract all needed fields
    solution = wordle_session.get("solution")
    guesses = wordle_session.get("guesses")
    guess = req.wordle_guess.lower()

    # Check if all guesses have been used up
    if guesses >= settings.MAX_WORDLE_GUESSES:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "WORDLE_GUESSES_EXHAUSTED",
                "message": "You've used all your guesses. Start a new Wordle.",
            },
        )

    # TODO potentially implement hard mode? ;)
    # Upon a non valid guess, reject guess and don't charge a guess
    if not is_valid_guess(guess):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "WORDLE_INVALID_GUESS",
                "message": "That word isn't in the word list. Try another five-letter word.",
            },
        )

    # Check the guess and if it is the solution
    evaluation = evaluate_guess(solution, guess)
    is_solution = is_guess_solution(solution, guess)
    wordle_token = ""

    # TODO Lock a solved session. It stays open after the solution, so sending
    # the solution again mints another Wordle token until the 6 guesses are
    # used. Delete or mark the session solved when it is solved. The guess
    # count is also read here and written below, so parallel guesses can all
    # pass the check; count atomically (a Lua script or WATCH) instead.
    # See the token budget TODO in helpers/jwt.py.
    # If it is, generate a wordle token
    if is_solution:
        wordle_token = create_access_token(
            type=AvailableTokenTypes.WORDLE.value,
            expires_minutes=settings.WORDLE_TOKEN_EXPIRES_IN,
        )

    # Update the 'guesses' count
    updated_challenge = {"solution": solution, "guesses": guesses + 1}
    # Calculate the remaining ones, always 0 or bigger upon any issue
    remaining_guesses = max(settings.MAX_WORDLE_GUESSES - (guesses + 1), 0)

    # Save the updated session again
    # NOTE: resets the previous TTL, so the TTL is a PER GUESS TTL
    await set_auth_state_json(
        auth_state_conn,
        key,
        updated_challenge,
        settings.CACHE_TTL_WORDLE_CHALLENGE,
    )

    result = {
        "evaluation": evaluation,
        "remaining_guesses": remaining_guesses,
        "is_solution": is_solution,
        "wordle_token": wordle_token,
    }

    # Reveal the answer upon no more guesses left or when the request solved the wordle
    if remaining_guesses == 0 or is_solution:
        result["solution"] = solution

    return result


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
