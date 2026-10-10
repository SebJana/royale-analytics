"""Halli Galli game start, card preload, and round action routes.

The browser first completes the calibration WebSocket using its Wordle token.
It POSTs the returned calibration ID in the body and the Wordle token as Bearer
to /halli_galli_id, which spends one of the token's games and closes its
previous game. The response gives the game ID, display rules, initial card
IDs, preload count, next-card interval, and life counts.

For each prepared round, the browser POSTs /halli_galli_card/{game_id}/{round_index}
to fetch encrypted image bytes. The key stays in game state until the browser
POSTs /halli_galli_action/{game_id}/{round_index} with action "reveal". It then
sends "buzz" with the clicked card and required fruit coordinates, or "next"
after the round closes. That action scores the round, reports both life counts,
and, while the game continues, names the next visible card and future preload.
A buzz or missed win can clear the visible pile. The round index and Redis
transaction prevent a second request from scoring the same round. The browser
can GET /halli_galli_status/{game_id} to recover a lost action response. A
player win returns the saved halli_galli_token for the security questions.
"""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from fastapi.security import HTTPAuthorizationCredentials
from fastapi_limiter.depends import RateLimiter
from pydantic import BaseModel, Field
from redis.exceptions import WatchError

from core.deps import AuthStateConn, MediaConn
from core.settings import settings
from helpers.halli_galli_game import (
    HalliGalliGame,
    add_next_round,
    eval_round,
    get_next_card_interval_ms,
    encrypt_prepared_round_image,
    get_public_game_rules,
    init_game,
    prepare_initial_rounds,
    prepare_round_card,
    reveal_card,
)
from helpers.jwt import AvailableTokenTypes, get_access_token_claims
from helpers.token_budget import (
    mint_token,
    open_session,
    queue_token_record,
    queue_token_spend,
)
from models.schema import HalliGalliStartRequest
from redis_service import (
    RedisConn,
    build_auth_state_key,
    consume_auth_state_json,
)
from routers.auth_routes.common import round_token_scheme

router = APIRouter()

# A preload and a game action can change the same saved state. Retry a few
# times if another request commits first.
GAME_STATE_UPDATE_ATTEMPTS = 3


class HalliGalliActionRequest(BaseModel):
    """One reveal, buzz, or next-card action for the current round."""

    action: Literal["reveal", "buzz", "next"]
    clicked_card_id: str | None = Field(
        default=None, max_length=settings.AUTH_ANSWER_MAX_LENGTH
    )
    click_x: float | None = Field(default=None, ge=0, le=1)
    click_y: float | None = Field(default=None, ge=0, le=1)


def _halli_galli_status(game: HalliGalliGame) -> dict:
    """Return progress and the last committed round for response recovery.

    Args:
        game (HalliGalliGame): Saved game whose progress is being reported.

    Returns:
        dict: Current round, life counts, outcome, win token, and last score.
    """
    if game.bot_lives == 0:
        game_status = "player_won"
    elif game.player_lives == 0:
        game_status = "player_lost"
    else:
        game_status = "playing"
    # A losing game cannot expose a completion token. The saved win token is
    # included here so the client can recover it after a lost final response.
    return {
        "current_round": game.current_round,
        "player_lives": game.player_lives,
        "bot_lives": game.bot_lives,
        "game_status": game_status,
        "halli_galli_token": (
            game.completion_token if game_status == "player_won" else None
        ),
        "last_round_index": game.last_round_index,
        "last_round_result": game.last_round_result,
        "last_round_reason": game.last_round_reason,
        "last_round_clear_cards": game.last_round_clear_cards,
        "last_round_late_by_ms": game.last_round_late_by_ms,
        "last_round_winning_card_ids": game.last_round_winning_card_ids,
    }


