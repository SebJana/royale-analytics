"""Final player-removal token exchange route."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from fastapi_limiter.depends import RateLimiter

from core.settings import settings
from helpers.jwt import AvailableTokenTypes, create_access_token, validate_access_token
from routers.auth_routes.common import round_token_scheme

router = APIRouter()


@router.post(
    "/remove_player_token",
    dependencies=[Depends(RateLimiter(times=5, seconds=60))],
    responses={
        401: {"description": "Security token missing or expired"},
        429: {"description": "Rate limit exceeded, see Retry-After"},
    },
)
async def get_remove_player_token(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(round_token_scheme),
    ],
):
    if not credentials or not validate_access_token(
        credentials.credentials,
        AvailableTokenTypes.SECURITY.value,
    ):
        raise HTTPException(
            status_code=401,
            detail={
                "code": "SECURITY_TOKEN_EXPIRED",
                "message": "Verification took too long. Restart verification.",
            },
        )

    return {
        "remove_player_token": create_access_token(
            type=AvailableTokenTypes.REMOVE_PLAYER_TOKEN.value,
            expires_minutes=settings.REMOVE_PLAYER_TOKEN_EXPIRES_IN,
        )
    }
