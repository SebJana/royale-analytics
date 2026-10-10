"""Server-side validation helpers for Fruit Buzz latency calibration."""

from statistics import median

from core.settings import settings


class CalibrationError(ValueError):
    """Raised when server-measured calibration samples are unusable."""


def calculate_calibration_rtt_ms(rtts_ms: list[float]) -> int:
    """Validate measured RTTs and return their integer median.

    The client never supplies these values: callers must derive them solely
    from the server's monotonic send and receive timestamps.

    Args:
        rtts_ms (list[float]): Round-trip times measured by the server.

    Returns:
        int: Median RTT in milliseconds, capped at the configured maximum.
    """

    # A few missing pongs are acceptable, but too few samples make the median
    # unreliable for the later buzz deadline.
    if len(rtts_ms) < settings.FRUIT_BUZZ_CALIBRATION_MIN_SAMPLES:
        raise CalibrationError("not enough valid probe replies")

    if any(rtt < 0 for rtt in rtts_ms):
        raise CalibrationError("invalid negative RTT")

    if max(rtts_ms) - min(rtts_ms) > settings.FRUIT_BUZZ_CALIBRATION_MAX_JITTER_MS:
        raise CalibrationError("connection latency is too unstable")

    # Keep one representative value instead of letting a single slow probe
    # give the player a large timing allowance for the whole game.
    median_rtt_ms = int(round(median(rtts_ms)))
    return min(median_rtt_ms, settings.FRUIT_BUZZ_CALIBRATION_MAX_RTT_MS)
