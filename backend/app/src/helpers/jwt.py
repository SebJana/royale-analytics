from datetime import datetime, timedelta
from jose import jwt, JWTError
import uuid
from enum import StrEnum
from core.settings import settings


# Types of tokens the api gives out and validates
class AvailableTokenTypes(StrEnum):
    CAPTCHA = "captcha"
    HALLI_GALLI = "halli_galli"
    SECURITY = "security"
    WORDLE = "wordle"
    REMOVE_PLAYER_TOKEN = "remove_player_token"


def create_access_token(type: str, expires_minutes: int = 30):
    """
    Create a JWT token with admin scope and expiration time.

    This function generates a JSON Web Token (JWT) that grants privileges
    for the specified duration. The token includes an expiration time and admin scope.

    Args:
        type (str): The type/role the token should be and enable.
        expires_minutes (int, optional): Token expiration time in minutes.
            Defaults to 5 minutes for security purposes.

    Returns:
        str: Encoded JWT token string that can be used for admin authentication.
    """
    expire = datetime.now() + timedelta(minutes=expires_minutes)

    payload = {
        "sub": "admin",
        "type": type,
        "jti": str(uuid.uuid4()),
        "exp": expire,
    }

    return jwt.encode(payload, settings.JWT_SECRET, algorithm="HS256")


def get_access_token_claims(token: str, token_type: str) -> dict | None:
    """Return verified claims for a token of ``token_type``, or ``None``.

    JWT decoding verifies the signature and expiry before this function returns
    any claims. In addition to checking the token type, it requires a string
    ``jti`` (JWT ID). The JTI is a unique identifier generated when this token
    was issued, so server-side state can be bound to this exact token without
    storing the bearer token itself.

    Args:
        token: Encoded JWT bearer token to verify and decode.
        token_type: Required value of the token's ``type`` claim.

    Returns:
        The verified JWT claims when the token is valid, unexpired, of the
        requested type, and has a JTI; otherwise ``None``.
    """

    # NOTE: Halli Galli binds a latency calibration to the Wordle token's JTI
    # for connection-specific fairness, not as a primary anti-cheat boundary.
    # A security-question attempt counter can likewise use the JTI as its
    # Redis key to limit attempts per issued Halli Galli token instead of per IP.

    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=["HS256"])
    except JWTError:
        return None

    if payload.get("type") != token_type or not isinstance(payload.get("jti"), str):
        return None
    return payload


# TODO Give every token a redemption budget. Routes only check type and
# expiry, so one token opens any number of next steps until it expires. Retries
# are fine, mass use is not; count redemptions per JTI atomically and reject
# past a budget (settings): ~5 Wordle sessions per CAPTCHA token, ~3 Halli
# Galli games per Wordle token, ~5 security attempts per Halli Galli token,
# 1 removal token per security token, a few removals per removal token.
# Together with locking solved challenges (TODOs in captcha.py and wordle.py).
def validate_access_token(token: str, type: str) -> bool:
    """Return whether ``token`` is a valid, unexpired token of ``type``.

    This boolean compatibility helper intentionally discards verified claims.
    Call ``get_access_token_claims`` when a caller needs the verified JTI or
    another claim for server-side token binding.

    Args:
        token: Encoded JWT bearer token to verify.
        type: Required value of the token's ``type`` claim.

    Returns:
        ``True`` when the token is valid, unexpired, and has the requested
        type; otherwise ``False``.
    """

    return get_access_token_claims(token, type) is not None