@router.post(
    "/halli_galli_id",
    dependencies=[Depends(RateLimiter(times=5, seconds=60))],
    responses={
        401: {
            "description": (
                "Wordle token missing or expired (WORDLE_TOKEN_EXPIRED), won or "
                "its games used up (WORDLE_TOKEN_USED_UP), or calibration invalid "
                "(CALIBRATION_INVALID)"
            )
        },
        429: {"description": "Rate limit exceeded, see Retry-After"},
        503: {"description": "Cards not ready yet (IMAGES_NOT_READY), see Retry-After"},
    },
)
async def get_halli_galli_id(
    req: HalliGalliStartRequest,
    auth_state_conn: AuthStateConn,
    card_image_conn: MediaConn,
    response: Response,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(round_token_scheme)
    ],
):
    """Start a Halli Galli game, spending one game of the Wordle token.

    The game replaces the token's previous one, which is deleted, as is the
    solved Wordle that minted the token.

    Challenge keys have no cache version. They remain readable until their TTL
    expires, regardless of data-scraper cache invalidation.

    Args:
        req (HalliGalliStartRequest): One-use ID from the calibration.
        auth_state_conn (AuthStateConn): Versionless auth-state Redis connection.
        card_image_conn (MediaConn): Binary connection to the media Redis.
        response (Response): HTTP response whose cache policy is set here.
        credentials (HTTPAuthorizationCredentials | None): The Wordle token.

    Returns:
        dict: Game ID, public rules, next-card interval, initial image IDs,
            both life counts, and the current game status.

    Raises:
        HTTPException: 401 with WORDLE_TOKEN_EXPIRED, WORDLE_TOKEN_USED_UP or
            CALIBRATION_INVALID.
        MediaPoolEmpty: If no card is ready, answered as 503 IMAGES_NOT_READY.
    """
    invalid_calibration = HTTPException(
        status_code=401,
        detail={
            "code": "CALIBRATION_INVALID",
            "message": "The connection check didn't complete correctly. Try again.",
        },
    )

    claims = (
        get_access_token_claims(credentials.credentials, AvailableTokenTypes.WORDLE)
        if credentials
        else None
    )
    if claims is None:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "WORDLE_TOKEN_EXPIRED",
                "message": "Halli Galli took too long. Restart verification.",
            },
        )

    calibration_key = build_auth_state_key(
        "halli_galli_calibration", req.calibration_id
    )
    calibration = await consume_auth_state_json(auth_state_conn, calibration_key)
    if (
        not isinstance(calibration, dict)
        or calibration.get("wordle_jti") != claims["jti"]
    ):
        raise invalid_calibration

    network_delay_rtt_ms = calibration.get("network_delay_rtt_ms")
    if type(network_delay_rtt_ms) is not int or network_delay_rtt_ms < 0:
        raise invalid_calibration

    # Pick the current card plus the configured number of future cards before
    # returning the game ID. This saves references to raw pool templates, not
    # extra image copies. AES keys are created when the client fetches a card.
    # Prepared BEFORE the charge, so an empty card pool costs no game.
    game_session = init_game(network_delay_rtt_ms=network_delay_rtt_ms)
    game_session.wordle_jti = claims["jti"]
    initial_cards = await prepare_initial_rounds(game_session, card_image_conn)
    game_id = str(uuid.uuid4())

    # Charging and saving happen together, and only after the calibration
    # checks out, so a broken connection check does not cost a game. Raw PNGs
    # live in redis-media; this game state lives in the auth-state Redis.
    opened = await open_session(
        auth_state_conn,
        claims,
        build_auth_state_key("halli_galli", game_id),
        game_session.model_dump_json(),
        settings.CACHE_TTL_HALLI_GALLI,
    )
    if not opened:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "WORDLE_TOKEN_USED_UP",
                "message": "This Wordle can't start another Halli Galli game. Restart verification.",
            },
        )

    # The game ID grants access to later routes. "no-store" tells browsers and
    # proxies not to retain it; "no-cache" would still allow storage. FastAPI
    # copies headers from this injected Response onto the JSON made from the
    # dict below, so the header has to be set before returning that dict.
    response.headers["Cache-Control"] = "no-store"
    return {
        "halli_galli_id": game_id,
        "rules": get_public_game_rules(game_session).model_dump(mode="json"),
        "next_card_interval_ms": get_next_card_interval_ms(game_session),
        "initial_cards": initial_cards,
        **_halli_galli_status(game_session),
    }


