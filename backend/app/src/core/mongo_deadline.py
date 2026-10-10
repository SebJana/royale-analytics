"""Bounds the time Mongo can hold a request, and answers 503 when it runs out.

Guarantees:
- All Mongo operations of one HTTP request share one deadline, counted from
  the request's start. Waiting for a reconnect, a pooled connection, server
  selection and each query draw from it, and Mongo itself stops a query that
  would overrun it, so a slow or unreachable Mongo fails a request instead of
  hanging it until nginx gives up.
- Motor runs each operation on a thread of its own executor, and a wait for a
  free thread is not covered. MOTOR_MAX_WORKERS (docker-compose.yml) keeps
  more threads than pooled connections, which moves most queueing into the
  driver's pool, where the deadline applies. Enough waiting operations can
  still occupy every thread.
- A request that ran out of Mongo time gets a 503 with Retry-After, even
  when the route wrapped the driver's error in its own 500 or 502.

Work started outside a request (lifespan tasks such as the search index
sync) has no deadline, so long reads there are not cut off.
"""

from fastapi import Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import JSONResponse
from pymongo.errors import PyMongoError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Receive, Scope, Send

from core.settings import settings
from mongo import is_mongo_timeout, request_deadline

# Documents the 503 on every route; any route that reads Mongo can return it.
MONGO_TIMEOUT_RESPONSES = {
    503: {"description": "The database did not answer in time, retry later"}
}


class MongoDeadlineMiddleware:
    """Pure ASGI middleware that runs each HTTP request under one Mongo deadline.

    The deadline lives in context variables, which tasks started by the
    request inherit, and Motor copies them into the thread that runs each
    operation.
    """

    def __init__(self, app: ASGIApp, timeout_s: float):
        self.app = app
        self.timeout_s = timeout_s

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        with request_deadline(self.timeout_s):
            await self.app(scope, receive, send)


def _mongo_timeout_response() -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "detail": {
                "code": "DATABASE_TIMEOUT",
                "message": "The database did not answer in time",
            }
        },
        headers={"Retry-After": str(settings.MONGO_TIMEOUT_RETRY_AFTER_S)},
    )


async def mongo_error_handler(request: Request, exc: PyMongoError):
    """Answer a driver error that left a route unhandled.

    Args:
        request: The failed request.
        exc: The driver's error.

    Returns:
        JSONResponse: 503 for a timeout, 500 for any other driver error.
    """

    if is_mongo_timeout(exc):
        print(f"[DB] [WARNING] {request.method} {request.url.path} timed out: {exc}")
        return _mongo_timeout_response()
    print(f"[DB] [ERROR] {request.method} {request.url.path} failed: {exc}")
    return JSONResponse(status_code=500, content={"detail": "Internal Server Error"})


async def http_error_handler(request: Request, exc: StarletteHTTPException):
    """Turn a route's 500 or 502 into a 503 when a Mongo timeout caused it.

    Routes catch every failure and raise their own 500 or 502, which hides the
    timeout from ``mongo_error_handler``. Python keeps the caught error as
    the new exception's context, so the chain still shows it.

    Args:
        request: The failed request.
        exc: The route's HTTP error.

    Returns:
        Response: 503 for a 500 or 502 caused by a Mongo timeout, FastAPI's
        default response otherwise.
    """

    # A 502 for a failed Clash Royale API call has no Mongo timeout in its
    # chain, so it stays a 502.
    if exc.status_code in (500, 502) and is_mongo_timeout(exc):
        print(f"[DB] [WARNING] {request.method} {request.url.path} timed out")
        return _mongo_timeout_response()
    return await http_exception_handler(request, exc)
