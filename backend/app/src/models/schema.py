from fastapi import Query
from pydantic import BaseModel, Field
from typing import Literal, List, Optional
from datetime import date, datetime
from core.settings import settings


# --- Request model ---
class BetweenRequest(BaseModel):
    start_date: date = Field(..., description="Start date (inclusive, YYYY-MM-DD)")
    end_date: date = Field(..., description="End date (inclusive, YYYY-MM-DD)")
    timezone: str = Field(
        ..., description=("Timezone of the given start and end dates")
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
    captcha_id: str = Field(..., description="Id of the captcha session")
    answer: str = Field(..., description="Visible text from the captcha image")


class SecurityQuestionsRequest(BaseModel):
    most_annoying_card: str = Field(
        ...,
        description="Answer to what is the single most annoying card in Clash Royale?",
    )
    most_skillful_card: str = Field(
        ...,
        description="Answer to what is the single most skillful card in Clash Royale?",
    )
    most_mousey_card: str = Field(
        ...,
        description="Answer to what is the most 'mausig (english: sweetie/cutie)' card in Clash Royale?",
    )


class WordleAnswerRequest(BaseModel):
    wordle_id: str = Field(..., description="Id of the Wordle session")
    wordle_guess: str = Field(..., description="Answer to the Wordle challenge")


class NYTWordleAnswerRequest(BaseModel):
    captcha_token: str = Field(
        ..., description="Token received by correctly solving the captcha"
    )
    wordle_guess: str = Field(..., description="Answer to todays Wordle challenge")
    timezone: str = Field(..., description="Timezone of the user")
