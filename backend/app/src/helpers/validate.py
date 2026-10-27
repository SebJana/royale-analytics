import re
from datetime import date, datetime, timedelta, time, timezone as tz_utc
from zoneinfo import ZoneInfo
from models.schema import BetweenRequest, BattlesRequest, DeckCardFilterRequest
from core.deps import DbConn, RedConn
from redis_service import CARDS_CACHE_KEY, GAME_MODES_CACHE_KEY, get_redis_json
from mongo import get_cards as get_stored_cards
from typing import Optional, List
from core.settings import settings
from helpers.seasons import parse_season_id, season_bounds, season_id_at

# A filtered card is "<cardId>-<evolutionLevel>": 0 regular, 1 evolution,
# 2 hero. Any level parses, so a variant added to the game later needs no
# change here; a level no deck has simply matches nothing. ASCII digits only:
# \d would also accept other scripts' digits.
CARD_FILTER_KEY_PATTERN = re.compile(r"([0-9]{1,12})-([0-9]{1,2})")

# Clash Royale game mode names, e.g. "CW_Battle_1v1" or "7xElixir_Ladder".
# ASCII only, so look-alike characters cannot create a second cache entry for
# the same filter.
GAME_MODE_PATTERN = re.compile(r"[A-Za-z0-9_]+")

# Tower troop id of decks from battles without tower data. Clash Royale ids
# are never 0.
# NOTE Match NO_SUPPORT_ID in the frontend's getCardMetaFields.ts.
NO_SUPPORT_ID = 0


class ParamsRequestError(Exception):
    """Raised when a request contains invalid parameters."""

    def __init__(
        self,
        detail: str | dict,
        code: int = 403,
    ):
        super().__init__(detail)
        self.detail = detail
        self.code = code


def str_to_date(date_str: str) -> date:
    """Convert a date string in YYYY-MM-DD format to a date object.

    Args:
        date_str (str): A date string in the format "YYYY-MM-DD".

    Returns:
        date: A date object representing the parsed date.

    Raises:
        ValueError: If the date string is not in the correct format or represents an invalid date.
    """
    return datetime.strptime(date_str, "%Y-%m-%d").date()


def get_clash_royale_release_date() -> date:
    """Get the official release date of Clash Royale.

    Returns:
        date: The official launch date of Clash Royale (March 2, 2016).
    """
    # Official launch date for Clash Royale
    return str_to_date("2016-03-02")


def valid_timezone(timezone: str):
    """ "
    Checks if a given timezone exists and is valid.

    Args:
        timezone (str): timezone

    Return:
        bool: True if it is valid, false otherwise.

    """

    # Check if timezone exists
    try:
        _ = ZoneInfo(timezone)
        return True
    except Exception:
        return False


def _validate_season(season: str) -> tuple[datetime, datetime]:
    """Resolve a season id to its UTC window.

    Args:
        season (str): Season id as "YYYY-MM".

    Returns:
        tuple[datetime, datetime]: Start (inclusive) and end (exclusive) in UTC.

    Raises:
        ParamsRequestError: For a malformed id, or a season before
            SEASON_FIRST_ID or after the current one.
    """
    try:
        parse_season_id(season)
    except ValueError as e:
        raise ParamsRequestError({"code": "INVALID_SEASON", "message": str(e)})
    if season < settings.SEASON_FIRST_ID:
        raise ParamsRequestError(
            {
                "code": "INVALID_SEASON",
                "message": f"Seasons start at {settings.SEASON_FIRST_ID}",
            }
        )
    # Zero-padded ids compare like the seasons themselves
    if season > season_id_at(datetime.now(tz_utc.utc)):
        raise ParamsRequestError(
            {"code": "INVALID_SEASON", "message": f"Season {season} has not started"}
        )
    return season_bounds(season)


