import math
import random
import time
import uuid
from typing import Literal

from pydantic import BaseModel, Field
from helpers.encrypt_image import encrypt_image
from helpers.media_pool.consumer import get_card_template, pick_pool_card
from helpers.fruit_buzz_rendering.models import FruitImagePosition
from helpers.fruit_buzz_card import (
    AVAILABLE_FRUITS,
    FRUIT_POSITIONS,
    pick_random_card,
)

from core.settings import settings
from redis_service import RedisConn

# TODO add min reaction time, if that is beat that was either guessing or a CV pipeline, not a human
# TODO add logging how many tries are needed, win rate, reaction times, etc. to get a feel for
# how hard this is (also log network delay)
# Are there good monitoring frameworks that can do exactly that, on a per route basis?

# The calibration stores RTT in milliseconds, while round deadlines use seconds.
MILLISECONDS_PER_SECOND = 1000
# Use half of the measured round-trip time to estimate the delay of a buzz
# travelling from the client to the server.
ROUND_TRIP_TO_ONE_WAY_DIVISOR = 2
# Convert the configured jitter percentage into a fraction of the round window.
PERCENT_DENOMINATOR = 100
# Ignore tiny floating-point differences when comparing two fruit edges.
FRUIT_EDGE_COMPARISON_EPSILON = 1e-12
# Choose one target direction when the game starts and keep it for that game.
# The direction is only enforced when target-fruit checking is enabled.
# Otherwise, the click only needs the winning card chosen for this game.
TARGET_FRUIT_EDGES = ("left", "right", "top", "bottom")
WINNING_CARD_AGES = ("oldest", "newest")

"""Server-side state and rules for the Fruit Buzz challenge.

Each card has one fruit type and an amount. The current card and a limited
number of earlier visible cards count towards a Fruit Buzz when one fruit's
total equals the configured winning amount exactly. A buzz or a missed Fruit
Buzz clears the pile, so earlier cards no longer count in later rounds.

The game is initialized with a server-selected network RTT and its own rule set,
including a randomly chosen winning card age and target fruit edge.
The rules are saved with the game, and only the display rules are sent to the client,
so later setting changes cannot alter a game already in progress. Cards are picked ahead
for image preloading: startup prepares the current card and the configured
number of future cards, then each completed round adds one at the far end.
Their raw PNGs come from a Redis pool of fruit/count variations. The game
stores a private template ID and a public image ID for each round, then creates
a fresh encrypted response and saves its key when the client preloads the card.
On the first reveal, the server returns the saved key and
stores one deadline: the configured reaction window with percentage jitter,
plus an estimate of the buzz's one-way network delay. Reveal retries keep the
same deadline. Every round gets this deadline, even if no fruit wins, so an
early request for the next card does not reveal whether the player should buzz.

Evaluation accepts either a buzz or a request to move on. A buzz must arrive
before the deadline, name a winning card in the current round, and satisfy the
configured winning-card age and target-fruit rules. A wrong or late buzz costs the
player a life; a correct one costs the bot a life. Moving on after the deadline
costs the player a life if a Fruit Buzz was missed. The helper advances the
round after scoring, while the gameplay route must save the changed game
atomically so two requests cannot score the same round.

Deadlines use the server's monotonic clock. A persisted game therefore needs
to stay on the same server clock and cannot survive a machine restart as-is.
"""


class FruitBuzzRound(BaseModel):
    """Persisted card metadata, completed as the image is prepared and revealed."""

    fruit: str
    amount: int = Field(ge=1, le=len(FRUIT_POSITIONS))
    fruit_positions: list[FruitImagePosition] = Field(default_factory=list)
    # Marks a cleared pile, including a wrong buzz, so older cards stop counting.
    fruit_buzz: bool = False
    # Both timestamps use the server's monotonic clock. The deadline is picked
    # once when the card is first revealed and survives reveal retries.
    revelation_timestamp: float | None = None
    buzz_deadline_timestamp: float | None = None
    # The template ID locates the shared raw PNG; the image ID is the stable ID
    # the frontend uses when clicking this card.
    template_id: str | None = None
    image_id: str | None = None
    # Each preload fetch replaces the key until reveal. The version lets the
    # frontend discard older responses if duplicate fetches finish out of order.
    image_version: int = Field(default=0, ge=0)
    encryption_key: str | None = None


