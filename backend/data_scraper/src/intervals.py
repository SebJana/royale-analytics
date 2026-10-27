"""Per-player battle sync intervals.

The base interval comes from the capacity estimate (see scrape_schedule's
capacity module): the shortest interval the usable keys can sustain for the
current mix of heavy, active, and idle players. Each player's next interval is
then adjusted by what its last sync found, so requests go where battles can
actually be lost.

battle_demand prices that mix for any candidate base with the same rules
next_battle_interval applies, so the estimate and the actual intervals cannot
drift apart.

Spreading due times
-------------------
A player's next due time is the end of its sync plus its interval. Players
that were synced back to back therefore come due back to back again, every
cycle: after a bulk insert, scraper downtime, or an API outage, all affected
players form one block that the keys work through at full rate (busy jobs,
long key waits), followed by idle time. Equal intervals never dissolve that
block, and the adaptive intervals only split it slowly.

The scheduled delay is therefore randomized, while the stored interval stays
the planned one, so the capacity estimate, the idle growth, and a schedule
rebuilt from Mongo keep working from exact values:

- A sync that waited in a backlog, or a first sync, is spread over 0.5x to
  1.5x its interval. One step turns the block into an even spread.
- Every other sync gets a small jitter, so players cannot fall back into step.
- Failure retries get a jitter, so a shared failure does not retry in waves.

Every spread is centred on the planned delay, so the average request rate
stays what the capacity estimate planned for. Shortening only (e.g. 0.5x to
1x) would add requests right after a backlog and could cause the next one.
"""

import random
from collections.abc import Iterable

from settings import settings


def load_pressure(base_interval_s: float) -> float:
    """How close the pool is to full load, from the base interval.

    0 up to LOAD_FADE_START, rising linearly to 1 at MAX_SYNC_INTERVAL, where
    the keys can only just sync every player once per longest interval.

    Args:
        base_interval_s (float): Current capacity based interval.

    Returns:
        float: Pressure between 0 and 1.
    """

    start = settings.LOAD_FADE_START
    end = settings.MAX_SYNC_INTERVAL
    if base_interval_s <= start:
        return 0.0
    if base_interval_s >= end:
        return 1.0
    return (base_interval_s - start) / (end - start)


def high_activity_factor(base_interval_s: float) -> float:
    """Share of the base interval a very active player gets.

    The shortened interval is paid for by the other players. Once the base has
    to stretch towards MAX_SYNC_INTERVAL there is nothing left to pay with, so
    the factor rises with the load pressure from HIGH_ACTIVITY_INTERVAL_FACTOR
    to 1. At full load every player, however active, then syncs at the longest
    interval.

    Args:
        base_interval_s (float): Current capacity based interval.

    Returns:
        float: Factor between HIGH_ACTIVITY_INTERVAL_FACTOR and 1.
    """

    factor = settings.HIGH_ACTIVITY_INTERVAL_FACTOR
    return factor + (1.0 - factor) * load_pressure(base_interval_s)


def min_profile_interval(base_interval_s: float) -> float:
    """Shortest profile interval at the current load.

    Rises with the load pressure from PROFILE_MIN_INTERVAL to
    PROFILE_PRESSURE_INTERVAL, so profiles hand their requests to battle
    syncs, which lose battles when late.

    Args:
        base_interval_s (float): Current capacity based interval.

    Returns:
        float: Seconds.
    """

    low = settings.PROFILE_MIN_INTERVAL
    high = settings.PROFILE_PRESSURE_INTERVAL
    return low + (high - low) * load_pressure(base_interval_s)


def _clamp_battle_interval(interval_s: float) -> float:
    return min(max(interval_s, settings.MIN_SYNC_INTERVAL), settings.MAX_SYNC_INTERVAL)


def next_battle_interval(
    base_interval_s: float, previous_interval_s: float | None, new_battles: int
) -> float:
    """Seconds until a player's next battle sync, based on its recent activity.

    Args:
        base_interval_s (float): Current capacity based interval.
        previous_interval_s (float | None): The player's last interval, None
            if it has none yet.
        new_battles (int): Battles the sync just inserted.

    Returns:
        float: The next interval, always within [MIN_SYNC_INTERVAL, MAX_SYNC_INTERVAL].
    """

    if new_battles >= settings.HIGH_ACTIVITY_BATTLES:
        # Close to the battle log window: sync sooner, so a long session
        # does not overflow it before the next sync.
        interval = base_interval_s * high_activity_factor(base_interval_s)
    elif new_battles > 0:
        interval = base_interval_s
    else:
        # Idle since the last sync: stretch the previous interval step by
        # step. A single new battle resets the player to the base interval.
        previous = previous_interval_s or base_interval_s
        interval = max(base_interval_s, previous * settings.IDLE_INTERVAL_GROWTH)

    return _clamp_battle_interval(interval)


