from dotenv import load_dotenv, find_dotenv
import os

load_dotenv(find_dotenv())


class Settings:
    """Application settings and configuration."""

    # Scraping starts at most one request per second on each key.
    # Zero disables the optional cap across the whole scraper pool.
    CR_KEY_REQUESTS_PER_SECOND: float = 1.0
    CR_KEY_POOL_REQUESTS_PER_SECOND: float = 0.0

    # Redis Configuration
    REDIS_PASSWORD: str = os.getenv("REDIS_PASSWORD", "")
    # The scraper only writes reconstructible data here (the card and game
    # mode lists).
    REDIS_HOST: str = "redis-cache"
    # Key leases, the scraping schedules, capacity, and metrics. Never evicted.
    KEY_STORE_REDIS_HOST: str = "redis-key-store"
    REDIS_PORT: int = 6379

    # Application Configuration
    INIT_RETRIES: int = 3
    INIT_RETRY_DELAY: float = 3

    # Battle sync intervals
    # NOTE The battle log only returns the last ~25 battles. A player who plays
    # more battles than that between two syncs loses the older ones for good.
    # Shortest interval; small deployments sync every player this often.
    MIN_SYNC_INTERVAL: float = 5 * 60  # 5 minutes
    # Longest interval, for idle players and for an overloaded key pool.
    # At ~3 minutes per battle, 60 minutes are ~20 battles, still inside the
    # battle log window when an idle player suddenly starts playing.
    MAX_SYNC_INTERVAL: float = 60 * 60  # 60 minutes
    # NOTE Entries the battle log endpoint returns at most. A full log that
    # does not reach back to the stored watermark may have lost battles.
    BATTLE_LOG_SIZE: int = 25

    # Activity based adjustment of a player's next interval
    # A sync with at least this many new battles counts as high activity; the
    # player is synced sooner, at the base interval times the factor below.
    HIGH_ACTIVITY_BATTLES: int = 12
    HIGH_ACTIVITY_INTERVAL_FACTOR: float = 0.5
    # Load pressure rises linearly from 0 at this base interval to 1 at
    # MAX_SYNC_INTERVAL. With it, the high activity factor rises to 1 and the
    # shortest profile interval to PROFILE_PRESSURE_INTERVAL. Under full load
    # even the most active players then sync at the longest interval, which
    # keeps admission at the worst case of one battle request per player per
    # MAX_SYNC_INTERVAL.
    LOAD_FADE_START: float = 30 * 60  # 30 minutes
    # A sync without new battles stretches the previous interval by this
    # factor, up to MAX_SYNC_INTERVAL. Idle accounts then cost fewer requests.
    IDLE_INTERVAL_GROWTH: float = 1.5

    # Spreading due times (see intervals.py, "Spreading due times")
    # Players synced back to back, e.g. after a bulk insert or downtime, would
    # otherwise stay one block that saturates the keys every cycle. Each value
    # is a share of the delay it randomizes; every spread keeps the average
    # delay, so the capacity estimate stays valid.
    # Every punctual battle sync: random factor in [0.9, 1.1]
    INTERVAL_JITTER: float = 0.1
    # A battle sync that waited in a backlog (claimed later than this share of
    # its interval, or its first sync): random factor in [0.5, 1.5]
    BACKLOG_LATENESS_SHARE: float = 0.1
    BACKLOG_SPREAD: float = 0.5
    # Retries after failures, which often hit many players at once
    FAILURE_BACKOFF_JITTER: float = 0.2
    # First profile refresh of a player without a stored profile (e.g. bulk
    # inserted): random time within this
    # window instead of all at once. A late profile is only stale.
    PROFILE_FIRST_REFRESH_SPREAD: float = 6 * 60 * 60  # 6 hours

    # Capacity planning
    # Share of the raw key rate (usable keys x requests per second) that is
    # planned for. The rest is headroom for retries, 429 cooldowns, and cards.
    # New players are admitted while every tracked player, all of them active
    # and with profiles at PROFILE_PRESSURE_INTERVAL, could still be synced
    # within MAX_SYNC_INTERVAL.
    CAPACITY_UTILIZATION: float = 0.8

    # Profile snapshots
    # Profile stats (trophies, wins, level) only change when the player plays.
    # A player with battles since the last refresh is refreshed again after the
    # minimum interval; without battles the interval grows up to the maximum.
    # Under load the minimum itself rises towards PROFILE_PRESSURE_INTERVAL.
    PROFILE_MIN_INTERVAL: float = 24 * 60 * 60  # 1 day
    PROFILE_MAX_INTERVAL: float = 7 * 24 * 60 * 60  # 7 days
    # Shortest profile interval under full load. A late profile is only stale,
    # never lost, so profiles give their requests to battle syncs first.
    PROFILE_PRESSURE_INTERVAL: float = 7 * 24 * 60 * 60  # 7 days
    # Each refresh without battles since the previous one multiplies the
    # interval by this factor: 1 -> 2 -> 4 -> (8 but capped at) 7 days. Steeper than battles
    # (IDLE_INTERVAL_GROWTH), since a late profile is only stale, never lost.
    PROFILE_IDLE_GROWTH: float = 2.0
    # Profiles only run when no battle sync is due, unless they are overdue by
    # more than this. A busy pool then still refreshes every profile eventually,
    # through one worker that takes overdue profiles first.
    PROFILE_MAX_LATENESS: float = 6 * 60 * 60  # 6 hours
    # Retry delay after a failed profile refresh
    PROFILE_RETRY_DELAY: float = 30 * 60  # 30 minutes

    # A claimed player is due again after this time if its worker never acks,
    # e.g. because the container stopped. Has to exceed the longest job:
    # one Clash Royale request budget (30 s) plus the Mongo writes.
    CLAIM_TTL: float = 2 * 60  # 2 minutes
    # A job is cancelled after this time, so it ends while its claim is still
    # owned and cannot overlap a second job of the same player. Has to stay
    # below CLAIM_TTL and above the request budget (30 s) plus Mongo writes.
    JOB_TIMEOUT: float = 90  # seconds

    # Workers per scraper process. Each worker processes one player at a time,
    # so enough workers are needed to keep every usable key busy while other
    # workers wait on Mongo. Recomputed from the usable key count on every
    # reconciliation, within the min/max bounds.
    WORKERS_PER_KEY_REQUEST_PER_SECOND: float = 3.0
    MIN_WORKERS: int = 2
    MAX_WORKERS: int = 64

    # Longest time an idle worker sleeps before checking the schedule again.
    # Newly added players are picked up within this time.
    IDLE_POLL_INTERVAL: float = 5  # seconds

    # Retry delays for a failed battle sync, doubled per consecutive failure
    FAILURE_BACKOFF_BASE: float = 30  # seconds
    FAILURE_BACKOFF_MAX: float = 30 * 60  # 30 minutes
    # A 404 means the account is gone or the tag became invalid. The retry
    # delay starts at the base interval, so a single glitch costs one sync at
    # most, and doubles up to this maximum.
    NOT_FOUND_RETRY_MAX: float = 6 * 60 * 60  # 6 hours
    # A player is untracked after this many consecutive 404s, and only if the
    # first of them is at least NOT_FOUND_MIN_SPAN old.
    NOT_FOUND_DEACTIVATE_COUNT: int = 3
    NOT_FOUND_MIN_SPAN: float = 24 * 60 * 60  # 24 hours
    # Delay for players claimed while the Clash Royale API went into maintenance
    MAINTENANCE_RETRY_DELAY: float = 60  # seconds

    # How often the schedules are compared with the tracked players in Mongo.
    # Also recomputes the capacity and resizes the worker pool.
    RECONCILE_INTERVAL: float = 5 * 60  # 5 minutes

    # How often newly seen game modes are written to Mongo
    GAME_MODES_FLUSH_INTERVAL: float = 60  # seconds
    # How often the cached game mode list is rebuilt from Mongo without a new
    # mode. Bounds how long the list stays missing after an eviction.
    GAME_MODES_CACHE_REFRESH_INTERVAL: float = 10 * 60  # 10 minutes

    # How often the card list is fetched from the Clash Royale API
    CARDS_REFRESH_INTERVAL: float = 6 * 60 * 60  # 6 hours
    # Retry delay if a card refresh failed
    CARDS_RETRY_DELAY: float = 5 * 60  # 5 minutes
    # How often a missing card cache entry is restored from Mongo between
    # refreshes. Bounds how long the API reads Mongo after an eviction.
    CARDS_CACHE_CHECK_INTERVAL: float = 10 * 60  # 10 minutes

    # Self-hosted card images (see card_images.py)
    # Shared volume the image sets are written to; nginx serves it read-only.
    CARD_IMAGES_DIR: str = os.getenv("CARD_IMAGES_DIR", "/data/card-images")
    # NOTE Match the /card-images/ location in frontend/nginx.conf and the
    # Vite dev proxy. Update all three together.
    CARD_IMAGES_URL_PREFIX: str = "/card-images"
    # Only images from this host are mirrored
    CARD_IMAGE_HOST: str = "api-assets.clashroyale.com"
    # The CDN art is 285x420 px. Cards render at most 150 CSS px wide (cards
    # page) and ~85 px in decks. 240 px is 1.6x the largest size and over 2x
    # the deck size: slightly soft on the cards page on 2x screens, in exchange
    # for 11-16 KB (regular, evolution/hero) instead of ~150 KB per image.
    CARD_IMAGE_WIDTH: int = 240  # px
    # WebP quality, 0-100
    CARD_IMAGE_QUALITY: int = 85
    # Parallel CDN downloads during a refresh
    CARD_IMAGE_DOWNLOAD_CONCURRENCY: int = 4
    # Timeout for a single image download
    CARD_IMAGE_DOWNLOAD_TIMEOUT: float = 15  # seconds
    # Largest accepted source image, far above the ~150 KB the CDN sends
    CARD_IMAGE_MAX_BYTES: int = 5 * 1024 * 1024  # 5 MB
    # Refresh interval while the CDN lacks images the card list names. Longer
    # than CARDS_RETRY_DELAY, since art can take days to appear and every
    # attempt checks each image again.
    CARD_IMAGE_MISSING_RETRY_DELAY: float = 30 * 60  # 30 minutes
    # How long a replaced set stays on disk, a grace period for clients that
    # still hold an earlier card list. Not a hard bound: a tab can keep a list
    # longer, and then falls back to the CDN for the removed set.
    # NOTE Keep well above the frontend's persisted query cache (maxAge in
    # main.tsx, 1 day), so a reload from that cache rarely meets a removed set.
    CARD_IMAGE_SET_RETENTION: float = 7 * 24 * 60 * 60  # 7 days

    # Monitoring
    # How often the metrics snapshot is written to Redis
    METRICS_INTERVAL: float = 10  # seconds
    # Longest wait for the Mongo health check of one snapshot. Below
    # METRICS_INTERVAL, so a hung Mongo cannot delay the next snapshot.
    MONGO_HEALTH_TIMEOUT: float = 5  # seconds
    # Time window the throughput counters cover
    METRICS_WINDOW: float = 60  # seconds
    # Length of one history sample. The dashboard plots one point per sample
    # (fewer for long ranges, see HISTORY_MAX_POINTS).
    METRICS_HISTORY_INTERVAL: float = 60  # seconds
    # How far back the history reaches. Samples are ~700 bytes, so 7 days are
    # ~7 MB in redis-key-store (128 MB, noeviction).
    METRICS_HISTORY_RETENTION: float = 7 * 24 * 60 * 60  # 7 days
    # Most points a history response returns. Longer ranges merge neighboring
    # samples, so a 7 day chart stays small enough to render quickly.
    HISTORY_MAX_POINTS: int = 720
    # How long a processed /api-history answer is reused. The API adds one
    # sample per METRICS_HISTORY_INTERVAL, while building a 7 day answer takes
    # seconds of CPU that every dashboard refresh would otherwise repeat.
    API_HISTORY_CACHE_S: float = 30  # seconds
    # How often a throughput and schedule summary is logged
    STATUS_LOG_INTERVAL: float = 60  # seconds
    # Port of the read-only status endpoint inside the container. Compose
    # publishes it on the host's loopback interface only.
    STATUS_PORT: int = 9100

    # MongoDB Configuration
    MONGO_CLIENT_NAME: str = "cr-analytics-data-scraper"

    # Cache TTL (Time To Live) in seconds
    # Twice the refresh interval keeps the cards cached if one refresh fails
    CACHE_TTL_CARDS: int = 2 * CARDS_REFRESH_INTERVAL
    # Twice the refresh interval keeps the game modes cached if one refresh fails
    CACHE_TTL_GAME_MODES: int = int(2 * GAME_MODES_CACHE_REFRESH_INTERVAL)


# Global settings instance
settings = Settings()