def validate_between_request(request: BetweenRequest) -> tuple[datetime, datetime]:
    """Validate a BetweenRequest and resolve it to a UTC time window.

    The request names either a season or a range of calendar days, never
    both, so the window it asks for is unambiguous. A season is a timed
    window: its reset does not fall on midnight, which calendar days cannot
    express. Calendar days are validated against:
    - Start date is not before Clash Royale's release date
    - End date is not before start date
    - End date is not in the future
    - Date range does not exceed maximum allowed days

    The timezone is validated in both cases. It places the calendar days and
    groups the daily statistics.

    Args:
        request (BetweenRequest): Either season, or start_date and end_date,
            plus the timezone.

    Returns:
        tuple[datetime, datetime]: Start (inclusive) and end (exclusive) as
            timezone-aware UTC datetimes, for a range match on battleTime.

    Raises:
        ParamsRequestError: If any validation constraint is violated, with specific error details.
    """
    start = request.start_date
    end = request.end_date
    release = get_clash_royale_release_date()

    # The request dates are calendar dates in the user's timezone. Validate the
    # timezone before using it to decide which calendar day is "today".
    if not valid_timezone(request.timezone):
        raise ParamsRequestError(f"Timezone {request.timezone} does not exist")
    tz = ZoneInfo(request.timezone)

    if request.season is not None:
        if start is not None or end is not None:
            raise ParamsRequestError(
                {
                    "code": "SEASON_WITH_DATES",
                    "message": "Request either a season or a date range, not both",
                }
            )
        return _validate_season(request.season)

    if start is None or end is None:
        raise ParamsRequestError(
            {
                "code": "MISSING_TIMESPAN",
                "message": "Request a season or both start_date and end_date",
            }
        )

    today = datetime.now(tz).date()

    # Check if start is after release
    if start < release:
        raise ParamsRequestError(
            f"Start date can not be before {release}, the Clash Royale release date"
        )
    # Check if end is after start
    if end < start:
        raise ParamsRequestError("Start date can not be after end date")
    # Check if end is today or before today
    if end > today:
        raise ParamsRequestError("End date can not be after today")

    # Check if the delta between start and date is valid
    date_diff = end - start
    if date_diff.days > settings.MAX_TIME_RANGE_DAYS:
        raise ParamsRequestError(
            f"Request can only span {settings.MAX_TIME_RANGE_DAYS} days"
        )

    # From the first day's local midnight to the midnight after the last day.
    # Converted per instant, so a DST change inside the range shifts nothing.
    start_local = datetime.combine(start, time(0, 0), tzinfo=tz)
    end_local = datetime.combine(end + timedelta(days=1), time(0, 0), tzinfo=tz)
    return start_local.astimezone(tz_utc.utc), end_local.astimezone(tz_utc.utc)


def validate_battles_request(request: BattlesRequest):
    """Validate a BattlesRequest for limit and date constraints.

    Performs validation of a battles request including:
    - Battle limit is within allowed minimum and maximum range
    - If specified, 'before' date is not before Clash Royale's release date
    - If specified, 'before' date is not in the future

    Args:
        request (BattlesRequest): The request object containing limit and optional before date.

    Raises:
        ParamsRequestError: If any validation constraint is violated, with specific error details.
    """
    tomorrow = datetime.today() + timedelta(days=1)
    release = get_clash_royale_release_date()

    # Check if the limit is in the allowed range
    if request.limit < settings.MIN_BATTLES or request.limit > settings.MAX_BATTLES:
        raise ParamsRequestError(f"Given limit {request.limit} is invalid")

    # Only check before time if there was any given
    if not request.before:
        return

    # Convert release date to datetime for comparison
    release_datetime = datetime.combine(release, time(0, 0, 0))

    # Normalize timezone awareness for comparison
    # If request.before is timezone-aware, make other datetimes timezone-aware too
    # If request.before is timezone-naive, ensure all comparisons are timezone-naive
    if request.before.tzinfo is not None:
        # request.before is timezone-aware, convert others to UTC for comparison
        if release_datetime.tzinfo is None:
            release_datetime = release_datetime.replace(tzinfo=ZoneInfo("UTC"))
        if tomorrow.tzinfo is None:
            tomorrow = tomorrow.replace(tzinfo=ZoneInfo("UTC"))
    else:
        # request.before is timezone-naive, make others timezone-naive too
        if release_datetime.tzinfo is not None:
            release_datetime = release_datetime.replace(tzinfo=None)
        if tomorrow.tzinfo is not None:
            tomorrow = tomorrow.replace(tzinfo=None)

    # Check if start is after release
    if request.before < release_datetime:
        raise ParamsRequestError(
            f"Start date can not be before {release}, the Clash Royale release date"
        )

    # Check if end is on or after tomorrow
    if request.before >= tomorrow:
        raise ParamsRequestError("End date can not be after today")


def _checked_game_modes(game_modes: List[str], verb: str) -> List[str]:
    """Deduplicate and sort game mode names and check their count and format.

    Sorted, the same set of modes builds the same cache key in any order.

    Args:
        game_modes (List[str]): Mode names as sent.
        verb (str): "selected" or "excluded", for the error message.

    Returns:
        List[str]: The names without duplicates, sorted.

    Raises:
        ParamsRequestError: For more than GAME_MODE_FILTER_MAX_MODES modes, or
            a mode name that is too long or not a Clash Royale mode name.
    """
    unique_modes = sorted(set(game_modes))

    if len(unique_modes) > settings.GAME_MODE_FILTER_MAX_MODES:
        raise ParamsRequestError(
            f"At most {settings.GAME_MODE_FILTER_MAX_MODES} game modes can be {verb}"
        )
    for mode in unique_modes:
        if len(mode) > settings.GAME_MODE_NAME_MAX_LENGTH or (
            not GAME_MODE_PATTERN.fullmatch(mode)
        ):
            # Not echoed: the value is neither short nor printable for sure
            raise ParamsRequestError("A game mode name is invalid")
    return unique_modes