class FruitBuzzRules(BaseModel):
    """Full rules chosen at game creation and kept in server-side state.

    The game keeps its own copy, so later changes to server settings do not
    silently change a challenge that has already started.
    """

    visible_card_count: int = Field(ge=1)
    # One new card enters the game each round. This counts future cards only;
    # the currently displayed card is prepared in addition to this number.
    max_preloaded_cards: int = Field(ge=1)
    winning_fruit_count: int = Field(ge=1)
    winning_card_age: Literal["oldest", "newest"]
    require_target_fruit: bool
    target_fruit_edge: Literal["left", "right", "top", "bottom"]
    target_fruit_buffer: float = Field(ge=0, le=1)
    target_fruit_hitbox_padding: float = Field(default=0, ge=0, le=1)
    round_window_ms: int = Field(gt=0)
    round_jitter_percent: float = Field(ge=0, le=100)


class FruitBuzzPublicRules(BaseModel):
    """Rules the frontend needs to display cards, preload, and explain clicks."""

    visible_card_count: int
    max_preloaded_cards: int
    winning_fruit_count: int
    winning_card_age: Literal["oldest", "newest"]
    require_target_fruit: bool
    target_fruit_edge: Literal["left", "right", "top", "bottom"]


def default_game_rules() -> FruitBuzzRules:
    """Copy settings and choose a winning card age and target edge for this game.

    Returns:
        FruitBuzzRules: Rule values to keep for the lifetime of one game.
    """
    return FruitBuzzRules(
        visible_card_count=settings.FRUIT_BUZZ_GAME_ROUND_CARDS,
        max_preloaded_cards=settings.FRUIT_BUZZ_MAX_PRELOADED_CARDS,
        winning_fruit_count=settings.FRUIT_BUZZ_WINNING_FRUIT_COUNT,
        winning_card_age=random.choice(WINNING_CARD_AGES),
        require_target_fruit=settings.FRUIT_BUZZ_REQUIRE_TARGET_FRUIT,
        target_fruit_edge=random.choice(TARGET_FRUIT_EDGES),
        target_fruit_buffer=settings.FRUIT_BUZZ_TARGET_FRUIT_BUFFER,
        target_fruit_hitbox_padding=settings.FRUIT_BUZZ_TARGET_FRUIT_HITBOX_PADDING,
        round_window_ms=settings.FRUIT_BUZZ_ROUND_WINDOW_MS,
        round_jitter_percent=settings.FRUIT_BUZZ_ROUND_JITTER_PERCENT,
    )


class FruitBuzzGame(BaseModel):
    """Persisted state and network delay allowance for one Fruit Buzz game."""

    current_round: int = Field(ge=0)
    player_lives: int = Field(ge=0)
    bot_lives: int = Field(ge=0)
    # RTT is used to account for network delay when checking whether a buzz
    # reached the server before the bot deadline. Game creation currently uses
    # the one-use calibration result consumed when this game starts.
    network_delay_rtt_ms: int = Field(ge=0)
    # Saved after a player win so a lost final response can be recovered from
    # the status route without issuing a different token for the same game.
    completion_token: str | None = None
    # JTI of the Word Guess token that started the game. A win spends that token,
    # so one Word Guess yields at most one Fruit Buzz token.
    word_guess_jti: str | None = None
    # Keep the last committed score and pile change so status can reconstruct
    # a round result when its action response never reaches the browser.
    last_round_index: int | None = None
    last_round_result: Literal["player_won", "player_lost", "no_fruit_buzz"] | None = (
        None
    )
    last_round_reason: (
        Literal[
            "correct_buzz",
            "late_buzz",
            "wrong_card",
            "wrong_fruit",
            "false_buzz",
            "missed_fruit_buzz",
            "no_fruit_buzz",
        ]
        | None
    ) = None
    last_round_clear_cards: bool | None = None
    # A late buzz's delay belongs to the committed result. Keep it for status
    # recovery, and leave it empty for every other action.
    last_round_late_by_ms: int | None = None
    # Send the required winning card only after settlement, when the answer is
    # no longer useful for buzzing. Status keeps it if the action reply is lost.
    last_round_winning_card_ids: list[str] = Field(default_factory=list)
    rules: FruitBuzzRules = Field(default_factory=default_game_rules)
    rounds: dict[int, FruitBuzzRound]  # round index -> card metadata


