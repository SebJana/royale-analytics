"""Security question challenge route."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from fastapi_limiter.depends import RateLimiter
from rapidfuzz import fuzz

from core.deps import AuthStateConn
from core.settings import settings
from helpers.jwt import AvailableTokenTypes, get_access_token_claims
from helpers.token_budget import redeem_token
from models.schema import SecurityQuestionsRequest
from routers.auth_routes.common import round_token_scheme

router = APIRouter()


# The questions are a fun barrier, not real protection: the answers are card
# names, compared case-insensitively and with fuzzy matching, and shared by
# everyone. For real protection one answer would have to be a long secret
# (and SECURITY_FUZZY_THRESHOLD 100, without the .lower() comparison).
# Guessing is slowed by the short Fruit Buzz token lifetime, the rate limit
# below and the FRUIT_BUZZ_TOKEN_BUDGET attempts each won game buys.
# TODO Serve 3 random questions out of a larger set, so the answers cannot be
# collected once and reused, and rotate a set out for a while once it was
# answered correctly.
@router.post(
    "/verify_security_questions",
    dependencies=[Depends(RateLimiter(times=3, seconds=60))],
    responses={
        401: {
            "description": (
                "Fruit Buzz token missing or expired (FRUIT_BUZZ_TOKEN_EXPIRED), "
                "already used or its attempts used up (FRUIT_BUZZ_TOKEN_USED_UP), "
                "or answers incorrect (SECURITY_ANSWERS_INCORRECT)"
            )
        },
        429: {"description": "Rate limit exceeded, see Retry-After"},
    },
)
async def get_security_token(
    req: SecurityQuestionsRequest,
    auth_state_conn: AuthStateConn,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(round_token_scheme)
    ],
):
    """Trade correct answers for the security token.

    Every attempt spends one redemption of the Fruit Buzz token; correct
    answers spend all of it and mint one security token, which a repeat of
    the correct answers gets again.

    Args:
        req (SecurityQuestionsRequest): The three answers.
        auth_state_conn (AuthStateConn): Auth-state Redis with the budgets.
        credentials (HTTPAuthorizationCredentials | None): The Fruit Buzz token.

    Returns:
        dict: The security token for the removal-token exchange.

    Raises:
        HTTPException: 401 with FRUIT_BUZZ_TOKEN_EXPIRED,
            FRUIT_BUZZ_TOKEN_USED_UP or SECURITY_ANSWERS_INCORRECT.
    """
    claims = (
        get_access_token_claims(credentials.credentials, AvailableTokenTypes.FRUIT_BUZZ)
        if credentials
        else None
    )
    if claims is None:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "FRUIT_BUZZ_TOKEN_EXPIRED",
                "message": "Fruit Buzz took too long. Restart verification.",
            },
        )

    # Fuzzy matching lets answers with slight typos pass.
    q1 = fuzz.ratio(settings.MOST_ANNOYING_CARD.lower(), req.most_annoying_card.lower())
    q2 = fuzz.ratio(settings.MOST_SKILLFUL_CARD.lower(), req.most_skillful_card.lower())
    q3 = fuzz.ratio(settings.MOST_MOUSEY_CARD.lower(), req.most_mousey_card.lower())
    correct = min(q1, q2, q3) >= settings.SECURITY_FUZZY_THRESHOLD

    # Every attempt counts, right or wrong: the budget exists to cap guessing.
    # Correct answers spend the token and mint one security token, which a
    # repeat of the correct answers gets again.
    security_token = await redeem_token(
        auth_state_conn, claims, AvailableTokenTypes.SECURITY, succeeded=correct
    )
    if security_token is None:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "FRUIT_BUZZ_TOKEN_USED_UP",
                "message": "No answer attempts left. Restart verification.",
            },
        )
    if not security_token:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "SECURITY_ANSWERS_INCORRECT",
                "message": "One or more answers are incorrect. Check all three answers and try again.",
            },
        )
    return {"security_token": security_token}
