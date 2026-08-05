from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.errors import DomainError
from app.routers import dependencies, health, todos

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
                    {"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]}
                    for e in exc.errors()
                ],
            },
        )


def create_app() -> FastAPI:
    app = FastAPI(
        title="SleekFlow TODO API",
        version="0.1.0",
        description="Shared TODO list with dependencies, recurrence, and optimistic concurrency.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["ETag"],
    )
    app.include_router(health.router)
    app.include_router(todos.router)
    app.include_router(dependencies.router)
    register_exception_handlers(app)
    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)