def init_game(
    network_delay_rtt_ms: int,
    rules: FruitBuzzRules | None = None,
) -> FruitBuzzGame:
    """Create a game session with the RTT chosen by the start route.

    Args:
        network_delay_rtt_ms (int): Round-trip time used for deadline allowance.
        rules (FruitBuzzRules | None): Per-game rules chosen by the backend, or
            None to copy the current server settings.

    Returns:
        FruitBuzzGame: A new game with starting lives and no prepared cards.
    """

    return FruitBuzzGame(
        current_round=0,
        player_lives=settings.FRUIT_BUZZ_PLAYER_LIVES,
        bot_lives=settings.FRUIT_BUZZ_BOT_LIVES,
        network_delay_rtt_ms=network_delay_rtt_ms,
        rules=(
            rules.model_copy(deep=True) if rules is not None else default_game_rules()
        ),
        rounds={},
    )


def get_public_game_rules(game: FruitBuzzGame) -> FruitBuzzPublicRules:
    """Select display and preload rules without sharing timing or hit-box tolerance.

    Args:
        game (FruitBuzzGame): Game whose rules will be sent to the frontend.

    Returns:
        FruitBuzzPublicRules: Card, preload, and click rules for the response.
    """
    rules = game.rules
    return FruitBuzzPublicRules(
        visible_card_count=rules.visible_card_count,
        max_preloaded_cards=rules.max_preloaded_cards,
        winning_fruit_count=rules.winning_fruit_count,
        winning_card_age=rules.winning_card_age,
        require_target_fruit=rules.require_target_fruit,
        target_fruit_edge=rules.target_fruit_edge,
    )


def add_next_round(game: FruitBuzzGame) -> None:
    """Pick the card that will enter the preload window in a future round.

    Args:
        game (FruitBuzzGame): Game whose future round is being prepared.

    Returns:
        None: The new card is added to ``game.rounds``.
    """

    # There is no fixed round limit. After a round ends, pick one card at the
    # far end of the preload window to replace the card that was just played.
    current_round = game.current_round
    next_round = create_round()
    # For example, round 10 prepares round 13 when the game's limit is three.
    next_round_index = current_round + game.rules.max_preloaded_cards
    game.rounds[next_round_index] = next_round


async def prepare_initial_rounds(
    game: FruitBuzzGame, card_image_conn: RedisConn
) -> list[dict[str, int | str]]:
    """Pick and prepare the first card and the future preload window.

    The current card has index zero. The configured preload count describes
    additional future cards, so an initial count of three prepares indices
    zero through three. Only image IDs are returned; encryption and key saving
    happen when the client fetches each image.

    Args:
        game (FruitBuzzGame): New game whose first cards are needed.
        card_image_conn (RedisConn): Binary connection to the media Redis.

    Returns:
        list[dict[str, int | str]]: Round indices and public image IDs in order.
    """
    if game.current_round != 0 or game.rounds:
        raise ValueError("Initial cards can only be prepared for a new game.")

    cards = []
    for round_index in range(game.rules.max_preloaded_cards + 1):
        # Pick fruit/count first. Preparing then finds a raw PNG in the pool
        # and saves its private template ID, public image ID, and hit boxes.
        game.rounds[round_index] = create_round()
        image_id = await prepare_round_card(game, round_index, card_image_conn)
        cards.append({"round_index": round_index, "image_id": image_id})
    return cards


