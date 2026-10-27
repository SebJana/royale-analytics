from redis_service import RedisConn, build_auth_state_key, get_auth_state_json


async def get_captcha_challenge(redis_conn: RedisConn, captcha_id: str):
    """Read a CAPTCHA challenge from the versionless auth-state Redis store.

    Args:
        redis_conn (RedisConn): Versionless Redis connection for auth challenges.
        captcha_id (str): Unique identifier for the captcha challenge.

    Returns:
        str | dict | None: The answer text of an open challenge, a dict with
            "text" and "token" once it is solved, or None if it is gone.
    """
    key = build_auth_state_key("captcha", captcha_id)
    return await get_auth_state_json(redis_conn, key)
