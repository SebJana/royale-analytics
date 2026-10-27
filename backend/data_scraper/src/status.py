"""Print the data scraper's current state for administrators.

Run on the deployed machine:

    docker compose exec data_scraper python src/status.py
    docker compose exec data_scraper python src/status.py --json

Reads the snapshot the scraper writes to redis-key-store every few seconds, so
it works without touching the running scraper process.
"""

import json
import sys
import time
from datetime import datetime

from redis import Redis

from scrape_schedule import METRICS_KEY
from settings import settings


def _duration(seconds) -> str:
    if seconds is None:
        return "-"
    # Job durations are mostly below a second, which whole seconds would hide.
    if seconds < 10:
        return f"{seconds:.1f}s"
    seconds = int(seconds)
    if seconds < 120:
        return f"{seconds}s"
    if seconds < 2 * 3600:
        return f"{seconds // 60}m {seconds % 60:02d}s"
    return f"{seconds // 3600}h {seconds % 3600 // 60:02d}m"


def _megabytes(value) -> str:
    return f"{value / 1024 / 1024:.1f} MB" if value else "no limit"


def print_status(snapshot: dict):
    now = time.time()
    age = now - snapshot["updatedAt"]
    print(f"Data scraper status (snapshot {age:.0f}s old)")
    if age > 3 * settings.METRICS_INTERVAL:
        print("  WARNING: snapshot is outdated, the scraper may be stopped")
    started = datetime.fromtimestamp(snapshot["startedAt"])
    print(f"  Running since {started:%Y-%m-%d %H:%M:%S}, {snapshot['workers']} workers")
    if snapshot["maintenance"]:
        print("  Clash Royale API in MAINTENANCE: claiming is paused")

    keys = snapshot["keys"]
    print(
        f"\nKeys: {keys['usable']} usable, {keys['invalid']} invalid, "
        f"{keys['configured']} configured"
    )

    capacity = snapshot.get("capacity")
    if capacity:
        max_players = capacity["maxPlayers"]
        print(
            f"Capacity: {capacity['activePlayers']} tracked / "
            f"{max_players if max_players is not None else '?'} max players, "
            f"base interval {_duration(capacity['baseIntervalS'])}, "
            f"{capacity['requestRate']} req/s planned "
            f"({capacity['battleRate']} for battles, "
            f"{capacity.get('battleDemand', '?')} needed)"
        )

    print(f"\nLast {snapshot['windowS']:.0f}s:")
    for kind, stats in snapshot["jobs"].items():
        outcomes = ", ".join(
            f"{name} {count}" for name, count in sorted(stats["outcomes"].items())
        )
        line = f"  {kind:<9} {stats['jobs']:>5} jobs"
        if kind == "battles":
            line += f", {stats['inserted']} battles inserted"
        line += (
            f", claim lateness p95 {_duration(stats.get('p95ClaimLatenessS'))}"
            f" / max {_duration(stats['maxClaimLatenessS'])}"
            f", duration p95 {_duration(stats.get('p95DurationS'))}"
        )
        print(line + (f"  ({outcomes})" if outcomes else ""))
    print(f"  Possible battle gaps since start: {snapshot.get('possibleGaps', '?')}")

    print("\nSchedules:")
    for kind, stats in snapshot["schedules"].items():
        print(
            f"  {kind:<9} {stats['scheduled']:>5} scheduled, {stats['due']} due now, "
            f"oldest due waiting {_duration(stats['oldest_due_lateness_s'])}"
        )

    redis = snapshot["redis"]
    print(
        f"\nredis-key-store memory: {_megabytes(redis['usedMemory'])} "
        f"of {_megabytes(redis['maxMemory'])}"
    )
    if snapshot["lastReconcileAt"]:
        print(f"Last reconciliation {_duration(now - snapshot['lastReconcileAt'])} ago")


def main():
    client = Redis(
        host=settings.KEY_STORE_REDIS_HOST,
        port=settings.REDIS_PORT,
        password=settings.REDIS_PASSWORD,
        decode_responses=True,
    )
    raw = client.get(METRICS_KEY)
    if raw is None:
        print("No status snapshot yet. Is the data scraper running?")
        sys.exit(1)
    snapshot = json.loads(raw)
    if "--json" in sys.argv[1:]:
        print(json.dumps(snapshot, indent=2))
    else:
        print_status(snapshot)


if __name__ == "__main__":
    main()
