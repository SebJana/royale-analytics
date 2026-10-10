"""Security question challenge route."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from fastapi_limiter.depends import RateLimiter
from rapidfuzz import fuzz

from core.settings import settings
from helpers.jwt import AvailableTokenTypes, create_access_token, validate_access_token
from models.schema import SecurityQuestionsRequest
from routers.auth_routes.common import round_token_scheme

router = APIRouter()


# NOTE: this is in no way, shape or form a secure protection for the authentication access
# mostly implemented as a fun way to have SOME sort of access denial
# For a more secure protection one of the answers could be an actual password instead of a card, in that
# case fuzzy matching and the .lower() comparison should be adjusted (set settings.SECURITY_FUZZY_THRESHOLD to 100)
# NOTE: having a short Halli Galli token expiry time and a tight rate limiting for requests to solve the security questions
# makes brute forcing the answers much harder
# TODO: to further increase "security", add more questions and serve 3 of those randomly to make brute forcing harder, or more so more time consuming
# Additionally upon 3 correctly being solved, rotate those ones out for X timeframe?
@router.post(
    "/verify_security_questions",
    dependencies=[Depends(RateLimiter(times=3, seconds=60))],
    responses={
        401: {"description": "Token expired or answers incorrect"},
        429: {"description": "Rate limit exceeded, see Retry-After"},
    },
)
async def get_security_token(
    req: SecurityQuestionsRequest,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(round_token_scheme)
    ],
):
    if not credentials or not validate_access_token(
        credentials.credentials,
        AvailableTokenTypes.HALLI_GALLI.value,
    ):
        raise HTTPException(
            status_code=401,
            detail={
                "code": "HALLI_GALLI_TOKEN_EXPIRED",
                "message": "Halli Galli took too long. Restart verification.",
            },
        )

    # Calculate similarity ratios for all three security questions using fuzzy matching
    q1 = fuzz.ratio(settings.MOST_ANNOYING_CARD.lower(), req.most_annoying_card.lower())
    q2 = fuzz.ratio(settings.MOST_SKILLFUL_CARD.lower(), req.most_skillful_card.lower())
    q3 = fuzz.ratio(settings.MOST_MOUSEY_CARD.lower(), req.most_mousey_card.lower())

    # Allow slight typos by using fuzzy matching
    if min(q1, q2, q3) >= settings.SECURITY_FUZZY_THRESHOLD:
        # Generate and return a valid token upon matching answers
        return {
            "security_token": create_access_token(
                type=AvailableTokenTypes.SECURITY.value,
                expires_minutes=settings.SECURITY_TOKEN_EXPIRES_IN,
            )
        }

    raise HTTPException(
        status_code=401,
        detail={
            "code": "SECURITY_ANSWERS_INCORRECT",
            "message": "One or more answers are incorrect. Check all three answers and try again.",
        },
    )
