from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import require_production_secret
from app.core.errors import DomainError
from app.core.schema import flatten_nullable_schemas
from app.routers import auth, bulk, dependencies, events, health, todos

PROBLEM_JSON = "application/problem+json"


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def handle_domain_error(_: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            media_type=PROBLEM_JSON,
            content={
                "type": "about:blank",
                "title": exc.title,
                "status": exc.status_code,
                "detail": exc.detail,
                "code": exc.code,
                **exc.extra,
            },
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            media_type=PROBLEM_JSON,
            content={
                "type": "about:blank",
                "title": "Request validation failed",
                "status": 422,
                "detail": "One or more fields are invalid.",
                "code": "VALIDATION_ERROR",
                "errors": [
                    {
                        "field": ".".join(str(p) for p in e["loc"][1:]),
                        "message": _readable(e["msg"]),
                    }
                    for e in exc.errors()
                ],
            },
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
        return flatten_nullable_schemas(_original_openapi())

    app.openapi = _openapi  # type: ignore[method-assign]

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)

