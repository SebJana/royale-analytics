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

    # Players of the last finished season's ranked leaderboard that are
    # tracked and protected for one season. Their slots are reserved by count:
    # the other active players share max_players - TOP_PLAYERS_N, so a
    # complete new list always fits, however many old top players were kept.
    # NOTE There is no automatic cap. Size it with the planned keys and the
    # storage: close to max_players the budget of the other players falls
    # below PARK_MIN_BUDGET_SHARE and parking stops (degraded capacity), and
    # every top player adds battles and profile snapshots to Mongo. A changed
    # value is applied by the scraper's next rollover pass, which fetches the
    # leaderboard again.
    TOP_PLAYERS_N: int = 1000

    # Usage (active players without topFinish / (max_players - TOP_PLAYERS_N))
    # above which the scraper starts parking the least viewed players.
    PARK_HIGH_WATERMARK: float = 0.85
    # Usage the scraper parks down to. The gap to the high watermark keeps the
    # hourly check from parking a few players every single hour.
    PARK_LOW_WATERMARK: float = 0.80
    # Players added, reactivated or viewed within this window are parked
    # only after all others, the least recently active of them first. Only an
    # order, never an exemption: the capacity limit has to hold even when
    # every player is recent, and then recent players do park each other.
    PARK_SPARE_DAYS: int = 3  # 3 days
    # Smallest share of max_players the budget of the players without
    # topFinish may fall to before capacity counts as degraded. Below it
    # nobody is parked and adds of new players are refused: max_players only
    # drops that far when keys turn invalid or a far too large TOP_PLAYERS_N
    # is deployed, and parking a large part of the players for either would
    # take days of views to undo. Without it, a budget of a few players would
    # park nearly every regular player at once.
    PARK_MIN_BUDGET_SHARE: float = 0.2


# Global settings instance, exported as schedule_settings so it does not
# shadow the settings of the service importing it.
settings = Settings()
