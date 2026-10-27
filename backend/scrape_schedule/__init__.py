"""Per-player scraping schedule, shared by the data scraper and the API.

This is a separate package instead of part of the data scraper because the API
also writes to the schedule: a newly tracked player is added as due immediately,
so its first sync starts within seconds, and an untracked player is removed
right away. The API image does not contain the scraper source, and a second
copy of the Redis key names and Lua scripts would drift apart.
"""

from .schedule import Schedule, Claim
from .capacity import (
    Capacity,
    compute_capacity,
    publish_capacity,
    read_capacity,
)
from .parking import budget_degraded, parking_budget, players_to_park, spare_since
from .settings import settings as schedule_settings

# Names shared by the scraper (claims and acks) and the API (add and remove).
BATTLES_SCHEDULE = "battles"
PROFILES_SCHEDULE = "profiles"

# Snapshot of the scraper's monitoring metrics (JSON), read by the status CLI.
METRICS_KEY = "crsched:metrics"
# Per-minute metric samples (a capped list of JSON strings, oldest first),
# read by the scraper's status dashboard.
SCRAPER_METRICS_HISTORY_KEY = "crsched:scraper:metrics:history"

# Per-minute API route timings, the same kind of capped list: written by the
# API, read by the scraper's status dashboard, which is the only monitoring
# page and the only one with a loopback-only port.
API_METRICS_HISTORY_KEY = "crsched:api:metrics:history"
# Upper bounds (seconds) of the API's response time histogram buckets; the
# last bucket counts everything slower. NOTE Writer and reader match buckets
# by position, so changing these misreads older samples: delete
# API_METRICS_HISTORY_KEY when changing them. The footer of the scraper's
# dashboard/api.html names the top bound.
API_DURATION_BOUNDS_S = (
    0.001,
    0.0025,
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1,
    2.5,
    5,
    10,
)

__all__ = [
    "Schedule",
    "Claim",
    "Capacity",
    "compute_capacity",
    "publish_capacity",
    "read_capacity",
    "budget_degraded",
    "parking_budget",
    "players_to_park",
    "spare_since",
    "BATTLES_SCHEDULE",
    "PROFILES_SCHEDULE",
    "schedule_settings",
    "METRICS_KEY",
    "SCRAPER_METRICS_HISTORY_KEY",
    "API_METRICS_HISTORY_KEY",
    "API_DURATION_BOUNDS_S",
]
