"""Register the separate authentication challenge routes under /auth."""

from fastapi import APIRouter

from routers.auth_routes import (
    captcha,
    word_guess,
    fruit_buzz,
    fruit_buzz_calibration,
    security_questions,
    token,
)

router = APIRouter(prefix="/auth", tags=["Authorization"])
router.include_router(captcha.router)
router.include_router(word_guess.router)
router.include_router(fruit_buzz_calibration.router)
router.include_router(fruit_buzz.router)
router.include_router(security_questions.router)
router.include_router(token.router)


# Capability tokens authorize the next step in the flow. Every HTTP endpoint
# that consumes one reads it from Authorization: Bearer <token>, never from a
# JSON body or query parameter. The browser WebSocket calibration is the sole
# exception because browser WebSocket APIs cannot set an Authorization header.
# Each token keeps one live session, yields one next token and has a small
# budget for retries (helpers/token_budget.py), so one solved step cannot
# multiply into many.
# A route that creates a session or spends a redemption is a POST, never a GET.

# Authentication Flow:
# 1) Captcha:
#    Request an ID, fetch its image, and submit the answer for a captcha_token.
# 2) Word Guess:
#    Use the captcha_token to start and solve Word Guess for a word_guess_token.
# 3) Fruit Buzz:
#    Use the word_guess_token to start the game. Only winning gives a
#    fruit_buzz_token; losing ends this attempt.
# 4) Security Questions:
#    Use the fruit_buzz_token to answer the questions for a security_token.
# 5) Player-Removal Token:
#    Exchange the security_token for the final remove_player_token.

# Use relatively strict rate limiting here to try and limit bot attack opportunities

# A solved CAPTCHA or Word Guess and a won game keep their one token for a client
# that lost the response, until the token is used for the next step or the
# session's TTL ends. Success spends the parent token, so no newer session can
# replace them. Unsolved sessions are closed by a newer one or expire.
