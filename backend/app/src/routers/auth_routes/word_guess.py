"""Word Guess challenge and guess routes."""

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
from helpers.word_guess import (
    pick_random_solution,
    is_valid_guess,
    evaluate_guess,
    is_guess_solution,
)
from models.schema import WordGuessAnswerRequest
from redis_service import build_auth_state_key
from routers.auth_routes.common import round_token_scheme

router = APIRouter()

# Two guesses on one session can race. Retry a few times if another request
# commits first.
WORD_GUESS_STATE_UPDATE_ATTEMPTS = 3

CAPTCHA_TOKEN_EXPIRED = {
    "code": "CAPTCHA_TOKEN_EXPIRED",
    "message": "CAPTCHA took too long. Restart verification.",
}
WORD_GUESS_EXPIRED = {
    "code": "WORD_GUESS_EXPIRED",
    "message": "Word Guess took too long. Restart Word Guess.",
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
    "/word_guess_id",
    dependencies=[Depends(RateLimiter(times=5, seconds=60))],
    responses={
        401: {
            "description": (
                "CAPTCHA token missing or expired (CAPTCHA_TOKEN_EXPIRED), or "
                "solved or its Word Guess sessions used up (CAPTCHA_TOKEN_USED_UP)"
            )
        },
        429: {"description": "Rate limit exceeded, see Retry-After"},
    },
)
async def get_word_guess_id(
    auth_state_conn: AuthStateConn,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(round_token_scheme)
    ],
):
    """Open a Word Guess session, spending one redemption of the CAPTCHA token.

    The session replaces the token's previous one, which is deleted.

    Args:
        auth_state_conn (AuthStateConn): Auth-state Redis for session and budget.
        credentials (HTTPAuthorizationCredentials | None): The CAPTCHA token.

    Returns:
        dict: The Word Guess session ID.

    Raises:
        HTTPException: 401 with CAPTCHA_TOKEN_EXPIRED or CAPTCHA_TOKEN_USED_UP.
    """
    claims = _captcha_claims(credentials)

    word_guess_id = str(uuid.uuid4())
    # The session belongs to the token that paid for it, so another CAPTCHA
    # token cannot guess on it.
    session = {
        "solution": pick_random_solution(),
        "guesses": 0,
        "captcha_jti": claims["jti"],
    }
    opened = await open_session(
        auth_state_conn,
        claims,
        build_auth_state_key("word_guess", word_guess_id),
        json.dumps(session),
        # Challenge TTL is exact; cache jitter is only for rebuildable data.
        settings.CACHE_TTL_WORD_GUESS_CHALLENGE,
    )
    if not opened:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "CAPTCHA_TOKEN_USED_UP",
                "message": "This CAPTCHA can't start another Word Guess. Restart verification.",
            },
        )

    return {"word_guess_id": word_guess_id}


def _evaluate(session: dict, guess: str) -> dict:
    """Score one guess against a live session, raising for unusable guesses.

    Args:
        session (dict): Stored session with solution and guess count.
        guess (str): Lowercased guess.

    Returns:
        dict: The guess's response, with the solution once revealed.

    Raises:
        HTTPException: 429 with WORD_GUESS_ATTEMPTS_EXHAUSTED, 422 with
            WORD_GUESS_INVALID_WORD, or 500 for a corrupted session.
    """
    # The session JSON is corrupted, abort session to not give up token on empty solution or similar problems
    if "guesses" not in session or "solution" not in session:
        raise HTTPException(
            status_code=500,
            detail="Something went wrong, try again with a new Word Guess challenge.",
        )

    solution = session["solution"]
    guesses = session["guesses"]

    if guesses >= settings.WORD_GUESS_MAX_ATTEMPTS:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "WORD_GUESS_ATTEMPTS_EXHAUSTED",
                "message": "You've used all your guesses. Start a new Word Guess.",
            },
        )

    # TODO Hard mode (revealed hints must be reused in later guesses) is not implemented.
    # A word outside the list costs no guess; it is likely a typo.
    if not is_valid_guess(guess):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "WORD_GUESS_INVALID_WORD",
                "message": "That word isn't in the word list. Try another five-letter word.",
            },
        )

    is_solution = is_guess_solution(solution, guess)
    # Never negative, even for a session stored with too many guesses.
    remaining_guesses = max(settings.WORD_GUESS_MAX_ATTEMPTS - (guesses + 1), 0)
    result = {
        "evaluation": evaluate_guess(solution, guess),
        "remaining_guesses": remaining_guesses,
        "is_solution": is_solution,
        "word_guess_token": "",
    }
    # Reveal the answer once no guesses are left or the request solved the Word Guess
    if remaining_guesses == 0 or is_solution:
        result["solution"] = solution
    return result