def update_round_template(
    game: FruitBuzzGame,
    round_index: int,
    image_id: str,
    template_id: str,
    fruit_positions: list[FruitImagePosition] | None = None,
) -> None:
    """Save the selected raw template and fruit hit boxes before reveal.

    The hit boxes stay in the server's game state. When target-fruit checking is
    enabled, reveal_card requires one box for each fruit on the card. The AES
    key is set later, when the frontend fetches the encrypted PNG.

    Args:
        game (FruitBuzzGame): Game containing the card to update.
        round_index (int): Index of the prepared, unrevealed card.
        image_id (str): ID used to identify the clicked card.
        template_id (str): Private Redis ID for the selected raw PNG.
        fruit_positions (list[FruitImagePosition] | None): Optional server-side
            hit boxes for the fruits on the rendered card.

    Returns:
        None: The card metadata is updated in ``game``.
    """

    current_round = game.current_round

    if round_index < current_round:
        raise ValueError(
            "Rounds that were already played cannot be updated and modified."
        )

    if round_index not in game.rounds:
        raise ValueError("The requested round is not yet generated.")
    if game.rounds[round_index].revelation_timestamp is not None:
        raise ValueError("A revealed card cannot be modified.")
    if (
        fruit_positions is not None
        and len(fruit_positions) != game.rounds[round_index].amount
    ):
        raise ValueError("Every fruit on the card needs a hit box.")

    game.rounds[round_index].image_id = image_id
    game.rounds[round_index].template_id = template_id
    if fruit_positions is not None:
        game.rounds[round_index].fruit_positions = fruit_positions


async def prepare_round_card(
    game: FruitBuzzGame, round_index: int, card_image_conn: RedisConn
) -> str:
    """Choose a raw PNG from the pool for a current or future round.

    The round saves the private template ID and hit boxes. It gets a public
    image ID that stays the same when the client fetches it more than once.
    No encrypted copy or AES key is made at this stage.

    Args:
        game (FruitBuzzGame): Game containing the selected round.
        round_index (int): Current or future round to prepare.
        card_image_conn (RedisConn): Binary connection to the media Redis.

    Returns:
        str: Public image ID stored on this round.
    """
    if round_index < game.current_round or round_index not in game.rounds:
        raise ValueError("The requested round is not available for preparation.")
    round_card = game.rounds[round_index]
    if round_card.revelation_timestamp is not None:
        raise ValueError("A revealed card cannot be prepared again.")
    if round_card.image_id is not None and round_card.template_id is not None:
        return round_card.image_id

    template_id, card = await pick_pool_card(
        card_image_conn,
        round_card.fruit,
        round_card.amount,
    )
    # NOTE: A fresh public ID for each round prevents the client from mapping
    # a previously revealed template ID to its fruit and amount on reuse.
    # The private template ID can repeat across games but stays server-side.
    image_id = uuid.uuid4().hex
    update_round_template(
        game, round_index, image_id, template_id, card.fruit_positions
    )
    return image_id


async def encrypt_prepared_round_image(
    game: FruitBuzzGame, round_index: int, card_image_conn: RedisConn
) -> tuple[bytes, int]:
    """Encrypt the selected raw PNG and replace this round's reveal key.

    This runs on every preload request, including retries. The caller must
    save the updated game atomically before returning the ciphertext, so a
    later reveal returns the key matching the latest image response.

    Args:
        game (FruitBuzzGame): Game that owns the requested round.
        round_index (int): Prepared, unrevealed round to encrypt.
        card_image_conn (RedisConn): Binary connection to the media Redis.

    Returns:
        tuple[bytes, int]: Encrypted image and this response's version number.
    """
    round_card = game.rounds.get(round_index)
    if (
        round_card is None
        or round_card.image_id is None
        or round_card.template_id is None
    ):
        raise ValueError("The requested card is not prepared.")
    if round_index < game.current_round or round_card.revelation_timestamp is not None:
        raise ValueError("A played or revealed card cannot receive a new key.")

    card = await get_card_template(
        card_image_conn,
        round_card.template_id,
        round_card.fruit,
        round_card.amount,
    )
    if card is None:
        # The template's grace ended before this preload request, e.g. after
        # a very slow client or a media Redis restart. This round is still
        # hidden, so choose a replacement and update its hit boxes before
        # saving the new key.
        template_id, card = await pick_pool_card(
            card_image_conn, round_card.fruit, round_card.amount
        )
        round_card.template_id = template_id
        round_card.fruit_positions = card.fruit_positions

    encrypted_image, encryption_key = encrypt_image(card.image)
    round_card.encryption_key = encryption_key
    round_card.image_version += 1
    return encrypted_image, round_card.image_version


