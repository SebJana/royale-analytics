"""Redemption budgets and session limits for the authentication flow's tokens.

A JWT alone stays valid until it expires, so one solved step could open any
number of next steps. Every token therefore gets a record in the auth-state
Redis when it is minted. Three rules keep one solved step from multiplying:

- One live session: opening a session (a Word Guess, a Fruit Buzz game) closes
  the one the token opened before (if there are any), so two never run side by side.
- One output token: the step's success spends the token. Its result, and the
  token it minted, are kept so a client that lost the response gets the same
  token again instead of a second one.
- A budget for retries: a failed session can be replaced only a few times.

Using a token for the next step also closes the session that minted it (its
origin). The client evidently received the token, so the replay is no longer
needed.

The record is required, not optional: a token without one is rejected. The
auth-state Redis does not persist, so a restart invalidates every outstanding
token instead of handing each a fresh budget. That costs a user a few minutes
of verification, as all of these tokens are short-lived anyway.

Record fields (a Redis hash at ``auth:token:<jti>``):
    remaining: Redemptions left.
    session: Key of the live session the token opened, if any.
    origin: Key of the session that minted the token, until its first use.
    minted: The token this one was exchanged for, replayed on repeats.
"""

from jose import jwt
from redis.asyncio.client import Pipeline
from redis.exceptions import RedisError

from core.settings import settings
from helpers.jwt import AvailableTokenTypes, create_access_token
from redis_service import RedisConn, build_auth_state_key

# Lifetime in minutes and redemption budget of every token type.
TOKEN_BUDGETS: dict[str, tuple[int, int]] = {
    AvailableTokenTypes.CAPTCHA: (
        settings.CAPTCHA_TOKEN_EXPIRES_IN,
        settings.CAPTCHA_TOKEN_BUDGET,
    ),
    AvailableTokenTypes.WORD_GUESS: (
        settings.WORD_GUESS_TOKEN_EXPIRES_IN,
        settings.WORD_GUESS_TOKEN_BUDGET,
    ),
    AvailableTokenTypes.FRUIT_BUZZ: (
        settings.FRUIT_BUZZ_TOKEN_EXPIRES_IN,
        settings.FRUIT_BUZZ_TOKEN_BUDGET,
    ),
    AvailableTokenTypes.SECURITY: (
        settings.SECURITY_TOKEN_EXPIRES_IN,
        settings.SECURITY_TOKEN_BUDGET,
    ),
    AvailableTokenTypes.REMOVE_PLAYER_TOKEN: (
        settings.REMOVE_PLAYER_TOKEN_EXPIRES_IN,
        settings.REMOVE_PLAYER_TOKEN_BUDGET,
    ),
}

# KEYS[1] is the token record. Returns 1 if a redemption was charged, 0 for a
# spent budget or a missing record. Reading and decrementing in one step stops
# parallel requests from all passing the check on the last redemption.
CHARGE_SCRIPT = """
local remaining = tonumber(redis.call('HGET', KEYS[1], 'remaining'))
if not remaining or remaining <= 0 then
    return 0
end
redis.call('HINCRBY', KEYS[1], 'remaining', -1)
return 1
"""

# KEYS[1] is the token record. Gives one redemption back if the record still
# exists. A plain HINCRBY on a record that expired a moment ago would create
# it again without a TTL, and the non-evicting auth-state Redis keeps it
# forever.
REFUND_SCRIPT = """
if redis.call('EXISTS', KEYS[1]) == 1 then
    return redis.call('HINCRBY', KEYS[1], 'remaining', 1)
end
return 0
"""

# KEYS[1] is the token record. Spends what is left of the budget, for a step
# that succeeded. Guarded by EXISTS for the same reason as REFUND_SCRIPT.
SPEND_SCRIPT = """
if redis.call('EXISTS', KEYS[1]) == 1 then
    redis.call('HSET', KEYS[1], 'remaining', 0)
end
return 0
"""