@router.post(
    "/halli_galli_card/{game_id}/{round_index}",
    dependencies=[Depends(RateLimiter(times=60, seconds=60))],
    responses={
        404: {"description": "Game or prepared card not found"},
        409: {"description": "Card image changed during preload"},
        429: {"description": "Rate limit exceeded, see Retry-After"},
        503: {"description": "No replacement card ready (IMAGES_NOT_READY)"},
    },
)
async def get_halli_galli_card(
    game_id: str,
    round_index: int,
    auth_state_conn: AuthStateConn,
    card_image_conn: MediaConn,
):
    """Encrypt one raw card and save the key for its future reveal.

    The image is safe to preload before reveal because its AES key stays in
    server-side game state until the card is revealed. A second request for the
    same round creates a new ciphertext and replaces the previous key. This
    endpoint uses POST because each request changes the saved game state.

    Args:
        game_id (str): Halli Galli session ID returned by the start route.
        round_index (int): Prepared card index returned with the game ID.
        auth_state_conn (AuthStateConn): Redis connection for game state.
        card_image_conn (MediaConn): Binary connection to the media Redis.

    Returns:
        Response: Fresh ciphertext with its nonce and image version header.
    """
    # The random game ID is this lookup's capability. The response contains
    # only ciphertext; the reveal route must still control when its key is sent.
    game_key = build_auth_state_key("halli_galli", game_id)
    for _ in range(GAME_STATE_UPDATE_ATTEMPTS):
        # WATCH makes the key update conditional on nobody changing this game
        # after the read. A duplicate fetch then retries with the new state
        # instead of silently overwriting its version or a reveal result.
        async with auth_state_conn.client.pipeline() as pipe:
            await pipe.watch(game_key)
            game_data = await pipe.get(game_key)
            if game_data is None:
                raise HTTPException(
                    status_code=404, detail="Halli Galli game not found"
                )
            game = HalliGalliGame.model_validate_json(game_data)
            try:
                image, image_version = await encrypt_prepared_round_image(
                    game, round_index, card_image_conn
                )
            except ValueError as error:
                raise HTTPException(status_code=404, detail=str(error)) from error

            # After WATCH and the read above, start the transaction that saves
            # the new key and version. EXEC will reject this write if another
            # request changed the game in the meantime, so the request retries.
            pipe.multi()
            # KEEPTTL does not extend the game's lifetime on each preload.
            pipe.set(game_key, game.model_dump_json(), keepttl=True)
            try:
                await pipe.execute()
            except WatchError:
                continue

        # The browser should retain the highest version if duplicate requests
        # complete out of order. Only that response matches the saved key.
        return Response(
            content=image,
            media_type="application/octet-stream",
            headers={
                "Cache-Control": "no-store",
                "X-Halli-Galli-Image-Version": str(image_version),
            },
        )
    raise HTTPException(status_code=409, detail="Card image changed; retry preload")


@router.get(
    "/halli_galli_status/{game_id}",
    responses={404: {"description": "Halli Galli game not found"}},
)
async def get_halli_galli_status(
    game_id: str, auth_state_conn: AuthStateConn, response: Response
):
    """Read saved progress and prepared card IDs after an action response is lost.

    Args:
        game_id (str): ID returned when this game started.
        auth_state_conn (AuthStateConn): Redis connection holding game state.
        response (Response): HTTP response whose cache policy is set here.

    Returns:
        dict: Current round, last score, life counts, outcome, prepared image
            IDs, and a saved completion token when the player won.
    """
    game_key = build_auth_state_key("halli_galli", game_id)
    game_data = await auth_state_conn.client.get(game_key)
    if game_data is None:
        raise HTTPException(status_code=404, detail="Halli Galli game not found")
    game = HalliGalliGame.model_validate_json(game_data)
    # A finished game has no next card to show. Active games return their
    # prepared IDs so a lost action reply cannot strand the preload window.
    current = (
        game.rounds.get(game.current_round)
        if game.player_lives > 0 and game.bot_lives > 0
        else None
    )
    # This GET can return the saved win token. Its URL stays the same throughout
    # the game, so a cache must not keep or replay any status response for it.
    response.headers["Cache-Control"] = "no-store"
    return {
        **_halli_galli_status(game),
        "current_image_id": current.image_id if current is not None else None,
        "prepared_cards": [
            {"round_index": index, "image_id": card.image_id}
            for index, card in sorted(game.rounds.items())
            if current is not None
            and index >= game.current_round
            and card.image_id is not None
        ],
    }