def get_fruit_buzz_winning_cards(game: FruitBuzzGame) -> dict[str, set[int]]:
    """Map each winning fruit to the visible cards that contribute to its count.

    Return an empty dictionary when there is no Fruit Buzz. Keep fruit types
    separate because several can win at once; the click rule can then choose
    from the contributing cards according to the current settings.

    Args:
        game (FruitBuzzGame): Game at the round being evaluated.

    Returns:
        dict[str, set[int]]: Winning fruit types and their contributing round
            indices, or an empty dictionary when no fruit has the exact count.
    """
    current_round = game.current_round
    # Keep both the total for each fruit and the cards that produced it.
    # The click check needs the card IDs, not just the winning fruit totals.
    fruit_count = dict.fromkeys(AVAILABLE_FRUITS, 0)
    fruit_rounds = {fruit: set() for fruit in AVAILABLE_FRUITS}

    # Walk backward through at most the number of cards visible in one round.
    for round_offset in range(game.rules.visible_card_count):
        round_index = current_round - round_offset

        # Stop when the start of the game is reached.
        if round_index < 0:
            break
        # A prior buzz or missed Fruit Buzz cleared the pile. That card and
        # older cards cannot contribute to the current round's fruit count.
        if game.rounds[round_index].fruit_buzz:
            break

        # Add this card to its fruit total and remember which round supplied it.
        fruit = game.rounds[round_index].fruit
        amount = game.rounds[round_index].amount

        fruit_count[fruit] += amount
        fruit_rounds[fruit].add(round_index)

    # A fruit wins only when its visible total equals the exact required count.
    fruit_rounds = {
        fruit: rounds
        for fruit, rounds in fruit_rounds.items()
        if fruit_count[fruit] == game.rules.winning_fruit_count
    }

    return fruit_rounds


RoundResult = Literal["player_won", "player_lost", "no_fruit_buzz"]
RoundReason = Literal[
    "correct_buzz",
    "late_buzz",
    "wrong_card",
    "wrong_fruit",
    "false_buzz",
    "missed_fruit_buzz",
    "no_fruit_buzz",
]


def _validate_round_action(
    game: FruitBuzzGame,
    round_index: int,
    clicked_card_id: str | None,
    click_x: float | None,
    click_y: float | None,
) -> FruitBuzzRound:
    """Reject finished games, stale rounds, and incomplete buzz requests.

    Args:
        game (FruitBuzzGame): Game receiving the action.
        round_index (int): Round index supplied with the request.
        clicked_card_id (str | None): Clicked image ID, or None to move on.
        click_x (float | None): Horizontal click position normalized to the card.
        click_y (float | None): Vertical click position normalized to the card.

    Returns:
        FruitBuzzRound: The revealed current card when the action is valid.
    """

    if game.player_lives == 0 or game.bot_lives == 0:
        raise ValueError("Game is over.")
    if round_index != game.current_round:
        raise ValueError("This is not the current round.")
    if clicked_card_id is None and (click_x is not None or click_y is not None):
        raise ValueError("A next-card request cannot include click coordinates.")
    if (
        clicked_card_id is not None
        and game.rules.require_target_fruit
        and (click_x is None or click_y is None)
    ):
        raise ValueError("A buzz requires click coordinates.")
    current = game.rounds.get(round_index)
    if current is None or current.revelation_timestamp is None:
        raise ValueError("The current card has not been revealed.")
    return current


def _network_delay_allowance_seconds(game: FruitBuzzGame) -> float:
    """Estimate one-way transit time from the calibrated round-trip time.

    Args:
        game (FruitBuzzGame): Game holding the calibrated RTT in milliseconds.

    Returns:
        float: Estimated client-to-server delay in seconds.
    """
    return (
        game.network_delay_rtt_ms
        / MILLISECONDS_PER_SECOND
        / ROUND_TRIP_TO_ONE_WAY_DIVISOR
    )


