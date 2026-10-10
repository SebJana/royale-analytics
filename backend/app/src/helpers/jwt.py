from datetime import datetime, timedelta
from jose import jwt, JWTError
import uuid
from enum import StrEnum
from core.settings import settings


# Types of tokens the api gives out and validates
class AvailableTokenTypes(StrEnum):
    CAPTCHA = "captcha"
    FRUIT_BUZZ = "fruit_buzz"
    SECURITY = "security"
    WORD_GUESS = "word_guess"
    REMOVE_PLAYER_TOKEN = "remove_player_token"


def create_access_token(type: str, expires_minutes: int = 30):
    """
    Create a JWT token with admin scope and expiration time.

    This function generates a JSON Web Token (JWT) that grants privileges
    for the specified duration. The token includes an expiration time and admin scope.

    Args:
        type (str): The type/role the token should be and enable.
        expires_minutes (int, optional): Token expiration time in minutes.
            Defaults to 30 minutes; callers pass their type's lifetime.

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

    # NOTE: Fruit Buzz binds a latency calibration to the Word Guess token's JTI
    # for connection-specific fairness, not as a primary anti-cheat boundary.
    # The redemption budget (helpers/token_budget.py) is keyed by the JTI too.

    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=["HS256"])
    except JWTError:
        return None

    if payload.get("type") != token_type or not isinstance(payload.get("jti"), str):
        return None
    return payload
