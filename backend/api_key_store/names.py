"""Map any pool name to a distinct, environment-safe variable prefix."""

import re

# Match keys for custom pools: CR_API_POOL_<UTF-8 pool name in hex>_KEY_<number>.
# Group 1 is the encoded pool name and group 2 is the numbered key slot.
# For example, pool "team" and slot 2 use CR_API_POOL_7465616D_KEY_2.
# The "app" and "scraper" pools use their readable prefixes instead.
_CUSTOM_ENTRY = re.compile(r"^CR_API_POOL_([0-9A-F]+)_KEY_([0-9]+)$")


def env_key_prefix(pool: str) -> str:
    if not isinstance(pool, str) or not pool.strip():
        raise ValueError("Pool name must be a non-empty string")
    # Preserve the readable names already used by the two deployed services.
    if pool in ("app", "scraper"):
        return f"CR_API_{pool.upper()}_KEY_"
    # Hex encoding is reversible and keeps names such as 'a:b', 'a/b', and
    # Unicode distinct while producing ordinary environment variable names.
    return f"CR_API_POOL_{pool.encode('utf-8').hex().upper()}_KEY_"


def parse_env_key_name(name: str) -> tuple[str, int] | None:
    for pool in ("app", "scraper"):
        prefix = env_key_prefix(pool)
        if name.startswith(prefix) and name[len(prefix) :].isdigit():
            return pool, int(name[len(prefix) :])
    match = _CUSTOM_ENTRY.fullmatch(name)
    if match:
        try:
            pool = bytes.fromhex(match.group(1)).decode("utf-8")
            if env_key_prefix(pool) + match.group(2) != name:
                return None
            return pool, int(match.group(2))
        except (ValueError, UnicodeDecodeError):
            return None
    return None
