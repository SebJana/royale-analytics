from typing import Annotated

from fastapi import APIRouter, Query

from core.settings import settings
from helpers.seasons import recent_seasons
from models.schema import SeasonResponse

router = APIRouter(prefix="/seasons", tags=["Seasons"])


@router.get(
    "",
    response_model=list[SeasonResponse],
    responses={422: {"description": "Limit out of range"}},
)
async def list_seasons(
    limit: Annotated[
        int,
        Query(
            ge=1,
            le=settings.SEASON_CATALOGUE_MAX_LIMIT,
        ),
    ] = settings.SEASON_CATALOGUE_LIMIT,
):
    """List the newest seasons, newest first.

    Args:
        limit (int): Maximum number of seasons, SEASON_CATALOGUE_LIMIT by
            default. Seasons before SEASON_FIRST_ID are never listed.

    Returns:
        list[SeasonResponse]: id, UTC start (inclusive) and end (exclusive)
            and whether it is the current season, per season.
    """
    # Pure computation from the season rule, so nothing is cached. Serving the
    # boundaries keeps the reset hour out of the frontend, which only shows
    # them and sends the id back.
    return recent_seasons(limit)
