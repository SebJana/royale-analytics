from dotenv import load_dotenv, find_dotenv
import os

load_dotenv(find_dotenv())


class Settings:
    """Application settings and configuration."""

    # Redis Configuration
    REDIS_PASSWORD: str = os.getenv("REDIS_PASSWORD", "")
    CACHE_REDIS_HOST: str = "redis-cache"
    AUTH_STATE_REDIS_HOST: str = "redis-auth-state"
    KEY_STORE_REDIS_HOST: str = "redis-key-store"
    REDIS_PORT: int = 6379

    # The app's key pool starts at most one request per second on each key.
    # Zero disables the optional cap across the whole app pool.
    CR_KEY_REQUESTS_PER_SECOND: float = 1.0
    CR_KEY_POOL_REQUESTS_PER_SECOND: float = 0.0

    # JWT Secret for Admin tokens
    JWT_SECRET: str = os.getenv("JWT_SECRET", "")

    # Security Question Answers
    MOST_ANNOYING_CARD: str = os.getenv("MOST_ANNOYING_CARD", "")
    MOST_SKILLFUL_CARD: str = os.getenv("MOST_SKILLFUL_CARD", "")
    MOST_MOUSEY_CARD: str = os.getenv("MOST_MOUSEY_CARD", "")

    # TODO add setting where neither .lower() nor any fuzzy matching are used to have a "secure" option
    # How close does the given security question answer need to be to the actual one from .env?
    SECURITY_FUZZY_THRESHOLD: int = 95  # Keep in high 90s to let cards with typos pass

    CAPTCHA_CHAR_LENGTH: int = 6  # Adjust how difficult the captcha is (default: 4-6)
    CAPTCHA_DIGIT_PERCENTAGE: float = (
        0.33  # Adjust what share of the captcha is digits (range: 0-1)
    )

    # Maximum number of cards shown together in one Halli Galli round.
    # Keep this equal to the number shown by the frontend, since both sides
    # need to count the same cards when deciding whether a fruit wins.
    HALLI_GALLI_GAME_ROUND_CARDS = 4

    # Keep this many future cards prepared for the frontend to preload.
    # One new card enters per round, so this is also how many rounds ahead
    # add_next_round picks a card. The value is saved with each new game.
    HALLI_GALLI_MAX_PRELOADED_CARDS = 3

    # Maximum number of reusable rendered PNGs for each fruit and amount pair.
    # For example, banana with four fruits has its own pool of this size.
    # NOTE This is a security relevant setting, if variations are not arbitrarily big
    # an "attacker" could start fingerprinting the already known images and compare
    # the incoming (from the same pool) to those
    HALLI_GALLI_CARD_VARIATIONS_PER_COMBINATION = 25

    # Rotate raw card variations after they are created, so that an automated attack
    # does not receive the same variations of cards over and over for hours.
    # Reusing a variation does not extend its lifetime.
    HALLI_GALLI_CARD_TTL_MINUTES = 10

    # A fruit counts as a Halli Galli only when its visible total equals this
    # number exactly. Tune it together with the maximum fruit amount per card
    # and the number of cards shown, since these determine how often a win occurs.
    HALLI_GALLI_WINNING_FRUIT_COUNT = 5

    # Base reaction time in milliseconds before adding jitter and the calibrated
    # network delay.
    # Every round receives a deadline, including those without a winning count.
    # An early next-card request is therefore handled the same way in both cases.
    HALLI_GALLI_ROUND_WINDOW_MS = 1750
    # Sample uniformly within plus or minus this percentage of the base time.
    # At 2000 ms and 12.5%, the sampled window is 1750 to 2250 ms.
    HALLI_GALLI_ROUND_JITTER_PERCENT = 12.5
    # Do not ask the frontend to reveal the next card more often than this.
    # Longer game windows still produce a longer interval automatically.
    HALLI_GALLI_MIN_NEXT_CARD_INTERVAL_MS = 3000

    # TODO Also randomly decide how many fruit at once (5,6,7) win?

    # When enabled, require a click on a fruit at the edge chosen for the game.
    # When disabled, a click anywhere on the required winning card is enough;
    # that card is still randomly chosen as oldest or newest when the game starts.
    HALLI_GALLI_REQUIRE_TARGET_FRUIT = True
    # Fruits whose relevant edges are this close to the extreme also count.
    # The value is normalized to card width for left/right and height for top/bottom.
    HALLI_GALLI_TARGET_FRUIT_BUFFER = 0.05
    # Expand each side of the target fruit's hitbox by X% of that fruit's
    # bounding-box width or height to allow slightly imprecise clicks.
    HALLI_GALLI_TARGET_FRUIT_HITBOX_PADDING = 0.05

    # Number of mistakes the player can make before losing the game.
    # A mistake includes buzzing incorrectly or failing to beat the bot.
    # The player loses when their remaining lives reach zero.
    HALLI_GALLI_PLAYER_LIVES = 3

    # Number of rounds the bot can lose before the player wins the game.
    # Keeping player and bot lives equal gives both sides the same number of
    # allowed losses. Skew these values to adjust the game's difficulty.
    HALLI_GALLI_BOT_LIVES = 3

    # A completed calibration ID can create exactly one game session within 15
    # seconds. The normal browser flow uses it immediately, while the short
    # lifetime limits opportunities to hand it to another client.
    HALLI_GALLI_CALIBRATION_TTL_SECONDS = 15
    # The server sends this many nonce-bound probes during one calibration run.
    HALLI_GALLI_CALIBRATION_PROBE_COUNT = 10
    # At least this many probes must receive a valid reply for a passing result.
    HALLI_GALLI_CALIBRATION_MIN_SAMPLES = 7
    # Each browser pong must arrive within this server-measured time limit.
    HALLI_GALLI_CALIBRATION_PROBE_TIMEOUT_SECONDS = 1.0
    # Reject connections whose fastest and slowest valid RTT samples differ by more than this.
    HALLI_GALLI_CALIBRATION_MAX_JITTER_MS = 150
    # Cap the saved median at 500 ms. This has one of the largest effects on
    # poor-network players: a generous cap helps genuine high-latency clients,
    # but also gives an AI attack bot more time if it deliberately slows traffic.
    # It remains above the roughly 269 ms broad-Internet P90 reported by
    # CAIDA's Frankfurt monitor.
    # TODO: Revisit accessibility for very high-latency players. At about
    # 950 ms RTT, this cap credits only 500 ms, and the 1-second probe timeout
    # leaves little room for variation. Evaluate a fairer allowance and probe
    # timeout without rewarding clients that deliberately delay replies.
    HALLI_GALLI_CALIBRATION_MAX_RTT_MS = 500
    # Browser origins permitted to open the calibration WebSocket. Configure the
    # production frontend origin with HALLI_GALLI_WS_ALLOWED_ORIGINS.
    HALLI_GALLI_WS_ALLOWED_ORIGINS = tuple(
        origin.strip()
        for origin in os.getenv(
            "HALLI_GALLI_WS_ALLOWED_ORIGINS",
            "http://localhost,http://127.0.0.1,"
            "http://localhost:3000,http://localhost:5173,"
            "http://127.0.0.1:3000,http://127.0.0.1:5173",
        ).split(",")
        if origin.strip()
    )

    # Token Expiry Minutes
    CAPTCHA_TOKEN_EXPIRES_IN: int = (
        30  # How long does the user have access to try wordle?
    )
    # Keep this one short, to minimize brute force attacks on the answers (+ rate limiting on verify route)
    WORDLE_TOKEN_EXPIRES_IN: int = (
        5  # How long does the user have access to start Halli Galli?
    )
    HALLI_GALLI_TOKEN_EXPIRES_IN: int = (
        5  # How long does the user have access to try the security questions?
    )
    SECURITY_TOKEN_EXPIRES_IN: int = (
        3  # How long does the user have access to the auth token request?
    )
    REMOVE_PLAYER_TOKEN_EXPIRES_IN: int = (
        15  # How long does the user have access to protected routes?
    )

    # Amount of guesses a user has to solve a wordle
    MAX_WORDLE_GUESSES: int = 6

    # Application Configuration
    INIT_RETRIES: int = 3
    INIT_RETRY_DELAY: float = 3

    # MongoDB Configuration
    MONGO_CLIENT_NAME: str = "cr-analytics-api"
    # Connections the API opens at most. Requests beyond it wait for a free
    # one, up to the request's Mongo deadline below.
    # NOTE Keep below MOTOR_MAX_WORKERS of the api service in docker-compose.yml.
    MONGO_MAX_POOL_SIZE: int = 100
    # Total time the Mongo operations of one request may take, counted from
    # the request's start; past it the API answers 503. Covers waiting for a
    # pooled connection, server selection and the queries themselves, and
    # Mongo stops a query that overruns it.
    # NOTE Keep below the browser's timeout (12 s, frontend axios.ts), which
    # stays below proxy_read_timeout (15 s) of /api/ in frontend/nginx.conf, so
    # the browser receives the 503 instead of giving up first.
    MONGO_REQUEST_TIMEOUT_S: float = 8  # seconds
    # Retry-After sent with that 503.
    MONGO_TIMEOUT_RETRY_AFTER_S: int = 5  # seconds

    # Untracked periods shorter than this are not shown on the player page.
    # The first sync after reactivation fetches the last 25 battles, so a short
    # gap loses nothing; an hour of nonstop play is about 20 battles.
    TRACKING_GAP_HINT_MIN_S: int = 60 * 60  # 1 hour

    # A newly tracked player's profile snapshot is refreshed after this time.
    # NOTE Keep equal to PROFILE_MIN_INTERVAL in the data scraper settings,
    # which schedules every later refresh.
    PROFILE_MIN_INTERVAL: float = 24 * 60 * 60  # 1 day

    # Maximum time interval that can be requested using a BetweenRequest for decks, cards, stats
    # NOTE: If none is wanted, just set the limit to an arbitrarily big number
    MAX_TIME_RANGE_DAYS: int = 10 * 365
    # Longest accepted timezone name. The longest IANA name,
    # "America/Argentina/ComodRivadavia", has 32 characters; the bound keeps
    # arbitrary strings out of the zoneinfo lookup and the error message.
    TIMEZONE_MAX_LENGTH: int = 64

    # Game mode filter limits. Unknown modes are accepted (see
    # validate_game_modes), so these bounds are what keeps a request from
    # naming thousands of them.
    # Source: RoyaleAPI's list of every mode the game has had,
    # https://github.com/RoyaleAPI/cr-api-data/blob/master/docs/json/game_modes.json
    # (last updated 2023-10-18): 380 modes, the longest name
    # "Event_ValentinesDay_MagicArcher_and_Princess_EventDeck" (54 characters),
    # all names ASCII [A-Za-z0-9_].
    # Assumptions: the game adds a few dozen modes per year, so 500 covers the
    # whole history for years. The stored modes never shrink, so a filter of
    # nearly every stored mode has to fit. Event names grow with the cards
    # they combine, so 100 characters leave room for longer ones.
    # Each limit applies to game_modes and exclude_game_modes alike.
    # NOTE Uvicorn caps the request line and headers at about 16 KB, which 500
    # modes of about 20 characters come close to. The frontend's
    # gameModesForQuery sends the shorter of the two lists, so a request names
    # at most half of the stored modes; keep it that way.
    GAME_MODE_FILTER_MAX_MODES: int = 500
    GAME_MODE_NAME_MAX_LENGTH: int = 100

    # Longest free-text answer of the auth challenges (CAPTCHA text, security
    # questions, Halli Galli card ids). Real answers are a few words; the
    # fuzzy comparison of the security answers costs time per character.
    AUTH_ANSWER_MAX_LENGTH: int = 64

    # Seasons start on the first Monday of a month at this UTC hour. Supercell
    # publishes the day but not the hour. Ranked battles around the October
    # 2026 reset point to 09:00 UTC, the logs bracket it between 08:25 and
    # 21:19 UTC (docs/battle-log-field-findings.md). Kept until a source or
    # more resets say otherwise.
    SEASON_RESET_HOUR_UTC: int = 9
    # Oldest season id a request may name. The first-Monday rule holds since
    # the first themed season, which started on Monday, July 1, 2019.
    SEASON_FIRST_ID: str = "2019-07"
    # Seasons /seasons returns by default, newest first. The frontend offers
    # exactly these in its timespan filter.
    SEASON_CATALOGUE_LIMIT: int = 4
    # Upper bound of the /seasons limit, more than every season since
    # SEASON_FIRST_ID for years to come
    SEASON_CATALOGUE_MAX_LIMIT: int = 240
    # Amount of battles that can be retrieved in one request
    MIN_BATTLES: int = 1
    MAX_BATTLES: int = 100

    # Cache TTL (Time To Live) in seconds
    # Player statistics are keyed by the player's syncVersion, which changes
    # whenever the data scraper stores new battles for that player. Cached data is
    # therefore always as up-to-date as the most recently scraped data in MongoDB.
    # Keep TTL still in the minutes to hours range as fallback, and for data
    # without a per-player version (total battle count)

    # Metadata estimates are cheap enough to refresh frequently as battles arrive.
    CACHE_TTL_TOTAL_BATTLES: int = 60  # 1 minute
    CACHE_TTL_PLAYER_BATTLE_STATS: int = 10 * 60  # 10 minutes
    CACHE_TTL_BATTLES: int = (
        1 * 60
    )  # 1 minute (short cache time, query params likely to change often with before timestamp. Also no real calculation effort needed for retrieving last battles)
    CACHE_TTL_DECK_STATS: int = 10 * 60  # 10 minutes
    CACHE_TTL_CARD_STATS: int = 10 * 60  # 10 minutes

    # Card filter limits of the deck statistics. Include mode keeps decks that
    # contain every selected card, so more cards than a deck holds can never
    # match. Decks have 8 cards; only the 12-card ClanWar_BoatBattle defenses
    # are larger, and filtering for a full defense is not a use case, so the
    # limit stays at a regular deck. Match mode scores decks by shared cards
    # and excluded cards drop decks, so both may name more.
    # NOTE Keep equal to the limits in the frontend's cardFilter.tsx.
    DECK_FILTER_MAX_INCLUDE_CARDS: int = 8
    DECK_FILTER_MAX_CARDS: int = 32
    # Tower troops per list. Include mode allows one: a deck has one tower.
    DECK_FILTER_MAX_SUPPORT: int = 8
    # Decks returned per deck statistics request, the top ones of the sort
    # order. Real players have 150-200 decks over their whole history, so this
    # only cuts extreme ranges; the response stays at about 200 KB of JSON,
    # well inside the browser's ~5 MB persisted query cache. The response
    # counts every matching deck, so the page can ask for tighter filters.
    DECK_STATS_LIMIT: int = 250

    # Retry-After for /cards before the data scraper's first card refresh on a
    # fresh install. The scraper refreshes right after it starts.
    CARDS_NOT_READY_RETRY_AFTER: int = 30  # seconds

    # Player search returns at most this many players per query.
    SEARCH_RESULT_LIMIT: int = 20
    # Longer queries are rejected before they reach the index: they cannot
    # match anything. Names are at most 15 characters (the game's limit,
    # emoji variation selectors included) and "#" plus a tag at most 13(?).
    # NOTE Keep maxQueryLength in the frontend's usePlayerSearch.ts equal.
    # The frontend trims whitespace before it applies the limit.
    SEARCH_QUERY_MAX_LENGTH: int = 15
    # The API indexes its own adds and removes instantly. The refresh only
    # picks up the data scraper's renames and deactivations, which may lag.
    # Cheap at any size: it reads only the players changed since the last one.
    SEARCH_REFRESH_INTERVAL_S: int = 60  # 1 minute
    # Every this often a refresh reads all tracked players instead, repairing
    # changes a writer made without moving searchChangedAt.
    SEARCH_FULL_REFRESH_INTERVAL_S: int = 6 * 60 * 60  # 6 hours
    # A changed index is saved at most this often, and always on shutdown.
    # A save copies the whole index on the event loop (~20 ms at 100k
    # players, ~450 ms at 1M); a restart catches up on what it missed.
    SEARCH_SNAPSHOT_INTERVAL_S: int = 30 * 60  # 30 minutes

    # Route timings for the scraper's status dashboard: one sample per
    # interval in the key store, kept for the retention. ~125 bytes per route
    # used in a minute, so a dozen busy routes take ~10-15 MB over 7 days.
    # NOTE Match METRICS_HISTORY_INTERVAL and METRICS_HISTORY_RETENTION in the
    # scraper's settings.py: the dashboard charts both on the same clock.
    ROUTE_METRICS_INTERVAL_S: int = 60  # seconds
    ROUTE_METRICS_RETENTION_S: int = 7 * 24 * 60 * 60  # 7 days
    # Retry delay while the first index build has not succeeded, also sent
    # as Retry-After with the 503 search responses in the meantime.
    SEARCH_BUILD_RETRY_S: int = 30  # seconds

    # Get set both in current and ahead version of the cache, and therefore not invalidated with every cycle
    CACHE_TTL_CAPTCHA_CHALLENGE: int = (
        5 * 60
    )  # 5 minutes (how long does a captcha challenge stay valid in cache)
    # Time PER VALID GUESS on the wordle challenge, as every new guess resets the redis json and ttl
    CACHE_TTL_WORDLE_CHALLENGE: int = 10 * 60  # 10 minutes
    CACHE_TTL_HALLI_GALLI: int = 15 * 60  # 10 minutes
    CACHE_TTL_NYT_WORDLE_ANSWER: int = 6 * 60 * 60  # 6 hours


# Global settings instance
settings = Settings()
