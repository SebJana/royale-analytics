class Settings:
    """Settings shared by the data scraper and the API through this package.

    Both sides read the same values, so the scraper's hourly parking and the
    API's on-demand parking agree on when and whom to park, and the API only
    trusts a capacity estimate as old as the scraper's reconciliation allows.
    """

    # A published capacity older than this is ignored by the API and the
    # parking check. Three missed reconciliations mean the scraper is down,
    # and its last estimate may no longer match its keys.
    CAPACITY_MAX_AGE_S: float = 15 * 60  # 15 minutes

    # The base battle interval is searched to this precision. A second is far
    # below the 5 minute reconciliation period that recomputes it anyway.
    BASE_INTERVAL_PRECISION_S: float = 1.0  # 1 second

    # Usage (active players / max_players) above which the scraper starts
    # parking the least viewed players.
    PARK_HIGH_WATERMARK: float = 0.85
    # Usage the scraper parks down to. The gap to the high watermark keeps the
    # hourly check from parking a few players every single hour.
    PARK_LOW_WATERMARK: float = 0.80
    # Players added, reactivated or viewed within this window are parked
    # only after all others, the least recently active of them first. Only an
    # order, never an exemption: the capacity limit has to hold even when
    # every player is recent, and then recent players do park each other.
    PARK_SPARE_DAYS: int = 3  # 3 days


# Global settings instance, exported as schedule_settings so it does not
# shadow the settings of the service importing it.
settings = Settings()