def _validate_action_request(req: HalliGalliActionRequest) -> None:
    """Check which click fields belong to this action before reading the game.

    Args:
        req (HalliGalliActionRequest): Reveal, buzz, or next-card request.

    Returns:
        None: Raises HTTP 422 when the action has missing or extra click fields.
    """
    if req.action == "buzz":
        # A buzz must name the clicked card. The game helper checks whether
        # that card and its optional fruit position satisfy this game's rules.
        if not req.clicked_card_id:
            raise HTTPException(status_code=422, detail="A buzz needs a card ID")
    elif (
        req.clicked_card_id is not None
        or req.click_x is not None
        or req.click_y is not None
    ):
        # Reveal and next-card requests have no click. Accepting one here would
        # make it unclear which action the caller intended to perform.
        raise HTTPException(status_code=422, detail="Only a buzz can include a click")


def _handle_reveal(game: HalliGalliGame, round_index: int) -> dict[str, object]:
    """Reveal the current card and return its matching image key and version.

    Args:
        game (HalliGalliGame): Current game state to update.
        round_index (int): Current card being revealed.

    Returns:
        dict[str, object]: Card ID, image version, and encryption key.
    """
    # reveal_card sets the deadline only once. A repeated reveal therefore
    # returns the same key and timing for the saved image version.
    encryption_key = reveal_card(game)
    current = game.rounds[round_index]
    return {
        "round_index": round_index,
        "image_id": current.image_id,
        "image_version": current.image_version,
        "encryption_key": encryption_key,
    }


async def _refill_preload_window(
    game: HalliGalliGame, card_image_conn: RedisConn
) -> tuple[dict[str, int | str], dict[str, int | str]]:
    """Keep one next card ready and prepare one new card at the far end.

    Args:
        game (HalliGalliGame): Active game after the current round advanced.
        card_image_conn (RedisConn): Binary connection to the media Redis.

    Returns:
        tuple[dict[str, int | str], dict[str, int | str]]: Next visible card
            and the new future card for background preloading.
    """
    # The next visible card was prepared earlier. Return its existing image ID
    # and replace only the card that left the future preload window.
    next_visible = game.rounds[game.current_round]
    next_card = {
        "round_index": game.current_round,
        "image_id": next_visible.image_id,
    }
    add_next_round(game)
    future_index = game.current_round + game.rules.max_preloaded_cards
    future_image_id = await prepare_round_card(game, future_index, card_image_conn)
    preloaded_card = {
        "round_index": future_index,
        "image_id": future_image_id,
    }
    return next_card, preloaded_card


async def _handle_round_end(
    game: HalliGalliGame,
    round_index: int,
    req: HalliGalliActionRequest,
    card_image_conn: RedisConn,
) -> dict[str, object]:
    """Score a buzz or next-card action and prepare another card if needed.

    Args:
        game (HalliGalliGame): Current game state to update.
        round_index (int): Round being settled.
        req (HalliGalliActionRequest): Buzz click or next-card action.
        card_image_conn (RedisConn): Binary connection to the media Redis.

    Returns:
        dict[str, object]: Round result, clear flag, and next card IDs.
    """
    # A next-card request has no clicked ID. eval_round applies the same round
    # deadline and scores a missed Halli Galli when the player did not buzz.
    result = eval_round(
        game, round_index, req.clicked_card_id, req.click_x, req.click_y
    )
    if game.bot_lives == 0:
        # Save the token only on the final player win. A status request can
        # return the same token if the committed win response is lost. Its
        # budget record is queued with the game state by the caller.
        game.completion_token = mint_token(AvailableTokenTypes.HALLI_GALLI)

    response: dict[str, object] = {
        "round_result": result,
        "round_reason": game.last_round_reason,
        "late_by_ms": game.last_round_late_by_ms,
        "winning_card_ids": game.last_round_winning_card_ids,
        "clear_cards": game.rounds[round_index].halli_galli,
        "next_card": None,
        "preloaded_card": None,
    }
    if _halli_galli_status(game)["game_status"] == "playing":
        # Finished games need no further cards. An active game prepares one
        # future card while the next visible card keeps its existing ID.
        next_card, preloaded_card = await _refill_preload_window(game, card_image_conn)
        response["next_card"] = next_card
        response["preloaded_card"] = preloaded_card
    return response