async def validate_game_modes(
    redis_conn: RedConn,
    game_modes: Optional[List[str]],
    exclude_game_modes: Optional[List[str]] = None,
) -> tuple[List[str], List[str]]:
    """Validate the game mode filter and drop one that covers every mode.

    The filter either lists the modes to keep or the modes to leave out,
    never both. The frontend sends whichever list is shorter, so "all but
    one" is one name instead of every other mode.

    Selected modes missing from the cached list are kept. The cache can lag
    behind the battles by a flush, so a missing mode may already exist in
    Mongo. Dropping it would narrow the result, and dropping every requested
    mode would turn the request into an unfiltered one. An unknown mode that
    really does not exist simply matches no battle. Excluded modes are never
    compared to the cache: leaving out every known mode still keeps the ones
    the cache does not list yet. The values are plain strings in an $in or
    $nin match, so they cannot inject query operators.

    Args:
        redis_conn (RedConn): Active Redis connection instance for accessing cached data.
        game_modes (Optional[List[str]]): Game mode names to keep. Can be
            None or an empty list.
        exclude_game_modes (Optional[List[str]]): Game mode names to leave
            out. Can be None or an empty list.

    Returns:
        tuple[List[str], List[str]]: The modes to keep and the modes to leave
            out, at most one of them non-empty, each deduplicated and sorted.
            Both are empty for no filter, also when the request names exactly
            the cached modes, so every form of "all modes" shares one cache
            key.

    Raises:
        ParamsRequestError: For both lists at once, more than
            GAME_MODE_FILTER_MAX_MODES modes in a list, or a mode name that is
            too long or not a Clash Royale mode name.
    """
    if game_modes and exclude_game_modes:
        raise ParamsRequestError(
            {
                "code": "GAME_MODES_WITH_EXCLUDE",
                "message": "Request either game modes or excluded game modes, "
                "not both",
            }
        )
    if exclude_game_modes:
        return [], _checked_game_modes(exclude_game_modes, "excluded")
    if not game_modes:
        return [], []

    unique_modes = _checked_game_modes(game_modes, "selected")

    all_game_modes = await get_redis_json(redis_conn, GAME_MODES_CACHE_KEY)
    # Without the cached list the request cannot be compared to all modes
    if not all_game_modes:
        return unique_modes, []

    # Mongo applies no game mode filter for an empty list, which saves the $in
    # match. Only an exact match qualifies: a request with an extra, uncached
    # mode is not known to cover every mode.
    if set(unique_modes) == set(all_game_modes.keys()):
        return [], []

    return unique_modes, []


async def _get_card_list(mongo_conn: DbConn, redis_conn: RedConn) -> Optional[dict]:
    """Card list as served by /cards, from the cache or else from Mongo.

    Returns:
        Optional[dict]: {"items": [...], "supportItems": [...]}, or None while
            no list is stored.
    """
    try:
        cached = await get_redis_json(redis_conn, CARDS_CACHE_KEY)
    except Exception as e:
        print(f"[CACHE] [WARNING] reading the cards failed, using Mongo: {e}")
        cached = None
    if cached is not None:
        return cached
    stored = await get_stored_cards(mongo_conn)
    return stored.get("payload") if stored else None


def _canonical_card_keys(keys: Optional[List[str]]) -> set[str]:
    """Parse card filter keys into the form the pipeline builds from the decks.

    The pipeline compares strings, so "26000000-00" or "026000000-0" would
    match no deck and let an excluded card through. Canonical keys also make
    deduplication and the selected/excluded conflict check reliable.

    Args:
        keys (Optional[List[str]]): Keys as sent, "<cardId>-<evolutionLevel>".

    Returns:
        set[str]: The keys as "<int id>-<int level>", without leading zeros.

    Raises:
        ParamsRequestError: For a key that is not in that format.
    """
    canonical = set()
    for key in keys or []:
        parsed = CARD_FILTER_KEY_PATTERN.fullmatch(key)
        if not parsed:
            raise ParamsRequestError(
                f"Card {key!r} is not in the <cardId>-<evolutionLevel> format"
            )
        canonical.add(f"{int(parsed[1])}-{int(parsed[2])}")
    return canonical