@router.post(
    "/verify_word_guess",
    dependencies=[Depends(RateLimiter(times=15, seconds=60))],
    responses={
        401: {"description": "CAPTCHA token missing or expired"},
        404: {
            "description": (
                "Word Guess challenge expired, replaced by a newer one, not found, or "
                "opened by another CAPTCHA token"
            )
        },
        409: {"description": "Word Guess changed by parallel guesses; retry"},
        422: {"description": "Invalid guess or request"},
        429: {
            "description": "Word Guess attempts exhausted, or rate limit exceeded (Retry-After)"
        },
        500: {"description": "Word Guess challenge state invalid"},
    },
)
async def get_word_guess_token(
    auth_state_conn: AuthStateConn,
    req: WordGuessAnswerRequest,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(round_token_scheme)
    ],
):
    """Score a guess; the solving guess mints the one Word Guess token.

    A solved session keeps its result, so a client that lost the response gets
    the same token again. Solving spends the CAPTCHA token: one CAPTCHA, one
    Word Guess token.

    Args:
        auth_state_conn (AuthStateConn): Auth-state Redis with the session.
        req (WordGuessAnswerRequest): Session ID and the guess.
        credentials (HTTPAuthorizationCredentials | None): The CAPTCHA token.

    Returns:
        dict: Evaluation, guesses left, whether it solved the Word Guess, the
            Word Guess token on a solve, and the solution once revealed.

    Raises:
        HTTPException: 401 with CAPTCHA_TOKEN_EXPIRED, 404 with WORD_GUESS_EXPIRED,
            422 with WORD_GUESS_INVALID_WORD, 429 with WORD_GUESS_ATTEMPTS_EXHAUSTED,
            409 if parallel guesses kept changing the session, or 500 for a
            corrupted session.
    """
    # Guesses spend no redemption: the session already caps them, and opening
    # it was charged.
    claims = _captcha_claims(credentials)
    key = build_auth_state_key("word_guess", req.word_guess_id)
    guess = req.guess.lower()

    for _ in range(WORD_GUESS_STATE_UPDATE_ATTEMPTS):
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
                raise HTTPException(status_code=404, detail=WORD_GUESS_EXPIRED)
            if "result" in session:
                return session["result"]

            result = _evaluate(session, guess)

            pipe.multi()
            if result["is_solution"]:
                token = mint_token(AvailableTokenTypes.WORD_GUESS)
                result["word_guess_token"] = token
                # KEEPTTL: the replay lasts as long as the session would have.
                pipe.set(key, json.dumps({**session, "result": result}), keepttl=True)
                # Starting Fruit Buzz with the token closes this session.
                queue_token_record(pipe, token, origin=key)
                queue_token_spend(pipe, claims)
            else:
                # NOTE: resets the previous TTL, so the TTL is a PER GUESS TTL
                pipe.set(
                    key,
                    json.dumps({**session, "guesses": session["guesses"] + 1}),
                    ex=settings.CACHE_TTL_WORD_GUESS_CHALLENGE,
                )
            try:
                await pipe.execute()
            except WatchError:
                continue
        return result

    raise HTTPException(status_code=409, detail="Word Guess changed; retry the guess")
