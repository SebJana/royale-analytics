"""Final player-removal token exchange route."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from fastapi_limiter.depends import RateLimiter

from core.deps import AuthStateConn
from helpers.jwt import AvailableTokenTypes, get_access_token_claims
from helpers.token_budget import redeem_token
from routers.auth_routes.common import round_token_scheme

router = APIRouter()


@router.post(
    "/remove_player_token",
    dependencies=[Depends(RateLimiter(times=5, seconds=60))],
    responses={
        401: {
            "description": (
                "Security token missing or expired (SECURITY_TOKEN_EXPIRED), or "
                "already exchanged without a token to replay (SECURITY_TOKEN_USED_UP)"
            )
        },
        429: {"description": "Rate limit exceeded, see Retry-After"},
    },
)
async def get_remove_player_token(
    auth_state_conn: AuthStateConn,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(round_token_scheme),
    ],
):
    """Exchange a security token for the removal token.

    A repeated request with the same security token gets the same removal
    token back, so a client whose response got lost can simply retry.

    Args:
        auth_state_conn (AuthStateConn): Auth-state Redis with the budgets.
        credentials (HTTPAuthorizationCredentials | None): The security token.

    Returns:
        dict: The removal token for the protected routes.

    Raises:
        HTTPException: 401 with SECURITY_TOKEN_EXPIRED or SECURITY_TOKEN_USED_UP.
    """
    claims = (
        get_access_token_claims(credentials.credentials, AvailableTokenTypes.SECURITY)
        if credentials
        else None
    )
    if claims is None:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "SECURITY_TOKEN_EXPIRED",
                "message": "Verification took too long. Restart verification.",
            },
        )

    remove_player_token = await redeem_token(
        auth_state_conn, claims, AvailableTokenTypes.REMOVE_PLAYER_TOKEN
    )
    if remove_player_token is None:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "SECURITY_TOKEN_USED_UP",
                "message": "This verification was already used. Restart verification.",
            },
        )

    return {"remove_player_token": remove_player_token}