async def validate_deck_card_filter(
    mongo_conn: DbConn,
    redis_conn: RedConn,
    card_query: DeckCardFilterRequest,
) -> Optional[dict]:
    """Normalize the card filter of the deck statistics and check it against the card list.

    Only cards of the current card list can be filtered. A card that left the
    game, or an event card the list never had, is rejected, since dropping it
    would silently widen an include filter. While no card list is stored (a
    fresh install before the data scraper's first card refresh), any well
    formed id is accepted; an id no deck has matches nothing.

    Without any selection there is no filter.
    Without selected cards or tower troops, the mode makes no difference and
    is normalized to include, so such requests share one cache entry.

    Args:
        mongo_conn (DbConn): Mongo connection, for the card list on a cache miss.
        redis_conn (RedConn): Redis connection holding the cached card list.
        card_query (DeckCardFilterRequest): The filter as requested:
            card_mode "include" keeps decks with all selected cards and the
            selected tower troop, "match" keeps decks with at least one of
            them, ranked by how many they share. cards and exclude_cards as
            "<cardId>-<evolutionLevel>", support_ids and exclude_support_ids
            as tower troop ids, NO_SUPPORT_ID for decks without tower data.
            No returned deck contains an excluded card or tower troop.

    Returns:
        Optional[dict]: None without any selection, otherwise {"mode", "cards",
            "exclude_cards", "support_ids", "exclude_support_ids"}, each list
            deduplicated and sorted for a stable cache key.

    Raises:
        ParamsRequestError: For a malformed or unknown card, a card or tower
            troop that is both selected and excluded, or a list over its limit.
    """
    card_mode = card_query.card_mode
    card_set = _canonical_card_keys(card_query.cards)
    exclude_card_set = _canonical_card_keys(card_query.exclude_cards)
    support_set = set(card_query.support_ids or [])
    exclude_support_set = set(card_query.exclude_support_ids or [])

    if not (card_set or exclude_card_set or support_set or exclude_support_set):
        return None

    max_cards = (
        settings.DECK_FILTER_MAX_INCLUDE_CARDS
        if card_mode == "include"
        else settings.DECK_FILTER_MAX_CARDS
    )
    if len(card_set) > max_cards:
        raise ParamsRequestError(
            f"At most {max_cards} cards can be selected in {card_mode} mode"
        )
    if len(exclude_card_set) > settings.DECK_FILTER_MAX_CARDS:
        raise ParamsRequestError(
            f"At most {settings.DECK_FILTER_MAX_CARDS} cards can be excluded"
        )
    max_support = 1 if card_mode == "include" else settings.DECK_FILTER_MAX_SUPPORT
    if len(support_set) > max_support:
        raise ParamsRequestError(
            f"Too many tower troops selected for {card_mode} mode (max {max_support})"
        )
    if len(exclude_support_set) > settings.DECK_FILTER_MAX_SUPPORT:
        raise ParamsRequestError(
            f"At most {settings.DECK_FILTER_MAX_SUPPORT} tower troops can be excluded"
        )
    if card_set & exclude_card_set or support_set & exclude_support_set:
        raise ParamsRequestError("A card can not be both selected and excluded")

    card_list = await _get_card_list(mongo_conn, redis_conn)
    # Without a stored list only the syntax is checked. The keys are compared
    # by value in the pipeline, so they cannot inject query operators.
    known_card_ids = {c["id"] for c in card_list["items"]} if card_list else None
    known_support_ids = (
        {s["id"] for s in card_list["supportItems"]} | {NO_SUPPORT_ID}
        if card_list
        else None
    )

    # TODO Decide whether the 12-card ClanWar_BoatBattle defenses belong in
    # the deck statistics at all. Their evolution levels are dropped while
    # cleaning (remove_boat_defense_evolutions in the data scraper's
    # clean.py), so no stored card has a level above 2. The level is not
    # checked against maxEvolutionLevel.
    if known_card_ids is not None:
        for key in card_set | exclude_card_set:
            card_id = int(key.split("-")[0])
            if card_id not in known_card_ids:
                raise ParamsRequestError(f"Card {card_id} is not a current card")

    if known_support_ids is not None:
        unknown = (support_set | exclude_support_set) - known_support_ids
        if unknown:
            raise ParamsRequestError(
                f"Tower troop {min(unknown)} is not a current tower troop"
            )

    return {
        "mode": card_mode if card_set or support_set else "include",
        "cards": sorted(card_set),
        "exclude_cards": sorted(exclude_card_set),
        "support_ids": sorted(support_set),
        "exclude_support_ids": sorted(exclude_support_set),
    }
