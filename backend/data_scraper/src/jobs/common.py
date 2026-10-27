"""Result type and error handling shared by the per-player jobs."""

from dataclasses import dataclass

import httpx

from api_key_store import NoKeyAvailable, KeyStoreUnavailable
from clash_royale_api import ClashRoyaleMaintenanceError
from intervals import failure_backoff, jitter
from settings import settings


@dataclass(frozen=True)
class JobResult:
    """Outcome of one per-player job and the delay until the player is due again.

    outcome is one of "synced", "inactive", "deactivated", "not_found",
    "failed", "busy" or "maintenance". "inactive" and "deactivated" players
    are removed from the schedules instead of being rescheduled.
    possible_gap marks a sync whose battle log no longer reached back to the
    previous sync, so battles in between may be lost.
    """

    outcome: str
    delay_s: float
    inserted: int = 0
    possible_gap: bool = False


def pool_level_result(error: Exception) -> JobResult | None:
    """Map errors that concern the key pool or the API, not the player.

    These never count as a player failure: the same request would have failed
    for any player.

    Returns:
        JobResult | None: The result for a pool level error, or None if the
        error belongs to the player and the job decides how to handle it.
    """

    if isinstance(error, ClashRoyaleMaintenanceError):
        return JobResult("maintenance", settings.MAINTENANCE_RETRY_DELAY)

    if isinstance(error, NoKeyAvailable):
        if error.reason == "maintenance":
            return JobResult("maintenance", settings.MAINTENANCE_RETRY_DELAY)
        # Every key is leased or cooling down. Every worker hits this at the
        # same time, so the retries are jittered to not arrive together again.
        return JobResult(
            "busy", jitter(error.retry_after, settings.FAILURE_BACKOFF_JITTER)
        )

    if isinstance(error, KeyStoreUnavailable):
        return JobResult("busy", failure_backoff(1))

    if isinstance(error, httpx.HTTPStatusError):
        code = error.response.status_code if error.response is not None else 0
        if code in (401, 403, 429):
            # The API client already moved the request across keys. Still
            # rejected or rate limited means a key/pool problem.
            return JobResult("busy", failure_backoff(1))

    return None