# KEYS[1] is the token record, KEYS[2] the new session. ARGV[1] is the
# session's JSON and ARGV[2] its TTL in seconds. Charges a redemption, deletes
# the session the token opened before and the session that minted the token,
# and writes the new one. Returns 1, or 0 for a spent budget or missing record.
# Writing the session in the same step matters: written afterwards, two
# parallel opens could each delete the other's predecessor before either
# session exists and leave both alive. Session keys come from the record, so
# this assumes a single Redis instance, not a cluster.
OPEN_SESSION_SCRIPT = """
local remaining = tonumber(redis.call('HGET', KEYS[1], 'remaining'))
if not remaining or remaining <= 0 then
    return 0
end
redis.call('HINCRBY', KEYS[1], 'remaining', -1)
local previous = redis.call('HGET', KEYS[1], 'session')
if previous then
    redis.call('DEL', previous)
end
local origin = redis.call('HGET', KEYS[1], 'origin')
if origin then
    redis.call('DEL', origin)
    redis.call('HDEL', KEYS[1], 'origin')
end
redis.call('HSET', KEYS[1], 'session', KEYS[2])
redis.call('SET', KEYS[2], ARGV[1], 'EX', ARGV[2])
return 1
"""

# KEYS[1] is the redeemed token's record, KEYS[2] the record of the token it
# would mint. ARGV[1] is that new token, or '' for a failed attempt (a wrong
# answer), ARGV[2] its budget and ARGV[3] its lifetime in seconds.
# - A token minted earlier is returned again for a successful repeat, so a
#   client whose response got lost can retry. A failed repeat gets false.
# - Otherwise one redemption is checked, the token's origin session closed,
#   and either one redemption charged ('' returned) or, on success, the whole
#   budget spent and the new token stored and given its record.
# Returns the token, '' for a charged failure, or false for a spent budget or
# missing record. All in one step, so parallel successes mint one token.
REDEEM_SCRIPT = """
local minted = redis.call('HGET', KEYS[1], 'minted')
if minted then
    if ARGV[1] == '' then
        return false
    end
    return minted
end
local remaining = tonumber(redis.call('HGET', KEYS[1], 'remaining'))
if not remaining or remaining <= 0 then
    return false
end
local origin = redis.call('HGET', KEYS[1], 'origin')
if origin then
    redis.call('DEL', origin)
    redis.call('HDEL', KEYS[1], 'origin')
end
if ARGV[1] == '' then
    redis.call('HINCRBY', KEYS[1], 'remaining', -1)
    return ''
end
redis.call('HSET', KEYS[1], 'minted', ARGV[1], 'remaining', 0)
redis.call('HSET', KEYS[2], 'remaining', ARGV[2])
redis.call('EXPIRE', KEYS[2], ARGV[3])
return ARGV[1]
"""


def token_record_key(jti: str) -> str:
    """Return the auth-state key holding the budget of the token ``jti``."""

    return build_auth_state_key("token", jti)


def mint_token(token_type: str) -> str:
    """Create a token of ``token_type`` with its configured lifetime.

    The token has no budget yet. Pass it to ``queue_token_record`` within the
    same transaction that hands it out, or use ``issue_token``.

    Args:
        token_type (str): One of ``AvailableTokenTypes``.

    Returns:
        str: The encoded JWT.
    """

    lifetime_minutes, _ = TOKEN_BUDGETS[token_type]
    return create_access_token(type=token_type, expires_minutes=lifetime_minutes)


def queue_token_record(pipe: Pipeline, token: str, origin: str | None = None) -> None:
    """Queue the budget record of a freshly minted token on ``pipe``.

    Args:
        pipe (Pipeline): Transaction pipeline, already in MULTI mode, that
            also stores whatever hands out the token.
        token (str): Token returned by ``mint_token``.
        origin (str | None): Key of the session that minted the token, closed
            when the token is first used.
    """

    # Minted by this process a moment ago, so its claims need no verification.
    claims = jwt.get_unverified_claims(token)
    lifetime_minutes, budget = TOKEN_BUDGETS[claims["type"]]
    key = token_record_key(claims["jti"])
    fields = {"remaining": budget}
    if origin is not None:
        fields["origin"] = origin
    pipe.hset(key, mapping=fields)
    pipe.expire(key, lifetime_minutes * 60)


