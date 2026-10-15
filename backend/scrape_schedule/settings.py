class Settings:
    """Settings shared by the data scraper and the API through this package.

    Both sides read the same values, so the API only trusts a capacity
    estimate as old as the scraper's reconciliation allows.
    """

    # A published capacity older than this is ignored by the API. Three
    # missed reconciliations mean the scraper is down, and its last estimate
    # may no longer match its keys.
    CAPACITY_MAX_AGE_S: float = 15 * 60  # 15 minutes

    # The base battle interval is searched to this precision. A second is far
    # below the 5 minute reconciliation period that recomputes it anyway.
    BASE_INTERVAL_PRECISION_S: float = 1.0  # 1 second


# Global settings instance, exported as schedule_settings so it does not
# shadow the settings of the service importing it.
settings = Settings()
