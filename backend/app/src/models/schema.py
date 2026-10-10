from fastapi import Query
from pydantic import BaseModel, Field
from typing import Literal, List, Optional
from datetime import date, datetime
from core.settings import settings


# --- Request model ---
class BetweenRequest(BaseModel):
    # Either a season or both dates, see validate_between_request
    start_date: Optional[date] = Field(
        None, description="Start date (inclusive, YYYY-MM-DD)"
    )
    end_date: Optional[date] = Field(
        None, description="End date (inclusive, YYYY-MM-DD)"
    )
    season: Optional[str] = Field(
        None,
        description="Season id (YYYY-MM) instead of start_date and end_date",
    )
    timezone: str = Field(
        ...,
        max_length=settings.TIMEZONE_MAX_LENGTH,
        description=(
            "Timezone of the given start and end dates, and of the days the "
            "daily statistics are grouped into"
        ),
    )


class DeckCardFilterRequest:
    """Card and tower troop filter of the deck statistics, as query parameters.

    A plain class instead of a BaseModel: FastAPI before 0.115 reads the list
    fields of a model dependency from the body, not from the query string.
    """

    def __init__(
        self,
        card_mode: Literal["include", "match"] = "include",
        # Cards as "<cardId>-<evolutionLevel>", e.g. "26000000-1" for the
        # evolution
        cards: Optional[List[str]] = Query(None),
        exclude_cards: Optional[List[str]] = Query(None),
        # Tower troops by id, 0 for decks without tower data
        support_ids: Optional[List[int]] = Query(None),
        exclude_support_ids: Optional[List[int]] = Query(None),
    ):
        self.card_mode = card_mode
        self.cards = cards
        self.exclude_cards = exclude_cards
        self.support_ids = support_ids
        self.exclude_support_ids = exclude_support_ids


# --- Response model ---
class SeasonResponse(BaseModel):
    id: str = Field(..., description="Season id, YYYY-MM")
    start: datetime = Field(..., description="Start of the season (inclusive), UTC")
    end: datetime = Field(..., description="End of the season (exclusive), UTC")
    is_current: bool = Field(
        ..., alias="isCurrent", description="Whether the season is running"
    )


class BattlesRequest(BaseModel):
    before: Optional[datetime] = Field(
        None,
        description="Optional before datetime; if set, returns battles strictly before that instant",
    )
    limit: int = Field(
        ...,
        description=f"Max battles to fetch ({settings.MIN_BATTLES}–{settings.MAX_BATTLES})",
    )


class CaptchaAnswerRequest(BaseModel):
    # Ids are UUIDs (36 characters)
    captcha_id: str = Field(..., max_length=36, description="Id of the captcha session")
    answer: str = Field(
        ...,
        max_length=settings.AUTH_ANSWER_MAX_LENGTH,
        description="Visible text from the captcha image",
    )


class FruitBuzzStartRequest(BaseModel):
    # Ids are UUIDs (36 characters)
    calibration_id: str = Field(
        ..., max_length=36, description="Id of the finished latency calibration"
    )


class SecurityQuestionsRequest(BaseModel):
    most_annoying_card: str = Field(
        ...,
        max_length=settings.AUTH_ANSWER_MAX_LENGTH,
        description="Answer to what is the single most annoying card in Clash Royale?",
    )
    most_skillful_card: str = Field(
        ...,
        max_length=settings.AUTH_ANSWER_MAX_LENGTH,
        description="Answer to what is the single most skillful card in Clash Royale?",
    )
    most_mousey_card: str = Field(
        ...,
        max_length=settings.AUTH_ANSWER_MAX_LENGTH,
        description="Answer to what is the most 'mausig (english: sweetie/cutie)' card in Clash Royale?",
    )


class WordGuessAnswerRequest(BaseModel):
    word_guess_id: str = Field(
        ..., max_length=36, description="Id of the Word Guess session"
    )
    guess: str = Field(
        ...,
        max_length=settings.AUTH_ANSWER_MAX_LENGTH,
        description="Answer to the Word Guess challenge",
    )