def _round_jitter_range_ms(game: FruitBuzzGame) -> float:
    """Calculate the largest timing change allowed by this game's jitter.

    Args:
        game (FruitBuzzGame): Game holding its saved window and jitter percent.

    Returns:
        float: Maximum milliseconds added to or removed from a round.
    """
    return (
        game.rules.round_window_ms
        * game.rules.round_jitter_percent
        / PERCENT_DENOMINATOR
    )


def get_next_card_interval_ms(game: FruitBuzzGame) -> int:
    """Return a frontend interval beyond this game's latest possible deadline.

    After the initial preload, the frontend uses this interval for ordinary
    next-card actions and for the countdown after resuming a paused game.
    Scored rounds also keep their answer visible for a short feedback period.
    Use the maximum percentage-jittered window, clamped to the configured
    minimum. The client starts its card timer after receiving the reveal and
    decrypting the image, so network transit has already begun to elapse and
    needs no additional allowance here.

    Args:
        game (FruitBuzzGame): Game whose round timing was chosen at startup.

    Returns:
        int: Milliseconds to wait between next-card reveal/preload cycles.
    """
    longest_window_ms = math.ceil(
        game.rules.round_window_ms + _round_jitter_range_ms(game)
    )
    return max(settings.FRUIT_BUZZ_MIN_NEXT_CARD_INTERVAL_MS, longest_window_ms)


def _round_deadline(game: FruitBuzzGame, current: FruitBuzzRound) -> float:
    """Return the deadline saved at reveal, or the old fixed deadline.

    Args:
        game (FruitBuzzGame): Game holding the calibrated network delay.
        current (FruitBuzzRound): Revealed card whose deadline is needed.

    Returns:
        float: Deadline on the server's monotonic clock, in seconds.
    """

    # Monotonic timestamps only work while requests use the same server clock.
    # A game cannot move to another host or survive a machine restart as-is.
    if current.revelation_timestamp is None:
        raise ValueError("The current card has not been revealed.")
    if current.buzz_deadline_timestamp is not None:
        return current.buzz_deadline_timestamp
    # Games created before deadlines were stored keep their original window,
    # without adding new random jitter to a round that is already open.
    return (
        current.revelation_timestamp
        + game.rules.round_window_ms / MILLISECONDS_PER_SECOND
        + _network_delay_allowance_seconds(game)
    )


def _hit_target_fruit(
    card: FruitBuzzRound,
    rules: FruitBuzzRules,
    click_x: float | None,
    click_y: float | None,
) -> bool:
    """Check whether the click hits a fruit at the configured card edge.

    The positions are normalized boxes from the rendered image. Compare the
    visible edge of each box, and accept near ties within the configured edge
    buffer. The click box has separate padding proportional to the fruit size.

    Args:
        card (FruitBuzzRound): Card whose fruit boxes are being checked.
        rules (FruitBuzzRules): Rules chosen for this game.
        click_x (float | None): Horizontal click position normalized to the card.
        click_y (float | None): Vertical click position normalized to the card.

    Returns:
        bool: Whether the click hits a fruit at the configured edge.
    """
    if (
        not card.fruit_positions
        or click_x is None
        or click_y is None
        or not math.isfinite(click_x)
        or not math.isfinite(click_y)
    ):
        return False

    # Left/top use the smallest edge value; right/bottom use the largest.
    direction = rules.target_fruit_edge
    if direction == "left":
        edges = [position.x for position in card.fruit_positions]
        target_edge = min(edges)
    elif direction == "right":
        edges = [position.x + position.width for position in card.fruit_positions]
        target_edge = max(edges)
    elif direction == "top":
        edges = [position.y for position in card.fruit_positions]
        target_edge = min(edges)
    elif direction == "bottom":
        edges = [position.y + position.height for position in card.fruit_positions]
        target_edge = max(edges)
    else:
        raise ValueError("Invalid target fruit edge setting.")

    return any(
        abs(edge - target_edge)
        <= rules.target_fruit_buffer + FRUIT_EDGE_COMPARISON_EPSILON
        and position.x - position.width * rules.target_fruit_hitbox_padding
        <= click_x
        <= position.x + position.width * (1 + rules.target_fruit_hitbox_padding)
        and position.y - position.height * rules.target_fruit_hitbox_padding
        <= click_y
        <= position.y + position.height * (1 + rules.target_fruit_hitbox_padding)
        for position, edge in zip(card.fruit_positions, edges)
    )


