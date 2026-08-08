import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import require_production_secret
from app.core.errors import DomainError
from app.core.schema import flatten_nullable_schemas
from app.routers import auth, bulk, dependencies, events, health, todos

PROBLEM_JSON = "application/problem+json"

logger = logging.getLogger(__name__)

# Codes for failures raised by the framework rather than the domain. Anything
# not listed falls back to HTTP_ERROR, so a status we have not thought about
# still arrives in the documented shape.
_HTTP_CODES = {
    401: "UNAUTHENTICATED",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
}


def _problem(status: int, title: str, detail: str, code: str, **extra: object) -> JSONResponse:
    """Every error body the API emits, in one shape (RFC 9457)."""
    return JSONResponse(
        status_code=status,
        media_type=PROBLEM_JSON,
        content={
            "type": "about:blank",
            "title": title,
            "status": status,
            "detail": detail,
            "code": code,
            **extra,
        },
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def handle_domain_error(_: Request, exc: DomainError) -> JSONResponse:
        return _problem(exc.status_code, exc.title, exc.detail, exc.code, **exc.extra)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return _problem(
            422,
            "Request validation failed",
            "One or more fields are invalid.",
            "VALIDATION_ERROR",
            errors=[
                {
                    "field": ".".join(str(p) for p in e["loc"][1:]),
                    "message": _readable(e["msg"]),
                }
                for e in exc.errors()
            ],
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        """Failures raised by the framework, not the domain: unknown routes,
        wrong methods.

        Without this they return Starlette's `{"detail": ...}` as plain
        `application/json` — no `code`, no `status`, different media type. The
        API documents RFC 9457 throughout, so a client that parses one error
        shape would meet a second one the first time it typo'd a URL.
        """
        return _problem(
            exc.status_code,
            str(exc.detail),
            str(exc.detail),
            _HTTP_CODES.get(exc.status_code, "HTTP_ERROR"),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        """Last resort, so a bug does not break the error contract too.

        The detail is deliberately generic — an exception message can carry a
        query fragment or a connection string, and this one reaches the client.
        The traceback is logged instead, because Starlette's own 500 path is
        what normally logs it and registering this handler replaces it.
        """
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return _problem(
            500,
            "Internal server error",
            "Something went wrong on our side. The error has been logged.",
            "INTERNAL_ERROR",
        )


# Pydantic prefixes messages raised from a validator with "Value error, ".
# That prefix names an implementation detail, and these strings are shown
# directly to people filling in a form.
_PYDANTIC_PREFIXES = ("Value error, ", "Assertion failed, ")


def _readable(message: str) -> str:
    for prefix in _PYDANTIC_PREFIXES:
        if message.startswith(prefix):
            return message[len(prefix) :]
    return message


def create_app() -> FastAPI:
    # Fail loudly at boot rather than signing tokens with a committed secret.
    require_production_secret()

    app = FastAPI(
        title="SleekFlow TODO API",
        version="0.1.0",
        description=(
            "A shared TODO list supporting dependencies, recurring tasks, "
            "pagination over 10,000+ items, and concurrent multi-user access "
            "with optimistic concurrency."
        ),
        openapi_tags=[
            {
                "name": "todos",
                "description": "CRUD, listing, status transitions, and soft-delete/restore. "
                "Every mutation requires an ``If-Match`` header (absent → 428, stale → 409).",
            },
            {
                "name": "dependencies",
                "description": "Add and remove dependency edges. A todo cannot start until "
                "every dependency is completed. Cycles are rejected.",
            },
            {
                "name": "auth",
                "description": "Register and sign in. The board is shared — an account "
                "gates access, it does not own todos.",
            },
            {
                "name": "bulk",
                "description": "Batch status changes and deletes with per-item results, so "
                "one blocked or stale todo does not fail the whole selection.",
            },
            {
                "name": "events",
                "description": "A server-sent event stream of committed changes, so open "
                "tabs stay current without polling.",
            },
            {"name": "health", "description": "Liveness check."},
        ],
    )
    app.add_middleware(
        CORSMiddleware,
        # Vite picks the next free port when 5173 is taken, so accept any
        # localhost port in development rather than failing with an opaque
        # "Failed to fetch". Production would pin the real origin here.
        allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["ETag"],
    )
    app.include_router(health.router)
    app.include_router(auth.router)
    # Before todos: "/api/todos/bulk/status" also matches the todo router's
    # "/api/todos/{todo_id}/status", and the first registered route wins. The
    # other order makes every bulk request a 422 on an unparseable UUID.
    app.include_router(bulk.router)
    app.include_router(todos.router)
    app.include_router(dependencies.router)
    app.include_router(events.router)
    register_exception_handlers(app)

    # Post-process the OpenAPI schema so Swagger UI shows field descriptions
    # on nullable fields instead of hiding them behind an anyOf dropdown.
    _original_openapi = app.openapi

    def _openapi() -> dict:
        if app.openapi_schema is not None:
            return app.openapi_schema
        # `_original_openapi()` stores the generated schema on
        # `app.openapi_schema` before returning it, and `flatten_nullable_schemas`
        # edits that same dict in place — which is the only reason the cache
        # above serves a flattened schema rather than the raw one. If flattening
        # is ever changed to return a copy, assign it back here.
        return flatten_nullable_schemas(_original_openapi())

    app.openapi = _openapi  # type: ignore[method-assign]

    return app


app = create_app()

if __name__ == "__main__":
    # A convenience for `python -m app.main` during development — note the
    # reloader. The container does not use this path: its CMD runs uvicorn
    # directly, without reload.
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)