def planned_battle_interval(
    base_interval_s: float,
    sync_interval_s: float | None,
    last_new_battles: int | None,
) -> float:
    """Interval a player is planned at if the base were base_interval_s.

    Unlike next_battle_interval, an idle player is priced at its current
    interval, not the stretched next one: its next sync has not happened yet.

    Args:
        base_interval_s (float): Candidate base interval.
        sync_interval_s (float | None): The player's stored interval.
        last_new_battles (int | None): Battles its last successful sync
            inserted. None (never synced, or synced before the count was
            stored) counts as an active player.

    Returns:
        float: The interval, within [MIN_SYNC_INTERVAL, MAX_SYNC_INTERVAL].
    """

    if last_new_battles is None or last_new_battles > 0:
        return next_battle_interval(base_interval_s, None, last_new_battles or 1)
    current = sync_interval_s or base_interval_s
    return _clamp_battle_interval(max(base_interval_s, current))


def battle_demand(players: Iterable[dict], base_interval_s: float) -> float:
    """Battle sync requests per second the players need at a candidate base.

    Args:
        players (Iterable[dict]): Tracked players with syncIntervalS and
            lastSyncNewBattles (see get_tracked_players_sync_times).
        base_interval_s (float): Candidate base interval.

    Returns:
        float: Requests per second. Never increases with a longer base.
    """

    return sum(
        1
        / planned_battle_interval(
            base_interval_s, player["syncIntervalS"], player["lastSyncNewBattles"]
        )
        for player in players
    )


def profile_demand(players: Iterable[dict], base_interval_s: float) -> float:
    """Profile requests per second the players need at a candidate base.

    Args:
        players (Iterable[dict]): Tracked players with profileSyncIntervalS
            (see get_tracked_players_sync_times).
        base_interval_s (float): Candidate base interval.

    Returns:
        float: Requests per second. Never increases with a longer base. A
            player is priced at its stored interval, but at least at the
            shortest interval the candidate base allows.
    """

    shortest = min_profile_interval(base_interval_s)
    return sum(
        1 / max(player["profileSyncIntervalS"] or shortest, shortest)
        for player in players
    )


def next_profile_interval(
    base_interval_s: float,
    previous_interval_s: float | None,
    played_since_last_refresh: bool,
) -> float:
    """Seconds until a player's next profile refresh.

    Args:
        base_interval_s (float): Current capacity based interval.
        previous_interval_s (float | None): The player's last profile interval,
            None if it has none yet.
        played_since_last_refresh (bool): The player has battles newer than the
            previous profile refresh, so the profile stats changed.

    Returns:
        float: The next interval, within [min_profile_interval(base),
            max(PROFILE_MAX_INTERVAL, min_profile_interval(base))].
    """

    shortest = min_profile_interval(base_interval_s)
    if played_since_last_refresh or previous_interval_s is None:
        return shortest
    # Unchanged since the last refresh: check less often, the stats can only
    # change once the player plays again.
    return min(
        max(previous_interval_s * settings.PROFILE_IDLE_GROWTH, shortest),
        max(settings.PROFILE_MAX_INTERVAL, shortest),
    )


def jitter(delay_s: float, spread: float) -> float:
    """Scale a delay by a random factor in [1 - spread, 1 + spread].

    The factor is uniform, so the average delay stays delay_s.

    Args:
        delay_s (float): Planned delay in seconds.
        spread (float): Largest relative deviation, e.g. 0.1 for +-10%.

    Returns:
        float: The randomized delay in seconds.
    """

    return delay_s * random.uniform(1.0 - spread, 1.0 + spread)


def scheduled_battle_delay(
    interval_s: float, lateness_s: float, first_sync: bool
) -> float:
    """Seconds until a player is due again, spread around its interval.

    See "Spreading due times" in the module docstring for why.

    Args:
        interval_s (float): The planned interval from next_battle_interval.
        lateness_s (float): How long the player waited after it became due
            (the claim lateness). 0 for a first sync, see first_sync.
        first_sync (bool): The player has no interval yet. It was added due
            immediately, so its lateness says nothing, but it was synced in
            whatever batch it was added with.

    Returns:
        float: The delay, at most MAX_SYNC_INTERVAL. The cap keeps the battle
        log guarantee of the longest interval.
    """

    backlog = first_sync or lateness_s > settings.BACKLOG_LATENESS_SHARE * interval_s
    spread = settings.BACKLOG_SPREAD if backlog else settings.INTERVAL_JITTER
    return min(jitter(interval_s, spread), settings.MAX_SYNC_INTERVAL)


def failure_backoff(consecutive_failures: int) -> float:
    """Delay before retrying a player after its n-th consecutive failure.

    Jittered, because failures usually hit many players at once (API errors,
    timeouts). Equal delays would retry them all in the same moment again.
    """

    exponent = max(0, consecutive_failures - 1)
    delay = settings.FAILURE_BACKOFF_BASE * 2 ** min(exponent, 16)
    return min(
        jitter(delay, settings.FAILURE_BACKOFF_JITTER), settings.FAILURE_BACKOFF_MAX
    )


def not_found_delay(base_interval_s: float, consecutive_not_found: int) -> float:
    """Delay before checking a player again after its n-th consecutive 404.

    Starts at the normal interval, so a single false 404 delays the player by
    one sync at most, and doubles up to NOT_FOUND_RETRY_MAX for accounts that
    are really gone.
    """

    exponent = max(0, consecutive_not_found - 1)
    return min(base_interval_s * 2 ** min(exponent, 16), settings.NOT_FOUND_RETRY_MAX)