def _required_winning_card_index(
    game: FruitBuzzGame, winning_cards: dict[str, set[int]]
) -> int | None:
    """Select the oldest or newest visible card contributing to a win."""
    eligible = {index for cards in winning_cards.values() for index in cards}
    if not eligible:
        return None
    return min(eligible) if game.rules.winning_card_age == "oldest" else max(eligible)


def _clicked_winning_card(
    game: FruitBuzzGame,
    winning_cards: dict[str, set[int]],
    clicked_card_id: str,
    click_x: float | None,
    click_y: float | None,
) -> bool:
    """Check the clicked image ID against the current round's winning cards.

    Require the chosen contributing card and optionally a hit on its target fruit.

    Args:
        game (FruitBuzzGame): Game containing the visible cards and image IDs.
        winning_cards (dict[str, set[int]]): Winning fruits and contributing
            round indices in the current round.
        clicked_card_id (str): Image ID supplied with the buzz.
        click_x (float | None): Horizontal click position normalized to the card.
        click_y (float | None): Vertical click position normalized to the card.

    Returns:
        bool: Whether the ID and optional fruit hit match the required card.
    """
    # The winning indices only refer to cards visible in the current round.
    selected_index = _required_winning_card_index(game, winning_cards)
    if selected_index is None:
        return False

    return game.rounds[selected_index].image_id == clicked_card_id and (
        not game.rules.require_target_fruit
        or _hit_target_fruit(game.rounds[selected_index], game.rules, click_x, click_y)
    )


def _score_round(
    game: FruitBuzzGame, has_fruit_buzz: bool, player_won: bool, buzzed: bool
) -> RoundResult:
    """Score a buzz or missed win; leave an ordinary round unchanged.

    Args:
        game (FruitBuzzGame): Game whose player or bot lives may change.
        has_fruit_buzz (bool): Whether this round has a winning fruit count.
        player_won (bool): Whether a valid buzz beat the deadline.
        buzzed (bool): Whether the player buzzed at all.

    Returns:
        RoundResult: Player win, player loss, or no Fruit Buzz.
    """

    if player_won:
        game.bot_lives -= 1
        return "player_won"
    if buzzed or has_fruit_buzz:
        game.player_lives -= 1
        return "player_lost"
    return "no_fruit_buzz"