def queue_token_spend(pipe: Pipeline, claims: dict) -> None:
    """Queue spending the rest of a token's budget, for a step that succeeded.

    Args:
        pipe (Pipeline): Transaction pipeline, already in MULTI mode.
        claims (dict): Claims of the token whose step succeeded.
    """

    pipe.eval(SPEND_SCRIPT, 1, token_record_key(claims["jti"]))


async def issue_token(conn: RedisConn, token_type: str) -> str:
    """Mint a token of ``token_type`` together with its budget record.

    Args:
        conn (RedisConn): Auth-state Redis connection.
        token_type (str): One of ``AvailableTokenTypes``.

    Returns:
        str: The encoded JWT, redeemable ``TOKEN_BUDGETS`` times.

    Raises:
        RedisError: If the record could not be stored.
    """

    token = mint_token(token_type)
    async with conn.client.pipeline(transaction=True) as pipe:
        queue_token_record(pipe, token)
        await pipe.execute()
    return token


async def charge_token(conn: RedisConn, claims: dict) -> bool:
    """Spend one redemption of a verified token.

    Args:
        conn (RedisConn): Auth-state Redis connection.
        claims (dict): Verified claims from ``get_access_token_claims``.

    Returns:
        bool: False if the budget is spent or the record is gone.
    """

    charged = await conn.client.eval(CHARGE_SCRIPT, 1, token_record_key(claims["jti"]))
    return charged == 1


async def token_has_budget(conn: RedisConn, claims: dict) -> bool:
    """Check, without charging, whether a token has a redemption left.

    For skipping costly preparation of a step the token could not open. Not a
    guarantee: the redemption is only taken by the step's own atomic charge.

    Args:
        conn (RedisConn): Auth-state Redis connection.
        claims (dict): Verified claims from ``get_access_token_claims``.

    Returns:
        bool: False if the budget is spent or the record is gone.
    """

    remaining = await conn.client.hget(token_record_key(claims["jti"]), "remaining")
    return remaining is not None and int(remaining) > 0


async def refund_token(conn: RedisConn, claims: dict) -> None:
    """Give back a redemption whose step failed on the server's side.

    Never raises: the failure that called for the refund is the error worth
    reporting, and an unrefunded redemption only costs the user a retry.

    Args:
        conn (RedisConn): Auth-state Redis connection.
        claims (dict): Claims of the token that was charged.
    """

    try:
        await conn.client.eval(REFUND_SCRIPT, 1, token_record_key(claims["jti"]))
    except RedisError as error:
        print(f"[WARNING] Could not refund token {claims['jti']}: {error}")


async def open_session(
    conn: RedisConn, claims: dict, session_key: str, session_json: str, ttl: int
) -> bool:
    """Charge a token and make ``session_key`` its only live session.

    Args:
        conn (RedisConn): Auth-state Redis connection.
        claims (dict): Verified claims of the token opening the session.
        session_key (str): Auth-state key of the new session.
        session_json (str): Serialized session state.
        ttl (int): Session lifetime in seconds.

    Returns:
        bool: False if the budget is spent or the record is gone; nothing was
            written then.
    """

    opened = await conn.client.eval(
        OPEN_SESSION_SCRIPT,
        2,
        token_record_key(claims["jti"]),
        session_key,
        session_json,
        ttl,
    )
    return opened == 1


async def redeem_token(
    conn: RedisConn, claims: dict, new_type: str, succeeded: bool = True
) -> str | None:
    """Redeem a token for a new one, or charge a failed attempt.

    Args:
        conn (RedisConn): Auth-state Redis connection.
        claims (dict): Verified claims of the redeemed token.
        new_type (str): Type of the token handed out on success.
        succeeded (bool): Whether the attempt earns the new token. A failed
            one only spends a redemption.

    Returns:
        str | None: The new token (or the one minted earlier, on a repeat),
            "" for a charged failed attempt, or None if the budget is spent.
    """

    token = mint_token(new_type) if succeeded else ""
    new_jti = jwt.get_unverified_claims(token)["jti"] if succeeded else "unused"
    lifetime_minutes, budget = TOKEN_BUDGETS[new_type]
    return await conn.client.eval(
        REDEEM_SCRIPT,
        2,
        token_record_key(claims["jti"]),
        token_record_key(new_jti),
        token,
        budget,
        lifetime_minutes * 60,
    )