async def _apply_action(
    game: HalliGalliGame,
    round_index: int,
    req: HalliGalliActionRequest,
    card_image_conn: RedisConn,
) -> dict[str, object]:
    """Dispatch the current-round action without handling Redis transactions.

    Args:
        game (HalliGalliGame): Current game state to update.
        round_index (int): Round receiving the action.
        req (HalliGalliActionRequest): Reveal, buzz, or next-card request.
        card_image_conn (RedisConn): Binary connection to the media Redis.

    Returns:
        dict[str, object]: Action-specific response fields.
    """
    if req.action == "reveal":
        return _handle_reveal(game, round_index)
    return await _handle_round_end(game, round_index, req, card_image_conn)


@router.post(
    "/halli_galli_action/{game_id}/{round_index}",
    responses={
        404: {"description": "Halli Galli game not found"},
        409: {"description": "Round or game state changed"},
        503: {"description": "Next card not ready yet (IMAGES_NOT_READY)"},
        422: {"description": "Invalid action or click details"},
    },
)
async def act_on_halli_galli_round(
    game_id: str,
    round_index: int,
    req: HalliGalliActionRequest,
    auth_state_conn: AuthStateConn,
    card_image_conn: MediaConn,
    response: Response,
):
    """reveal, buzz, or move on using one saved current round.

    Reveal returns the matching image key and starts the deadline once. Buzz
    scores a click immediately. Next settles the round only after its deadline.
    The transaction stops concurrent actions from settling one round twice.

    Args:
        game_id (str): ID returned when this game started.
        round_index (int): Round receiving the action.
        req (HalliGalliActionRequest): Action and optional buzz coordinates.
        auth_state_conn (AuthStateConn): Redis connection holding game state.
        card_image_conn (MediaConn): Binary connection to the media Redis.
        response (Response): HTTP response whose cache policy is set here.

    Returns:
        dict: Reveal key or round result, plus lives and game status.
    """
    _validate_action_request(req)

    game_key = build_auth_state_key("halli_galli", game_id)
    for _ in range(GAME_STATE_UPDATE_ATTEMPTS):
        # WATCH covers reveal, buzz, and next-card together. A competing image
        # preload or action makes this request retry against the newer state.
        async with auth_state_conn.client.pipeline() as pipe:
            await pipe.watch(game_key)
            game_data = await pipe.get(game_key)
            if game_data is None:
                raise HTTPException(
                    status_code=404, detail="Halli Galli game not found"
                )
            game = HalliGalliGame.model_validate_json(game_data)
            previous_token = game.completion_token
            # The round index is supplied by the client, so reject a second
            # action for a round that another request has already settled.
            if round_index != game.current_round:
                raise HTTPException(
                    status_code=409, detail="This is not the current round"
                )

            try:
                action_response = await _apply_action(
                    game, round_index, req, card_image_conn
                )
            except ValueError as error:
                raise HTTPException(status_code=409, detail=str(error)) from error

            # Commit the score, token, and new preload reference together.
            # KEEPTTL prevents each action from extending the game session.
            pipe.multi()
            pipe.set(game_key, game.model_dump_json(), keepttl=True)
            if game.completion_token != previous_token:
                # In the same transaction, so a retry after a WatchError never
                # leaves a budget behind for a token that was not handed out.
                # The first security attempt with the token closes this game.
                queue_token_record(pipe, game.completion_token, origin=game_key)
                # The win spends the Wordle token: no further game could
                # replace this one and its saved win token.
                if game.wordle_jti is not None:
                    queue_token_spend(pipe, {"jti": game.wordle_jti})
            try:
                await pipe.execute()
            except WatchError:
                continue

        # Reveal carries a decryption key and a final buzz carries a token.
        # Set the header on FastAPI's injected Response so the returned JSON
        # cannot be stored by a browser or a proxy configured to cache POSTs.
        response.headers["Cache-Control"] = "no-store"
        return {**_halli_galli_status(game), **action_response}
    raise HTTPException(status_code=409, detail="Game changed; retry action")