def eval_round(
    game: FruitBuzzGame,
    round_index: int,
    clicked_card_id: str | None = None,
    click_x: float | None = None,
    click_y: float | None = None,
) -> RoundResult:
    """Settle one buzz or one attempt to move on without buzzing.

    Reject a next-card attempt before the deadline for every round, including
    those without a Fruit Buzz, so the response does not reveal the answer.
    A buzz must reach the server before the deadline and hit an eligible card
    and fruit. The caller must save the changed game before serving another card.
    The exact outcome is saved on the game for status recovery.

    Args:
        game (FruitBuzzGame): Game state to evaluate and advance.
        round_index (int): Current round index supplied with the request.
        clicked_card_id (str | None): Clicked image ID, or None to move on.
        click_x (float | None): Horizontal click position normalized to the card.
        click_y (float | None): Vertical click position normalized to the card.

    Returns:
        RoundResult: Player win, player loss, or no Fruit Buzz.
    """
    current = _validate_round_action(
        game, round_index, clicked_card_id, click_x, click_y
    )

    now = time.monotonic()
    deadline = _round_deadline(game, current)
    if clicked_card_id is None and now < deadline:
        raise ValueError("Round still open.")

    winning_cards = get_fruit_buzz_winning_cards(game)
    has_fruit_buzz = bool(winning_cards)
    clicked_winning_card = clicked_card_id is not None and _clicked_winning_card(
        game, winning_cards, clicked_card_id, click_x, click_y
    )
    player_won = clicked_winning_card and now < deadline
    result = _score_round(game, has_fruit_buzz, player_won, clicked_card_id is not None)

    # Keep the exact cause with the committed score. A lost HTTP response can
    # then be recovered without guessing whether a buzz was late or inaccurate.
    # Report an incorrect click as such even if it arrived after the deadline.
    # Only a click on the required card and fruit can receive a late-buzz reason.
    if clicked_card_id is None:
        reason: RoundReason = "missed_fruit_buzz" if has_fruit_buzz else "no_fruit_buzz"
    elif not has_fruit_buzz:
        reason = "false_buzz"
    elif clicked_winning_card and now >= deadline:
        reason = "late_buzz"
    elif clicked_winning_card:
        reason = "correct_buzz"
    else:
        selected_index = _required_winning_card_index(game, winning_cards)
        assert selected_index is not None
        clicked_eligible = game.rounds[selected_index].image_id == clicked_card_id
        reason = "wrong_fruit" if clicked_eligible else "wrong_card"

    # Any buzz settles and clears the pile, including a wrong buzz. A missed
    # Fruit Buzz also clears it. Earlier cards then cannot count again.
    current.fruit_buzz = clicked_card_id is not None or has_fruit_buzz
    game.last_round_index = round_index
    game.last_round_result = result
    game.last_round_reason = reason
    game.last_round_clear_cards = current.fruit_buzz
    # Only report this after settlement. Exposing the live deadline would let
    # a client time its buzz instead of reacting to the visible cards.
    game.last_round_late_by_ms = (
        max(0, math.ceil((now - deadline) * MILLISECONDS_PER_SECOND))
        if reason == "late_buzz"
        else None
    )
    required_index = _required_winning_card_index(game, winning_cards)
    required_image_id = (
        game.rounds[required_index].image_id if required_index is not None else None
    )
    game.last_round_winning_card_ids = (
        [required_image_id] if required_image_id is not None else []
    )
    game.current_round += 1
    return result


def create_round() -> FruitBuzzRound:
    """Pick the fruit and amount before the card image is generated.

    The raw template ID, fruit positions, and public image ID are added when
    the round is prepared. Its encryption key is added when the client asks
    to preload that image.

    Returns:
        FruitBuzzRound: A card with its fruit type and amount selected.
    """
    fruit, amount = pick_random_card()

    return FruitBuzzRound(
        fruit=fruit,
        amount=amount,
    )


def reveal_card(game: FruitBuzzGame) -> str:
    """Reveal the current card's key and set its deadline on the first request.

    Sample the deadline from the same range for every round, whether or not its
    cards contain a Fruit Buzz. Retrying a reveal returns the same key without
    giving the player more time. The action route returns this round's
    image_version with the key, so the frontend can use the matching
    encrypted preload response when duplicate fetches arrive out of order.

    Args:
        game (FruitBuzzGame): Game whose current card is being revealed.

    Returns:
        str: Encryption key for the current card's image.
    """
    current = game.rounds[game.current_round]
    if game.player_lives == 0 or game.bot_lives == 0:
        raise ValueError("Game is over.")
    if current.encryption_key is None or current.image_id is None:
        raise ValueError("The current card is not ready.")
    if (
        game.rules.require_target_fruit
        and len(current.fruit_positions) != current.amount
    ):
        raise ValueError("The current card is not ready.")
    # Pick jitter only once. Buzzes and next-card requests use this same
    # deadline, so an early next-card response cannot reveal a winning count.

    # NOTE: After a round settles, the next reveal can be delayed until the
    # game session expires; actions preserve its original TTL. This gives
    # humans and slower solvers time to analyze the old pile. Fast solvers can
    # already track it during play, so this does not materially weaken the
    # challenge. A shorter server-side wait limit could enforce a steady pace.
    if current.revelation_timestamp is None:
        revealed_at = time.monotonic()
        jitter_range = _round_jitter_range_ms(game)
        jitter = random.uniform(
            -jitter_range,
            jitter_range,
        )
        current.revelation_timestamp = revealed_at
        current.buzz_deadline_timestamp = (
            revealed_at
            + max(0.0, game.rules.round_window_ms + jitter) / MILLISECONDS_PER_SECOND
            + _network_delay_allowance_seconds(game)
        )
    return current.encryption_key
